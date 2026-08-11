"""API-level tests for ``POST /predict`` and ``POST /batch-predict``."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from tests.builders import a_model_version, an_experiment, some_metrics
from tests.fakes import FakeAnomalyDetector
from tests.unit.api.conftest import FakeContainer, bearer_header, build_test_app

from factoryai.domain.ports.detection import RawPrediction
from factoryai.domain.value_objects import AnomalyScore, ExperimentId, ModelStage, UserRole

pytestmark = pytest.mark.unit


async def _seed_production_model(container: FakeContainer, *, threshold: float = 0.5) -> None:
    experiment_id = ExperimentId(uuid.uuid4())
    experiment = an_experiment(id=experiment_id, model_family="patchcore", backbone="resnet18")
    await container.uow.experiments.add(experiment)
    model = (
        a_model_version(experiment_id=experiment_id, threshold=threshold, metrics=some_metrics())
        .transition_to(ModelStage.STAGING)
        .transition_to(ModelStage.PRODUCTION)
    )
    await container.uow.models.add(model)


class TestPredict:
    async def test_a_valid_upload_returns_a_full_prediction_response(
        self, fake_container: FakeContainer
    ) -> None:
        fake_container._detector = FakeAnomalyDetector(
            prediction=RawPrediction(
                score=AnomalyScore(value=0.9, threshold=0.5), inference_time_ms=10.0
            )
        )
        await _seed_production_model(fake_container)
        headers = await bearer_header(fake_container)
        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/predict",
                data={"category": "bottle"},
                files={"image": ("test.png", b"fake-bytes", "image/png")},
                headers=headers,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["is_anomalous"] is True
        assert body["anomaly_score"] == 0.9
        assert body["threshold"] == 0.5
        assert 0.0 <= body["confidence"] <= 1.0
        assert body["prediction_id"]
        assert body["model_version_id"]

    async def test_echoes_the_correlation_id_header(self, fake_container: FakeContainer) -> None:
        fake_container._detector = FakeAnomalyDetector()
        await _seed_production_model(fake_container)
        headers = await bearer_header(fake_container)
        headers["X-Correlation-ID"] = "my-request-id"
        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/predict",
                data={"category": "bottle"},
                files={"image": ("test.png", b"fake-bytes", "image/png")},
                headers=headers,
            )

        assert response.headers["X-Correlation-ID"] == "my-request-id"
        assert response.json()["request_id"] == "my-request-id"

    async def test_no_production_model_returns_409(self, fake_container: FakeContainer) -> None:
        headers = await bearer_header(fake_container)
        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/predict",
                data={"category": "bottle"},
                files={"image": ("test.png", b"fake-bytes", "image/png")},
                headers=headers,
            )

        assert response.status_code == 409

    async def test_missing_bearer_token_returns_401(self, fake_container: FakeContainer) -> None:
        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/predict",
                data={"category": "bottle"},
                files={"image": ("test.png", b"fake-bytes", "image/png")},
            )

        assert response.status_code == 401

    async def test_a_viewer_cannot_submit_a_prediction(self, fake_container: FakeContainer) -> None:
        headers = await bearer_header(fake_container, UserRole.VIEWER)
        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/predict",
                data={"category": "bottle"},
                files={"image": ("test.png", b"fake-bytes", "image/png")},
                headers=headers,
            )

        assert response.status_code == 403


class TestBatchPredict:
    async def test_scores_every_image_in_the_batch(self, fake_container: FakeContainer) -> None:
        fake_container._detector = FakeAnomalyDetector()
        await _seed_production_model(fake_container)
        headers = await bearer_header(fake_container)
        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/batch-predict",
                data={"category": "bottle"},
                files=[
                    ("images", ("a.png", b"one", "image/png")),
                    ("images", ("b.png", b"two", "image/png")),
                ],
                headers=headers,
            )

        assert response.status_code == 200
        predictions = response.json()["predictions"]
        assert len(predictions) == 2

    async def test_exceeding_the_max_batch_size_returns_400(
        self, fake_container: FakeContainer
    ) -> None:
        fake_container.settings = fake_container.settings.model_copy(
            update={"api": fake_container.settings.api.model_copy(update={"max_batch_size": 1})}
        )
        fake_container._detector = FakeAnomalyDetector()
        await _seed_production_model(fake_container)
        headers = await bearer_header(fake_container)
        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/batch-predict",
                data={"category": "bottle"},
                files=[
                    ("images", ("a.png", b"one", "image/png")),
                    ("images", ("b.png", b"two", "image/png")),
                ],
                headers=headers,
            )

        assert response.status_code == 400
