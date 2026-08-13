"""Unit tests for ``factoryai.pipeline_client``, against fakes.

Exercises the thin client both Celery and Airflow call into (ADR-0005, ADR-0013) without
either scheduler in the loop — a duck-typed fake container is enough, since every function
here only ever calls ``container.<use_case>_use_case()`` or a handful of adapter methods.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from factoryai import pipeline_client
from factoryai.application.use_cases.create_dataset_version import CreateDatasetVersion
from factoryai.application.use_cases.generate_drift_report import GenerateDriftReport
from factoryai.application.use_cases.ingest_image import IngestImage
from factoryai.application.use_cases.promote_model import PromoteModel
from factoryai.application.use_cases.train_model import TrainModel
from factoryai.domain.entities import (
    DatasetMember,
    DatasetVersion,
    EvaluationMetrics,
    ModelVersion,
)
from factoryai.domain.errors import NoProductionModelError, PromotionRejectedError
from factoryai.domain.ports.detection import AnomalyDetector
from factoryai.domain.value_objects import (
    AnomalyScore,
    Category,
    Checksum,
    DatasetSplit,
    DatasetVersionId,
    DecodedImage,
    ImageId,
    ImageLabel,
    ModelStage,
    ProcessingStatus,
    Resolution,
    StorageLocation,
)
from factoryai.infrastructure.monitoring.evidently_drift import EvidentlyDriftDetector
from tests.builders import NOW, a_dataset, a_model_version, a_prediction, an_experiment, an_image
from tests.fakes import (
    FakeAnomalyDetector,
    FakeClock,
    FakeExperimentTracker,
    FakeHardwareProbe,
    FakeIdGenerator,
    FakeImageCodec,
    FakeModelRegistry,
    FakeObjectStore,
    FakeUnitOfWork,
    FakeVersionControl,
)
from tests.use_case_factory import (
    make_create_dataset_version_use_case,
    make_ingest_image_use_case,
    make_promote_model_use_case,
    make_train_model_use_case,
)

pytestmark = pytest.mark.unit

_CATEGORY = Category("bottle")


@dataclass
class _FakeContainer:
    """A minimal duck-typed stand-in for :class:`~factoryai.bootstrap.container.Container`.

    Exposes exactly the surface ``pipeline_client`` calls — the same "not a subclass"
    reasoning as ``tests/unit/api/conftest.py``'s own ``FakeContainer``.
    """

    uow: FakeUnitOfWork
    object_store: FakeObjectStore
    model_registry: FakeModelRegistry
    workdir: Path

    class _Settings:
        class _Storage:
            bucket_raw = "factoryai-raw"

        class _Promotion:
            min_auroc = 0.95
            improvement_margin = 0.005
            max_recall_regression = 0.01

        class _Drift:
            window_hours = 24
            min_samples = 2
            data_threshold = 0.15
            prediction_threshold = 0.10

        storage = _Storage()
        promotion = _Promotion()
        drift = _Drift()

    settings = _Settings()

    def unit_of_work(self) -> FakeUnitOfWork:
        return self.uow

    def ingest_image_use_case(self) -> IngestImage:
        return make_ingest_image_use_case(
            uow=self.uow,
            object_store=self.object_store,
            image_codec=FakeImageCodec(
                DecodedImage(resolution=Resolution(512, 512), image_format="PNG", color_mode="RGB")
            ),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

    def create_dataset_version_use_case(self) -> CreateDatasetVersion:
        return make_create_dataset_version_use_case(
            uow=self.uow,
            version_control=FakeVersionControl(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

    def train_model_use_case(self) -> TrainModel:
        detector: AnomalyDetector = FakeAnomalyDetector()

        def _build(_name: str, _backbone: str | None) -> AnomalyDetector:
            return detector

        return make_train_model_use_case(
            uow=self.uow,
            object_store=self.object_store,
            detector_factory=_build,
            experiment_tracker=FakeExperimentTracker(),
            model_registry=self.model_registry,
            version_control=FakeVersionControl(),
            hardware_probe=FakeHardwareProbe(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
            workdir=self.workdir,
        )

    def promote_model_use_case(self) -> PromoteModel:
        return make_promote_model_use_case(
            uow=self.uow,
            model_registry=self.model_registry,
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

    def generate_drift_report_use_case(self) -> GenerateDriftReport:
        return GenerateDriftReport(
            uow_factory=lambda: self.uow,
            drift_detector=EvidentlyDriftDetector(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )


class TestIngestFromObjectStore:
    async def test_every_object_under_the_prefix_is_ingested(self) -> None:
        object_store = FakeObjectStore()
        await object_store.put(StorageLocation("factoryai-raw", "incoming/bottle/a.png"), b"a" * 16)
        await object_store.put(StorageLocation("factoryai-raw", "incoming/bottle/b.png"), b"b" * 16)
        container = _FakeContainer(
            uow=FakeUnitOfWork(),
            object_store=object_store,
            model_registry=FakeModelRegistry(),
            workdir=Path(),
        )

        summary = await pipeline_client.ingest_from_object_store(
            container, category="bottle", prefix="incoming/bottle/"
        )

        # FakeImageCodec's perceptual hash is fixed regardless of content, so the second
        # object is flagged a near-duplicate of the first — a fake-codec artefact, not
        # something this test is about. What it *is* about: every scanned object reached
        # IngestImage exactly once, which "accepted + duplicate == scanned" confirms.
        assert summary["scanned"] == 2
        assert summary["accepted"] + summary["duplicate"] == 2

    async def test_an_empty_prefix_ingests_nothing(self) -> None:
        container = _FakeContainer(
            uow=FakeUnitOfWork(),
            object_store=FakeObjectStore(),
            model_registry=FakeModelRegistry(),
            workdir=Path(),
        )

        summary = await pipeline_client.ingest_from_object_store(
            container, category="bottle", prefix="incoming/bottle/"
        )

        assert summary["scanned"] == 0


class TestVersionDataset:
    async def test_a_dataset_version_is_created_from_a_payload(self) -> None:
        uow = FakeUnitOfWork()
        image = an_image(status=ProcessingStatus.VALID, label=ImageLabel.GOOD)
        await uow.images.add(image)
        container = _FakeContainer(
            uow=uow,
            object_store=FakeObjectStore(),
            model_registry=FakeModelRegistry(),
            workdir=Path(),
        )

        result = await pipeline_client.version_dataset(
            container,
            {"dataset_name": "bottle", "category": "bottle", "version_tag": "bottle-v1"},
        )

        assert result["version_tag"] == "bottle-v1"
        assert result["image_count"] == 1


async def _seed_trainable_version(
    uow: FakeUnitOfWork, object_store: FakeObjectStore
) -> DatasetVersion:
    """Seed a dataset version with one nominal image, mirroring test_train_model.py."""
    dataset = a_dataset(name="bottle", category=_CATEGORY)
    await uow.datasets.add_dataset(dataset)
    image = an_image(
        id=ImageId(uuid.uuid4()),
        checksum=Checksum(f"{1:064x}"),
        status=ProcessingStatus.VALID,
        label=ImageLabel.GOOD,
        location=StorageLocation("factoryai-raw", "bottle/train-1.png"),
    )
    await uow.images.add(image)
    await object_store.put(image.location, b"train-bytes")
    version = DatasetVersion(
        id=DatasetVersionId(uuid.uuid4()),
        dataset_id=dataset.id,
        version_tag="bottle-v1",
        dvc_hash="d41d8cd98f00b204e9800998ecf8427e",
        git_commit="a" * 40,
        members=(DatasetMember(image.id, DatasetSplit.TRAIN),),
        created_at=NOW,
    )
    await uow.datasets.add_version(version)
    return version


class TestTrain:
    async def test_a_training_run_returns_its_metrics(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        object_store = FakeObjectStore()
        await _seed_trainable_version(uow, object_store)
        container = _FakeContainer(
            uow=uow,
            object_store=object_store,
            model_registry=FakeModelRegistry(),
            workdir=tmp_path,
        )

        result = await pipeline_client.train(
            container,
            {
                "dataset_name": "bottle",
                "dataset_version_tag": "bottle-v1",
                "category": "bottle",
                "model_name": "fake-family",
            },
        )

        assert result["model_version_id"]
        assert "image_auroc" in result


class TestEvaluate:
    async def test_a_model_at_or_above_the_floor_passes(self) -> None:
        uow = FakeUnitOfWork()
        model = a_model_version(
            category=_CATEGORY,
            metrics=EvaluationMetrics(
                image_auroc=0.97, precision=0.9, recall=0.9, f1=0.9, threshold=0.5
            ),
        )
        await uow.models.add(model)
        container = _FakeContainer(
            uow=uow,
            object_store=FakeObjectStore(),
            model_registry=FakeModelRegistry(),
            workdir=Path(),
        )

        result = await pipeline_client.evaluate(container, model_version_id=str(model.id))

        assert result["passed"] is True
        assert result["image_auroc"] == pytest.approx(0.97)

    async def test_a_model_below_the_floor_fails(self) -> None:
        uow = FakeUnitOfWork()
        model = a_model_version(
            category=_CATEGORY,
            metrics=EvaluationMetrics(
                image_auroc=0.5, precision=0.5, recall=0.5, f1=0.5, threshold=0.5
            ),
        )
        await uow.models.add(model)
        container = _FakeContainer(
            uow=uow,
            object_store=FakeObjectStore(),
            model_registry=FakeModelRegistry(),
            workdir=Path(),
        )

        result = await pipeline_client.evaluate(container, model_version_id=str(model.id))

        assert result["passed"] is False


class TestDeploy:
    async def test_a_first_promotion_with_no_incumbent_succeeds(self) -> None:
        uow = FakeUnitOfWork()
        model_registry = FakeModelRegistry()
        model = a_model_version(
            category=_CATEGORY,
            stage=ModelStage.DEVELOPMENT,
            metrics=EvaluationMetrics(
                image_auroc=0.97, precision=0.9, recall=0.9, f1=0.9, threshold=0.5
            ),
        )
        model_registry.register(
            name=model.registry_name, run_id="run-1", artifact_path=Path("model.ckpt")
        )
        await uow.models.add(model)
        container = _FakeContainer(
            uow=uow, object_store=FakeObjectStore(), model_registry=model_registry, workdir=Path()
        )

        result = await pipeline_client.deploy(
            container, category="bottle", model_version_id=str(model.id)
        )

        assert result["model_version_id"] == str(model.id)
        assert result["previous_model_version_id"] is None

    async def test_a_below_floor_candidate_is_rejected(self) -> None:
        uow = FakeUnitOfWork()
        model_registry = FakeModelRegistry()
        model = a_model_version(
            category=_CATEGORY,
            stage=ModelStage.DEVELOPMENT,
            metrics=EvaluationMetrics(
                image_auroc=0.5, precision=0.5, recall=0.5, f1=0.5, threshold=0.5
            ),
        )
        model_registry.register(
            name=model.registry_name, run_id="run-1", artifact_path=Path("model.ckpt")
        )
        await uow.models.add(model)
        container = _FakeContainer(
            uow=uow, object_store=FakeObjectStore(), model_registry=model_registry, workdir=Path()
        )

        with pytest.raises(PromotionRejectedError):
            await pipeline_client.deploy(
                container, category="bottle", model_version_id=str(model.id)
            )


async def _seed_production_model(uow: FakeUnitOfWork, *, created_at: datetime) -> ModelVersion:
    """Register a production model for ``_CATEGORY``, created at a given timestamp."""
    experiment = an_experiment(dataset_version_id=DatasetVersionId(uuid.uuid4()))
    await uow.experiments.add(experiment)
    model = a_model_version(
        experiment_id=experiment.id,
        category=_CATEGORY,
        stage=ModelStage.PRODUCTION,
        created_at=created_at,
    )
    await uow.models.add(model)
    return model


class TestGenerateDriftReport:
    async def test_no_production_model_raises(self) -> None:
        container = _FakeContainer(
            uow=FakeUnitOfWork(),
            object_store=FakeObjectStore(),
            model_registry=FakeModelRegistry(),
            workdir=Path(),
        )

        with pytest.raises(NoProductionModelError):
            await pipeline_client.generate_drift_report(container, {"category": "bottle"})

    async def test_a_window_below_min_samples_is_inconclusive(self) -> None:
        uow = FakeUnitOfWork()
        model = await _seed_production_model(uow, created_at=NOW - timedelta(hours=48))
        await uow.predictions.add(
            a_prediction(model_version_id=model.id, predicted_at=NOW - timedelta(hours=1))
        )
        container = _FakeContainer(
            uow=uow,
            object_store=FakeObjectStore(),
            model_registry=FakeModelRegistry(),
            workdir=Path(),
        )

        result = await pipeline_client.generate_drift_report(container, {"category": "bottle"})

        assert result["is_conclusive"] is False
        assert result["sample_count"] == 1
        assert result["signals"] == []

    async def test_a_shifted_window_reports_breached_signals(self) -> None:
        uow = FakeUnitOfWork()
        model = await _seed_production_model(uow, created_at=NOW - timedelta(hours=48))
        for _ in range(5):
            await uow.predictions.add(
                a_prediction(
                    model_version_id=model.id,
                    predicted_at=NOW - timedelta(hours=47),
                    score=AnomalyScore(value=0.2, threshold=0.5),
                )
            )
        for _ in range(5):
            await uow.predictions.add(
                a_prediction(
                    model_version_id=model.id,
                    predicted_at=NOW - timedelta(hours=1),
                    score=AnomalyScore(value=0.9, threshold=0.5),
                )
            )
        container = _FakeContainer(
            uow=uow,
            object_store=FakeObjectStore(),
            model_registry=FakeModelRegistry(),
            workdir=Path(),
        )

        result = await pipeline_client.generate_drift_report(container, {"category": "bottle"})

        assert result["is_conclusive"] is True
        assert result["sample_count"] == 5
        assert result["drift_detected"] is True
        assert any(
            signal["name"] == "anomaly_score" and signal["breached"] for signal in result["signals"]
        )
