"""API-level tests for ``POST /feedback``."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from tests.builders import a_prediction, an_image
from tests.unit.api.conftest import FakeContainer, bearer_header, build_test_app

from factoryai.domain.value_objects import UserRole

pytestmark = pytest.mark.unit


class TestSubmitFeedback:
    async def test_a_correction_is_accepted(self, fake_container: FakeContainer) -> None:
        prediction = a_prediction()
        await fake_container.uow.predictions.add(prediction)
        await fake_container.uow.images.add(an_image(id=prediction.image_id))
        headers = await bearer_header(fake_container)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/feedback",
                json={
                    "prediction_id": str(prediction.id),
                    "verdict": "incorrect",
                    "corrected_label": "defect",
                    "notes": "missed a scratch",
                },
                headers=headers,
            )

        assert response.status_code == 201
        assert response.json()["feedback_id"]

    async def test_an_unknown_prediction_returns_404(self, fake_container: FakeContainer) -> None:
        headers = await bearer_header(fake_container)
        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/feedback",
                json={"prediction_id": str(uuid.uuid4()), "verdict": "correct"},
                headers=headers,
            )

        assert response.status_code == 404

    async def test_incorrect_with_no_correction_returns_422(
        self, fake_container: FakeContainer
    ) -> None:
        prediction = a_prediction()
        await fake_container.uow.predictions.add(prediction)
        headers = await bearer_header(fake_container)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/feedback",
                json={"prediction_id": str(prediction.id), "verdict": "incorrect"},
                headers=headers,
            )

        assert response.status_code == 422

    async def test_missing_bearer_token_returns_401(self, fake_container: FakeContainer) -> None:
        prediction = a_prediction()
        await fake_container.uow.predictions.add(prediction)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/feedback", json={"prediction_id": str(prediction.id), "verdict": "correct"}
            )

        assert response.status_code == 401

    async def test_a_viewer_cannot_submit_feedback(self, fake_container: FakeContainer) -> None:
        prediction = a_prediction()
        await fake_container.uow.predictions.add(prediction)
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/feedback",
                json={"prediction_id": str(prediction.id), "verdict": "correct"},
                headers=headers,
            )

        assert response.status_code == 403
