"""SQLAlchemy implementations of the domain repository ports.

Every method here does exactly one thing: translate a domain-shaped question into SQL,
and a row-shaped answer back into a domain entity via :mod:`.mappers`. Business rules do
not belong here — see ``docs/ARCHITECTURE.md`` §2.2 for where they do.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, selectinload

from factoryai.domain.entities import (
    AuditEvent,
    Dataset,
    DatasetVersion,
    Deployment,
    DriftReport,
    Experiment,
    Feedback,
    InspectionImage,
    ModelVersion,
    Prediction,
    User,
)
from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.ports.auth import TokenRevocationList
from factoryai.domain.ports.repositories import (
    AuditRepository,
    DatasetRepository,
    DriftReportRepository,
    ExperimentRepository,
    ImageRepository,
    ModelRepository,
    PredictionRepository,
    UserRepository,
)
from factoryai.domain.value_objects import (
    Category,
    Checksum,
    DatasetId,
    DatasetVersionId,
    ExperimentId,
    ImageId,
    ModelStage,
    ModelVersionId,
    PredictionId,
    ProcessingStatus,
    UserId,
)
from factoryai.infrastructure.persistence import mappers
from factoryai.infrastructure.persistence.orm import (
    AuditLogRow,
    DatasetRow,
    DatasetVersionRow,
    DeploymentRow,
    DriftReportRow,
    ExperimentRow,
    FeedbackRow,
    ImageRow,
    ModelVersionRow,
    PredictionRow,
    RevokedTokenRow,
    UserRow,
)
from factoryai.shared.errors import TransientError

_Row = TypeVar("_Row", bound=DeclarativeBase)


async def _add(session: AsyncSession, row: _Row) -> _Row:
    """Add a row and flush it immediately.

    SQLAlchemy only orders a flush's INSERTs by foreign-key dependency between mapped
    classes that have an ORM ``relationship()`` configured between them; a bare FK column
    on the ``Table`` is not enough. These repositories deliberately skip declaring
    relationships everywhere (ADR-0001's mapper layer is meant to stay boring), so two
    rows added in the same transaction — a model version and its deployment, a prediction
    and its feedback — can otherwise be flushed in the wrong order and trip a foreign-key
    violation that has nothing to do with the data being invalid. Flushing after every
    individual add removes the ambiguity: each flush only ever has one new row in it.
    """
    session.add(row)
    await session.flush()
    return row


_AUDIT_CHAIN_LOCK_KEY = 0x_FAC7_0AA1_D17_C4A1
"""Fixed key for the transaction-scoped advisory lock guarding the audit chain.

A plain ``SELECT ... ORDER BY seq DESC LIMIT 1 FOR UPDATE`` only locks a row that already
exists — it does nothing to stop a second transaction from inserting a brand new row with
a higher sequence number before the first commits, which would let two events compute
their hash against the same "latest" row and corrupt the chain. An advisory lock held for
the transaction's duration serializes every append, which is what a hash chain actually
needs.
"""


class SqlAlchemyImageRepository(ImageRepository):
    """Image metadata persistence backed by PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind this repository to a session owned by the enclosing unit of work."""
        self._session = session

    async def add(self, image: InspectionImage) -> None:
        """Insert a new image row."""
        await _add(self._session, mappers.image_to_row(image))

    async def update(self, image: InspectionImage) -> None:
        """Overwrite an existing image row with the entity's current state.

        Fields are copied onto the already-tracked row rather than adding a fresh row
        object, so SQLAlchemy emits an ``UPDATE`` against the identity-mapped instance
        instead of colliding with it as a second insert.
        """
        row = await self._get_row(image.id)
        fresh = mappers.image_to_row(image)
        for column in ImageRow.__table__.columns:
            if column.key != "id":
                setattr(row, column.key, getattr(fresh, column.key))

    async def get(self, image_id: ImageId) -> InspectionImage:
        """Return an image by id.

        Raises:
            EntityNotFoundError: If no such image exists.
        """
        return mappers.image_to_entity(await self._get_row(image_id))

    async def find_by_checksum(self, checksum: Checksum) -> InspectionImage | None:
        """Return the image with this exact content hash, if stored."""
        row = await self._session.scalar(
            select(ImageRow).where(ImageRow.checksum_sha256 == checksum.value)
        )
        return mappers.image_to_entity(row) if row else None

    async def find_near_duplicates(
        self, perceptual_hash: str, *, max_distance: int
    ) -> list[InspectionImage]:
        """Return images whose perceptual hash is within ``max_distance``.

        Hamming distance is computed in Python rather than SQL: perceptual hashing itself
        does not exist until Phase 3, so there is no real volume yet to justify a
        bit-level SQL comparison (Postgres 14+'s ``bit`` type would work but adds a
        migration for a query with no callers). Revisit once Phase 3 exercises this at
        scale — see ``docs/ROADMAP.md`` Phase 3.
        """
        target = int(perceptual_hash, 16)
        rows = (
            await self._session.scalars(
                select(ImageRow).where(ImageRow.perceptual_hash.is_not(None))
            )
        ).all()
        matches = []
        for row in rows:
            if row.perceptual_hash is None:  # narrows the type; excluded by the query above
                continue
            distance = (target ^ int(row.perceptual_hash, 16)).bit_count()
            if distance <= max_distance:
                matches.append(mappers.image_to_entity(row))
        return matches

    async def list_trainable(
        self, category: Category, *, limit: int | None = None
    ) -> list[InspectionImage]:
        """Return validated images eligible for a dataset version."""
        stmt = (
            select(ImageRow)
            .where(
                ImageRow.category_code == category.code,
                ImageRow.processing_status == ProcessingStatus.VALID.value,
            )
            .order_by(ImageRow.uploaded_at)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self._session.scalars(stmt)).all()
        return [mappers.image_to_entity(row) for row in rows]

    async def count_by_status(self, category: Category) -> dict[str, int]:
        """Return image counts grouped by processing status, zero-filled for every status."""
        stmt = (
            select(ImageRow.processing_status, func.count())
            .where(ImageRow.category_code == category.code)
            .group_by(ImageRow.processing_status)
        )
        counts = {status.value: 0 for status in ProcessingStatus}
        for status, count in await self._session.execute(stmt):
            counts[status] = count
        return counts

    async def _get_row(self, image_id: ImageId) -> ImageRow:
        row = await self._session.get(ImageRow, image_id)
        if row is None:
            raise EntityNotFoundError("InspectionImage", image_id)
        return row


class SqlAlchemyDatasetRepository(DatasetRepository):
    """Dataset and dataset-version persistence backed by PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind this repository to a session owned by the enclosing unit of work."""
        self._session = session

    async def add_dataset(self, dataset: Dataset) -> None:
        """Insert a new dataset row."""
        await _add(self._session, mappers.dataset_to_row(dataset))

    async def get_dataset(self, dataset_id: DatasetId) -> Dataset:
        """Return a dataset by id.

        Raises:
            EntityNotFoundError: If no such dataset exists.
        """
        row = await self._session.get(DatasetRow, dataset_id)
        if row is None:
            raise EntityNotFoundError("Dataset", dataset_id)
        return mappers.dataset_to_entity(row)

    async def find_dataset_by_name(self, name: str) -> Dataset | None:
        """Return a dataset by its unique name, if it exists."""
        row = await self._session.scalar(select(DatasetRow).where(DatasetRow.name == name))
        return mappers.dataset_to_entity(row) if row else None

    async def add_version(self, version: DatasetVersion) -> None:
        """Insert a new dataset version and its membership rows.

        The content checksum is computed here, from each member image's stored checksum,
        because the entity does not carry it — see
        :meth:`~factoryai.domain.entities.dataset.DatasetVersion.content_checksum`.
        """
        image_ids = version.image_ids()
        result = await self._session.execute(
            select(ImageRow.id, ImageRow.checksum_sha256).where(ImageRow.id.in_(image_ids))
        )
        checksums = {
            ImageId(row_id): Checksum(checksum_value) for row_id, checksum_value in result.all()
        }
        content_checksum = version.content_checksum(checksums)
        await _add(self._session, mappers.dataset_version_to_row(version, content_checksum))

    async def get_version(self, version_id: DatasetVersionId) -> DatasetVersion:
        """Return a dataset version by id, with its members loaded.

        Raises:
            EntityNotFoundError: If no such version exists.
        """
        row = await self._session.scalar(
            select(DatasetVersionRow)
            .where(DatasetVersionRow.id == version_id)
            .options(selectinload(DatasetVersionRow.members))
        )
        if row is None:
            raise EntityNotFoundError("DatasetVersion", version_id)
        return mappers.dataset_version_to_entity(row)

    async def find_version_by_tag(self, dataset_id: DatasetId, tag: str) -> DatasetVersion | None:
        """Return a version by its human-readable tag, if it exists."""
        row = await self._session.scalar(
            select(DatasetVersionRow)
            .where(
                DatasetVersionRow.dataset_id == dataset_id,
                DatasetVersionRow.version_tag == tag,
            )
            .options(selectinload(DatasetVersionRow.members))
        )
        return mappers.dataset_version_to_entity(row) if row else None

    async def list_versions(self, dataset_id: DatasetId) -> list[DatasetVersion]:
        """Return every version of a dataset, newest first."""
        rows = await self._session.scalars(
            select(DatasetVersionRow)
            .where(DatasetVersionRow.dataset_id == dataset_id)
            .order_by(DatasetVersionRow.created_at.desc())
            .options(selectinload(DatasetVersionRow.members))
        )
        return [mappers.dataset_version_to_entity(row) for row in rows]


class SqlAlchemyExperimentRepository(ExperimentRepository):
    """Training run persistence backed by PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind this repository to a session owned by the enclosing unit of work."""
        self._session = session

    async def add(self, experiment: Experiment) -> None:
        """Insert a new experiment row."""
        await _add(self._session, mappers.experiment_to_row(experiment))

    async def update(self, experiment: Experiment) -> None:
        """Overwrite an existing experiment row with the entity's current state."""
        row = await self._get_row(experiment.id)
        fresh = mappers.experiment_to_row(experiment)
        for column in ExperimentRow.__table__.columns:
            if column.key != "id":
                setattr(row, column.key, getattr(fresh, column.key))

    async def get(self, experiment_id: ExperimentId) -> Experiment:
        """Return an experiment by id.

        Raises:
            EntityNotFoundError: If no such experiment exists.
        """
        return mappers.experiment_to_entity(await self._get_row(experiment_id))

    async def list_for_dataset_version(self, version_id: DatasetVersionId) -> list[Experiment]:
        """Return every run trained on a given dataset version."""
        rows = await self._session.scalars(
            select(ExperimentRow).where(ExperimentRow.dataset_version_id == version_id)
        )
        return [mappers.experiment_to_entity(row) for row in rows]

    async def _get_row(self, experiment_id: ExperimentId) -> ExperimentRow:
        row = await self._session.get(ExperimentRow, experiment_id)
        if row is None:
            raise EntityNotFoundError("Experiment", experiment_id)
        return row


class SqlAlchemyModelRepository(ModelRepository):
    """Model version and deployment persistence backed by PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind this repository to a session owned by the enclosing unit of work."""
        self._session = session

    async def add(self, model: ModelVersion) -> None:
        """Insert a new model version row."""
        await _add(self._session, mappers.model_version_to_row(model))

    async def update(self, model: ModelVersion) -> None:
        """Overwrite an existing model version row with the entity's current state."""
        row = await self._get_row(model.id)
        fresh = mappers.model_version_to_row(model)
        for column in ModelVersionRow.__table__.columns:
            if column.key != "id":
                setattr(row, column.key, getattr(fresh, column.key))

    async def get(self, model_version_id: ModelVersionId) -> ModelVersion:
        """Return a model version by id.

        Raises:
            EntityNotFoundError: If no such version exists.
        """
        return mappers.model_version_to_entity(await self._get_row(model_version_id))

    async def find_by_stage(self, category: Category, stage: ModelStage) -> ModelVersion | None:
        """Return the model currently occupying a stage for a category.

        Orders by ``created_at`` descending so that if more than one row is ever found in
        the same stage — which correct application usage should prevent — the most
        recently registered one is treated as the current occupant, deterministically.
        """
        row = await self._session.scalar(
            select(ModelVersionRow)
            .where(
                ModelVersionRow.category_code == category.code,
                ModelVersionRow.stage == stage.value,
            )
            .order_by(ModelVersionRow.created_at.desc())
            .limit(1)
        )
        return mappers.model_version_to_entity(row) if row else None

    async def list_versions(self, category: Category) -> list[ModelVersion]:
        """Return every registered version for a category, newest first."""
        rows = await self._session.scalars(
            select(ModelVersionRow)
            .where(ModelVersionRow.category_code == category.code)
            .order_by(ModelVersionRow.created_at.desc())
        )
        return [mappers.model_version_to_entity(row) for row in rows]

    async def add_deployment(self, deployment: Deployment) -> None:
        """Append a deployment record."""
        await _add(self._session, mappers.deployment_to_row(deployment))

    async def list_deployments(
        self, category: Category, *, environment: str, limit: int = 50
    ) -> list[Deployment]:
        """Return deployment history for an environment, newest first."""
        rows = await self._session.scalars(
            select(DeploymentRow)
            .join(ModelVersionRow, DeploymentRow.model_version_id == ModelVersionRow.id)
            .where(
                ModelVersionRow.category_code == category.code,
                DeploymentRow.environment == environment,
            )
            .order_by(DeploymentRow.deployed_at.desc())
            .limit(limit)
        )
        return [mappers.deployment_to_entity(row) for row in rows]

    async def _get_row(self, model_version_id: ModelVersionId) -> ModelVersionRow:
        row = await self._session.get(ModelVersionRow, model_version_id)
        if row is None:
            raise EntityNotFoundError("ModelVersion", model_version_id)
        return row


class SqlAlchemyPredictionRepository(PredictionRepository):
    """Prediction and feedback persistence backed by PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind this repository to a session owned by the enclosing unit of work."""
        self._session = session

    async def add(self, prediction: Prediction) -> None:
        """Append a prediction row."""
        await _add(self._session, mappers.prediction_to_row(prediction))

    async def add_many(self, predictions: list[Prediction]) -> None:
        """Append a batch of prediction rows in one round trip."""
        self._session.add_all(mappers.prediction_to_row(prediction) for prediction in predictions)

    async def get(self, prediction_id: PredictionId) -> Prediction:
        """Return a prediction by id.

        Raises:
            EntityNotFoundError: If no such prediction exists.
        """
        row = await self._session.get(PredictionRow, prediction_id)
        if row is None:
            raise EntityNotFoundError("Prediction", prediction_id)
        return mappers.prediction_to_entity(row)

    async def list_in_window(
        self,
        model_version_id: ModelVersionId,
        *,
        start: datetime,
        end: datetime,
        limit: int | None = None,
    ) -> list[Prediction]:
        """Return predictions served in a time window."""
        stmt = (
            select(PredictionRow)
            .where(
                PredictionRow.model_version_id == model_version_id,
                PredictionRow.predicted_at >= start,
                PredictionRow.predicted_at < end,
            )
            .order_by(PredictionRow.predicted_at)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = await self._session.scalars(stmt)
        return [mappers.prediction_to_entity(row) for row in rows]

    async def add_feedback(self, feedback: Feedback) -> None:
        """Append an operator feedback row.

        Raises:
            EntityNotFoundError: If ``feedback.user_id`` names no existing user. Surfaced
                explicitly rather than left as a raw ``IntegrityError`` because
                ``POST /feedback`` (Phase 7) is the first caller passing a client-supplied
                user id straight through with no auth layer (Phase 8) to have validated it
                first — a bad id from an HTTP caller is an expected occurrence, not a bug.
        """
        try:
            await _add(self._session, mappers.feedback_to_row(feedback))
        except IntegrityError as exc:
            # Translated, not rolled back here: the enclosing unit of work's __aexit__
            # rolls back the whole transaction once this propagates out of it, exactly as
            # it already does for any other exception (see SqlAlchemyUnitOfWork).
            raise EntityNotFoundError("User", feedback.user_id) from exc

    async def list_corrections(self, category: Category, *, since: datetime) -> list[Feedback]:
        """Return feedback that overturned a prediction, for the next training round."""
        rows = await self._session.scalars(
            select(FeedbackRow)
            .join(PredictionRow, FeedbackRow.prediction_id == PredictionRow.id)
            .join(ImageRow, PredictionRow.image_id == ImageRow.id)
            .where(
                ImageRow.category_code == category.code,
                FeedbackRow.verdict == "incorrect",
                FeedbackRow.created_at >= since,
            )
            .order_by(FeedbackRow.created_at)
        )
        return [mappers.feedback_to_entity(row) for row in rows]


class SqlAlchemyDriftReportRepository(DriftReportRepository):
    """Drift analysis persistence backed by PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind this repository to a session owned by the enclosing unit of work."""
        self._session = session

    async def add(self, report: DriftReport) -> None:
        """Append a drift report row."""
        await _add(self._session, mappers.drift_report_to_row(report))

    async def latest(self, model_version_id: ModelVersionId) -> DriftReport | None:
        """Return the most recent report for a model, if any exists."""
        row = await self._session.scalar(
            select(DriftReportRow)
            .where(DriftReportRow.model_version_id == model_version_id)
            .order_by(DriftReportRow.created_at.desc())
            .limit(1)
        )
        return mappers.drift_report_to_entity(row) if row else None


class SqlAlchemyAuditRepository(AuditRepository):
    """Append-only, hash-chained audit persistence backed by PostgreSQL.

    See :data:`_AUDIT_CHAIN_LOCK_KEY` for why appends take an advisory lock rather than
    relying on row-level locking alone.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind this repository to a session owned by the enclosing unit of work."""
        self._session = session

    async def append(self, event: AuditEvent) -> None:
        """Append an audit record, extending the hash chain.

        Args:
            event: The event to append. Its ``sequence`` and ``prev_hash`` must match the
                current chain head at the time this call resolves — callers construct the
                event from :meth:`latest` and race the check against a concurrent appender
                serialised by the advisory lock below.

        Raises:
            TransientError: If another transaction extended the chain first. Retryable:
                the caller should re-read :meth:`latest` and construct a fresh event.
        """
        await self._session.execute(select(func.pg_advisory_xact_lock(_AUDIT_CHAIN_LOCK_KEY)))
        latest = await self.latest()
        expected_sequence = latest.sequence + 1 if latest else 1
        expected_prev_hash = latest.row_hash() if latest else event.prev_hash
        if int(event.sequence) != expected_sequence or event.prev_hash != expected_prev_hash:
            raise TransientError(
                "audit chain head moved before this event could be appended; "
                "re-read the latest event and retry",
                details={
                    "expected_sequence": expected_sequence,
                    "actual_sequence": int(event.sequence),
                },
            )
        await _add(self._session, mappers.audit_event_to_row(event))

    async def latest(self) -> AuditEvent | None:
        """Return the most recent record, whose hash the next record must reference."""
        row = await self._session.scalar(
            select(AuditLogRow).order_by(AuditLogRow.seq.desc()).limit(1)
        )
        return mappers.audit_event_to_entity(row) if row else None

    async def list_for_resource(
        self, resource_type: str, resource_id: str, *, limit: int = 100
    ) -> list[AuditEvent]:
        """Return the audit trail for one entity, newest first."""
        rows = await self._session.scalars(
            select(AuditLogRow)
            .where(
                AuditLogRow.resource_type == resource_type,
                AuditLogRow.resource_id == resource_id,
            )
            .order_by(AuditLogRow.seq.desc())
            .limit(limit)
        )
        return [mappers.audit_event_to_entity(row) for row in rows]

    async def list_all(self) -> list[AuditEvent]:
        """Return every record in the chain, oldest first."""
        rows = await self._session.scalars(select(AuditLogRow).order_by(AuditLogRow.seq))
        return [mappers.audit_event_to_entity(row) for row in rows]


class SqlAlchemyUserRepository(UserRepository):
    """User persistence backed by PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind this repository to a session owned by the enclosing unit of work."""
        self._session = session

    async def add(self, user: User) -> None:
        """Insert a new user row."""
        await _add(self._session, mappers.user_to_row(user))

    async def update(self, user: User) -> None:
        """Overwrite an existing user row with the entity's current state.

        Leaves ``password_hash`` untouched: :func:`~factoryai.infrastructure.persistence.
        mappers.user_to_row` never sets it (the entity does not carry one — see
        :class:`~factoryai.domain.entities.user.User`'s docstring), so copying every column
        from a freshly built row would silently wipe a real hash on every role change or
        deactivation. Only :meth:`set_password_hash` may touch that column.
        """
        row = await self._get_row(user.id)
        fresh = mappers.user_to_row(user)
        for column in UserRow.__table__.columns:
            if column.key not in {"id", "password_hash"}:
                setattr(row, column.key, getattr(fresh, column.key))

    async def get(self, user_id: UserId) -> User:
        """Return a user by id.

        Raises:
            EntityNotFoundError: If no such user exists.
        """
        return mappers.user_to_entity(await self._get_row(user_id))

    async def find_by_email(self, email: str) -> User | None:
        """Return a user by their lowercase email address, if one exists."""
        row = await self._session.scalar(select(UserRow).where(UserRow.email == email))
        return mappers.user_to_entity(row) if row else None

    async def set_password_hash(self, user_id: UserId, password_hash: str) -> None:
        """Overwrite a user's password hash.

        Raises:
            EntityNotFoundError: If no such user exists.
        """
        row = await self._get_row(user_id)
        row.password_hash = password_hash

    async def get_password_hash(self, user_id: UserId) -> str | None:
        """Return a user's password hash, or ``None`` if one was never set.

        Raises:
            EntityNotFoundError: If no such user exists.
        """
        return (await self._get_row(user_id)).password_hash

    async def _get_row(self, user_id: UserId) -> UserRow:
        row = await self._session.get(UserRow, user_id)
        if row is None:
            raise EntityNotFoundError("User", user_id)
        return row


class SqlAlchemyTokenRevocationList(TokenRevocationList):
    """Refresh-token revocation persistence backed by PostgreSQL.

    Deliberately not a repository on :class:`~factoryai.domain.ports.repositories.
    UnitOfWork`: revoking a token is an independent, self-contained write with nothing to
    stay atomic with (unlike, say, a prediction and its audit event) — it opens and commits
    its own short-lived session per call rather than requiring every caller to first open a
    unit of work just to reach it.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialise with the session factory every call opens a transaction from."""
        self._session_factory = session_factory

    async def revoke(self, jti: str, *, expires_at: datetime) -> None:
        """Blacklist a refresh token identifier."""
        async with self._session_factory() as session:
            await session.merge(
                RevokedTokenRow(jti=jti, revoked_at=datetime.now(UTC), expires_at=expires_at)
            )
            await session.commit()

    async def is_revoked(self, jti: str) -> bool:
        """Return whether a refresh token identifier has been revoked."""
        async with self._session_factory() as session:
            row = await session.get(RevokedTokenRow, jti)
            return row is not None


__all__: Sequence[str] = (
    "SqlAlchemyAuditRepository",
    "SqlAlchemyDatasetRepository",
    "SqlAlchemyDriftReportRepository",
    "SqlAlchemyExperimentRepository",
    "SqlAlchemyImageRepository",
    "SqlAlchemyModelRepository",
    "SqlAlchemyPredictionRepository",
    "SqlAlchemyTokenRevocationList",
    "SqlAlchemyUserRepository",
)
