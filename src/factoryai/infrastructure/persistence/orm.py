"""SQLAlchemy ORM models mirroring ``docs/DATA_MODEL.md``.

Deliberate deviations from that document, each a scope decision rather than an oversight:

- **No ``categories`` table.** Category *enablement* is configuration
  (:mod:`factoryai.shared.config`), not database state (ADR: category is config, not
  code). A table would either duplicate ``configs/categories.yaml`` or need syncing with
  it. Referential safety is kept with a ``CHECK`` constraint against the same 15 codes the
  domain's :data:`~factoryai.domain.value_objects.category.MVTEC_CATEGORIES` recognises.
- **No ``password_hash`` column on ``users`` yet.** Credential storage is a Phase 8
  concern; adding the column now would mean writing a placeholder hash from a repository
  that has no business knowing about hashing. It arrives with the migration that
  implements authentication.
- **No ``validation_results`` table yet.** Nothing references it until the Phase 3
  ingestion pipeline exists; speculative schema is schema nobody has tested.
- **Enum columns are ``TEXT`` + ``CHECK``, not native Postgres ``ENUM`` types.** Native
  enums make adding a value an ``ALTER TYPE`` with transactional restrictions in older
  Postgres versions; a ``CHECK`` constraint is a one-line migration.
- **The audit hash chain is enforced by the repository, not a SQL trigger.** Replicating
  Python's canonical JSON serialisation (sorted keys, specific float/None formatting)
  byte-for-byte inside a SQL trigger is fragile. The repository serialises the single
  writer path instead (``SELECT ... FOR UPDATE`` on the latest row before inserting);
  immutability (no ``UPDATE``/``DELETE``) is still enforced by a trigger, since that does
  not require reproducing application logic in SQL.
- **No separate least-privilege DB role for the API vs. migrations yet.** Real, but
  deferred to Phase 14 alongside the rest of the security hardening — tracked there rather
  than half-done here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from factoryai.domain.value_objects import (
    MVTEC_CATEGORIES,
    DatasetSplit,
    DeploymentAction,
    ExperimentStatus,
    FeedbackVerdict,
    ImageLabel,
    ModelStage,
    ProcessingStatus,
    UserRole,
)


def _values(*members: object) -> str:
    """Render a comma-separated, single-quoted list for a ``CHECK ... IN (...)`` clause."""
    return ", ".join(f"'{member}'" for member in members)


_CATEGORY_CHECK = f"category_code IN ({_values(*sorted(MVTEC_CATEGORIES))})"


class Base(DeclarativeBase):
    """Declarative base for every FactoryAI table."""


class UserRow(Base):
    """See :class:`factoryai.domain.entities.user.User`."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint(f"role IN ({_values(*UserRole)})", name="ck_users_role"),)

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ImageRow(Base):
    """See :class:`factoryai.domain.entities.image.InspectionImage`."""

    __tablename__ = "images"
    __table_args__ = (
        CheckConstraint(_CATEGORY_CHECK, name="ck_images_category"),
        CheckConstraint(
            f"processing_status IN ({_values(*ProcessingStatus)})",
            name="ck_images_processing_status",
        ),
        CheckConstraint(f"label IN ({_values(*ImageLabel)})", name="ck_images_label"),
        CheckConstraint("size_bytes > 0", name="ck_images_size_positive"),
        CheckConstraint("width > 0 AND height > 0", name="ck_images_resolution_positive"),
        Index(
            "ix_images_category_status_uploaded",
            "category_code",
            "processing_status",
            "uploaded_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    category_code: Mapped[str] = mapped_column(String(32), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    perceptual_hash: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    processing_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ProcessingStatus.PENDING.value
    )
    label: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ImageLabel.UNLABELED.value
    )
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        "metadata", postgresql.JSONB, nullable=False, default=dict
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DatasetRow(Base):
    """See :class:`factoryai.domain.entities.dataset.Dataset`."""

    __tablename__ = "datasets"
    __table_args__ = (CheckConstraint(_CATEGORY_CHECK, name="ck_datasets_category"),)

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    category_code: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    versions: Mapped[list[DatasetVersionRow]] = relationship(back_populates="dataset")


class DatasetVersionRow(Base):
    """See :class:`factoryai.domain.entities.dataset.DatasetVersion`.

    ``image_count`` and ``content_checksum`` are cached at creation time rather than
    recomputed on read: both are pure functions of the membership rows, but recomputing
    the content checksum means hashing every member's checksum on every read.
    """

    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_tag", name="uq_dataset_versions_tag"),
        CheckConstraint("image_count > 0", name="ck_dataset_versions_nonempty"),
        CheckConstraint(
            "char_length(git_commit) = 40", name="ck_dataset_versions_git_commit_length"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False
    )
    version_tag: Mapped[str] = mapped_column(String(255), nullable=False)
    dvc_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    git_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    image_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    dataset: Mapped[DatasetRow] = relationship(back_populates="versions")
    members: Mapped[list[DatasetVersionImageRow]] = relationship(
        back_populates="dataset_version", cascade="all, delete-orphan"
    )


