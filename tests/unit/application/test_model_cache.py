"""Unit tests for the model cache, against fakes."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from factoryai.application.services.model_cache import ModelCache
from factoryai.domain.value_objects import Category, ModelVersionId
from tests.fakes import FakeAnomalyDetector, FakeModelRegistry

pytestmark = pytest.mark.unit

_CATEGORY = Category("bottle")


def _cache(
    tmp_path: Path, registry: FakeModelRegistry, detector: FakeAnomalyDetector
) -> ModelCache:
    registry.register(name="factoryai-bottle", run_id="run-1", artifact_path=Path("model.ckpt"))
    return ModelCache(
        detector_factory=lambda name, backbone: detector,  # noqa: ARG005
        model_registry=registry,
        workdir=tmp_path,
    )


class TestGet:
    async def test_a_fresh_category_is_loaded(self, tmp_path: Path) -> None:
        registry = FakeModelRegistry()
        detector = FakeAnomalyDetector()
        cache = _cache(tmp_path, registry, detector)
        version_id = ModelVersionId(uuid.uuid4())

        loaded = await cache.get(
            _CATEGORY,
            model_version_id=version_id,
            registry_name="factoryai-bottle",
            registry_version=1,
            threshold=0.5,
            model_family="patchcore",
            backbone="wide_resnet50_2",
        )

        assert loaded is detector
        assert detector.loaded
        assert cache.loaded_categories() == (_CATEGORY,)

    async def test_the_same_version_reuses_the_cached_detector_without_reloading(
        self, tmp_path: Path
    ) -> None:
        registry = FakeModelRegistry()
        detector = FakeAnomalyDetector()
        cache = _cache(tmp_path, registry, detector)
        version_id = ModelVersionId(uuid.uuid4())

        first = await cache.get(
            _CATEGORY,
            model_version_id=version_id,
            registry_name="factoryai-bottle",
            registry_version=1,
            threshold=0.5,
            model_family="patchcore",
            backbone="wide_resnet50_2",
        )
        detector.loaded = False  # prove a second call doesn't reload
        second = await cache.get(
            _CATEGORY,
            model_version_id=version_id,
            registry_name="factoryai-bottle",
            registry_version=1,
            threshold=0.5,
            model_family="patchcore",
            backbone="wide_resnet50_2",
        )

        assert first is second
        assert detector.loaded is False

    async def test_a_new_version_replaces_the_cached_detector(self, tmp_path: Path) -> None:
        registry = FakeModelRegistry()
        detectors = [FakeAnomalyDetector(), FakeAnomalyDetector()]
        cache = ModelCache(
            detector_factory=lambda name, backbone: detectors.pop(0),  # noqa: ARG005
            model_registry=registry,
            workdir=tmp_path,
        )
        registry.register(name="factoryai-bottle", run_id="run-1", artifact_path=Path("model.ckpt"))
        registry.register(name="factoryai-bottle", run_id="run-2", artifact_path=Path("model.ckpt"))

        first_loaded = await cache.get(
            _CATEGORY,
            model_version_id=ModelVersionId(uuid.uuid4()),
            registry_name="factoryai-bottle",
            registry_version=1,
            threshold=0.5,
            model_family="patchcore",
            backbone="wide_resnet50_2",
        )
        second_loaded = await cache.get(
            _CATEGORY,
            model_version_id=ModelVersionId(uuid.uuid4()),
            registry_name="factoryai-bottle",
            registry_version=2,
            threshold=0.6,
            model_family="patchcore",
            backbone="wide_resnet50_2",
        )

        assert first_loaded is not second_loaded
