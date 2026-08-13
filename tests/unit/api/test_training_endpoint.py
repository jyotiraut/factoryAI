"""API-level tests for ``GET /training/runs``."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.builders import NOW, an_experiment
from tests.unit.api.conftest import FakeContainer, bearer_header, build_test_app

from factoryai.domain.value_objects import UserRole

pytestmark = pytest.mark.unit


class TestListTrainingRuns:
    async def test_a_viewer_gets_the_training_run_history(
        self, fake_container: FakeContainer
    ) -> None:
        older = an_experiment(started_at=NOW)
        newer = an_experiment(started_at=NOW.replace(hour=13))
        await fake_container.uow.experiments.add(older)
        await fake_container.uow.experiments.add(newer)
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/training/runs", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert [item["experiment_id"] for item in body["items"]] == [
            str(newer.id),
            str(older.id),
        ]

    async def test_no_runs_returns_an_empty_page(self, fake_container: FakeContainer) -> None:
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/training/runs", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_missing_bearer_token_returns_401(self, fake_container: FakeContainer) -> None:
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/training/runs")

        assert response.status_code == 401
