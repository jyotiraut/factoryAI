"""The training use case: fit, evaluate, log and register one model for one dataset version.

Follows the fixed sequence ADR-0009 decided on: resolve the dataset version, stage its
member images to local disk, fit via the :class:`AnomalyDetector` port (which covers build
datamodule -> fit -> evaluate internally — Anomalib does not expose "evaluate" as a
separable call), log the run to MLflow, register the artifact, persist an ``Experiment``
and ``ModelVersion`` row, and append an audit event. A failed fit still produces a
recorded, queryable ``Experiment`` — training runs are not silently lost, they end up
``FAILED`` with a reason.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from factoryai.domain.entities import (
    AuditEvent,
    EvaluationMetrics,
    Experiment,
    HardwareInfo,
    InspectionImage,
    ModelVersion,
)
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.entities.dataset import DatasetVersion
from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.ports.detection import AnomalyDetector, TrainingRequest
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import Clock, HardwareProbe, IdGenerator
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.ports.tracking import ExperimentTracker, ModelRegistry
from factoryai.domain.ports.versioning import VersionControl
from factoryai.domain.value_objects import (
    AuditSequence,
    Category,
    DatasetSplit,
    ExperimentId,
    ImageLabel,
    ModelVersionId,
    UserId,
)


@dataclass(frozen=True, slots=True)
class TrainModelCommand:
    """Everything one training run needs.

    Attributes:
        dataset_name: The dataset the version to train on belongs to.
        dataset_version_tag: Which frozen snapshot to train on.
        category: The product class being modelled.
        model_name: Registered detector family, e.g. ``"patchcore"``.
        backbone: Feature extractor override; ``None`` uses the detector's own default.
        hyperparameters: Model-family-specific configuration.
        image_size: Input resolution ``(width, height)``.
        seed: Random seed pinned for reproducibility.
        device: ``"auto"``, ``"cpu"`` or ``"cuda"``.
        note: Optional free-text description of this run.
        started_by: The user launching the run; absent for an automated trigger.
    """

    dataset_name: str
    dataset_version_tag: str
    category: Category
    model_name: str
    backbone: str | None = None
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    image_size: tuple[int, int] = (256, 256)
    seed: int = 42
    device: str = "auto"
    note: str = ""
    started_by: UserId | None = None

    def config_hash(self) -> str:
        """Return a stable fingerprint of every field that determines training's outcome.

        Two runs with the same hash and the same dataset version are the reproducibility
        claim Phase 4's exit criteria and this phase's both rest on.
        """
        payload = {
            "dataset_name": self.dataset_name,
            "dataset_version_tag": self.dataset_version_tag,
            "category": self.category.code,
            "model_name": self.model_name,
            "backbone": self.backbone,
            "hyperparameters": self.hyperparameters,
            "image_size": list(self.image_size),
            "seed": self.seed,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TrainModelResult:
    """The outcome of a successful training run.

    Attributes:
        experiment_id: The recorded run.
        model_version_id: The registered artifact.
        mlflow_run_id: The corresponding MLflow run.
        registry_name: Registry the artifact was registered under.
        registry_version: Assigned version number within that registry.
        metrics: Held-out evaluation results.
        training_time_seconds: Wall-clock duration of the fit.
    """

    experiment_id: ExperimentId
    model_version_id: ModelVersionId
    mlflow_run_id: str
    registry_name: str
    registry_version: int
    metrics: EvaluationMetrics
    training_time_seconds: float


def load_training_config(path: Path) -> TrainModelCommand:
    """Parse a training YAML config (``configs/<category>/<model>.yaml``) into a command.

    Raises:
        KeyError: If a required key (``dataset_name``, ``dataset_version_tag``,
            ``category``, ``model.name``) is missing.
        ValueError: If ``image_size`` is present but not a ``"WIDTHxHEIGHT"`` string.
    """
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    model = raw.get("model", {})
    image_size = raw.get("image_size", "256x256")
    if isinstance(image_size, str):
        width, _, height = image_size.lower().partition("x")
        image_size = (int(width), int(height))
    return TrainModelCommand(
        dataset_name=raw["dataset_name"],
        dataset_version_tag=raw["dataset_version_tag"],
        category=Category.parse(raw["category"]),
        model_name=model["name"],
        backbone=model.get("backbone"),
        hyperparameters=dict(model.get("hyperparameters", {})),
        image_size=tuple(image_size),
        seed=raw.get("seed", 42),
        device=raw.get("device", "auto"),
        note=raw.get("note", ""),
    )


@dataclass(frozen=True, slots=True)
class _StagedDataset:
    """Local directories an :class:`AnomalyDetector` can read directly."""

    train_dir: Path
    test_dir: Path


class _DatasetStager:
    """Materialises a dataset version's member images into an Anomalib-style folder layout.

    ``train_dir/good/`` holds nominal training images (PatchCore-family detectors fit on
    nominal samples only, regardless of what other labels a version happens to carry);
    ``test_dir/good/`` and ``test_dir/defect/`` hold everything else the version assigned
    to validation or test, for held-out evaluation. This is the one pipeline step broken
    out as its own class rather than a private method (ADR-0009) — it is the one piece
    with real, independently testable logic.
    """

    def __init__(self, object_store: ObjectStore) -> None:
        """Bind to the object store member image bytes are fetched from."""
        self._object_store = object_store

    async def stage(
        self, members_by_split: dict[DatasetSplit, list[InspectionImage]], workdir: Path
    ) -> _StagedDataset:
        """Write every member's bytes to disk under ``workdir``, split by role."""
        train_dir, test_dir = workdir / "train", workdir / "test"
        for split, images in members_by_split.items():
            for image in images:
                if split is DatasetSplit.TRAIN:
                    if image.label is not ImageLabel.GOOD:
                        continue
                    target_dir = train_dir / "good"
                else:
                    is_nominal = image.label in {ImageLabel.GOOD, ImageLabel.UNLABELED}
                    target_dir = test_dir / ("good" if is_nominal else "defect")
                await self._write_one(image, target_dir)
        return _StagedDataset(train_dir=train_dir, test_dir=test_dir)

    async def _write_one(self, image: InspectionImage, target_dir: Path) -> None:
        """Fetch one member's bytes and write them under ``target_dir``."""
        target_dir.mkdir(parents=True, exist_ok=True)
        payload = await self._object_store.get(image.location)
        extension = Path(image.location.key).suffix or ".png"
        (target_dir / f"{image.id}{extension}").write_bytes(payload)


