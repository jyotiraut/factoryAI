"""Unit tests for the feedback use case, against fakes."""

from __future__ import annotations

import uuid

import pytest

from factoryai.application.use_cases.submit_feedback import SubmitFeedbackCommand
from factoryai.domain.errors import EntityNotFoundError, InvariantViolationError
from factoryai.domain.value_objects import (
    FeedbackVerdict,
    ImageLabel,
    PredictionId,
    ProcessingStatus,
    UserId,
)
from tests.builders import NOW, a_prediction, an_image
from tests.fakes import FakeClock, FakeIdGenerator, FakeUnitOfWork
from tests.use_case_factory import make_submit_feedback_use_case

pytestmark = pytest.mark.unit


class TestSubmitFeedback:
    async def test_a_correction_is_persisted(self) -> None:
        uow = FakeUnitOfWork()
        prediction = a_prediction()
        await uow.predictions.add(prediction)
        image = an_image(id=prediction.image_id)
        await uow.images.add(image)
        use_case = make_submit_feedback_use_case(
            uow=uow, clock=FakeClock(NOW), id_generator=FakeIdGenerator()
        )

        result = await use_case.execute(
            SubmitFeedbackCommand(
                prediction_id=prediction.id,
                user_id=UserId(uuid.uuid4()),
                verdict=FeedbackVerdict.INCORRECT,
                corrected_label=ImageLabel.DEFECT,
                notes="missed a scratch",
            )
        )

        assert result.feedback_id is not None
        latest = await uow.audit.latest()
        assert latest is not None
        assert latest.action == "feedback.submitted"

        updated_image = await uow.images.get(image.id)
        assert updated_image.status is ProcessingStatus.VALID
        assert updated_image.label is ImageLabel.DEFECT
        assert updated_image.metadata["feedback_reviewed"] is True

    async def test_a_confirmation_needs_no_corrected_label(self) -> None:
        uow = FakeUnitOfWork()
        prediction = a_prediction()
        await uow.predictions.add(prediction)
        image = an_image(id=prediction.image_id)
        await uow.images.add(image)
        use_case = make_submit_feedback_use_case(
            uow=uow, clock=FakeClock(NOW), id_generator=FakeIdGenerator()
        )

        result = await use_case.execute(
            SubmitFeedbackCommand(
                prediction_id=prediction.id,
                user_id=UserId(uuid.uuid4()),
                verdict=FeedbackVerdict.CORRECT,
            )
        )

        assert result.feedback_id is not None

    async def test_an_unknown_prediction_raises(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_submit_feedback_use_case(
            uow=uow, clock=FakeClock(NOW), id_generator=FakeIdGenerator()
        )

        with pytest.raises(EntityNotFoundError):
            await use_case.execute(
                SubmitFeedbackCommand(
                    prediction_id=PredictionId(uuid.uuid4()),
                    user_id=UserId(uuid.uuid4()),
                    verdict=FeedbackVerdict.CORRECT,
                )
            )

    async def test_an_incorrect_verdict_with_no_correction_raises(self) -> None:
        uow = FakeUnitOfWork()
        prediction = a_prediction()
        await uow.predictions.add(prediction)
        use_case = make_submit_feedback_use_case(
            uow=uow, clock=FakeClock(NOW), id_generator=FakeIdGenerator()
        )

        with pytest.raises(InvariantViolationError):
            await use_case.execute(
                SubmitFeedbackCommand(
                    prediction_id=prediction.id,
                    user_id=UserId(uuid.uuid4()),
                    verdict=FeedbackVerdict.INCORRECT,
                )
            )
