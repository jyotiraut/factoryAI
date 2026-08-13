"""Unit tests for the read-only use case behind ``GET /predictions``, against fakes."""

from __future__ import annotations

import uuid

import pytest

from factoryai.application.use_cases.list_predictions import ListPredictionsCommand
from factoryai.domain.value_objects import ModelVersionId
from tests.builders import NOW, a_prediction
from tests.fakes import FakeUnitOfWork
from tests.use_case_factory import make_list_predictions_use_case

pytestmark = pytest.mark.unit


class TestListPredictions:
    async def test_predictions_are_returned_newest_first(self) -> None:
        uow = FakeUnitOfWork()
        older = a_prediction(predicted_at=NOW)
        newer = a_prediction(predicted_at=NOW.replace(hour=13))
        await uow.predictions.add(older)
        await uow.predictions.add(newer)
        use_case = make_list_predictions_use_case(uow=uow)

        page = await use_case.execute(ListPredictionsCommand())

        assert [p.id for p in page.items] == [newer.id, older.id]
        assert page.total == 2

    async def test_pagination_math_reflects_limit_and_offset(self) -> None:
        uow = FakeUnitOfWork()
        for hour in range(5):
            await uow.predictions.add(a_prediction(predicted_at=NOW.replace(hour=hour)))
        use_case = make_list_predictions_use_case(uow=uow)

        page = await use_case.execute(ListPredictionsCommand(limit=2, offset=1))

        assert len(page.items) == 2
        assert page.total == 5
        assert page.limit == 2
        assert page.offset == 1

    async def test_narrowing_by_model_version_id_excludes_other_models(self) -> None:
        uow = FakeUnitOfWork()
        target = ModelVersionId(uuid.uuid4())
        matching = a_prediction(model_version_id=target)
        other = a_prediction()
        await uow.predictions.add(matching)
        await uow.predictions.add(other)
        use_case = make_list_predictions_use_case(uow=uow)

        page = await use_case.execute(ListPredictionsCommand(model_version_id=target))

        assert [p.id for p in page.items] == [matching.id]
        assert page.total == 1

    async def test_no_predictions_returns_an_empty_page(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_list_predictions_use_case(uow=uow)

        page = await use_case.execute(ListPredictionsCommand())

        assert page.items == []
        assert page.total == 0
