"""The thin client both Celery (``factoryai.worker``) and Airflow call into (ADR-0005).

ADR-0005 drew the boundary between the two schedulers but promised they would "call the
*same* application use cases through a thin client" with "no business logic" on either
side of it. This module is that client: every function here builds nothing but a use-case
command from a plain dict payload, awaits `execute()`, and returns a plain dict result.
Nothing here decides *whether* a candidate is good enough, *how* a split is assigned, or
*what* counts as a duplicate — those decisions live in the use cases and the domain
entities they call, exactly where ADR-0001 already put them.

Airflow's DAG files (``pipelines/airflow/dags/``) import this module directly inside a
container that has ``factoryai`` installed (see ``deploy/compose/airflow.Dockerfile``);
``factoryai.worker.tasks`` imports it in-process. Neither caller's own code appears here.
"""

from __future__ import annotations

from typing import Any, Protocol

from factoryai.application.use_cases.create_dataset_version import (
    CreateDatasetVersion,
    CreateDatasetVersionCommand,
    SplitRatios,
)
from factoryai.application.use_cases.ingest_image import (
    IngestImage,
    IngestImageCommand,
    IngestOutcome,
)
from factoryai.application.use_cases.promote_model import (
    PromoteModel,
    PromoteModelCommand,
    PromotionGate,
    meets_minimum_bar,
)
from factoryai.application.use_cases.train_model import TrainModel, TrainModelCommand
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.value_objects import (
    Category,
    ImageLabel,
    ModelVersionId,
    StorageLocation,
    UserId,
    parse_uuid,
)


class _PromotionSettings(Protocol):
    @property
    def min_auroc(self) -> float: ...
    @property
    def improvement_margin(self) -> float: ...
    @property
    def max_recall_regression(self) -> float: ...


class _StorageSettings(Protocol):
    @property
    def bucket_raw(self) -> str: ...


class _Settings(Protocol):
    @property
    def storage(self) -> _StorageSettings: ...
    @property
    def promotion(self) -> _PromotionSettings: ...


class Container(Protocol):
    """Exactly what this module needs from a composition root.

    A structural type, not `factoryai.bootstrap.container.Container` itself: this module is
    called from both a Celery worker (the real container) and tests (a duck-typed fake, see
    `tests/unit/test_pipeline_client.py`), and a `Protocol` is what lets both satisfy the
    same signature without the fake having to subclass a dataclass built around live
    settings and `cached_property` adapters (see `bootstrap/container.py`'s own docstring
    for why that dataclass isn't meant to be partially faked that way).
    """

    @property
    def settings(self) -> _Settings:
        """Layered configuration, as read by every function in this module."""

    @property
    def object_store(self) -> ObjectStore:
        """Where :func:`ingest_from_object_store` reads staged images from."""

    def unit_of_work(self) -> UnitOfWork:
        """Return a fresh unit of work."""

    def ingest_image_use_case(self) -> IngestImage:
        """Build the :class:`IngestImage` use case."""

    def create_dataset_version_use_case(self) -> CreateDatasetVersion:
        """Build the :class:`CreateDatasetVersion` use case."""

    def train_model_use_case(self) -> TrainModel:
        """Build the :class:`TrainModel` use case."""

    def promote_model_use_case(self) -> PromoteModel:
        """Build the :class:`PromoteModel` use case."""


async def ingest_from_object_store(
    container: Container,
    *,
    category: str,
    prefix: str,
    label: str = "unlabeled",
    uploaded_by: UserId | None = None,
) -> dict[str, Any]:
    """Ingest every object under ``prefix`` in the raw bucket, one :class:`IngestImage` call each.

    The Airflow-facing counterpart to ``factoryai ingest`` (Phase 3, filesystem-sourced):
    a scheduled data-validation run has no host filesystem to scan, only the object store a
    camera/upload pipeline already writes into — see ADR-0013.
    """
    category_vo = Category.parse(category)
    label_vo = ImageLabel(label)
    use_case = container.ingest_image_use_case()
    bucket = container.settings.storage.bucket_raw

    counts = {outcome.value: 0 for outcome in IngestOutcome}
    keys = [key async for key in container.object_store.list_keys(bucket, prefix=prefix)]
    for key in keys:
        payload = await container.object_store.get(StorageLocation(bucket=bucket, key=key))
        result = await use_case.execute(
            IngestImageCommand(
                category=category_vo,
                filename=key.rsplit("/", 1)[-1],
                payload=payload,
                label=label_vo,
                uploaded_by=uploaded_by,
            )
        )
        counts[result.outcome.value] += 1
    return {"scanned": len(keys), **counts}


