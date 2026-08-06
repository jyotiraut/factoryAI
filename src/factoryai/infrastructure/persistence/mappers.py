"""Translation between domain entities and ORM rows.

Each pair of functions is a one-to-one, boring, exhaustive field mapping — intentionally.
The moment a mapper does anything clever (defaulting, coercion beyond type conversion,
validation) it has quietly taken over a responsibility that belongs to the domain entity's
own constructor.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from factoryai.domain.entities import (
    AuditEvent,
    Dataset,
    DatasetMember,
    DatasetVersion,
    Deployment,
    DriftReport,
    DriftSignal,
    EvaluationMetrics,
    Experiment,
    Feedback,
    HardwareInfo,
    InspectionImage,
    ModelVersion,
    Prediction,
    User,
)
from factoryai.domain.value_objects import (
    AnomalyScore,
    AuditSequence,
    Category,
    Checksum,
    DatasetId,
    DatasetSplit,
    DatasetVersionId,
    DeploymentAction,
    DeploymentId,
    DriftReportId,
    ExperimentId,
    ExperimentStatus,
    FeedbackId,
    FeedbackVerdict,
    ImageId,
    ImageLabel,
    ModelStage,
    ModelVersionId,
    PredictionId,
    ProcessingStatus,
    Resolution,
    StorageLocation,
    UserId,
    UserRole,
)
from factoryai.infrastructure.persistence.orm import (
    AuditLogRow,
    DatasetRow,
    DatasetVersionImageRow,
    DatasetVersionRow,
    DeploymentRow,
    DriftReportRow,
    ExperimentRow,
    FeedbackRow,
    ImageRow,
    ModelVersionRow,
    PredictionRow,
    UserRow,
)

# --------------------------------------------------------------------------------------
# User
# --------------------------------------------------------------------------------------


def user_to_row(user: User) -> UserRow:
    """Build a row from a user entity."""
    return UserRow(
        id=user.id,
        email=user.email,
        role=user.role.value,
        display_name=user.display_name,
        is_active=user.is_active,
        created_at=user.created_at,
    )


def user_to_entity(row: UserRow) -> User:
    """Build a user entity from a row."""
    return User(
        id=UserId(row.id),
        email=row.email,
        role=UserRole(row.role),
        display_name=row.display_name,
        is_active=row.is_active,
        created_at=row.created_at,
    )


# --------------------------------------------------------------------------------------
# InspectionImage
# --------------------------------------------------------------------------------------


def image_to_row(image: InspectionImage) -> ImageRow:
    """Build a row from an inspection image entity."""
    return ImageRow(
        id=image.id,
        category_code=image.category.code,
        checksum_sha256=image.checksum.value,
        perceptual_hash=image.perceptual_hash,
        width=image.resolution.width,
        height=image.resolution.height,
        size_bytes=image.size_bytes,
        storage_bucket=image.location.bucket,
        storage_key=image.location.key,
        processing_status=image.status.value,
        label=image.label.value,
        metadata_json=dict(image.metadata),
        uploaded_at=image.uploaded_at,
    )


def image_to_entity(row: ImageRow) -> InspectionImage:
    """Build an inspection image entity from a row."""
    return InspectionImage(
        id=ImageId(row.id),
        category=Category(row.category_code),
        checksum=Checksum(row.checksum_sha256),
        resolution=Resolution(row.width, row.height),
        size_bytes=row.size_bytes,
        location=StorageLocation(row.storage_bucket, row.storage_key),
        uploaded_at=row.uploaded_at,
        status=ProcessingStatus(row.processing_status),
        label=ImageLabel(row.label),
        perceptual_hash=row.perceptual_hash,
        metadata=dict(row.metadata_json),
    )


# --------------------------------------------------------------------------------------
# Dataset / DatasetVersion
# --------------------------------------------------------------------------------------


def dataset_to_row(dataset: Dataset) -> DatasetRow:
    """Build a row from a dataset entity."""
    return DatasetRow(
        id=dataset.id,
        name=dataset.name,
        category_code=dataset.category.code,
        description=dataset.description,
        created_at=dataset.created_at,
    )


def dataset_to_entity(row: DatasetRow) -> Dataset:
    """Build a dataset entity from a row."""
    return Dataset(
        id=DatasetId(row.id),
        name=row.name,
        category=Category(row.category_code),
        description=row.description,
        created_at=row.created_at,
    )


def dataset_version_to_row(
    version: DatasetVersion, content_checksum: Checksum
) -> DatasetVersionRow:
    """Build a row (with its membership rows attached) from a dataset version entity.

    Args:
        version: The entity to persist.
        content_checksum: Pre-computed content fingerprint. The entity does not carry this
            itself — see :meth:`DatasetVersion.content_checksum` — so the caller (the
            repository, which can look up every member's image checksum) computes it once
            before the row is built rather than the mapper reaching back into the database.
    """
    return DatasetVersionRow(
        id=version.id,
        dataset_id=version.dataset_id,
        version_tag=version.version_tag,
        dvc_hash=version.dvc_hash,
        git_commit=version.git_commit,
        image_count=version.image_count,
        content_checksum=content_checksum.value,
        note=version.note,
        created_at=version.created_at,
        members=[
            DatasetVersionImageRow(image_id=member.image_id, split=member.split.value)
            for member in version.members
        ],
    )


def dataset_version_to_entity(row: DatasetVersionRow) -> DatasetVersion:
    """Build a dataset version entity from a row.

    Requires ``row.members`` to be loaded (eagerly or via a prior access within an open
    session) — an unloaded relationship on a detached row raises rather than silently
    producing an empty version.
    """
    return DatasetVersion(
        id=DatasetVersionId(row.id),
        dataset_id=DatasetId(row.dataset_id),
        version_tag=row.version_tag,
        dvc_hash=row.dvc_hash,
        git_commit=row.git_commit,
        members=tuple(
            DatasetMember(ImageId(member.image_id), DatasetSplit(member.split))
            for member in row.members
        ),
        created_at=row.created_at,
        note=row.note,
    )


# --------------------------------------------------------------------------------------
# Experiment
# --------------------------------------------------------------------------------------


def _metrics_to_json(metrics: EvaluationMetrics) -> dict[str, Any]:
    """Serialise metrics, dropping absent optional fields (see ``EvaluationMetrics.to_dict``)."""
    return metrics.to_dict()


def _metrics_from_json(data: dict[str, Any]) -> EvaluationMetrics:
    """Reconstruct metrics from their serialised form."""
    payload = dict(data)
    if payload.get("confusion_matrix") is not None:
        payload["confusion_matrix"] = tuple(payload["confusion_matrix"])
    return EvaluationMetrics(**payload)


def _hardware_to_json(hardware: HardwareInfo) -> dict[str, Any]:
    """Serialise hardware info."""
    return dataclasses.asdict(hardware)


def _hardware_from_json(data: dict[str, Any]) -> HardwareInfo:
    """Reconstruct hardware info from its serialised form."""
    return HardwareInfo(**data)


def experiment_to_row(experiment: Experiment) -> ExperimentRow:
    """Build a row from an experiment entity."""
    return ExperimentRow(
        id=experiment.id,
        mlflow_run_id=experiment.mlflow_run_id,
        dataset_version_id=experiment.dataset_version_id,
        model_family=experiment.model_family,
        backbone=experiment.backbone,
        hyperparameters=dict(experiment.hyperparameters),
        config_hash=experiment.config_hash,
        git_commit=experiment.git_commit,
        status=experiment.status.value,
        started_at=experiment.started_at,
        finished_at=experiment.finished_at,
        metrics=_metrics_to_json(experiment.metrics) if experiment.metrics else None,
        hardware_info=_hardware_to_json(experiment.hardware) if experiment.hardware else None,
        failure_reason=experiment.failure_reason,
    )


def experiment_to_entity(row: ExperimentRow) -> Experiment:
    """Build an experiment entity from a row."""
    return Experiment(
        id=ExperimentId(row.id),
        mlflow_run_id=row.mlflow_run_id,
        dataset_version_id=DatasetVersionId(row.dataset_version_id),
        model_family=row.model_family,
        backbone=row.backbone,
        hyperparameters=dict(row.hyperparameters),
        config_hash=row.config_hash,
        git_commit=row.git_commit,
        status=ExperimentStatus(row.status),
        started_at=row.started_at,
        finished_at=row.finished_at,
        metrics=_metrics_from_json(row.metrics) if row.metrics else None,
        hardware=_hardware_from_json(row.hardware_info) if row.hardware_info else None,
        failure_reason=row.failure_reason,
    )


# --------------------------------------------------------------------------------------
# ModelVersion / Deployment
# --------------------------------------------------------------------------------------


def model_version_to_row(model: ModelVersion) -> ModelVersionRow:
    """Build a row from a model version entity."""
    return ModelVersionRow(
        id=model.id,
        experiment_id=model.experiment_id,
        category_code=model.category.code,
        registry_name=model.registry_name,
        registry_version=model.registry_version,
        stage=model.stage.value,
        threshold=model.threshold,
        artifact_bucket=model.artifact_location.bucket,
        artifact_key=model.artifact_location.key,
        metrics=_metrics_to_json(model.metrics),
        tags=dict(model.tags),
        created_at=model.created_at,
    )


def model_version_to_entity(row: ModelVersionRow) -> ModelVersion:
    """Build a model version entity from a row."""
    return ModelVersion(
        id=ModelVersionId(row.id),
        experiment_id=ExperimentId(row.experiment_id),
        category=Category(row.category_code),
        registry_name=row.registry_name,
        registry_version=row.registry_version,
        threshold=row.threshold,
        artifact_location=StorageLocation(row.artifact_bucket, row.artifact_key),
        metrics=_metrics_from_json(row.metrics),
        created_at=row.created_at,
        stage=ModelStage(row.stage),
        tags=dict(row.tags),
    )


def deployment_to_row(deployment: Deployment) -> DeploymentRow:
    """Build a row from a deployment entity."""
    return DeploymentRow(
        id=deployment.id,
        model_version_id=deployment.model_version_id,
        previous_model_version_id=deployment.previous_model_version_id,
        actor_id=deployment.actor_id,
        action=deployment.action.value,
        environment=deployment.environment,
        comparison_report=dict(deployment.comparison_report),
        reason=deployment.reason,
        deployed_at=deployment.deployed_at,
    )


def deployment_to_entity(row: DeploymentRow) -> Deployment:
    """Build a deployment entity from a row."""
    return Deployment(
        id=DeploymentId(row.id),
        model_version_id=ModelVersionId(row.model_version_id),
        action=DeploymentAction(row.action),
        environment=row.environment,
        deployed_at=row.deployed_at,
        actor_id=UserId(row.actor_id) if row.actor_id else None,
        previous_model_version_id=(
            ModelVersionId(row.previous_model_version_id) if row.previous_model_version_id else None
        ),
        comparison_report=dict(row.comparison_report),
        reason=row.reason,
    )


# --------------------------------------------------------------------------------------
# Prediction / Feedback
# --------------------------------------------------------------------------------------


def prediction_to_row(prediction: Prediction) -> PredictionRow:
    """Build a row from a prediction entity."""
    return PredictionRow(
        id=prediction.id,
        image_id=prediction.image_id,
        model_version_id=prediction.model_version_id,
        dataset_version_id=prediction.dataset_version_id,
        anomaly_score=prediction.score.value,
        threshold=prediction.score.threshold,
        is_anomalous=prediction.is_anomalous,
        inference_time_ms=prediction.inference_time_ms,
        heatmap_bucket=prediction.heatmap_location.bucket if prediction.heatmap_location else None,
        heatmap_key=prediction.heatmap_location.key if prediction.heatmap_location else None,
        correlation_id=prediction.correlation_id,
        predicted_at=prediction.predicted_at,
    )


def prediction_to_entity(row: PredictionRow) -> Prediction:
    """Build a prediction entity from a row."""
    heatmap_location = (
        StorageLocation(row.heatmap_bucket, row.heatmap_key)
        if row.heatmap_bucket and row.heatmap_key
        else None
    )
    return Prediction(
        id=PredictionId(row.id),
        image_id=ImageId(row.image_id),
        model_version_id=ModelVersionId(row.model_version_id),
        dataset_version_id=DatasetVersionId(row.dataset_version_id),
        score=AnomalyScore(value=row.anomaly_score, threshold=row.threshold),
        inference_time_ms=row.inference_time_ms,
        predicted_at=row.predicted_at,
        heatmap_location=heatmap_location,
        correlation_id=row.correlation_id,
    )


def feedback_to_row(feedback: Feedback) -> FeedbackRow:
    """Build a row from a feedback entity."""
    return FeedbackRow(
        id=feedback.id,
        prediction_id=feedback.prediction_id,
        user_id=feedback.user_id,
        verdict=feedback.verdict.value,
        corrected_label=feedback.corrected_label.value if feedback.corrected_label else None,
        notes=feedback.notes,
        region=list(feedback.region) if feedback.region else None,
        created_at=feedback.created_at,
    )


def feedback_to_entity(row: FeedbackRow) -> Feedback:
    """Build a feedback entity from a row."""
    return Feedback(
        id=FeedbackId(row.id),
        prediction_id=PredictionId(row.prediction_id),
        user_id=UserId(row.user_id),
        verdict=FeedbackVerdict(row.verdict),
        created_at=row.created_at,
        corrected_label=ImageLabel(row.corrected_label) if row.corrected_label else None,
        notes=row.notes,
        region=tuple(row.region) if row.region else None,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------------------
# DriftReport
# --------------------------------------------------------------------------------------


def drift_report_to_row(report: DriftReport) -> DriftReportRow:
    """Build a row from a drift report entity."""
    return DriftReportRow(
        id=report.id,
        model_version_id=report.model_version_id,
        reference_dataset_version_id=report.reference_dataset_version_id,
        window_start=report.window_start,
        window_end=report.window_end,
        sample_count=report.sample_count,
        min_samples=report.min_samples,
        signals=[dataclasses.asdict(signal) for signal in report.signals],
        created_at=report.created_at,
    )


def _signal_from_json(data: dict[str, Any]) -> DriftSignal:
    """Reconstruct a drift signal from its serialised form."""
    return DriftSignal(
        name=str(data["name"]),
        statistic=float(data["statistic"]),
        threshold=float(data["threshold"]),
        method=str(data["method"]),
    )


def drift_report_to_entity(row: DriftReportRow) -> DriftReport:
    """Build a drift report entity from a row."""
    return DriftReport(
        id=DriftReportId(row.id),
        model_version_id=ModelVersionId(row.model_version_id),
        reference_dataset_version_id=DatasetVersionId(row.reference_dataset_version_id),
        window_start=row.window_start,
        window_end=row.window_end,
        sample_count=row.sample_count,
        signals=tuple(_signal_from_json(signal) for signal in row.signals),
        created_at=row.created_at,
        min_samples=row.min_samples,
    )


# --------------------------------------------------------------------------------------
# AuditEvent
# --------------------------------------------------------------------------------------


def audit_event_to_row(event: AuditEvent) -> AuditLogRow:
    """Build a row from an audit event.

    ``seq`` is intentionally omitted: it is the database identity column, assigned on
    insert, not a value the domain event chooses.
    """
    return AuditLogRow(
        actor_id=event.actor_id,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        payload=dict(event.payload),
        correlation_id=event.correlation_id,
        prev_hash=event.prev_hash,
        row_hash=event.row_hash(),
        occurred_at=event.occurred_at,
    )


def audit_event_to_entity(row: AuditLogRow) -> AuditEvent:
    """Build an audit event entity from a row."""
    return AuditEvent(
        sequence=AuditSequence(row.seq),
        action=row.action,
        resource_type=row.resource_type,
        occurred_at=row.occurred_at,
        prev_hash=row.prev_hash,
        actor_id=UserId(row.actor_id) if row.actor_id else None,
        resource_id=row.resource_id,
        payload=dict(row.payload),
        correlation_id=row.correlation_id,
    )
