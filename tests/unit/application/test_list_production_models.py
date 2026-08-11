"""Unit tests for the read-only use case behind ``GET /models``, against fakes."""

from __future__ import annotations

import pytest

from factoryai.domain.value_objects import Category, ModelStage
from tests.builders import a_model_version, some_metrics
from tests.fakes import FakeUnitOfWork
from tests.use_case_factory import make_list_production_models_use_case

pytestmark = pytest.mark.unit


class TestListProductionModels:
    async def test_a_category_with_a_production_model_reports_it(self) -> None:
        uow = FakeUnitOfWork()
        model = (
            a_model_version(category=Category("bottle"), metrics=some_metrics(image_auroc=0.99))
            .transition_to(ModelStage.STAGING)
            .transition_to(ModelStage.PRODUCTION)
        )
        await uow.models.add(model)
        use_case = make_list_production_models_use_case(uow=uow)

        summaries = await use_case.execute([Category("bottle")])

        assert len(summaries) == 1
        assert summaries[0].model_version_id == model.id
        assert summaries[0].metrics is not None
        assert summaries[0].metrics.image_auroc == 0.99

    async def test_a_category_with_no_production_model_reports_absence(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_list_production_models_use_case(uow=uow)

        summaries = await use_case.execute([Category("bottle")])

        assert len(summaries) == 1
        assert summaries[0].model_version_id is None
        assert summaries[0].metrics is None

    async def test_multiple_categories_are_reported_in_order(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_list_production_models_use_case(uow=uow)

        summaries = await use_case.execute([Category("bottle"), Category("cable")])

        assert [summary.category for summary in summaries] == [
            Category("bottle"),
            Category("cable"),
        ]