async def version_dataset(
    container: Container, payload: dict[str, Any], *, created_by: UserId | None = None
) -> dict[str, Any]:
    """Freeze a new dataset version from a plain payload.

    See :class:`CreateDatasetVersionCommand`.
    """
    ratios = payload.get("split_ratios") or {}
    command = CreateDatasetVersionCommand(
        dataset_name=payload["dataset_name"],
        category=Category.parse(payload["category"]),
        version_tag=payload["version_tag"],
        seed=payload.get("seed", 42),
        split_ratios=SplitRatios(**ratios) if ratios else SplitRatios(),
        note=payload.get("note", ""),
        created_by=created_by,
    )
    result = await container.create_dataset_version_use_case().execute(command)
    return {
        "dataset_id": str(result.dataset_id),
        "version_id": str(result.version_id),
        "version_tag": result.version_tag,
        "dvc_hash": result.dvc_hash,
        "image_count": result.image_count,
    }


async def train(
    container: Container, payload: dict[str, Any], *, started_by: UserId | None = None
) -> dict[str, Any]:
    """Run one training pass from a plain payload — see :class:`TrainModelCommand`."""
    command = TrainModelCommand(
        dataset_name=payload["dataset_name"],
        dataset_version_tag=payload["dataset_version_tag"],
        category=Category.parse(payload["category"]),
        model_name=payload["model_name"],
        backbone=payload.get("backbone"),
        hyperparameters=payload.get("hyperparameters", {}),
        image_size=tuple(payload.get("image_size", (256, 256))),
        seed=payload.get("seed", 42),
        device=payload.get("device", "auto"),
        note=payload.get("note", ""),
        started_by=started_by,
    )
    result = await container.train_model_use_case().execute(command)
    return {
        "experiment_id": str(result.experiment_id),
        "model_version_id": str(result.model_version_id),
        "mlflow_run_id": result.mlflow_run_id,
        "registry_version": result.registry_version,
        "image_auroc": result.metrics.image_auroc,
        "recall": result.metrics.recall,
    }


async def evaluate(container: Container, *, model_version_id: str) -> dict[str, Any]:
    """Check a freshly trained model against the gate's absolute floor, nothing more.

    Deliberately not the full promotion gate: comparing against the current incumbent is
    :class:`~factoryai.application.use_cases.promote_model.PromoteModel`'s job alone, since
    only it holds a transaction across reading the incumbent and recording the outcome.
    This is the cheap early-exit check a retraining pipeline runs before ever attempting
    that real, auditable call — see :func:`~factoryai.application.use_cases.promote_model.
    meets_minimum_bar` and ADR-0013.
    """
    promotion = container.settings.promotion
    gate = PromotionGate(
        min_auroc=promotion.min_auroc,
        improvement_margin=promotion.improvement_margin,
        max_recall_regression=promotion.max_recall_regression,
    )
    async with container.unit_of_work() as uow:
        model = await uow.models.get(ModelVersionId(parse_uuid(model_version_id)))
    passed = meets_minimum_bar(model.metrics, gate)
    return {
        "model_version_id": model_version_id,
        "image_auroc": model.metrics.image_auroc,
        "min_auroc": gate.min_auroc,
        "passed": passed,
    }


async def deploy(
    container: Container,
    *,
    category: str,
    model_version_id: str,
    reason: str = "",
    actor_id: UserId | None = None,
) -> dict[str, Any]:
    """Attempt promotion — see :class:`PromoteModelCommand`.

    Raises:
        PromotionRejectedError: If the candidate fails the gate. The rejection is still
            durably recorded before this propagates — see ``PromoteModel.execute``'s
            docstring — so a caller catching this to mark a DAG task "failed" is not
            silently losing the rejection.
    """
    command = PromoteModelCommand(
        category=Category.parse(category),
        candidate_model_version_id=ModelVersionId(parse_uuid(model_version_id)),
        reason=reason,
        actor_id=actor_id,
    )
    result = await container.promote_model_use_case().execute(command)
    return {
        "model_version_id": str(result.model_version_id),
        "previous_model_version_id": (
            str(result.previous_model_version_id) if result.previous_model_version_id else None
        ),
        "comparison_report": result.comparison_report,
    }


async def generate_drift_report(container: Container, payload: dict[str, Any]) -> dict[str, Any]:
    """Generate a drift report.

    Raises:
        NotImplementedError: Always. Drift detection is Phase 11 scope — see
            ``docs/ROADMAP.md`` Phase 10's scope-cut note and
            ``factoryai.worker.tasks.run_drift_report``, which documents the identical cut
            on the Celery side.
    """
    raise NotImplementedError(
        "drift report generation requires the drift detector built in Phase 11"
    )
