"""Integration tests for :class:`MlflowExperimentTracker` against the real MLflow server."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from factoryai.domain.entities import EvaluationMetrics
from factoryai.infrastructure.tracking.mlflow_tracker import MlflowExperimentTracker
from tests.integration.tracking.conftest import MLFLOW_TRACKING_URI

pytestmark = pytest.mark.integration


@pytest.fixture
def tracker() -> MlflowExperimentTracker:
    """A tracker against the real, running MLflow server."""
    return MlflowExperimentTracker(MLFLOW_TRACKING_URI)


@pytest.fixture
def experiment_name() -> str:
    """A fresh experiment name per test, so repeated runs never collide."""
    return f"factoryai-test-{uuid.uuid4()}"


class TestRunLifecycle:
    def test_a_run_can_be_started_logged_and_ended(
        self, tracker: MlflowExperimentTracker, experiment_name: str
    ) -> None:
        run_id = tracker.start_run(experiment_name=experiment_name, run_name="test-run")

        tracker.log_params(run_id, {"model_name": "patchcore", "seed": 42})
        tracker.log_metrics(run_id, {"training_time_seconds": 1.5})
        tracker.log_evaluation(
            run_id,
            EvaluationMetrics(
                image_auroc=0.98,
                precision=0.95,
                recall=0.93,
                f1=0.94,
                threshold=0.5,
                confusion_matrix=(80, 5, 7, 93),
            ),
        )
        tracker.end_run(run_id)

        client = tracker._client
        run = client.get_run(run_id)
        assert run.data.params["model_name"] == "patchcore"
        assert run.data.metrics["training_time_seconds"] == pytest.approx(1.5)
        assert run.data.metrics["image_auroc"] == pytest.approx(0.98)
        assert run.data.metrics["confusion_matrix_tp"] == pytest.approx(93.0)
        assert run.info.status == "FINISHED"

    def test_a_second_run_in_the_same_experiment_is_independent(
        self, tracker: MlflowExperimentTracker, experiment_name: str
    ) -> None:
        first = tracker.start_run(experiment_name=experiment_name, run_name="first")
        second = tracker.start_run(experiment_name=experiment_name, run_name="second")
        assert first != second

        tracker.log_params(first, {"seed": 1})
        tracker.log_params(second, {"seed": 2})

        client = tracker._client
        assert client.get_run(first).data.params["seed"] == "1"
        assert client.get_run(second).data.params["seed"] == "2"

    def test_an_artifact_can_be_logged_and_retrieved(
        self, tracker: MlflowExperimentTracker, experiment_name: str, tmp_path: Path
    ) -> None:
        run_id = tracker.start_run(experiment_name=experiment_name, run_name="artifact-run")
        artifact = tmp_path / "checkpoint.txt"
        artifact.write_text("not a real model, just proving the round trip")

        tracker.log_artifact(run_id, artifact, artifact_path="model")
        tracker.end_run(run_id)

        client = tracker._client
        artifacts = client.list_artifacts(run_id, path="model")
        assert any(entry.path == "model/checkpoint.txt" for entry in artifacts)

    def test_a_failed_run_is_marked_failed(
        self, tracker: MlflowExperimentTracker, experiment_name: str
    ) -> None:
        run_id = tracker.start_run(experiment_name=experiment_name, run_name="failed-run")

        tracker.end_run(run_id, status="FAILED")

        client = tracker._client
        assert client.get_run(run_id).info.status == "FAILED"
