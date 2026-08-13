"""Unit tests for the read-only use case behind ``GET /analytics/defect-trend``."""

from __future__ import annotations

from datetime import timedelta

import pytest

from factoryai.domain.value_objects import AnomalyScore, Category, ModelStage
from tests.builders import NOW, a_model_version, a_prediction
from tests.fakes import FakeClock, FakeUnitOfWork
from tests.use_case_factory import make_get_defect_trend_use_case

pytestmark = pytest.mark.unit

_CATEGORY = Category("bottle")


class TestGetDefectTrend:
    async def test_predictions_are_bucketed_by_day_against_the_production_model(self) -> None:
        uow = FakeUnitOfWork()
        model = (
            a_model_version(category=_CATEGORY)
            .transition_to(ModelStage.STAGING)
            .transition_to(ModelStage.PRODUCTION)
        )
        await uow.models.add(model)
        day_one = NOW.replace(day=1, hour=8)
        day_two = NOW.replace(day=2, hour=8)
        await uow.predictions.add(
            a_prediction(
                model_version_id=model.id,
                predicted_at=day_one,
                score=AnomalyScore(value=0.2, threshold=0.5),
            )
        )
        await uow.predictions.add(
            a_prediction(
                model_version_id=model.id,
                predicted_at=day_two,
                score=AnomalyScore(value=0.9, threshold=0.5),
            )
        )
        await uow.predictions.add(
            a_prediction(
                model_version_id=model.id,
                predicted_at=day_two,
                score=AnomalyScore(value=0.1, threshold=0.5),
            )
        )
        use_case = make_get_defect_trend_use_case(uow=uow, clock=FakeClock(NOW))

        points = await use_case.execute(_CATEGORY, days=30)

        by_day = {point.day: point for point in points}
        assert by_day[day_one.date()].total == 1
        assert by_day[day_one.date()].defective == 0
        assert by_day[day_two.date()].total == 2
        assert by_day[day_two.date()].defective == 1
        assert by_day[day_two.date()].rate == pytest.approx(0.5)
        assert [point.day for point in points] == sorted(by_day)

    async def test_a_category_with_no_production_model_returns_an_empty_list(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_get_defect_trend_use_case(uow=uow, clock=FakeClock(NOW))

        points = await use_case.execute(_CATEGORY, days=30)

        assert points == []

    async def test_predictions_outside_the_window_are_excluded(self) -> None:
        uow = FakeUnitOfWork()
        model = (
            a_model_version(category=_CATEGORY)
            .transition_to(ModelStage.STAGING)
            .transition_to(ModelStage.PRODUCTION)
        )
        await uow.models.add(model)
        too_old = NOW.replace(day=1) - timedelta(days=60)
        await uow.predictions.add(a_prediction(model_version_id=model.id, predicted_at=too_old))
        use_case = make_get_defect_trend_use_case(uow=uow, clock=FakeClock(NOW))

        points = await use_case.execute(_CATEGORY, days=30)

        assert points == []
