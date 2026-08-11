"""Repository ports and the transactional boundary that groups them.

Repositories speak in entities, never in rows. A repository method that returned an ORM
model would defeat the whole layering, so the return types here are all domain types.

Query methods are kept deliberately specific — ``list_trainable`` rather than a generic
``find(**filters)``. A generic query interface pushes the query logic into the caller and
makes it impossible to index for, which is how metadata tables get slow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from types import TracebackType
from typing import Self

from factoryai.domain.entities import (
    AuditEvent,
    Dataset,
    DatasetVersion,
    Deployment,
    DriftReport,
    Experiment,
    Feedback,
    InspectionImage,
    Job,
    ModelVersion,
    Prediction,
    User,
)
from factoryai.domain.value_objects import (
    Category,
    Checksum,
    DatasetId,
    DatasetVersionId,
    ExperimentId,
    ImageId,
    JobId,
    JobStatus,
    ModelStage,
    ModelVersionId,
    PredictionId,
    UserId,
)


class ImageRepository(ABC):
    """Persistence for inspection image metadata."""

    @abstractmethod
    async def add(self, image: InspectionImage) -> None:
        """Insert a new image record."""

    @abstractmethod
    async def update(self, image: InspectionImage) -> None:
        """Persist a state transition on an existing image.

        Raises:
            EntityNotFoundError: If the image does not exist.
        """

    @abstractmethod
    async def get(self, image_id: ImageId) -> InspectionImage:
        """Return an image by identifier.

        Raises:
            EntityNotFoundError: If no such image exists.
        """

    @abstractmethod
    async def find_by_checksum(self, checksum: Checksum) -> InspectionImage | None:
        """Return the image with this exact content hash, if one is stored.

        This is the exact-duplicate check performed during ingestion.
        """

    @abstractmethod
    async def find_near_duplicates(
        self, perceptual_hash: str, *, max_distance: int
    ) -> list[InspectionImage]:
        """Return images whose perceptual hash is within ``max_distance``.

        Catches re-encoded, resized or lightly recompressed copies that a checksum misses.
        """

    @abstractmethod
    async def list_trainable(
        self, category: Category, *, limit: int | None = None
    ) -> list[InspectionImage]:
        """Return validated images eligible for inclusion in a dataset version."""

    @abstractmethod
    async def count_by_status(self, category: Category) -> dict[str, int]:
        """Return image counts grouped by processing status, for the ingestion dashboard."""


class DatasetRepository(ABC):
    """Persistence for datasets and their versions."""

    @abstractmethod
    async def add_dataset(self, dataset: Dataset) -> None:
        """Insert a new dataset."""

    @abstractmethod
    async def get_dataset(self, dataset_id: DatasetId) -> Dataset:
        """Return a dataset by identifier.

        Raises:
            EntityNotFoundError: If no such dataset exists.
        """

    @abstractmethod
    async def find_dataset_by_name(self, name: str) -> Dataset | None:
        """Return a dataset by its unique name, if it exists."""

    @abstractmethod
    async def add_version(self, version: DatasetVersion) -> None:
        """Insert a new dataset version and its membership rows."""

    @abstractmethod
    async def get_version(self, version_id: DatasetVersionId) -> DatasetVersion:
        """Return a dataset version by identifier.

        Raises:
            EntityNotFoundError: If no such version exists.
        """

    @abstractmethod
    async def find_version_by_tag(self, dataset_id: DatasetId, tag: str) -> DatasetVersion | None:
        """Return a version by its human-readable tag, if it exists."""

    @abstractmethod
    async def list_versions(self, dataset_id: DatasetId) -> list[DatasetVersion]:
        """Return every version of a dataset, newest first."""


class ExperimentRepository(ABC):
    """Persistence for training runs.

    Metrics are mirrored here from MLflow so that lineage queries stay in SQL and the
    dashboard survives MLflow being unreachable (ADR-0004).
    """

    @abstractmethod
    async def add(self, experiment: Experiment) -> None:
        """Insert a new experiment record."""

    @abstractmethod
    async def update(self, experiment: Experiment) -> None:
        """Persist a status change or the arrival of metrics."""

    @abstractmethod
    async def get(self, experiment_id: ExperimentId) -> Experiment:
        """Return an experiment by identifier.

        Raises:
            EntityNotFoundError: If no such experiment exists.
        """

    @abstractmethod
    async def list_for_dataset_version(self, version_id: DatasetVersionId) -> list[Experiment]:
        """Return every run trained on a given dataset version."""


class ModelRepository(ABC):
    """Persistence for registered model versions and their deployment history."""

    @abstractmethod
    async def add(self, model: ModelVersion) -> None:
        """Insert a new model version record."""

    @abstractmethod
    async def update(self, model: ModelVersion) -> None:
        """Persist a stage change or recalibration."""

    @abstractmethod
    async def get(self, model_version_id: ModelVersionId) -> ModelVersion:
        """Return a model version by identifier.

        Raises:
            EntityNotFoundError: If no such version exists.
        """

    @abstractmethod
    async def find_by_stage(self, category: Category, stage: ModelStage) -> ModelVersion | None:
        """Return the model currently occupying a stage for a category.

        At most one model occupies production per category; the inference service resolves
        what to serve through this method.
        """

    @abstractmethod
    async def list_versions(self, category: Category) -> list[ModelVersion]:
        """Return every registered version for a category, newest first."""

    @abstractmethod
    async def add_deployment(self, deployment: Deployment) -> None:
        """Append a deployment record. Deployment history is never updated or deleted."""

    @abstractmethod
    async def list_deployments(
        self, category: Category, *, environment: str, limit: int = 50
    ) -> list[Deployment]:
        """Return deployment history for an environment, newest first.

        This is what a rollback reads to find the version to restore.
        """


class PredictionRepository(ABC):
    """Persistence for served predictions and operator feedback."""

    @abstractmethod
    async def add(self, prediction: Prediction) -> None:
        """Append a prediction record."""

    @abstractmethod
    async def add_many(self, predictions: list[Prediction]) -> None:
        """Append a batch of predictions in one round trip."""

    @abstractmethod
    async def get(self, prediction_id: PredictionId) -> Prediction:
        """Return a prediction by identifier.

        Raises:
            EntityNotFoundError: If no such prediction exists.
        """

    @abstractmethod
    async def list_in_window(
        self,
        model_version_id: ModelVersionId,
        *,
        start: datetime,
        end: datetime,
        limit: int | None = None,
    ) -> list[Prediction]:
        """Return predictions served in a time window.

        This is the query drift analysis runs against, hence the composite index on
        ``(model_version_id, predicted_at)``.
        """

    @abstractmethod
    async def add_feedback(self, feedback: Feedback) -> None:
        """Append an operator feedback record."""

    @abstractmethod
    async def list_corrections(self, category: Category, *, since: datetime) -> list[Feedback]:
        """Return feedback that overturned a prediction, for the next training round."""


class DriftReportRepository(ABC):
    """Persistence for drift analysis results."""

    @abstractmethod
    async def add(self, report: DriftReport) -> None:
        """Append a drift report."""

    @abstractmethod
    async def latest(self, model_version_id: ModelVersionId) -> DriftReport | None:
        """Return the most recent report for a model, if any exists."""


class AuditRepository(ABC):
    """Append-only persistence for the audit chain."""

    @abstractmethod
    async def append(self, event: AuditEvent) -> None:
        """Append an audit record, extending the hash chain."""

    @abstractmethod
    async def latest(self) -> AuditEvent | None:
        """Return the most recent record, whose hash the next record must reference."""

    @abstractmethod
    async def list_for_resource(
        self, resource_type: str, resource_id: str, *, limit: int = 100
    ) -> list[AuditEvent]:
        """Return the audit trail for one entity, newest first."""

    @abstractmethod
    async def list_all(self) -> list[AuditEvent]:
        """Return every record in the chain, oldest first.

        This is what tamper detection reads: :func:`~factoryai.domain.entities.audit.
        verify_chain` needs the *whole* chain, in sequence order, to recompute every link.
        """


class UserRepository(ABC):
    """Persistence for platform users.

    Credentials are handled by the auth adapter, not here — the :class:`User` entity
    itself never carries a password or a hash (see its docstring). The hash still has to
    live *somewhere*, though, and the natural place is the same ``users`` row the rest of
    this repository already owns; :meth:`set_password_hash`/:meth:`get_password_hash` are
    that extra column's accessors, kept off the entity but not off the repository.
    """

    @abstractmethod
    async def add(self, user: User) -> None:
        """Insert a new user."""

    @abstractmethod
    async def update(self, user: User) -> None:
        """Persist a role change or activation state change."""

    @abstractmethod
    async def get(self, user_id: UserId) -> User:
        """Return a user by identifier.

        Raises:
            EntityNotFoundError: If no such user exists.
        """

    @abstractmethod
    async def find_by_email(self, email: str) -> User | None:
        """Return a user by their lowercase email address, if one exists."""

    @abstractmethod
    async def set_password_hash(self, user_id: UserId, password_hash: str) -> None:
        """Store a user's password hash, overwriting any previous value.

        Raises:
            EntityNotFoundError: If no such user exists.
        """

    @abstractmethod
    async def get_password_hash(self, user_id: UserId) -> str | None:
        """Return a user's password hash, or ``None`` if one was never set.

        Raises:
            EntityNotFoundError: If no such user exists.
        """


class JobRepository(ABC):
    """Persistence for background jobs (Phase 9).

    ``find_by_idempotency_key`` is what makes job submission safe to retry: a use case
    checks it before inserting, so a client that resends a request after a timeout gets
    back the job that was already created rather than starting the work twice.
    """

    @abstractmethod
    async def add(self, job: Job) -> None:
        """Insert a new job record.

        Raises:
            InvariantViolationError: If ``job.idempotency_key`` already belongs to another
                job — surfaced as a domain error, not a raw constraint violation, so a use
                case can distinguish "duplicate submission" from any other failure.
        """

    @abstractmethod
    async def update(self, job: Job) -> None:
        """Persist a status transition, progress update or result/error.

        Raises:
            EntityNotFoundError: If the job does not exist.
        """

    @abstractmethod
    async def get(self, job_id: JobId) -> Job:
        """Return a job by identifier.

        Raises:
            EntityNotFoundError: If no such job exists.
        """

    @abstractmethod
    async def find_by_idempotency_key(self, idempotency_key: str) -> Job | None:
        """Return the job submitted with this key, if one exists."""

    @abstractmethod
    async def list_by_status(self, status: JobStatus, *, limit: int = 100) -> list[Job]:
        """Return jobs in a given status, oldest first.

        Used by operational tooling to inspect the queue (e.g. everything stuck
        ``running``), not by any use case on the request path.
        """


class UnitOfWork(ABC):
    """Transactional boundary spanning every repository.

    Use cases open exactly one unit of work and either commit it whole or roll it back
    whole. This is what makes "store the image, record its metadata, emit the audit event"
    a single atomic step rather than three that can partially fail.

    Exiting the context without an explicit :meth:`commit` rolls back, so a forgotten
    commit loses work loudly instead of leaving a half-written transaction open.

    Example:
        >>> async with uow:  # doctest: +SKIP
        ...     await uow.images.add(image)
        ...     await uow.audit.append(event)
        ...     await uow.commit()
    """

    images: ImageRepository
    datasets: DatasetRepository
    experiments: ExperimentRepository
    models: ModelRepository
    predictions: PredictionRepository
    drift_reports: DriftReportRepository
    audit: AuditRepository
    users: UserRepository
    jobs: JobRepository

    @abstractmethod
    async def __aenter__(self) -> Self:
        """Begin a transaction."""

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit if :meth:`commit` was called, otherwise roll back."""

    @abstractmethod
    async def commit(self) -> None:
        """Mark the transaction for commit on context exit."""

    @abstractmethod
    async def rollback(self) -> None:
        """Discard every change made in this transaction."""
