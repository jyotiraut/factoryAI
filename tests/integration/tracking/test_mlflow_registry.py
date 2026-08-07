"""Integration tests for :class:`MlflowModelRegistry` against the real MLflow server."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from factoryai.domain.value_objects import Category, ModelStage
from factoryai.infrastructure.tracking.mlflow_registry import MlflowModelRegistry
from factoryai.infrastructure.tracking.mlflow_tracker import MlflowExperimentTracker
from tests.integration.tracking.conftest import MLFLOW_TRACKING_URI

pytestmark = pytest.mark.integration


@pytest.fixture
def registry() -> MlflowModelRegistry:
    """A registry against the real, running MLflow server."""
    return MlflowModelRegistry(MLFLOW_TRACKING_URI)


@pytest.fixture
def registered_name() -> str:
    """A fresh registry name per test, so repeated runs never collide."""
    return f"factoryai-test-{uuid.uuid4()}"


def _run_with_artifact(tmp_path: Path, experiment_name: str) -> tuple[str, Path]:
    """Start a real run and log a placeholder artifact, returning its run id and path."""
    tracker = MlflowExperimentTracker(MLFLOW_TRACKING_URI)
    run_id = tracker.start_run(experiment_name=experiment_name, run_name="registry-test")
    artifact = tmp_path / "checkpoint.ckpt"
    artifact.write_bytes(b"not a real model, just proving the round trip")
    tracker.log_artifact(run_id, artifact, artifact_path="model")
    tracker.end_run(run_id)
    return run_id, Path("model") / artifact.name


class TestRegisterAndTransition:
    def test_a_version_can_be_registered_and_downloaded(
        self, registry: MlflowModelRegistry, registered_name: str, tmp_path: Path
    ) -> None:
        run_id, artifact_path = _run_with_artifact(tmp_path, registered_name)

        version = registry.register(
            name=registered_name, run_id=run_id, artifact_path=artifact_path
        )

        assert version == 1
        assert registry.list_versions(name=registered_name) == [1]

        destination = tmp_path / "downloaded"
        downloaded = registry.download(
            name=registered_name, version=version, destination=destination
        )
        assert downloaded.exists()

    def test_a_second_version_increments(
        self, registry: MlflowModelRegistry, registered_name: str, tmp_path: Path
    ) -> None:
        first_run, artifact_path = _run_with_artifact(tmp_path, registered_name)
        registry.register(name=registered_name, run_id=first_run, artifact_path=artifact_path)

        second_run, artifact_path = _run_with_artifact(tmp_path, registered_name)
        second_version = registry.register(
            name=registered_name, run_id=second_run, artifact_path=artifact_path
        )

        assert second_version == 2
        assert registry.list_versions(name=registered_name) == [1, 2]

    def test_transitioning_a_stage_is_reflected_by_get_stage_version(
        self, registry: MlflowModelRegistry, registered_name: str, tmp_path: Path
    ) -> None:
        run_id, artifact_path = _run_with_artifact(tmp_path, registered_name)
        version = registry.register(
            name=registered_name, run_id=run_id, artifact_path=artifact_path
        )

        registry.transition_stage(name=registered_name, version=version, stage=ModelStage.STAGING)

        assert registry.get_stage_version(name=registered_name, stage=ModelStage.STAGING) == version
        assert registry.get_stage_version(name=registered_name, stage=ModelStage.PRODUCTION) is None

    def test_resolve_artifact_location_points_at_a_real_s3_object(
        self, registry: MlflowModelRegistry, registered_name: str, tmp_path: Path
    ) -> None:
        run_id, artifact_path = _run_with_artifact(tmp_path, registered_name)
        version = registry.register(
            name=registered_name, run_id=run_id, artifact_path=artifact_path
        )

        location = registry.resolve_artifact_location(name=registered_name, version=version)

        assert location.bucket == "factoryai-artifacts"
        assert location.key.endswith("checkpoint.ckpt")


class TestRegistryNameForCategory:
    def test_is_deterministic_per_category(self, registry: MlflowModelRegistry) -> None:
        name = registry.registry_name_for(Category("bottle"))
        assert name == registry.registry_name_for(Category("bottle"))
        assert name != registry.registry_name_for(Category("cable"))
