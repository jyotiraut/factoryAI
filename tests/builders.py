"""Test data builders.

Every builder returns a valid entity with sensible defaults, so a test overrides only the
one field it is actually about. This keeps the intent of a test visible instead of buried
under fifteen lines of irrelevant construction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
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
    InspectionImage,
    Job,
    ModelVersion,
    Prediction,
    User,
)
from factoryai.domain.entities.audit import GENESIS_HASH
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
    FeedbackId,
    FeedbackVerdict,
    ImageId,
    JobId,
    JobStatus,
    JobType,
    ModelVersionId,
    PredictionId,
    Resolution,
    StorageLocation,
    UserId,
    UserRole,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
GIT_COMMIT = "a" * 40


def _digest(seed: int) -> Checksum:
    """Return a deterministic, well-formed checksum for a test seed."""
    return Checksum(f"{seed:064x}")


def an_image(**overrides: Any) -> InspectionImage:
    """Build a valid inspection image."""
    defaults: dict[str, Any] = {
        "id": ImageId(uuid.uuid4()),
        "category": Category("bottle"),
        "checksum": _digest(1),
        "resolution": Resolution(1024, 1024),
        "size_bytes": 524_288,
        "location": StorageLocation("factoryai-raw", "bottle/2026/08/image.png"),
        "uploaded_at": NOW,
    }
    return InspectionImage(**{**defaults, **overrides})


def a_job(**overrides: Any) -> Job:
    """Build a valid, freshly-queued job."""
    defaults: dict[str, Any] = {
        "id": JobId(uuid.uuid4()),
        "job_type": JobType.BULK_INFERENCE,
        "status": JobStatus.QUEUED,
        "idempotency_key": f"test-key-{uuid.uuid4()}",
        "payload": {"category": "bottle", "images": []},
        "created_at": NOW,
    }
    return Job(**{**defaults, **overrides})


def a_dataset(**overrides: Any) -> Dataset:
    """Build a valid dataset."""
    defaults: dict[str, Any] = {
        "id": DatasetId(uuid.uuid4()),
        "name": "bottle-production",
        "category": Category("bottle"),
        "created_at": NOW,
    }
    return Dataset(**{**defaults, **overrides})


def a_dataset_version(**overrides: Any) -> DatasetVersion:
    """Build a valid dataset version with one member per split."""
    defaults: dict[str, Any] = {
        "id": DatasetVersionId(uuid.uuid4()),
        "dataset_id": DatasetId(uuid.uuid4()),
        "version_tag": "bottle-v1",
        "dvc_hash": "d41d8cd98f00b204e9800998ecf8427e",
        "git_commit": GIT_COMMIT,
        "members": (
            DatasetMember(ImageId(uuid.uuid4()), DatasetSplit.TRAIN),
            DatasetMember(ImageId(uuid.uuid4()), DatasetSplit.VAL),
            DatasetMember(ImageId(uuid.uuid4()), DatasetSplit.TEST),
        ),
        "created_at": NOW,
    }
    return DatasetVersion(**{**defaults, **overrides})


def some_metrics(**overrides: Any) -> EvaluationMetrics:
    """Build a valid set of evaluation metrics."""
    defaults: dict[str, Any] = {
        "image_auroc": 0.98,
        "precision": 0.95,
        "recall": 0.93,
        "f1": 0.94,
        "threshold": 0.5,
        "pixel_auroc": 0.97,
        "confusion_matrix": (80, 5, 7, 93),
    }
    return EvaluationMetrics(**{**defaults, **overrides})


def an_experiment(**overrides: Any) -> Experiment:
    """Build a running experiment."""
    defaults: dict[str, Any] = {
        "id": ExperimentId(uuid.uuid4()),
        "mlflow_run_id": "run-abc123",
        "dataset_version_id": DatasetVersionId(uuid.uuid4()),
        "model_family": "patchcore",
        "backbone": "wide_resnet50_2",
        "hyperparameters": {"coreset_sampling_ratio": 0.1},
        "config_hash": "c" * 64,
        "git_commit": GIT_COMMIT,
        "started_at": NOW,
    }
    return Experiment(**{**defaults, **overrides})


def a_model_version(**overrides: Any) -> ModelVersion:
    """Build a registered model version in the development stage."""
    defaults: dict[str, Any] = {
        "id": ModelVersionId(uuid.uuid4()),
        "experiment_id": ExperimentId(uuid.uuid4()),
        "category": Category("bottle"),
        "registry_name": "factoryai-patchcore-bottle",
        "registry_version": 1,
        "threshold": 0.5,
        "artifact_location": StorageLocation("factoryai-artifacts", "bottle/v1/model.ckpt"),
        "metrics": some_metrics(),
        "created_at": NOW,
    }
    return ModelVersion(**{**defaults, **overrides})


def a_deployment(**overrides: Any) -> Deployment:
    """Build a promotion record."""
    defaults: dict[str, Any] = {
        "id": DeploymentId(uuid.uuid4()),
        "model_version_id": ModelVersionId(uuid.uuid4()),
        "action": DeploymentAction.PROMOTE,
        "environment": "production",
        "deployed_at": NOW,
    }
    return Deployment(**{**defaults, **overrides})


def a_prediction(**overrides: Any) -> Prediction:
    """Build a nominal prediction."""
    defaults: dict[str, Any] = {
        "id": PredictionId(uuid.uuid4()),
        "image_id": ImageId(uuid.uuid4()),
        "model_version_id": ModelVersionId(uuid.uuid4()),
        "dataset_version_id": DatasetVersionId(uuid.uuid4()),
        "score": AnomalyScore(value=0.2, threshold=0.5),
        "inference_time_ms": 42.0,
        "predicted_at": NOW,
    }
    return Prediction(**{**defaults, **overrides})


def some_feedback(**overrides: Any) -> Feedback:
    """Build feedback confirming a prediction."""
    defaults: dict[str, Any] = {
        "id": FeedbackId(uuid.uuid4()),
        "prediction_id": PredictionId(uuid.uuid4()),
        "user_id": UserId(uuid.uuid4()),
        "verdict": FeedbackVerdict.CORRECT,
        "created_at": NOW,
    }
    return Feedback(**{**defaults, **overrides})


def a_drift_signal(**overrides: Any) -> DriftSignal:
    """Build a drift signal that is within its threshold."""
    defaults: dict[str, Any] = {
        "name": "anomaly_score",
        "statistic": 0.05,
        "threshold": 0.10,
        "method": "wasserstein",
    }
    return DriftSignal(**{**defaults, **overrides})


def a_drift_report(**overrides: Any) -> DriftReport:
    """Build a conclusive drift report with no breached signals."""
    defaults: dict[str, Any] = {
        "id": DriftReportId(uuid.uuid4()),
        "model_version_id": ModelVersionId(uuid.uuid4()),
        "reference_dataset_version_id": DatasetVersionId(uuid.uuid4()),
        "window_start": NOW,
        "window_end": NOW,
        "sample_count": 500,
        "signals": (a_drift_signal(),),
        "created_at": NOW,
    }
    return DriftReport(**{**defaults, **overrides})


def an_audit_event(**overrides: Any) -> AuditEvent:
    """Build the genesis audit record."""
    defaults: dict[str, Any] = {
        "sequence": AuditSequence(1),
        "action": "image.ingested",
        "resource_type": "image",
        "occurred_at": NOW,
        "prev_hash": GENESIS_HASH,
    }
    return AuditEvent(**{**defaults, **overrides})


def a_user(**overrides: Any) -> User:
    """Build an active operator."""
    defaults: dict[str, Any] = {
        "id": UserId(uuid.uuid4()),
        "email": "operator@factory.example",
        "role": UserRole.OPERATOR,
        "created_at": NOW,
    }
    return User(**{**defaults, **overrides})


def a_path(name: str = "model.ckpt") -> Path:
    """Return a placeholder artifact path. Never touched on disk; value only."""
    return Path("/tmp/factoryai") / name
