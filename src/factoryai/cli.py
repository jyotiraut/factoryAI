"""FactoryAI command line interface.

The CLI is a presentation adapter: every command resolves dependencies from the
composition root, builds a command object and invokes an application use case. No business
logic lives here.

Commands are added as their use cases land — ``ingest`` in Phase 3, ``dataset`` in Phase 4,
``train`` in Phase 5 (see ``docs/ROADMAP.md``).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from factoryai import __version__
from factoryai.application.use_cases.create_dataset_version import (
    CreateDatasetVersionCommand,
    SplitRatios,
)
from factoryai.application.use_cases.ingest_image import (
    BatchIngestReport,
    IngestImageCommand,
    IngestImageResult,
    IngestOutcome,
)
from factoryai.application.use_cases.train_model import load_training_config
from factoryai.bootstrap.container import build_container
from factoryai.domain.entities import EvaluationMetrics
from factoryai.domain.errors import DatasetVersionTagExistsError, EmptyDatasetVersionError
from factoryai.domain.value_objects import Category, ImageLabel
from factoryai.shared.asyncio_compat import configure_event_loop_policy
from factoryai.shared.config import Settings, get_settings
from factoryai.shared.console import configure_stdio_encoding
from factoryai.shared.logging import configure_logging, get_logger

configure_event_loop_policy()
configure_stdio_encoding()

app = typer.Typer(
    name="factoryai",
    help="Industrial Visual Inspection Platform - command line interface.",
    no_args_is_help=True,
    add_completion=False,
)

_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"})

dataset_app = typer.Typer(
    name="dataset", help="Dataset versioning commands (Phase 4).", no_args_is_help=True
)


@app.callback()
def main() -> None:
    """Keep the CLI a command group.

    Without an explicit callback, Typer collapses a single-command application into that
    command, so ``factoryai version`` would be parsed as an unexpected argument. The
    callback preserves the ``factoryai <command>`` shape that later phases rely on.
    """


app.add_typer(dataset_app, name="dataset")


@app.command()
def version() -> None:
    """Print the installed FactoryAI version."""
    typer.echo(__version__)


@app.command()
def ingest(
    path: Path = typer.Option(
        ...,
        "--path",
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Directory of images to ingest, scanned recursively.",
    ),
    category: str = typer.Option(..., "--category", help="MVTec category code, e.g. 'bottle'."),
    dataset: str = typer.Option(
        "raw",
        "--dataset",
        help=(
            "Free-text label recorded on each image's metadata. Grouping images into a "
            "real dataset version is Phase 4; this tags them for that later step."
        ),
    ),
    label: str = typer.Option(
        "unlabeled",
        "--label",
        help=(
            "Ground truth for every image in this batch: 'good', 'defect' or 'unlabeled'. "
            "A curated benchmark (MVTec's train/good vs. test/broken_large) states this "
            "directly, so one invocation per source folder can pass it through; a "
            "production camera feed would leave this as the default."
        ),
    ),
    report_path: Path | None = typer.Option(
        None, "--report-path", help="Write the JSON batch report to this file."
    ),
) -> None:
    """Validate, hash, store and record every image found under PATH.

    Nothing under PATH is trusted: each file is decoded, checked against the configured
    validation rules, and checked for exact and near-duplicates before it is stored. A
    file that fails any of that is reported, not silently skipped, and does not stop the
    rest of the batch from being processed.
    """
    settings = get_settings()
    configure_logging(
        level=settings.log_level, log_format=settings.log_format, service="factoryai-cli"
    )
    try:
        label_vo = ImageLabel(label)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in ImageLabel)
        typer.echo(f"Invalid --label {label!r}; must be one of: {allowed}")
        raise typer.Exit(code=2) from exc
    exit_code = asyncio.run(_ingest_async(path, category, dataset, label_vo, report_path, settings))
    raise typer.Exit(code=exit_code)


async def _ingest_async(
    path: Path,
    category: str,
    dataset: str,
    label: ImageLabel,
    report_path: Path | None,
    settings: Settings,
) -> int:
    """Run the ingestion batch and return the process exit code."""
    log = get_logger(__name__)
    category_vo = Category.parse(category)
    files = sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file() and candidate.suffix.lower() in _IMAGE_EXTENSIONS
    )
    if not files:
        typer.echo(f"No image files found under {path}")
        return 1

    container = build_container(settings)
    use_case = container.ingest_image_use_case()
    results: list[IngestImageResult] = []
    try:
        for file_path in files:
            command = IngestImageCommand(
                category=category_vo,
                filename=file_path.name,
                payload=file_path.read_bytes(),
                label=label,
                metadata={"ingest_batch": dataset},
            )
            try:
                result = await use_case.execute(command)
            except Exception as exc:
                # One file's infrastructure failure must not sink the rest of the batch.
                log.exception("ingest_failed", filename=file_path.name)
                result = IngestImageResult(
                    outcome=IngestOutcome.REJECTED,
                    filename=file_path.name,
                    failures=(f"internal_error: {exc}",),
                )
            results.append(result)
            typer.echo(f"{result.outcome.value:>9}  {result.filename}")
    finally:
        await container.dispose()

    report = BatchIngestReport(results=tuple(results))
    typer.echo(report.summary())
    if report_path is not None:
        report_path.write_text(report.to_json(), encoding="utf-8")
        typer.echo(f"Report written to {report_path}")
    return 0


@dataset_app.command("version")
def dataset_version(
    dataset_name: str = typer.Option(
        ..., "--dataset", help="Named dataset this version belongs to; created on first use."
    ),
    category: str = typer.Option(..., "--category", help="MVTec category code, e.g. 'bottle'."),
    tag: str = typer.Option(
        ..., "--tag", help="Unique, human-readable version tag, e.g. 'bottle-v1'."
    ),
    note: str = typer.Option("", "--note", help="Optional free-text description of what changed."),
    train: float = typer.Option(0.7, "--train", help="Fraction of images in the train split."),
    val: float = typer.Option(0.15, "--val", help="Fraction of images in the val split."),
    test: float = typer.Option(0.15, "--test", help="Fraction of images in the test split."),
    seed: int = typer.Option(42, "--seed", help="Seed driving the deterministic split assignment."),
) -> None:
    """Freeze CATEGORY's current trainable images into a new, versioned snapshot.

    Selects every valid, trainable image for CATEGORY, assigns each a train/val/test
    partition deterministically (same seed, same trainable set -> same split every time),
    materialises a manifest, version-controls it with DVC, and records a ``DatasetVersion``
    row carrying the DVC hash and the current Git commit (ADR-0006).
    """
    settings = get_settings()
    configure_logging(
        level=settings.log_level, log_format=settings.log_format, service="factoryai-cli"
    )
    try:
        split_ratios = SplitRatios(train=train, val=val, test=test)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
    exit_code = asyncio.run(
        _dataset_version_async(dataset_name, category, tag, note, split_ratios, seed, settings)
    )
    raise typer.Exit(code=exit_code)


async def _dataset_version_async(
    dataset_name: str,
    category: str,
    tag: str,
    note: str,
    split_ratios: SplitRatios,
    seed: int,
    settings: Settings,
) -> int:
    """Run dataset-version creation and return the process exit code."""
    container = build_container(settings)
    use_case = container.create_dataset_version_use_case()
    try:
        result = await use_case.execute(
            CreateDatasetVersionCommand(
                dataset_name=dataset_name,
                category=Category.parse(category),
                version_tag=tag,
                seed=seed,
                split_ratios=split_ratios,
                note=note,
            )
        )
    except (DatasetVersionTagExistsError, EmptyDatasetVersionError) as exc:
        typer.echo(f"Could not create version: {exc.message}")
        return 1
    finally:
        await container.dispose()

    typer.echo(f"Created {result.version_tag} ({result.image_count} images)")
    typer.echo(f"  dvc_hash    {result.dvc_hash}")
    typer.echo(f"  git_commit  {result.git_commit}")
    splits = ", ".join(f"{split.value}={count}" for split, count in result.split_counts.items())
    typer.echo(f"  splits      {splits}")
    labels = ", ".join(f"{label.value}={count}" for label, count in result.class_balance.items())
    typer.echo(f"  labels      {labels}")
    typer.echo(f"  checksum    {result.content_checksum}")
    return 0


@dataset_app.command("checkout")
def dataset_checkout(
    dataset_name: str = typer.Option(..., "--dataset", help="The dataset the version belongs to."),
    tag: str = typer.Option(..., "--tag", help="The version tag to check out."),
) -> None:
    """Pull the exact, versioned bytes for a dataset version from the DVC remote.

    Reproduces the manifest DVC has stored for TAG — ``git checkout <commit>`` to move the
    code itself onto the matching revision is left to the caller (ADR-0006): this command
    only recovers the data half of "which data produced this model".
    """
    settings = get_settings()
    configure_logging(
        level=settings.log_level, log_format=settings.log_format, service="factoryai-cli"
    )
    exit_code = asyncio.run(_dataset_checkout_async(dataset_name, tag, settings))
    raise typer.Exit(code=exit_code)


async def _dataset_checkout_async(dataset_name: str, tag: str, settings: Settings) -> int:
    """Run the checkout and return the process exit code."""
    container = build_container(settings)
    try:
        async with container.unit_of_work() as uow:
            dataset = await uow.datasets.find_dataset_by_name(dataset_name)
            if dataset is None:
                typer.echo(f"No dataset named {dataset_name!r}")
                return 1
            version = await uow.datasets.find_version_by_tag(dataset.id, tag)
            if version is None:
                typer.echo(f"Dataset {dataset_name!r} has no version tagged {tag!r}")
                return 1
        payload = await container.version_control.pull(f"{dataset_name}/{tag}.json")
    finally:
        await container.dispose()

    manifest = json.loads(payload)
    typer.echo(
        f"Checked out {tag}: {len(manifest['images'])} images "
        f"(dvc_hash={version.dvc_hash}, git_commit={version.git_commit})"
    )
    return 0


@app.command()
def train(
    config: Path = typer.Option(
        ...,
        "--config",
        exists=True,
        dir_okay=False,
        help="Training config YAML, e.g. configs/bottle/patchcore.yaml.",
    ),
) -> None:
    """Fit, evaluate, log and register a model from a training config.

    Every lineage fact ADR-0004 asks for — dataset version, Git commit, config hash,
    hardware — is recorded automatically; nothing here is optional or a CLI flag.
    """
    settings = get_settings()
    configure_logging(
        level=settings.log_level, log_format=settings.log_format, service="factoryai-cli"
    )
    exit_code = asyncio.run(_train_async(config, settings))
    raise typer.Exit(code=exit_code)


async def _train_async(config_path: Path, settings: Settings) -> int:
    """Run one training cycle and return the process exit code."""
    log = get_logger(__name__)
    command = load_training_config(config_path)
    container = build_container(settings)
    use_case = container.train_model_use_case()
    try:
        result = await use_case.execute(command)
    except Exception as exc:
        log.exception("train_failed", config=str(config_path))
        typer.echo(f"Training failed: {exc}")
        return 1
    finally:
        await container.dispose()

    typer.echo(f"Registered {result.registry_name} v{result.registry_version}")
    typer.echo(f"  mlflow_run_id  {result.mlflow_run_id}")
    typer.echo(f"  training_time  {result.training_time_seconds:.1f}s")
    _echo_metrics(result.metrics)
    return 0


def _echo_metrics(metrics: EvaluationMetrics) -> None:
    """Print every evaluation metric present, one per line."""
    typer.echo(f"  image_auroc    {metrics.image_auroc:.4f}")
    typer.echo(f"  precision      {metrics.precision:.4f}")
    typer.echo(f"  recall         {metrics.recall:.4f}")
    typer.echo(f"  f1             {metrics.f1:.4f}")
    typer.echo(f"  threshold      {metrics.threshold:.4f}")
    if metrics.pixel_auroc is not None:
        typer.echo(f"  pixel_auroc    {metrics.pixel_auroc:.4f}")
    if metrics.confusion_matrix is not None:
        tn, fp, fn, tp = metrics.confusion_matrix
        typer.echo(f"  confusion      tn={tn} fp={fp} fn={fn} tp={tp}")


if __name__ == "__main__":  # pragma: no cover
    app()
