"""Contract tests: pin the published OpenAPI shape so a breaking change is loud.

Not a rendering test — these assert on the schema FastAPI generates from
``factoryai/api/schemas.py``, which is exactly what a client generating a typed SDK from
``/openapi.json`` would depend on.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.unit.api.conftest import FakeContainer, build_test_app

pytestmark = pytest.mark.unit

_EXPECTED_PATHS = {
    "/predict",
    "/batch-predict",
    "/models",
    "/feedback",
    "/health/live",
    "/health/ready",
    "/metrics",
}

_EXPECTED_PREDICTION_RESPONSE_FIELDS = {
    "prediction_id",
    "image_id",
    "request_id",
    "anomaly_score",
    "threshold",
    "is_anomalous",
    "confidence",
    "inference_time_ms",
    "model_version_id",
    "dataset_version_id",
    "heatmap_url",
}


@pytest.fixture
def openapi_schema(fake_container: FakeContainer) -> dict[str, Any]:
    with TestClient(build_test_app(fake_container)) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    return dict(response.json())


class TestPublishedPaths:
    def test_every_expected_path_is_published(self, openapi_schema: dict[str, Any]) -> None:
        assert _EXPECTED_PATHS.issubset(openapi_schema["paths"].keys())

    def test_predict_accepts_multipart_form_data(self, openapi_schema: dict[str, Any]) -> None:
        request_body = openapi_schema["paths"]["/predict"]["post"]["requestBody"]
        assert "multipart/form-data" in request_body["content"]


class TestPredictionResponseShape:
    def test_the_prediction_response_schema_has_exactly_the_pinned_fields(
        self, openapi_schema: dict[str, Any]
    ) -> None:
        schema = openapi_schema["components"]["schemas"]["PredictionResponse"]
        assert set(schema["properties"]) == _EXPECTED_PREDICTION_RESPONSE_FIELDS

    def test_confidence_is_bounded_zero_to_one(self, openapi_schema: dict[str, Any]) -> None:
        confidence = openapi_schema["components"]["schemas"]["PredictionResponse"]["properties"][
            "confidence"
        ]
        assert confidence["minimum"] == 0.0
        assert confidence["maximum"] == 1.0
