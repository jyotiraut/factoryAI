"""``POST /predict`` and ``POST /batch-predict``."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from factoryai.api.dependencies import acquire_prediction_slot, get_container, require_permission
from factoryai.api.metrics import PREDICTION_LATENCY_SECONDS, PREDICTIONS_TOTAL
from factoryai.api.schemas import BatchPredictionResponse, PredictionResponse
from factoryai.application.use_cases.predict_image import PredictImageCommand, PredictImageResult
from factoryai.bootstrap.container import Container
from factoryai.domain.errors import CorruptImageError, NoProductionModelError
from factoryai.domain.policies.permissions import Permission
from factoryai.domain.value_objects import Category
from factoryai.shared.correlation import get_correlation_id

router = APIRouter(tags=["predict"])


@router.post(
    "/predict",
    response_model=PredictionResponse,
    dependencies=[
        Depends(acquire_prediction_slot),
        Depends(require_permission(Permission.SUBMIT_PREDICTION)),
    ],
)
async def predict(
    category: str = Form(...),
    image: UploadFile = File(...),
    container: Container = Depends(get_container),
) -> PredictionResponse:
    """Score one image against the category's current production model.

    Raises:
        HTTPException: 401/403 if the caller is not authenticated or lacks
            ``submit_prediction``; 409 if the category has no production model; 400 if the
            upload cannot be decoded as an image; 504 if inference exceeds
            ``API_REQUEST_TIMEOUT_SECONDS``.
    """
    payload = await image.read()
    use_case = container.predict_image_use_case()
    command = PredictImageCommand(
        category=Category.parse(category), payload=payload, correlation_id=get_correlation_id()
    )
    try:
        async with asyncio.timeout(container.settings.api.request_timeout_seconds):
            result = await use_case.execute(command)
    except NoProductionModelError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except CorruptImageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="prediction timed out"
        ) from exc

    _record_metrics(command.category, result)
    return await _to_response(container, result)


@router.post(
    "/batch-predict",
    response_model=BatchPredictionResponse,
    dependencies=[
        Depends(acquire_prediction_slot),
        Depends(require_permission(Permission.SUBMIT_PREDICTION)),
    ],
)
async def batch_predict(
    category: str = Form(...),
    images: list[UploadFile] = File(...),
    container: Container = Depends(get_container),
) -> BatchPredictionResponse:
    """Score several images in one request, sharing one detector forward pass.

    Raises:
        HTTPException: 401/403 if the caller is not authenticated or lacks
            ``submit_prediction``; 400 if the batch is empty, exceeds
            ``API_MAX_BATCH_SIZE``, or any upload cannot be decoded; 409 if the category has
            no production model; 504 if inference exceeds ``API_REQUEST_TIMEOUT_SECONDS``.
    """
    max_batch_size = container.settings.api.max_batch_size
    if not images:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="at least one image is required")
    if len(images) > max_batch_size:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"batch of {len(images)} exceeds the maximum of {max_batch_size}",
        )

    category_vo = Category.parse(category)
    correlation_id = get_correlation_id()
    commands = [
        PredictImageCommand(
            category=category_vo, payload=await image.read(), correlation_id=correlation_id
        )
        for image in images
    ]
    use_case = container.predict_image_use_case()
    try:
        async with asyncio.timeout(container.settings.api.request_timeout_seconds):
            results = await use_case.execute_batch(commands)
    except NoProductionModelError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except CorruptImageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="prediction timed out"
        ) from exc

    for result in results:
        _record_metrics(category_vo, result)
    responses = [await _to_response(container, result) for result in results]
    return BatchPredictionResponse(predictions=responses)


def _record_metrics(category: Category, result: PredictImageResult) -> None:
    """Update the Prometheus collectors for one served prediction."""
    PREDICTIONS_TOTAL.labels(category=category.code, is_anomalous=str(result.is_anomalous)).inc()
    PREDICTION_LATENCY_SECONDS.labels(category=category.code).observe(
        result.inference_time_ms / 1000
    )


async def _to_response(container: Container, result: PredictImageResult) -> PredictionResponse:
    """Build the response, presigning the heatmap URL if one was stored."""
    heatmap_url = None
    if result.heatmap_location is not None:
        heatmap_url = await container.object_store.presign(
            result.heatmap_location, ttl_seconds=container.settings.api.heatmap_url_ttl_seconds
        )
    return PredictionResponse(
        prediction_id=str(result.prediction_id),
        image_id=str(result.image_id),
        request_id=result.correlation_id,
        anomaly_score=result.anomaly_score,
        threshold=result.threshold,
        is_anomalous=result.is_anomalous,
        confidence=result.confidence,
        inference_time_ms=result.inference_time_ms,
        model_version_id=str(result.model_version_id),
        dataset_version_id=str(result.dataset_version_id),
        heatmap_url=heatmap_url,
    )
