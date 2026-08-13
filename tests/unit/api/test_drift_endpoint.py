"""API-level tests for ``GET /drift/reports``."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.builders import NOW, a_drift_report
from tests.unit.api.conftest import FakeContainer, bearer_header, build_test_app

from factoryai.domain.value_objects import UserRole

pytestmark = pytest.mark.unit


class TestListDriftReports:
    async def test_a_viewer_gets_the_drift_history(self, fake_container: FakeContainer) -> None:
        older = a_drift_report(created_at=NOW)
        newer = a_drift_report(created_at=NOW.replace(hour=13))
        await fake_container.uow.drift_reports.add(older)
        await fake_container.uow.drift_reports.add(newer)
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/drift/reports", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert [item["report_id"] for item in body["items"]] == [str(newer.id), str(older.id)]

    async def test_no_reports_returns_an_empty_page(self, fake_container: FakeContainer) -> None:
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/drift/reports", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_missing_bearer_token_returns_401(self, fake_container: FakeContainer) -> None:
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/drift/reports")

        assert response.status_code == 401
