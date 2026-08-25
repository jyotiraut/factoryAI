"""Pydantic request/response models — the API's published contract (ADR-0010).

These are pinned by the contract tests (``tests/unit/api/test_contract.py``): a field
renamed or removed here without updating those tests is exactly the kind of accidental
breaking change this module exists to make loud instead of silent.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

_T = TypeVar("_T")


class Page(BaseModel, Generic[_T]):
    """One page of a larger, ordered result set — the dashboard's pagination envelope."""

    items: list[_T]
    total: int
    limit: int
    offset: int


class PredictionResponse(BaseModel):
    """One scored image, everything a caller needs to act on the verdict."""

    prediction_id: str
    image_id: str
    request_id: str | None
    anomaly_score: float
    threshold: float
    is_anomalous: bool
    confidence: float = Field(ge=0.0, le=1.0)
    inference_time_ms: float
    model_version_id: str
    dataset_version_id: str
    heatmap_url: str | None = None


class BatchPredictionResponse(BaseModel):
    """The result of scoring several images in one request."""

    predictions: list[PredictionResponse]


class ModelSummaryResponse(BaseModel):
    """What is currently serving one category, or the absence of anything."""

    category: str
    model_version_id: str | None = None
    registry_name: str | None = None
    registry_version: int | None = None
    threshold: float | None = None
    metrics: dict[str, float | int | list[int] | None] | None = None


class FeedbackRequest(BaseModel):
    """An operator's judgement of a served prediction.

    Carries no ``user_id`` (Phase 7 did, and trusted it straight from the request body —
    Phase 8 closes that gap): the submitting user is now the authenticated principal
    ``require_permission(Permission.SUBMIT_FEEDBACK)`` resolves, never a value the caller
    could spoof.
    """

    prediction_id: str
    verdict: Literal["correct", "incorrect"]
    corrected_label: Literal["good", "defect", "unlabeled"] | None = None
    notes: str = ""
    region: tuple[int, int, int, int] | None = None


class FeedbackResponse(BaseModel):
    """The persisted feedback record's identifier."""

    feedback_id: str


class HealthResponse(BaseModel):
    """Liveness/readiness status, with the individual checks that produced it."""

    status: Literal["ok", "degraded"]
    checks: dict[str, bool]


class LoginRequest(BaseModel):
    """Credentials presented to ``POST /auth/login``."""

    email: str
    password: str


class TokenResponse(BaseModel):
    """A token pair, or an access token alone from a refresh."""

    access_token: str
    refresh_token: str | None = None
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds, from issuance.")


class RefreshRequest(BaseModel):
    """A refresh token presented to ``POST /auth/refresh``."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """A refresh token presented to ``POST /auth/logout`` for revocation."""

    refresh_token: str


class RegisterUserRequest(BaseModel):
    """A new account, presented to ``POST /auth/register`` by an administrator."""

    email: str
    password: str
    role: Literal["viewer", "operator", "ml_engineer", "administrator"]
    display_name: str = ""


class UserResponse(BaseModel):
    """A newly created account's identity."""

    user_id: str
    email: str
    role: str


class DeploymentActionRequest(BaseModel):
    """Common fields for a promotion or rollback request."""

    reason: str = ""


class PromoteModelRequest(DeploymentActionRequest):
    """A candidate model version to evaluate for promotion."""

    model_version_id: str


class RollbackModelRequest(DeploymentActionRequest):
    """A rollback request, via ``POST /models/{category}/rollback``.

    ``target_model_version_id`` absent restores the most recently displaced version.
    """

    target_model_version_id: str | None = None


class DeploymentResponse(BaseModel):
    """The outcome of a successful promotion or rollback.

    Reaching this response at all means the new version is now in production — a rejected
    promotion or a rollback with nothing to restore raises before a response is built, so
    there is no ``stage`` field to carry: it is always ``"production"``.
    """

    model_version_id: str
    previous_model_version_id: str | None = None


class AuditVerificationResponse(BaseModel):
    """The result of walking the entire audit chain for tampering."""

    total_events: int
    is_intact: bool
    first_broken_sequence: int | None = None


