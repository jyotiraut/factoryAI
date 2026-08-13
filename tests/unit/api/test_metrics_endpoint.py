"""API-level tests for ``GET /metrics`` (Phase 11 expansion, ADR-0014)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from tests.builders import NOW, a_model_version, an_experiment
from tests.unit.api.conftest import FakeContainer, build_test_app

from factoryai.domain.entities import DriftReport, DriftSignal
from factoryai.domain.value_objects import Category, DriftReportId, ModelStage

pytestmark = pytest.mark.unit


class TestGetMetrics:
    async def test_returns_prometheus_text_exposition(self, fake_container: FakeContainer) -> None:
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/metrics")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    async def test_system_and_cache_gauges_are_present(self, fake_container: FakeContainer) -> None:
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/metrics")

        body = response.text
        assert "factoryai_system_cpu_percent" in body
        assert "factoryai_system_memory_percent" in body
        assert "factoryai_model_cache_hit_ratio" in body
        assert "factoryai_jobs" in body

    async def test_a_breached_drift_report_is_exposed_per_signal(
        self, fake_container: FakeContainer
    ) -> None:
        experiment = an_experiment()
        await fake_container.uow.experiments.add(experiment)
        model = a_model_version(
            experiment_id=experiment.id,
            category=Category("bottle"),
            stage=ModelStage.PRODUCTION,
        )
        await fake_container.uow.models.add(model)
        report = DriftReport(
            id=DriftReportId(uuid.uuid4()),
            model_version_id=model.id,
            reference_dataset_version_id=experiment.dataset_version_id,
            window_start=NOW,
            window_end=NOW,
            sample_count=250,
            signals=(
                DriftSignal(
                    name="anomaly_score", statistic=0.5, threshold=0.1, method="wasserstein"
                ),
            ),
            created_at=NOW,
        )
        await fake_container.uow.drift_reports.add(report)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/metrics")

        body = response.text
        assert 'factoryai_drift_severity{category="bottle"} 3.0' in body
        assert (
            'factoryai_drift_signal_breached{category="bottle",signal="anomaly_score"} 1.0' in body
        )

    async def test_a_database_outage_degrades_instead_of_500ing(
        self, fake_container: FakeContainer
    ) -> None:
        """Regression test: a DB-backed gauge failing must not take the whole scrape down.

        Found live (ADR-0014): the first cut of this endpoint let a `uow.jobs.
        count_by_status()`/`uow.drift_reports.latest()` failure propagate straight to a 500,
        which makes Prometheus mark the *entire* target down — losing the CPU/memory gauges
        an operator most needs while the database is unreachable, not just the two that
        actually depend on it.
        """

        class _BrokenUnitOfWork:
            async def __aenter__(self) -> _BrokenUnitOfWork:
                raise ConnectionError("simulated database outage")

            async def __aexit__(self, *exc_info: object) -> None:
                pass

        original_unit_of_work = fake_container.unit_of_work
        fake_container.unit_of_work = _BrokenUnitOfWork  # type: ignore[assignment]
        try:
            with TestClient(build_test_app(fake_container)) as client:
                response = client.get("/metrics")
        finally:
            fake_container.unit_of_work = original_unit_of_work  # type: ignore[method-assign]

        assert response.status_code == 200
        assert "factoryai_system_cpu_percent" in response.text
