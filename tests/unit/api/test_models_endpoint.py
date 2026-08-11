"""API-level tests for ``GET /models`` and the promotion/rollback routes."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from tests.builders import a_model_version, an_experiment, some_metrics
from tests.unit.api.conftest import FakeContainer, bearer_header, build_test_app

from factoryai.domain.value_objects import ExperimentId, ModelStage, UserRole

pytestmark = pytest.mark.unit


class TestListModels:
    async def test_reports_the_bottle_production_model(self, fake_container: FakeContainer) -> None:
        model = (
            a_model_version(metrics=some_metrics(image_auroc=0.97))
            .transition_to(ModelStage.STAGING)
            .transition_to(ModelStage.PRODUCTION)
        )
        await fake_container.uow.models.add(model)
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/models", headers=headers)

        assert response.status_code == 200
        summaries = {entry["category"]: entry for entry in response.json()}
        assert summaries["bottle"]["model_version_id"] == str(model.id)
        assert summaries["bottle"]["metrics"]["image_auroc"] == pytest.approx(0.97)

    async def test_reports_absence_when_nothing_is_promoted(
        self, fake_container: FakeContainer
    ) -> None:
        headers = await bearer_header(fake_container, UserRole.VIEWER)
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/models", headers=headers)

        assert response.status_code == 200
        summaries = {entry["category"]: entry for entry in response.json()}
        assert summaries["bottle"]["model_version_id"] is None

    async def test_missing_bearer_token_returns_401(self, fake_container: FakeContainer) -> None:
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/models")

        assert response.status_code == 401


class TestPromoteModel:
    async def test_an_ml_engineer_can_promote_a_candidate(
        self, fake_container: FakeContainer
    ) -> None:
        experiment_id = ExperimentId(uuid.uuid4())
        experiment = an_experiment(id=experiment_id)
        await fake_container.uow.experiments.add(experiment)
        candidate = a_model_version(experiment_id=experiment_id, metrics=some_metrics())
        await fake_container.uow.models.add(candidate)
        headers = await bearer_header(fake_container, UserRole.ML_ENGINEER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/models/bottle/promote",
                json={"model_version_id": str(candidate.id)},
                headers=headers,
            )

        assert response.status_code == 200
        assert response.json()["model_version_id"] == str(candidate.id)

    async def test_an_operator_cannot_promote_a_model(self, fake_container: FakeContainer) -> None:
        """The Phase 8 exit criterion: an operator cannot promote a model."""
        experiment_id = ExperimentId(uuid.uuid4())
        experiment = an_experiment(id=experiment_id)
        await fake_container.uow.experiments.add(experiment)
        candidate = a_model_version(experiment_id=experiment_id, metrics=some_metrics())
        await fake_container.uow.models.add(candidate)
        headers = await bearer_header(fake_container, UserRole.OPERATOR)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/models/bottle/promote",
                json={"model_version_id": str(candidate.id)},
                headers=headers,
            )

        assert response.status_code == 403

    async def test_a_viewer_cannot_promote_a_model(self, fake_container: FakeContainer) -> None:
        headers = await bearer_header(fake_container, UserRole.VIEWER)
        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/models/bottle/promote",
                json={"model_version_id": str(uuid.uuid4())},
                headers=headers,
            )

        assert response.status_code == 403


class TestRollbackModel:
    async def test_an_operator_cannot_roll_back_a_model(
        self, fake_container: FakeContainer
    ) -> None:
        headers = await bearer_header(fake_container, UserRole.OPERATOR)
        with TestClient(build_test_app(fake_container)) as client:
            response = client.post("/models/bottle/rollback", json={}, headers=headers)

        assert response.status_code == 403
