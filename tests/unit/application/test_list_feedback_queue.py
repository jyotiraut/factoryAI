"""Unit tests for the read-only use case behind ``GET /predictions/feedback-queue``."""

from __future__ import annotations

import pytest

from factoryai.application.use_cases.list_feedback_queue import ListFeedbackQueueCommand
from tests.builders import NOW, a_prediction, an_image, some_feedback
from tests.fakes import FakeUnitOfWork
from tests.use_case_factory import make_list_feedback_queue_use_case

pytestmark = pytest.mark.unit


async def _add_prediction_with_image(uow: FakeUnitOfWork, **overrides: object) -> object:
    """Seed a prediction and the image it references — the use case now resolves both."""
    prediction = a_prediction(**overrides)  # type: ignore[arg-type]
    await uow.images.add(an_image(id=prediction.image_id))
    await uow.predictions.add(prediction)
    return prediction


class TestListFeedbackQueue:
    async def test_predictions_without_feedback_are_returned_newest_first(self) -> None:
        uow = FakeUnitOfWork()
        older = await _add_prediction_with_image(uow, predicted_at=NOW)
        newer = await _add_prediction_with_image(uow, predicted_at=NOW.replace(hour=13))
        use_case = make_list_feedback_queue_use_case(uow=uow)

        page = await use_case.execute(ListFeedbackQueueCommand())

        assert [p.prediction.id for p in page.items] == [newer.id, older.id]
        assert page.total == 2

    async def test_a_prediction_with_feedback_is_excluded(self) -> None:
        uow = FakeUnitOfWork()
        reviewed = await _add_prediction_with_image(uow)
        pending = await _add_prediction_with_image(uow)
        await uow.predictions.add_feedback(some_feedback(prediction_id=reviewed.id))
        use_case = make_list_feedback_queue_use_case(uow=uow)

        page = await use_case.execute(ListFeedbackQueueCommand())

        assert [p.prediction.id for p in page.items] == [pending.id]
        assert page.total == 1

    async def test_pagination_math_reflects_limit_and_offset(self) -> None:
        uow = FakeUnitOfWork()
        for hour in range(5):
            await _add_prediction_with_image(uow, predicted_at=NOW.replace(hour=hour))
        use_case = make_list_feedback_queue_use_case(uow=uow)

        page = await use_case.execute(ListFeedbackQueueCommand(limit=2, offset=1))

        assert len(page.items) == 2
        assert page.total == 5
        assert page.limit == 2
        assert page.offset == 1

    async def test_an_empty_queue_returns_an_empty_page(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_list_feedback_queue_use_case(uow=uow)

        page = await use_case.execute(ListFeedbackQueueCommand())

        assert page.items == []
        assert page.total == 0
