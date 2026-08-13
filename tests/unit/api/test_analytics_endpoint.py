"""API-level tests for ``GET /analytics/defect-trend``."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from tests.builders import NOW, a_model_version, a_prediction
from tests.unit.api.conftest import FakeContainer, bearer_header, build_test_app

from factoryai.domain.value_objects import AnomalyScore, ModelStage, UserRole

pytestmark = pytest.mark.unit


class TestGetDefectTrend:
    async def test_a_viewer_gets_the_daily_defect_rate(self, fake_container: FakeContainer) -> None:
        model = (
            a_model_version().transition_to(ModelStage.STAGING).transition_to(ModelStage.PRODUCTION)
        )
        await fake_container.uow.models.add(model)
        await fake_container.uow.predictions.add(
            a_prediction(
                model_version_id=model.id,
                predicted_at=NOW - timedelta(hours=1),
                score=AnomalyScore(value=0.9, threshold=0.5),
            )
        )
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get(
                "/analytics/defect-trend", params={"category": "bottle"}, headers=headers
            )

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["total"] == 1
        assert body[0]["defective"] == 1
        assert body[0]["rate"] == pytest.approx(1.0)

    async def test_a_category_with_no_production_model_returns_an_empty_list(
        self, fake_container: FakeContainer
    ) -> None:
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get(
                "/analytics/defect-trend", params={"category": "bottle"}, headers=headers
            )

        assert response.status_code == 200
        assert response.json() == []

    async def test_missing_bearer_token_returns_401(self, fake_container: FakeContainer) -> None:
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/analytics/defect-trend", params={"category": "bottle"})

        assert response.status_code == 401
