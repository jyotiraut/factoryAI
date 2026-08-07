"""Unit tests for the training use case, against fakes."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from factoryai.application.use_cases.train_model import (
    TrainModel,
    TrainModelCommand,
    load_training_config,
)
from factoryai.domain.entities import DatasetMember, DatasetVersion
from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.value_objects import (
    Category,
    Checksum,
    DatasetSplit,
    DatasetVersionId,
    ImageId,
    ImageLabel,
    ModelStage,
    ProcessingStatus,
    StorageLocation,
)
from tests.builders import NOW, a_dataset, an_image
from tests.fakes import (
    FakeAnomalyDetector,
    FakeClock,
    FakeExperimentTracker,
    FakeHardwareProbe,
    FakeIdGenerator,
    FakeModelRegistry,
    FakeObjectStore,
    FakeUnitOfWork,
    FakeVersionControl,
)
from tests.use_case_factory import make_train_model_use_case

pytestmark = pytest.mark.unit

_CATEGORY = Category("bottle")


async def _seed_version(uow: FakeUnitOfWork, object_store: FakeObjectStore) -> DatasetVersion:
    """Seed a dataset with one nominal train image and one defect test image."""
    dataset = a_dataset(name="bottle", category=_CATEGORY)
    await uow.datasets.add_dataset(dataset)

    train_image = an_image(
        id=ImageId(uuid.uuid4()),
        checksum=Checksum(f"{1:064x}"),
        status=ProcessingStatus.VALID,
        label=ImageLabel.GOOD,
        location=StorageLocation("factoryai-raw", "bottle/train-1.png"),
    )
    test_image = an_image(
        id=ImageId(uuid.uuid4()),
        checksum=Checksum(f"{2:064x}"),
        status=ProcessingStatus.VALID,
        label=ImageLabel.DEFECT,
        location=StorageLocation("factoryai-raw", "bottle/test-1.png"),
    )
    for image, payload in ((train_image, b"train-bytes"), (test_image, b"test-bytes")):
        await uow.images.add(image)
        await object_store.put(image.location, payload)

    version = DatasetVersion(
        id=DatasetVersionId(uuid.uuid4()),
        dataset_id=dataset.id,
        version_tag="bottle-v1",
        dvc_hash="d41d8cd98f00b204e9800998ecf8427e",
        git_commit="a" * 40,
        members=(
            DatasetMember(train_image.id, DatasetSplit.TRAIN),
            DatasetMember(test_image.id, DatasetSplit.TEST),
        ),
        created_at=NOW,
    )
    await uow.datasets.add_version(version)
    return version


def _command(**overrides: object) -> TrainModelCommand:
    defaults: dict[str, object] = {
        "dataset_name": "bottle",
        "dataset_version_tag": "bottle-v1",
        "category": _CATEGORY,
        "model_name": "fake-family",
    }
    return TrainModelCommand(**{**defaults, **overrides})  # type: ignore[arg-type]


def _use_case(
    *,
    uow: FakeUnitOfWork,
    object_store: FakeObjectStore,
    workdir: Path,
    detector: FakeAnomalyDetector | None = None,
) -> tuple[TrainModel, FakeAnomalyDetector, FakeExperimentTracker, FakeModelRegistry]:
    fake_detector = detector or FakeAnomalyDetector(family="fake-family", backbone="fake-backbone")
    tracker = FakeExperimentTracker()
    registry = FakeModelRegistry()
    use_case = make_train_model_use_case(
        uow=uow,
        object_store=object_store,
        detector_factory=lambda name, backbone: fake_detector,  # noqa: ARG005
        experiment_tracker=tracker,
        model_registry=registry,
        version_control=FakeVersionControl(commit="b" * 40),
        hardware_probe=FakeHardwareProbe(),
        clock=FakeClock(NOW),
        id_generator=FakeIdGenerator(),
        workdir=workdir,
    )
    return use_case, fake_detector, tracker, registry


class TestSuccessfulRun:
    async def test_an_experiment_and_model_version_are_persisted(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        object_store = FakeObjectStore()
        await _seed_version(uow, object_store)
        use_case, _, _, _ = _use_case(uow=uow, object_store=object_store, workdir=tmp_path)

        result = await use_case.execute(_command())

        experiment = await uow.experiments.get(result.experiment_id)
        assert experiment.model_family == "fake-family"
        assert experiment.status.value == "completed"
        model_version = await uow.models.get(result.model_version_id)
        assert model_version.registry_name == "factoryai-bottle"
        assert model_version.registry_version == result.registry_version

    async def test_only_the_nominal_train_image_is_staged_for_fitting(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        object_store = FakeObjectStore()
        await _seed_version(uow, object_store)
        use_case, detector, _, _ = _use_case(uow=uow, object_store=object_store, workdir=tmp_path)

        await use_case.execute(_command())

        request = detector.fitted_requests[0]
        train_files = list((request.train_dir / "good").glob("*"))
        test_files = list((request.test_dir / "defect").glob("*"))
        assert len(train_files) == 1
        assert train_files[0].read_bytes() == b"train-bytes"
        assert len(test_files) == 1
        assert test_files[0].read_bytes() == b"test-bytes"

    async def test_the_run_is_logged_to_the_experiment_tracker(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        object_store = FakeObjectStore()
        await _seed_version(uow, object_store)
        use_case, _, tracker, _ = _use_case(uow=uow, object_store=object_store, workdir=tmp_path)

        result = await use_case.execute(_command())

        run = tracker.runs[result.mlflow_run_id]
        assert run["status"] == "FINISHED"
        assert run["params"]["dataset_version_tag"] == "bottle-v1"
        assert run["params"]["git_commit"] == "b" * 40
        assert run["metrics"]["image_auroc"] == pytest.approx(0.99)
        assert run["artifacts"]

    async def test_an_audit_event_is_appended(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        object_store = FakeObjectStore()
        await _seed_version(uow, object_store)
        use_case, _, _, _ = _use_case(uow=uow, object_store=object_store, workdir=tmp_path)

        await use_case.execute(_command())

        latest = await uow.audit.latest()
        assert latest is not None
        assert latest.action == "model.trained"

    async def test_the_model_version_is_registered_in_the_development_stage(
        self, tmp_path: Path
    ) -> None:
        uow = FakeUnitOfWork()
        object_store = FakeObjectStore()
        await _seed_version(uow, object_store)
        use_case, _, _, registry = _use_case(uow=uow, object_store=object_store, workdir=tmp_path)

        result = await use_case.execute(_command())

        production = registry.get_stage_version(
            name=result.registry_name, stage=ModelStage.PRODUCTION
        )
        assert production is None
        assert result.registry_version in registry.list_versions(name=result.registry_name)


class TestFailedRun:
    async def test_a_failed_fit_records_a_failed_experiment_and_reraises(
        self, tmp_path: Path
    ) -> None:
        uow = FakeUnitOfWork()
        object_store = FakeObjectStore()
        version = await _seed_version(uow, object_store)
        detector = FakeAnomalyDetector(family="fake-family", backbone="fake-backbone")
        detector.raise_on_fit = RuntimeError("out of memory")
        use_case, _, tracker, _ = _use_case(
            uow=uow, object_store=object_store, workdir=tmp_path, detector=detector
        )

        with pytest.raises(RuntimeError, match="out of memory"):
            await use_case.execute(_command())

        experiments = await uow.experiments.list_for_dataset_version(version.id)
        assert len(experiments) == 1
        assert experiments[0].status.value == "failed"
        assert experiments[0].failure_reason == "out of memory"
        assert next(iter(tracker.runs.values()))["status"] == "FAILED"


class TestMissingDatasetOrVersion:
    async def test_an_unknown_dataset_name_raises(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        object_store = FakeObjectStore()
        use_case, _, _, _ = _use_case(uow=uow, object_store=object_store, workdir=tmp_path)

        with pytest.raises(EntityNotFoundError):
            await use_case.execute(_command(dataset_name="does-not-exist"))

    async def test_an_unknown_version_tag_raises(self, tmp_path: Path) -> None:
        uow = FakeUnitOfWork()
        object_store = FakeObjectStore()
        await _seed_version(uow, object_store)
        use_case, _, _, _ = _use_case(uow=uow, object_store=object_store, workdir=tmp_path)

        with pytest.raises(EntityNotFoundError):
            await use_case.execute(_command(dataset_version_tag="does-not-exist"))


class TestConfigHash:
    def test_is_stable_for_the_same_inputs(self) -> None:
        assert _command().config_hash() == _command().config_hash()

    def test_changes_with_a_hyperparameter(self) -> None:
        first = _command(hyperparameters={"coreset_sampling_ratio": 0.1})
        second = _command(hyperparameters={"coreset_sampling_ratio": 0.2})
        assert first.config_hash() != second.config_hash()


class TestLoadTrainingConfig:
    def test_parses_a_minimal_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "patchcore.yaml"
        config_path.write_text(
            "dataset_name: bottle\n"
            "dataset_version_tag: bottle-v1\n"
            "category: bottle\n"
            "model:\n"
            "  name: patchcore\n"
            "  backbone: wide_resnet50_2\n"
            "  hyperparameters:\n"
            "    coreset_sampling_ratio: 0.1\n"
            "image_size: 256x256\n"
            "seed: 7\n",
            encoding="utf-8",
        )

        command = load_training_config(config_path)

        assert command.dataset_name == "bottle"
        assert command.model_name == "patchcore"
        assert command.backbone == "wide_resnet50_2"
        assert command.hyperparameters == {"coreset_sampling_ratio": 0.1}
        assert command.image_size == (256, 256)
        assert command.seed == 7

    def test_defaults_are_applied_when_optional_keys_are_absent(self, tmp_path: Path) -> None:
        config_path = tmp_path / "minimal.yaml"
        config_path.write_text(
            "dataset_name: bottle\ndataset_version_tag: bottle-v1\ncategory: bottle\n"
            "model:\n  name: patchcore\n",
            encoding="utf-8",
        )

        command = load_training_config(config_path)

        assert command.backbone is None
        assert command.image_size == (256, 256)
        assert command.seed == 42
        assert command.device == "auto"
