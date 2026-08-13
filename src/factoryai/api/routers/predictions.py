"""``GET /predictions`` and ``GET /predictions/feedback-queue`` — dashboard read views."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from factoryai.api.dependencies import get_container, require_permission
from factoryai.api.schemas import Page, PredictionHistoryResponse
from factoryai.application.use_cases.list_feedback_queue import ListFeedbackQueueCommand
from factoryai.application.use_cases.list_predictions import ListPredictionsCommand
from factoryai.bootstrap.container import Container
from factoryai.domain.entities import Prediction
from factoryai.domain.policies.permissions import Permission
from factoryai.domain.value_objects import ModelVersionId, parse_uuid

router = APIRouter(tags=["predictions"])


def _to_response(prediction: Prediction) -> PredictionHistoryResponse:
    """Map a domain prediction onto its dashboard representation."""
    return PredictionHistoryResponse(
        prediction_id=str(prediction.id),
        image_id=str(prediction.image_id),
        model_version_id=str(prediction.model_version_id),
        dataset_version_id=str(prediction.dataset_version_id),
        anomaly_score=prediction.score.value,
        threshold=prediction.score.threshold,
        is_anomalous=prediction.is_anomalous,
        confidence=prediction.confidence,
        inference_time_ms=prediction.inference_time_ms,
        predicted_at=prediction.predicted_at.isoformat(),
        correlation_id=prediction.correlation_id,
    )


@router.get(
    "/predictions",
    response_model=Page[PredictionHistoryResponse],
    dependencies=[Depends(require_permission(Permission.VIEW_PREDICTIONS))],
)
async def list_predictions(
    model_version_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    container: Container = Depends(get_container),
) -> Page[PredictionHistoryResponse]:
    """Return a page of prediction history, newest first, for the dashboard."""
    use_case = container.list_predictions_use_case()
    page = await use_case.execute(
        ListPredictionsCommand(
            model_version_id=(
                ModelVersionId(parse_uuid(model_version_id)) if model_version_id else None
            ),
            limit=limit,
            offset=offset,
        )
    )
    return Page(
        items=[_to_response(p) for p in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/predictions/feedback-queue",
    response_model=Page[PredictionHistoryResponse],
    dependencies=[Depends(require_permission(Permission.VIEW_PREDICTIONS))],
)
async def list_feedback_queue(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    container: Container = Depends(get_container),
) -> Page[PredictionHistoryResponse]:
    """Return a page of predictions awaiting operator review, newest first."""
    use_case = container.list_feedback_queue_use_case()
    page = await use_case.execute(ListFeedbackQueueCommand(limit=limit, offset=offset))
    return Page(
        items=[_to_response(p) for p in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
