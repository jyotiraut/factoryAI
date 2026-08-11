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
from factoryai.application.use_cases.promote_model import (
    PromoteModelCommand,
    PromoteModelResult,
)
from factoryai.application.use_cases.register_user import RegisterUserCommand
from factoryai.application.use_cases.rollback_deployment import (
    NoPriorProductionVersionError,
    NothingToRollBackError,
    RollbackDeploymentCommand,
    RollbackDeploymentResult,
)
from factoryai.application.use_cases.train_model import load_training_config
from factoryai.bootstrap.container import build_container
from factoryai.domain.entities import EvaluationMetrics
from factoryai.domain.errors import (
    DatasetVersionTagExistsError,
    EmailAlreadyRegisteredError,
    EmptyDatasetVersionError,
    EntityNotFoundError,
    PromotionRejectedError,
)
from factoryai.domain.value_objects import (
    Category,
    ImageLabel,
    ModelVersionId,
    UserRole,
    parse_uuid,
)
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

model_app = typer.Typer(
    name="model", help="Model promotion and rollback commands (Phase 6).", no_args_is_help=True
)

user_app = typer.Typer(
    name="user", help="Account management commands (Phase 8).", no_args_is_help=True
)

audit_app = typer.Typer(
    name="audit", help="Audit chain inspection commands (Phase 8).", no_args_is_help=True
)


@app.callback()
def main() -> None:
    """Keep the CLI a command group.

    Without an explicit callback, Typer collapses a single-command application into that
    command, so ``factoryai version`` would be parsed as an unexpected argument. The
    callback preserves the ``factoryai <command>`` shape that later phases rely on.
    """


app.add_typer(dataset_app, name="dataset")
app.add_typer(model_app, name="model")
app.add_typer(user_app, name="user")
app.add_typer(audit_app, name="audit")


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


@model_app.command("promote")
def model_promote(
    category: str = typer.Option(..., "--category", help="MVTec category code, e.g. 'bottle'."),
    model_version_id: str = typer.Option(
        ..., "--model-version-id", help="The candidate model version's UUID."
    ),
    environment: str = typer.Option(
        "production", "--environment", help="Target environment for the deployment record."
    ),
    reason: str = typer.Option("", "--reason", help="Free-text justification."),
) -> None:
    """Promote a candidate model version to production, if it clears the automated gate.

    A rejected candidate is still recorded as a ``Deployment`` (action ``reject``) with the
    full numeric comparison — nothing about a failed promotion is silently dropped.
    """
    settings = get_settings()
    configure_logging(
        level=settings.log_level, log_format=settings.log_format, service="factoryai-cli"
    )
    exit_code = asyncio.run(
        _model_promote_async(category, model_version_id, environment, reason, settings)
    )
    raise typer.Exit(code=exit_code)


async def _model_promote_async(
    category: str, model_version_id: str, environment: str, reason: str, settings: Settings
) -> int:
    """Run the promotion and return the process exit code."""
    container = build_container(settings)
    use_case = container.promote_model_use_case()
    try:
        result = await use_case.execute(
            PromoteModelCommand(
                category=Category.parse(category),
                candidate_model_version_id=ModelVersionId(parse_uuid(model_version_id)),
                environment=environment,
                reason=reason,
            )
        )
    except PromotionRejectedError as exc:
        typer.echo(f"Promotion rejected: {exc.message}")
        return 1
    except EntityNotFoundError as exc:
        typer.echo(f"Could not promote: {exc.message}")
        return 1
    finally:
        await container.dispose()

    _echo_promotion_result(result)
    return 0


def _echo_promotion_result(result: PromoteModelResult) -> None:
    """Print the promotion outcome and its comparison report."""
    typer.echo(f"Promoted {result.model_version_id} to production")
    if result.previous_model_version_id is not None:
        typer.echo(f"  replaced      {result.previous_model_version_id}")
    for key, value in result.comparison_report.items():
        typer.echo(f"  {key:<24} {value}")


@model_app.command("rollback")
def model_rollback(
    category: str = typer.Option(..., "--category", help="MVTec category code, e.g. 'bottle'."),
    to: str | None = typer.Option(
        None,
        "--to",
        help="The model version UUID to restore. Defaults to the most recently displaced one.",
    ),
    environment: str = typer.Option(
        "production", "--environment", help="Target environment for the deployment record."
    ),
    reason: str = typer.Option("", "--reason", help="Free-text justification."),
) -> None:
    """Restore a prior production version, displacing whatever is serving now.

    No gate runs here — the target already earned production once, when it was first
    promoted.
    """
    settings = get_settings()
    configure_logging(
        level=settings.log_level, log_format=settings.log_format, service="factoryai-cli"
    )
    exit_code = asyncio.run(_model_rollback_async(category, to, environment, reason, settings))
    raise typer.Exit(code=exit_code)


