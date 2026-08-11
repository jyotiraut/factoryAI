"""``POST /jobs/*`` submission endpoints and ``GET /jobs/{id}``.

Every submission route requires an ``Idempotency-Key`` header: the client, not the
platform, decides what "the same submission" means (a retried request after a timeout, a
double-click), and :class:`~factoryai.application.use_cases.submit_job.SubmitJob` only
needs that one opaque string to deduplicate correctly.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status

from factoryai.api.dependencies import get_container, require_permission
from factoryai.api.schemas import (
    BulkInferenceJobRequest,
    DatasetVersioningJobRequest,
    JobResponse,
    RetrainingJobRequest,
)
from factoryai.application.use_cases.submit_job import SubmitJobCommand
from factoryai.bootstrap.container import Container
from factoryai.domain.entities import Job, User
from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.policies.permissions import Permission
from factoryai.domain.value_objects import JobId, JobType, parse_uuid

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/bulk-predict", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_bulk_inference(
    request: BulkInferenceJobRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    container: Container = Depends(get_container),
    user: User = Depends(require_permission(Permission.SUBMIT_PREDICTION)),
) -> JobResponse:
    """Score a batch of already-uploaded images without blocking on the whole batch.

    Raises:
        HTTPException: 401/403 if the caller is not authenticated or lacks
            ``submit_prediction``.
    """
    payload: dict[str, Any] = {
        "category": request.category,
        "images": [image.model_dump() for image in request.images],
    }
    return await _submit(container, JobType.BULK_INFERENCE, idempotency_key, payload, user)


@router.post("/retrain", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_retraining(
    request: RetrainingJobRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    container: Container = Depends(get_container),
    user: User = Depends(require_permission(Permission.TRAIN_MODEL)),
) -> JobResponse:
    """Submit a training run as a background job.

    Raises:
        HTTPException: 401/403 if the caller is not authenticated or lacks ``train_model``.
    """
    payload = request.model_dump()
    return await _submit(container, JobType.RETRAINING, idempotency_key, payload, user)


@router.post("/dataset-version", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_dataset_versioning(
    request: DatasetVersioningJobRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    container: Container = Depends(get_container),
    user: User = Depends(require_permission(Permission.MANAGE_DATASETS)),
) -> JobResponse:
    """Submit a dataset-versioning run as a background job.

    Raises:
        HTTPException: 401/403 if the caller is not authenticated or lacks
            ``manage_datasets``.
    """
    payload = request.model_dump()
    return await _submit(container, JobType.DATASET_VERSIONING, idempotency_key, payload, user)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    container: Container = Depends(get_container),
    _user: User = Depends(require_permission(Permission.VIEW_JOBS)),
) -> JobResponse:
    """Return a job's current status, progress and result.

    Raises:
        HTTPException: 401/403 if the caller is not authenticated or lacks ``view_jobs``;
            404 if no such job exists or ``job_id`` is not a well-formed identifier.
    """
    try:
        parsed = JobId(parse_uuid(job_id))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="job not found") from exc
    try:
        job = await container.get_job_status_use_case().execute(parsed)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    return _to_response(job)


async def _submit(
    container: Container,
    job_type: JobType,
    idempotency_key: str,
    payload: dict[str, Any],
    user: User,
) -> JobResponse:
    """Record the job, dispatch its Celery task if newly created, and respond."""
    result = await container.submit_job_use_case().execute(
        SubmitJobCommand(
            job_type=job_type,
            idempotency_key=idempotency_key,
            payload=payload,
            submitted_by=user.id,
        )
    )
    if result.is_new:
        container.dispatch_job(result.job)
    return _to_response(result.job)


def _to_response(job: Job) -> JobResponse:
    """Build the response from a job entity."""
    return JobResponse(
        job_id=str(job.id),
        job_type=job.job_type.value,
        status=job.status.value,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        attempts=job.attempts,
        progress_completed=job.progress_completed,
        progress_total=job.progress_total,
        result=job.result,
        error=job.error,
    )