class ImageReference(BaseModel):
    """A previously-uploaded raw image, identified by its object store location."""

    bucket: str
    key: str


class BulkInferenceJobRequest(BaseModel):
    """A batch of already-stored images to score, submitted as a background job."""

    category: str
    images: list[ImageReference] = Field(min_length=1)


class RetrainingJobRequest(BaseModel):
    """A training run to submit as a background job — mirrors ``factoryai train``'s config."""

    dataset_name: str
    dataset_version_tag: str
    category: str
    model_name: str
    backbone: str | None = None
    hyperparameters: dict[str, object] = Field(default_factory=dict)
    image_size: tuple[int, int] = (256, 256)
    seed: int = 42
    device: Literal["auto", "cpu", "cuda"] = "auto"
    note: str = ""


class DatasetVersioningJobRequest(BaseModel):
    """A dataset version to freeze, submitted as a background job."""

    dataset_name: str
    category: str
    version_tag: str
    seed: int = 42
    split_ratios: dict[str, float] | None = None
    note: str = ""


class JobResponse(BaseModel):
    """The current state of a background job, as returned by ``GET /jobs/{id}``."""

    job_id: str
    job_type: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    attempts: int
    progress_completed: int
    progress_total: int
    result: dict[str, object] | None = None
    error: str | None = None


class PredictionHistoryResponse(BaseModel):
    """One served prediction, for the prediction-history and feedback-queue dashboard views."""

    prediction_id: str
    image_id: str
    model_version_id: str
    dataset_version_id: str
    anomaly_score: float
    threshold: float
    is_anomalous: bool
    confidence: float = Field(ge=0.0, le=1.0)
    inference_time_ms: float
    predicted_at: str
    correlation_id: str | None = None
    image_url: str | None = None
    heatmap_url: str | None = None


class DriftSignalResponse(BaseModel):
    """One measured drift statistic within a report."""

    name: str
    statistic: float
    threshold: float
    method: str
    breached: bool


class DriftReportResponse(BaseModel):
    """One drift analysis result, for the drift-status dashboard view."""

    report_id: str
    model_version_id: str
    reference_dataset_version_id: str
    window_start: str
    window_end: str
    sample_count: int
    severity: Literal["none", "low", "medium", "high"]
    should_trigger_retraining: bool
    signals: list[DriftSignalResponse]
    created_at: str


class DatasetVersionResponse(BaseModel):
    """One dataset version, for the dataset-versions dashboard view."""

    version_id: str
    dataset_id: str
    version_tag: str
    dvc_hash: str
    git_commit: str
    image_count: int
    note: str
    created_at: str


class TrainingRunResponse(BaseModel):
    """One training run, for the training-runs dashboard view."""

    experiment_id: str
    mlflow_run_id: str
    dataset_version_id: str
    model_family: str
    backbone: str
    status: Literal["running", "completed", "failed"]
    started_at: str
    finished_at: str | None = None
    metrics: dict[str, float | int | list[int] | None] | None = None
    failure_reason: str | None = None


class ModelVersionResponse(BaseModel):
    """One registered model version, for the model-versions dashboard view."""

    model_version_id: str
    experiment_id: str
    category: str
    registry_name: str
    registry_version: int
    stage: Literal["development", "staging", "production", "archived"]
    threshold: float
    created_at: str


class HistoricalDeploymentResponse(BaseModel):
    """One deployment or rejection record, for the deployment-history dashboard view."""

    deployment_id: str
    model_version_id: str
    action: Literal["promote", "rollback", "reject"]
    environment: str
    deployed_at: str
    previous_model_version_id: str | None = None
    reason: str = ""


class DefectTrendPointResponse(BaseModel):
    """One day's defect rate, for the defect-trends dashboard view."""

    day: str
    total: int
    defective: int
    rate: float = Field(ge=0.0, le=1.0)


class SystemHealthResponse(BaseModel):
    """A live snapshot of host and queue health, for the system-health dashboard view."""

    cpu_percent: float
    memory_percent: float
    disk_percent: float
    jobs_by_status: dict[str, int]
    model_cache_hit_ratio: float
