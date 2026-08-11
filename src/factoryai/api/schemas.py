"""Pydantic request/response models — the API's published contract (ADR-0010).

These are pinned by the contract tests (``tests/unit/api/test_contract.py``): a field
renamed or removed here without updating those tests is exactly the kind of accidental
breaking change this module exists to make loud instead of silent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