class TrainModel:
    """Fits, evaluates, logs and registers one model for one dataset version."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        object_store: ObjectStore,
        detector_factory: Callable[[str, str | None], AnomalyDetector],
        experiment_tracker: ExperimentTracker,
        model_registry: ModelRegistry,
        version_control: VersionControl,
        hardware_probe: HardwareProbe,
        clock: Clock,
        id_generator: IdGenerator,
        workdir: Path,
        mlflow_experiment_name: str,
    ) -> None:
        """Initialise with every collaborator this use case needs.

        Args:
            uow_factory: Builds a fresh unit of work per call.
            object_store: Where member images are read from to stage the dataset.
            detector_factory: Builds a detector for ``(model_name, backbone)`` — the
                plugin registry lookup lives at the container, not here (ADR-0001).
            experiment_tracker: Records the run's parameters, metrics and artifacts.
            model_registry: Registers the fitted artifact and reports where it landed.
            version_control: Reports the Git commit this run executed at.
            hardware_probe: Reports the machine this run executed on.
            clock: Source of "now", for every timestamp this run records.
            id_generator: Source of new experiment and model-version identifiers.
            workdir: Local scratch directory the dataset is staged under.
            mlflow_experiment_name: The MLflow experiment every run is grouped into.
        """
        self._uow_factory = uow_factory
        self._stager = _DatasetStager(object_store)
        self._detector_factory = detector_factory
        self._experiment_tracker = experiment_tracker
        self._model_registry = model_registry
        self._version_control = version_control
        self._hardware_probe = hardware_probe
        self._clock = clock
        self._id_generator = id_generator
        self._workdir = workdir
        self._mlflow_experiment_name = mlflow_experiment_name

    async def execute(self, command: TrainModelCommand) -> TrainModelResult:
        """Run one full training cycle.

        Raises:
            EntityNotFoundError: If the named dataset or version tag does not exist.
            Exception: Propagated from the detector on a genuine training failure — the
                run is still recorded, as a ``FAILED`` :class:`Experiment`, before this
                re-raises.
        """
        started_at = self._clock.now()
        version, members_by_split = await self._load_dataset_version(command)
        staged = await self._stager.stage(members_by_split, self._workdir / str(version.id))

        detector = self._detector_factory(command.model_name, command.backbone)
        request = TrainingRequest(
            train_dir=staged.train_dir,
            test_dir=staged.test_dir,
            image_size=command.image_size,
            seed=command.seed,
            device=command.device,
            hyperparameters=command.hyperparameters,
        )

        git_commit = await self._version_control.current_commit()
        hardware = self._hardware_probe.capture()
        run_id = self._start_run(command, started_at)
        self._log_launch_params(run_id, command, version, git_commit)

        try:
            trained = await asyncio.to_thread(detector.fit, request)
        except Exception as exc:
            self._experiment_tracker.end_run(run_id, status="FAILED")
            await self._record_failure(
                command, version, run_id, git_commit, hardware, started_at, str(exc)
            )
            raise

        self._log_completed_run(run_id, trained.metrics, trained.training_time_seconds)
        self._experiment_tracker.log_artifact(run_id, trained.artifact_path, artifact_path="model")
        self._experiment_tracker.end_run(run_id)

        registry_name = self._model_registry.registry_name_for(command.category)
        registry_version = self._model_registry.register(
            name=registry_name,
            run_id=run_id,
            artifact_path=Path("model") / trained.artifact_path.name,
            tags={"model_family": detector.family},
        )
        artifact_location = self._model_registry.resolve_artifact_location(
            name=registry_name, version=registry_version
        )

        finished_at = self._clock.now()
        experiment = Experiment(
            id=ExperimentId(self._id_generator.new_id()),
            mlflow_run_id=run_id,
            dataset_version_id=version.id,
            model_family=detector.family,
            backbone=detector.backbone,
            hyperparameters=command.hyperparameters,
            config_hash=command.config_hash(),
            git_commit=git_commit,
            started_at=started_at,
            hardware=hardware,
        ).complete(trained.metrics, finished_at=finished_at)
        model_version = ModelVersion(
            id=ModelVersionId(self._id_generator.new_id()),
            experiment_id=experiment.id,
            category=command.category,
            registry_name=registry_name,
            registry_version=registry_version,
            threshold=trained.threshold,
            artifact_location=artifact_location,
            metrics=trained.metrics,
            created_at=finished_at,
            tags=dict(trained.extra),
        )
        await self._persist(experiment, model_version, command)

        return TrainModelResult(
            experiment_id=experiment.id,
            model_version_id=model_version.id,
            mlflow_run_id=run_id,
            registry_name=registry_name,
            registry_version=registry_version,
            metrics=trained.metrics,
            training_time_seconds=trained.training_time_seconds,
        )

    async def _load_dataset_version(
        self, command: TrainModelCommand
    ) -> tuple[DatasetVersion, dict[DatasetSplit, list[InspectionImage]]]:
        """Resolve the named dataset version and every member image it references.

        Raises:
            EntityNotFoundError: If the dataset or the version tag does not exist.
        """
        async with self._uow_factory() as uow:
            dataset = await uow.datasets.find_dataset_by_name(command.dataset_name)
            if dataset is None:
                raise EntityNotFoundError("Dataset", command.dataset_name)
            version = await uow.datasets.find_version_by_tag(
                dataset.id, command.dataset_version_tag
            )
            if version is None:
                raise EntityNotFoundError("DatasetVersion", command.dataset_version_tag)
            members_by_split: dict[DatasetSplit, list[InspectionImage]] = {
                split: [] for split in DatasetSplit
            }
            for member in version.members:
                image = await uow.images.get(member.image_id)
                members_by_split[member.split].append(image)
        return version, members_by_split

    def _start_run(self, command: TrainModelCommand, started_at: datetime) -> str:
        """Begin the MLflow run this training cycle logs against."""
        return self._experiment_tracker.start_run(
            experiment_name=self._mlflow_experiment_name,
            run_name=f"{command.model_name}-{command.dataset_version_tag}-{started_at:%Y%m%dT%H%M%S}",
        )

    def _log_launch_params(
        self,
        run_id: str,
        command: TrainModelCommand,
        version: DatasetVersion,
        git_commit: str,
    ) -> None:
        """Record every lineage fact fixed before the fit even starts."""
        self._experiment_tracker.log_params(
            run_id,
            {
                "dataset_name": command.dataset_name,
                "dataset_version_tag": command.dataset_version_tag,
                "dataset_version_id": str(version.id),
                "git_commit": git_commit,
                "config_hash": command.config_hash(),
                "model_name": command.model_name,
                "seed": command.seed,
                "device": command.device,
                **{f"hp_{key}": value for key, value in command.hyperparameters.items()},
            },
        )

    def _log_completed_run(
        self, run_id: str, metrics: EvaluationMetrics, training_time_seconds: float
    ) -> None:
        """Record the evaluation and timing of a successful fit."""
        self._experiment_tracker.log_evaluation(run_id, metrics)
        self._experiment_tracker.log_metrics(
            run_id, {"training_time_seconds": training_time_seconds}
        )

    async def _record_failure(
        self,
        command: TrainModelCommand,
        version: DatasetVersion,
        run_id: str,
        git_commit: str,
        hardware: HardwareInfo,
        started_at: datetime,
        reason: str,
    ) -> None:
        """Persist a failed run so it is still queryable, not silently lost."""
        experiment = Experiment(
            id=ExperimentId(self._id_generator.new_id()),
            mlflow_run_id=run_id,
            dataset_version_id=version.id,
            model_family=command.model_name,
            backbone=command.backbone or "unknown",
            hyperparameters=command.hyperparameters,
            config_hash=command.config_hash(),
            git_commit=git_commit,
            started_at=started_at,
            hardware=hardware,
        ).fail(reason, finished_at=self._clock.now())
        async with self._uow_factory() as uow:
            await uow.experiments.add(experiment)
            await uow.commit()

    async def _persist(
        self, experiment: Experiment, model_version: ModelVersion, command: TrainModelCommand
    ) -> None:
        """Persist the completed experiment and model version, plus an audit event."""
        async with self._uow_factory() as uow:
            await uow.experiments.add(experiment)
            await uow.models.add(model_version)
            latest = await uow.audit.latest()
            event = AuditEvent(
                sequence=AuditSequence((latest.sequence + 1) if latest else 1),
                action="model.trained",
                resource_type="model_version",
                resource_id=str(model_version.id),
                occurred_at=model_version.created_at,
                prev_hash=latest.row_hash() if latest else GENESIS_HASH,
                actor_id=command.started_by,
                payload={
                    "registry_name": model_version.registry_name,
                    "registry_version": model_version.registry_version,
                    "mlflow_run_id": experiment.mlflow_run_id,
                    "dataset_version_tag": command.dataset_version_tag,
                },
            )
            await uow.audit.append(event)
            await uow.commit()
