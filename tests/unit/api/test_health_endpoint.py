"""API-level tests for ``GET /health/live`` and ``GET /health/ready``."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.unit.api.conftest import FakeContainer, build_test_app

from factoryai.api.dependencies import get_container
from factoryai.bootstrap.container import Container

pytestmark = pytest.mark.unit


def _always_healthy_registry(container: object) -> bool:
    """Stand in for a real MLflow reachability check that always succeeds."""
    del container
    return True


class TestLiveness:
    async def test_always_reports_ok(self, fake_container: FakeContainer) -> None:
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/health/live")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestReadiness:
    async def test_reports_ok_when_the_database_is_reachable(
        self, fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "factoryai.api.routers.health._check_model_registry", _always_healthy_registry
        )
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["checks"]["database"] is True

    async def test_reports_degraded_when_the_database_is_unreachable(
        self, fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = build_test_app(fake_container)

        class _BrokenUnitOfWork:
            async def __aenter__(self) -> _BrokenUnitOfWork:
                raise ConnectionError("database unreachable")

            async def __aexit__(self, *exc_info: object) -> None:
                return None

        class _BrokenContainer:
            settings = fake_container.settings

            def unit_of_work(self) -> _BrokenUnitOfWork:
                return _BrokenUnitOfWork()

        def _get_broken_container() -> Container:
            return _BrokenContainer()  # type: ignore[return-value]

        app.dependency_overrides[get_container] = _get_broken_container
        monkeypatch.setattr(
            "factoryai.api.routers.health._check_model_registry", _always_healthy_registry
        )

        with TestClient(app) as client:
            response = client.get("/health/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["checks"]["database"] is False
