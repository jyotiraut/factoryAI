"""Unit tests for the inference use case, against fakes."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from factoryai.application.services.model_cache import ModelCache
from factoryai.application.use_cases.predict_image import PredictImage, PredictImageCommand
from factoryai.domain.errors import NoProductionModelError
from factoryai.domain.ports.detection import RawPrediction
from factoryai.domain.value_objects import (
    AnomalyScore,
    Category,
    DecodedImage,
    ExperimentId,
    ModelStage,
    Resolution,
)
from tests.builders import NOW, a_model_version, an_experiment, some_metrics
from tests.fakes import (
    FakeAnomalyDetector,
    FakeClock,
    FakeIdGenerator,
    FakeImageCodec,
    FakeModelRegistry,
    FakeObjectStore,
    FakeUnitOfWork,
)

pytestmark = pytest.mark.unit

_CATEGORY = Category("bottle")


async def _seed_production_model(uow: FakeUnitOfWork, *, threshold: float = 0.5) -> tuple[str, str]:
    """Seed a production model version and its experiment; return (family, backbone)."""
    experiment_id = ExperimentId(uuid.uuid4())
    experiment = an_experiment(id=experiment_id, model_family="patchcore", backbone="resnet18")
    await uow.experiments.add(experiment)
    model = (
        a_model_version(experiment_id=experiment_id, threshold=threshold, metrics=some_metrics())
        .transition_to(ModelStage.STAGING)
        .transition_to(ModelStage.PRODUCTION)
    )
    await uow.models.add(model)
    return experiment.model_family, experiment.backbone


def _use_case(
    uow: FakeUnitOfWork,
    object_store: FakeObjectStore,
    *,
    detector: FakeAnomalyDetector | None = None,
    workdir: Path,
) -> tuple[PredictImage, FakeModelRegistry]:
    registry = FakeModelRegistry()
    registry.register(name="factoryai-bottle", run_id="run-1", artifact_path=Path("model.ckpt"))
    fake_detector = detector or FakeAnomalyDetector()
    model_cache = ModelCache(
        detector_factory=lambda name, backbone: fake_detector,  # noqa: ARG005
        model_registry=registry,
        workdir=workdir,
    )
    use_case = PredictImage(
        uow_factory=lambda: uow,
        object_store=object_store,
        image_codec=FakeImageCodec(
            decoded=DecodedImage(
                resolution=Resolution(64, 64), image_format="PNG", color_mode="RGB"
            )
        ),
        model_cache=model_cache,
        clock=FakeClock(NOW),
        id_generator=FakeIdGenerator(),
        raw_bucket="factoryai-raw",
        heatmap_bucket="factoryai-heatmaps",
    )
    return use_case, registry


class TestSuccessfulPrediction:
    async def test_a_prediction_is_persisted_with_the_production_models_identity(
        self, tmp_path: Path
    ) -> None:
        uow = FakeUnitOfWork()
        family, backbone = await _seed_production_model(uow, threshold=0.4)
        detector = FakeAnomalyDetector(
            family=family,
            backbone=backbone,
            prediction=RawPrediction(
                score=AnomalyScore(value=0.9, threshold=0.4), inference_time_ms=12.0
            ),
        )
        use_case, _ = _use_case(uow, FakeObjectStore(), detector=detector, workdir=tmp_path)

        result = await use_case.execute(
            PredictImageCommand(
                category=_CATEGORY, payload=b"fake-png-bytes", correlation_id="req-1"
            )
        )

        assert result.is_anomalous is True
        assert result.anomaly_score == 0.9
        assert result.threshold == 0.4
        assert result.correlation_id == "req-1"

        stored = await uow.predictions.get(result.prediction_id)
        assert stored.image_id == result.image_id
        stored_image = await uow.images.get(result.image_id)
        assert stored_image.category == _CATEGORY

    async def test_the_raw_image_is_written_to_the_object_store(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        await _seed_production_model(uow)
        object_store = FakeObjectStore()
        use_case, _ = _use_case(uow, object_store, workdir=tmp_path)

        result = await use_case.execute(
            PredictImageCommand(category=_CATEGORY, payload=b"fake-png-bytes")
        )

        image = await uow.images.get(result.image_id)
        assert await object_store.exists(image.location)
        assert await object_store.get(image.location) == b"fake-png-bytes"

    async def test_a_heatmap_is_stored_when_the_detector_produces_one(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        await _seed_production_model(uow)
        detector = FakeAnomalyDetector(
            prediction=RawPrediction(
                score=AnomalyScore(value=0.1, threshold=0.5),
                inference_time_ms=5.0,
                anomaly_map=b"fake-heatmap-bytes",
            )
        )
        object_store = FakeObjectStore()
        use_case, _ = _use_case(uow, object_store, detector=detector, workdir=tmp_path)

        result = await use_case.execute(
            PredictImageCommand(category=_CATEGORY, payload=b"fake-png-bytes")
        )

        assert result.heatmap_location is not None
        assert await object_store.get(result.heatmap_location) == b"fake-heatmap-bytes"

    async def test_no_heatmap_is_stored_when_the_detector_does_not_localise(
        self, tmp_path: Path
    ) -> None:
        uow = FakeUnitOfWork()
        await _seed_production_model(uow)
        use_case, _ = _use_case(uow, FakeObjectStore(), workdir=tmp_path)

        result = await use_case.execute(
            PredictImageCommand(category=_CATEGORY, payload=b"fake-png-bytes")
        )

        assert result.heatmap_location is None

    async def test_an_audit_event_is_appended(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        await _seed_production_model(uow)
        use_case, _ = _use_case(uow, FakeObjectStore(), workdir=tmp_path)

        await use_case.execute(PredictImageCommand(category=_CATEGORY, payload=b"fake-png-bytes"))

        latest = await uow.audit.latest()
        assert latest is not None
        assert latest.action == "prediction.served"


class TestContentAddressedImages:
    async def test_scoring_the_same_bytes_twice_reuses_the_existing_image_row(
        self, tmp_path: Path
    ) -> None:
        """The real bug this guards against: a duplicate insert on ``images.checksum_sha256``.

        A camera re-photographing the same nominal product, or — as Phase 7's live
        verification against the real bottle model found — resubmitting an MVTec file
        already sitting in the training set, both submit byte-identical payloads. Every
        submission must still be scored, but it must produce a second ``Prediction``
        against the *same* image row, not a second, constraint-violating image row.
        """
        uow = FakeUnitOfWork()
        await _seed_production_model(uow)
        object_store = FakeObjectStore()
        use_case, _ = _use_case(uow, object_store, workdir=tmp_path)
        command = PredictImageCommand(category=_CATEGORY, payload=b"same-bytes-both-times")

        first = await use_case.execute(command)
        second = await use_case.execute(command)

        assert first.image_id == second.image_id
        assert first.prediction_id != second.prediction_id
        assert await uow.predictions.get(first.prediction_id) is not None
        assert await uow.predictions.get(second.prediction_id) is not None

    async def test_a_batch_with_a_repeated_image_still_scores_it_twice(
        self, tmp_path: Path
    ) -> None:
        uow = FakeUnitOfWork()
        await _seed_production_model(uow)
        use_case, _ = _use_case(uow, FakeObjectStore(), workdir=tmp_path)
        commands = [
            PredictImageCommand(category=_CATEGORY, payload=b"repeated"),
            PredictImageCommand(category=_CATEGORY, payload=b"repeated"),
        ]

        results = await use_case.execute_batch(commands)

        assert results[0].image_id == results[1].image_id
        assert results[0].prediction_id != results[1].prediction_id


class TestNoProductionModel:
    async def test_raises_when_the_category_has_no_production_model(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        use_case, _ = _use_case(uow, FakeObjectStore(), workdir=tmp_path)

        with pytest.raises(NoProductionModelError):
            await use_case.execute(PredictImageCommand(category=_CATEGORY, payload=b"x"))


class TestBatchPrediction:
    async def test_a_batch_shares_one_detector_call_and_persists_every_result(
        self, tmp_path: Path
    ) -> None:
        uow = FakeUnitOfWork()
        await _seed_production_model(uow)
        use_case, _ = _use_case(uow, FakeObjectStore(), workdir=tmp_path)
        commands = [
            PredictImageCommand(category=_CATEGORY, payload=b"one"),
            PredictImageCommand(category=_CATEGORY, payload=b"two"),
        ]

        results = await use_case.execute_batch(commands)

        assert len(results) == 2
        assert results[0].prediction_id != results[1].prediction_id
        for result in results:
            assert await uow.predictions.get(result.prediction_id) is not None

    async def test_an_empty_batch_raises(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        use_case, _ = _use_case(uow, FakeObjectStore(), workdir=tmp_path)

        with pytest.raises(ValueError, match="at least one"):
            await use_case.execute_batch([])

    async def test_mixed_categories_raise(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        use_case, _ = _use_case(uow, FakeObjectStore(), workdir=tmp_path)
        commands = [
            PredictImageCommand(category=Category("bottle"), payload=b"one"),
            PredictImageCommand(category=Category("cable"), payload=b"two"),
        ]

        with pytest.raises(ValueError, match="one category"):
            await use_case.execute_batch(commands)
