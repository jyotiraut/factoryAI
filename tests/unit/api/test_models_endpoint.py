"""API-level tests for ``GET /models`` and the promotion/rollback routes."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from tests.builders import NOW, a_deployment, a_model_version, an_experiment, some_metrics
from tests.unit.api.conftest import FakeContainer, bearer_header, build_test_app

from factoryai.domain.value_objects import Category, ExperimentId, ModelStage, UserRole

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


class TestListModelVersions:
    async def test_a_viewer_gets_every_version_for_the_category_newest_first(
        self, fake_container: FakeContainer
    ) -> None:
        older = a_model_version(category=Category("bottle"), created_at=NOW)
        newer = a_model_version(category=Category("bottle"), created_at=NOW.replace(hour=13))
        other_category = a_model_version(category=Category("cable"))
        await fake_container.uow.models.add(older)
        await fake_container.uow.models.add(newer)
        await fake_container.uow.models.add(other_category)
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get(
                "/models/versions", params={"category": "bottle"}, headers=headers
            )

        assert response.status_code == 200
        ids = [entry["model_version_id"] for entry in response.json()]
        assert ids == [str(newer.id), str(older.id)]

    async def test_a_category_with_no_versions_returns_an_empty_list(
        self, fake_container: FakeContainer
    ) -> None:
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get(
                "/models/versions", params={"category": "bottle"}, headers=headers
            )

        assert response.status_code == 200
        assert response.json() == []

    async def test_missing_bearer_token_returns_401(self, fake_container: FakeContainer) -> None:
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/models/versions", params={"category": "bottle"})

        assert response.status_code == 401


class TestListDeployments:
    async def test_a_viewer_gets_the_deployment_history_newest_first(
        self, fake_container: FakeContainer
    ) -> None:
        model = a_model_version(category=Category("bottle"))
        await fake_container.uow.models.add(model)
        older = a_deployment(model_version_id=model.id, environment="production", deployed_at=NOW)
        newer = a_deployment(
            model_version_id=model.id,
            environment="production",
            deployed_at=NOW.replace(hour=13),
        )
        await fake_container.uow.models.add_deployment(older)
        await fake_container.uow.models.add_deployment(newer)
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get(
                "/models/deployments", params={"category": "bottle"}, headers=headers
            )

        assert response.status_code == 200
        ids = [entry["deployment_id"] for entry in response.json()]
        assert ids == [str(newer.id), str(older.id)]

    async def test_a_category_with_no_deployments_returns_an_empty_list(
        self, fake_container: FakeContainer
    ) -> None:
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get(
                "/models/deployments", params={"category": "bottle"}, headers=headers
            )

        assert response.status_code == 200
        assert response.json() == []

    async def test_missing_bearer_token_returns_401(self, fake_container: FakeContainer) -> None:
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/models/deployments", params={"category": "bottle"})

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