class DatasetVersionImageRow(Base):
    """See :class:`factoryai.domain.entities.dataset.DatasetMember`."""

    __tablename__ = "dataset_version_images"
    __table_args__ = (CheckConstraint(f"split IN ({_values(*DatasetSplit)})", name="ck_dvi_split"),)

    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("dataset_versions.id"), primary_key=True
    )
    image_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("images.id"), primary_key=True
    )
    split: Mapped[str] = mapped_column(String(8), nullable=False)

    dataset_version: Mapped[DatasetVersionRow] = relationship(back_populates="members")


class ExperimentRow(Base):
    """See :class:`factoryai.domain.entities.experiment.Experiment`."""

    __tablename__ = "experiments"
    __table_args__ = (
        CheckConstraint(f"status IN ({_values(*ExperimentStatus)})", name="ck_experiments_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    mlflow_run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("dataset_versions.id"), nullable=False
    )
    model_family: Mapped[str] = mapped_column(String(64), nullable=False)
    backbone: Mapped[str] = mapped_column(String(128), nullable=False)
    hyperparameters: Mapped[dict[str, object]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict
    )
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    git_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ExperimentStatus.RUNNING.value
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict[str, object] | None] = mapped_column(postgresql.JSONB)
    hardware_info: Mapped[dict[str, object] | None] = mapped_column(postgresql.JSONB)
    failure_reason: Mapped[str | None] = mapped_column(String(2000))


class ModelVersionRow(Base):
    """See :class:`factoryai.domain.entities.model.ModelVersion`."""

    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("registry_name", "registry_version", name="uq_model_versions_registry"),
        CheckConstraint(_CATEGORY_CHECK, name="ck_model_versions_category"),
        CheckConstraint(f"stage IN ({_values(*ModelStage)})", name="ck_model_versions_stage"),
        CheckConstraint("registry_version > 0", name="ck_model_versions_registry_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    category_code: Mapped[str] = mapped_column(String(32), nullable=False)
    registry_name: Mapped[str] = mapped_column(String(255), nullable=False)
    registry_version: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ModelStage.DEVELOPMENT.value
    )
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    artifact_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    metrics: Mapped[dict[str, object]] = mapped_column(postgresql.JSONB, nullable=False)
    tags: Mapped[dict[str, str]] = mapped_column(postgresql.JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeploymentRow(Base):
    """See :class:`factoryai.domain.entities.model.Deployment`."""

    __tablename__ = "deployments"
    __table_args__ = (
        CheckConstraint(f"action IN ({_values(*DeploymentAction)})", name="ck_deployments_action"),
        Index("ix_deployments_environment_deployed_at", "environment", "deployed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False
    )
    previous_model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("model_versions.id")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("users.id")
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    comparison_report: Mapped[dict[str, object]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict
    )
    reason: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PredictionRow(Base):
    """See :class:`factoryai.domain.entities.prediction.Prediction`.

    Not yet partitioned by month (``docs/DATA_MODEL.md`` §4 flags this as the table that
    grows without bound). Partitioning is deferred until there is a real volume to
    partition — premature partitioning is untestable against a fresh testcontainer and
    adds operational surface (per-partition indexes, retention jobs) with no data to
    justify it yet.
    """

    __tablename__ = "predictions"
    __table_args__ = (
        Index("ix_predictions_model_version_predicted_at", "model_version_id", "predicted_at"),
        Index(
            "ix_predictions_anomalous",
            "is_anomalous",
            postgresql_where="is_anomalous",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    image_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("images.id"), nullable=False
    )
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False
    )
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("dataset_versions.id"), nullable=False
    )
    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    is_anomalous: Mapped[bool] = mapped_column(nullable=False)
    inference_time_ms: Mapped[float] = mapped_column(Float, nullable=False)
    heatmap_bucket: Mapped[str | None] = mapped_column(String(255))
    heatmap_key: Mapped[str | None] = mapped_column(String(1024))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeedbackRow(Base):
    """See :class:`factoryai.domain.entities.prediction.Feedback`."""

    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint(f"verdict IN ({_values(*FeedbackVerdict)})", name="ck_feedback_verdict"),
        CheckConstraint(
            f"corrected_label IS NULL OR corrected_label IN ({_values(*ImageLabel)})",
            name="ck_feedback_corrected_label",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    prediction_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("predictions.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    corrected_label: Mapped[str | None] = mapped_column(String(16))
    notes: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    region: Mapped[list[int] | None] = mapped_column(postgresql.JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DriftReportRow(Base):
    """See :class:`factoryai.domain.entities.monitoring.DriftReport`."""

    __tablename__ = "drift_reports"

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False
    )
    reference_dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("dataset_versions.id"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    min_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    signals: Mapped[list[dict[str, object]]] = mapped_column(postgresql.JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLogRow(Base):
    """See :class:`factoryai.domain.entities.audit.AuditEvent`.

    Immutability (no ``UPDATE``/``DELETE``) is enforced by a trigger installed in the
    initial migration, not by application discipline alone.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_resource", "resource_type", "resource_id", "occurred_at"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("users.id")
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict
    )
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
