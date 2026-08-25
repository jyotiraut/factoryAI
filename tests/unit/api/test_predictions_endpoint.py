"""API-level tests for ``GET /predictions`` and ``GET /predictions/feedback-queue``."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.builders import NOW, a_prediction, an_image, some_feedback
from tests.unit.api.conftest import FakeContainer, bearer_header, build_test_app

from factoryai.domain.entities import Prediction
from factoryai.domain.value_objects import UserRole

pytestmark = pytest.mark.unit


async def _add_prediction_with_image(container: FakeContainer, **overrides: object) -> Prediction:
    """Seed a prediction and the image it references — the endpoint now resolves both."""
    prediction = a_prediction(**overrides)
    await container.uow.images.add(an_image(id=prediction.image_id))
    await container.uow.predictions.add(prediction)
    return prediction


class TestListPredictions:
    async def test_a_viewer_gets_the_prediction_history(
        self, fake_container: FakeContainer
    ) -> None:
        older = await _add_prediction_with_image(fake_container, predicted_at=NOW)
        newer = await _add_prediction_with_image(fake_container, predicted_at=NOW.replace(hour=13))
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/predictions", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert [item["prediction_id"] for item in body["items"]] == [
            str(newer.id),
            str(older.id),
        ]
        assert all(item["image_url"] for item in body["items"])

    async def test_no_predictions_returns_an_empty_page(
        self, fake_container: FakeContainer
    ) -> None:
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/predictions", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_missing_bearer_token_returns_401(self, fake_container: FakeContainer) -> None:
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/predictions")

        assert response.status_code == 401


class TestListFeedbackQueue:
    async def test_a_viewer_gets_predictions_awaiting_review(
        self, fake_container: FakeContainer
    ) -> None:
        reviewed = await _add_prediction_with_image(fake_container)
        pending = await _add_prediction_with_image(fake_container)
        await fake_container.uow.predictions.add_feedback(some_feedback(prediction_id=reviewed.id))
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/predictions/feedback-queue", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["prediction_id"] == str(pending.id)

    async def test_missing_bearer_token_returns_401(self, fake_container: FakeContainer) -> None:
        with TestClient(build_test_app(fake_container)) as client:
            response = client.get("/predictions/feedback-queue")

        assert response.status_code == 401
