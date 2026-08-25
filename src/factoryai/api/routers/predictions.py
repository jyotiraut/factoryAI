"""``GET /predictions`` and ``GET /predictions/feedback-queue`` — dashboard read views."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from factoryai.api.dependencies import get_container, require_permission
from factoryai.api.schemas import Page, PredictionHistoryResponse
from factoryai.application.use_cases.list_feedback_queue import ListFeedbackQueueCommand
from factoryai.application.use_cases.list_predictions import (
    ListPredictionsCommand,
    PredictionWithImage,
)
from factoryai.bootstrap.container import Container
from factoryai.domain.policies.permissions import Permission
from factoryai.domain.value_objects import ModelVersionId, parse_uuid

router = APIRouter(tags=["predictions"])


async def _to_response(
    container: Container, item: PredictionWithImage
) -> PredictionHistoryResponse:
    """Map an enriched prediction onto its dashboard representation.

    Presigns both URLs here, at the API boundary, rather than in the use case — matching
    ``POST /predict``'s own ``_to_response`` (`api/routers/predict.py`), the only other
    place a stored object becomes a URL a browser can load. ``heatmap_url`` is ``None``
    whenever ``heatmap_location`` was never set (a model family without localisation, or
    the retention window already elapsed) — a missing heatmap on an old prediction is
    normal, not an error.
    """
    prediction = item.prediction
    heatmap_url = None
    if prediction.heatmap_location is not None:
        heatmap_url = await container.object_store.presign(
            prediction.heatmap_location, ttl_seconds=container.settings.api.heatmap_url_ttl_seconds
        )
    image_url = await container.object_store.presign(
        item.image_location, ttl_seconds=container.settings.api.heatmap_url_ttl_seconds
    )
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
        image_url=image_url,
        heatmap_url=heatmap_url,
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
        items=[await _to_response(container, p) for p in page.items],
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
        items=[await _to_response(container, p) for p in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
