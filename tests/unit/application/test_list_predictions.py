"""Unit tests for the read-only use case behind ``GET /predictions``, against fakes."""

from __future__ import annotations

import uuid

import pytest

from factoryai.application.use_cases.list_predictions import ListPredictionsCommand
from factoryai.domain.value_objects import ModelVersionId
from tests.builders import NOW, a_prediction, an_image
from tests.fakes import FakeUnitOfWork
from tests.use_case_factory import make_list_predictions_use_case

pytestmark = pytest.mark.unit


async def _add_prediction_with_image(uow: FakeUnitOfWork, **overrides: object) -> object:
    """Seed a prediction and the image it references — the use case now resolves both."""
    prediction = a_prediction(**overrides)  # type: ignore[arg-type]
    await uow.images.add(an_image(id=prediction.image_id))
    await uow.predictions.add(prediction)
    return prediction


class TestListPredictions:
    async def test_predictions_are_returned_newest_first(self) -> None:
        uow = FakeUnitOfWork()
        older = await _add_prediction_with_image(uow, predicted_at=NOW)
        newer = await _add_prediction_with_image(uow, predicted_at=NOW.replace(hour=13))
        use_case = make_list_predictions_use_case(uow=uow)

        page = await use_case.execute(ListPredictionsCommand())

        assert [p.prediction.id for p in page.items] == [newer.id, older.id]
        assert page.total == 2

    async def test_pagination_math_reflects_limit_and_offset(self) -> None:
        uow = FakeUnitOfWork()
        for hour in range(5):
            await _add_prediction_with_image(uow, predicted_at=NOW.replace(hour=hour))
        use_case = make_list_predictions_use_case(uow=uow)

        page = await use_case.execute(ListPredictionsCommand(limit=2, offset=1))

        assert len(page.items) == 2
        assert page.total == 5
        assert page.limit == 2
        assert page.offset == 1

    async def test_narrowing_by_model_version_id_excludes_other_models(self) -> None:
        uow = FakeUnitOfWork()
        target = ModelVersionId(uuid.uuid4())
        matching = await _add_prediction_with_image(uow, model_version_id=target)
        await _add_prediction_with_image(uow)
        use_case = make_list_predictions_use_case(uow=uow)

        page = await use_case.execute(ListPredictionsCommand(model_version_id=target))

        assert [p.prediction.id for p in page.items] == [matching.id]
        assert page.total == 1

    async def test_each_item_carries_its_image_location(self) -> None:
        uow = FakeUnitOfWork()
        prediction = await _add_prediction_with_image(uow)
        use_case = make_list_predictions_use_case(uow=uow)

        page = await use_case.execute(ListPredictionsCommand())

        image = await uow.images.get(prediction.image_id)
        assert page.items[0].image_location == image.location

    async def test_no_predictions_returns_an_empty_page(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_list_predictions_use_case(uow=uow)

        page = await use_case.execute(ListPredictionsCommand())

        assert page.items == []
        assert page.total == 0
