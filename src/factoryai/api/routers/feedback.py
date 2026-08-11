"""``POST /feedback`` — an operator's judgement of a served prediction."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from factoryai.api.dependencies import get_container, require_permission
from factoryai.api.schemas import FeedbackRequest, FeedbackResponse
from factoryai.application.use_cases.submit_feedback import SubmitFeedbackCommand
from factoryai.bootstrap.container import Container
from factoryai.domain.entities import User
from factoryai.domain.errors import EntityNotFoundError, InvariantViolationError
from factoryai.domain.policies.permissions import Permission
from factoryai.domain.value_objects import FeedbackVerdict, ImageLabel, PredictionId, parse_uuid

router = APIRouter(tags=["feedback"])


@router.post("/feedback", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    payload: FeedbackRequest,
    container: Container = Depends(get_container),
    user: User = Depends(require_permission(Permission.SUBMIT_FEEDBACK)),
) -> FeedbackResponse:
    """Record an operator's correction or confirmation of a prediction.

    Raises:
        HTTPException: 401/403 if the caller is not authenticated or lacks
            ``submit_feedback``; 404 if the prediction does not exist; 422 if the feedback
            is internally inconsistent (e.g. "incorrect" with no corrected label).
    """
    use_case = container.submit_feedback_use_case()
    try:
        result = await use_case.execute(
            SubmitFeedbackCommand(
                prediction_id=PredictionId(parse_uuid(payload.prediction_id)),
                user_id=user.id,
                verdict=FeedbackVerdict(payload.verdict),
                corrected_label=(
                    ImageLabel(payload.corrected_label) if payload.corrected_label else None
                ),
                notes=payload.notes,
                region=payload.region,
            )
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except InvariantViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc

    return FeedbackResponse(feedback_id=str(result.feedback_id))