async def _model_rollback_async(
    category: str, to: str | None, environment: str, reason: str, settings: Settings
) -> int:
    """Run the rollback and return the process exit code."""
    container = build_container(settings)
    use_case = container.rollback_deployment_use_case()
    try:
        result = await use_case.execute(
            RollbackDeploymentCommand(
                category=Category.parse(category),
                environment=environment,
                target_model_version_id=ModelVersionId(parse_uuid(to)) if to else None,
                reason=reason,
            )
        )
    except (NothingToRollBackError, NoPriorProductionVersionError, EntityNotFoundError) as exc:
        typer.echo(f"Could not roll back: {exc.message}")
        return 1
    finally:
        await container.dispose()

    _echo_rollback_result(result)
    return 0


def _echo_rollback_result(result: RollbackDeploymentResult) -> None:
    """Print the rollback outcome."""
    typer.echo(f"Rolled back to {result.model_version_id}")
    typer.echo(f"  replaced      {result.previous_model_version_id}")


@user_app.command("create")
def user_create(
    email: str = typer.Option(..., "--email", help="Login identifier."),
    role: str = typer.Option(
        ..., "--role", help="One of: viewer, operator, ml_engineer, administrator."
    ),
    display_name: str = typer.Option("", "--display-name", help="Optional human-readable name."),
    password: str | None = typer.Option(
        None,
        "--password",
        help="Plaintext password. Omit to be prompted (recommended: avoids shell history).",
    ),
) -> None:
    """Create a new account, including the very first administrator.

    This is the bootstrap path ``POST /auth/register`` cannot be: that route requires an
    already-authenticated administrator, so the first one has to come from somewhere else
    a trusted operator already controls — a local shell on the deployment host.
    """
    settings = get_settings()
    configure_logging(
        level=settings.log_level, log_format=settings.log_format, service="factoryai-cli"
    )
    try:
        role_vo = UserRole(role)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in UserRole)
        typer.echo(f"Invalid --role {role!r}; must be one of: {allowed}")
        raise typer.Exit(code=2) from exc
    resolved_password = password or typer.prompt(
        "Password", hide_input=True, confirmation_prompt=True
    )
    exit_code = asyncio.run(
        _user_create_async(email, role_vo, display_name, resolved_password, settings)
    )
    raise typer.Exit(code=exit_code)


async def _user_create_async(
    email: str, role: UserRole, display_name: str, password: str, settings: Settings
) -> int:
    """Run account creation and return the process exit code."""
    container = build_container(settings)
    use_case = container.register_user_use_case()
    try:
        result = await use_case.execute(
            RegisterUserCommand(
                email=email, password=password, role=role, display_name=display_name
            )
        )
    except EmailAlreadyRegisteredError as exc:
        typer.echo(f"Could not create user: {exc.message}")
        return 1
    finally:
        await container.dispose()

    typer.echo(f"Created user {result.user_id} ({email}, role={role.value})")
    return 0


@audit_app.command("verify")
def audit_verify() -> None:
    """Walk the entire audit chain and report the first tampered or deleted row, if any.

    Exits non-zero when a broken link is found, so this can run as a scheduled integrity
    check rather than only ever being read by a human.
    """
    settings = get_settings()
    configure_logging(
        level=settings.log_level, log_format=settings.log_format, service="factoryai-cli"
    )
    exit_code = asyncio.run(_audit_verify_async(settings))
    raise typer.Exit(code=exit_code)


async def _audit_verify_async(settings: Settings) -> int:
    """Run chain verification and return the process exit code."""
    container = build_container(settings)
    use_case = container.verify_audit_chain_use_case()
    try:
        result = await use_case.execute()
    finally:
        await container.dispose()

    typer.echo(f"Examined {result.total_events} audit record(s)")
    if result.is_intact:
        typer.echo("Chain is intact: every link verified.")
        return 0
    typer.echo(f"TAMPERING DETECTED: chain breaks at sequence {result.first_broken_sequence}")
    return 1


@app.command()
def serve(
    host: str | None = typer.Option(None, "--host", help="Overrides API_HOST."),
    port: int | None = typer.Option(None, "--port", help="Overrides API_PORT."),
    reload: bool = typer.Option(False, "--reload", help="Auto-restart on code changes (dev only)."),
) -> None:
    """Run the inference service (Phase 7) with uvicorn.

    On Linux this is equivalent to ``uvicorn factoryai.api.main:app``. On Windows it is
    not a thin wrapper: ``uvicorn.run(...)`` picks ``ProactorEventLoop`` for its
    single-process main loop by design (``uvicorn.loops.asyncio.asyncio_loop_factory``),
    which is exactly the loop psycopg's async driver refuses to run under
    (``shared/asyncio_compat.py``). Driving ``Server.serve()`` directly, inside our own
    ``asyncio.run()``, is what lets the already-configured
    ``WindowsSelectorEventLoopPolicy`` actually take effect instead of being silently
    overridden — plain ``uvicorn factoryai.api.main:app`` on Windows will hit this and
    fail every database call from ``/health/ready`` onwards.
    """
    import asyncio

    import uvicorn

    settings = get_settings()
    config = uvicorn.Config(
        "factoryai.api.main:app",
        host=host or settings.api.host,
        port=port or settings.api.port,
        reload=reload,
    )
    asyncio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":  # pragma: no cover
    app()
