"""Builders for use cases wired to fakes.

Mirrors ``tests/builders.py``: each function returns a fully wired use case with sensible
defaults, so a test overrides only the collaborator it actually cares about instead of
repeating the full constructor call everywhere.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from factoryai.application.use_cases.create_dataset_version import CreateDatasetVersion
from factoryai.application.use_cases.get_defect_trend import GetDefectTrend
from factoryai.application.use_cases.ingest_image import IngestImage
from factoryai.application.use_cases.list_dataset_versions import ListDatasetVersions
from factoryai.application.use_cases.list_deployments import ListDeployments
from factoryai.application.use_cases.list_drift_reports import ListDriftReports
from factoryai.application.use_cases.list_feedback_queue import ListFeedbackQueue
from factoryai.application.use_cases.list_model_versions import ListModelVersions
from factoryai.application.use_cases.list_predictions import ListPredictions
from factoryai.application.use_cases.list_production_models import ListProductionModels
from factoryai.application.use_cases.list_training_runs import ListTrainingRuns
from factoryai.application.use_cases.promote_model import PromoteModel, PromotionGate
from factoryai.application.use_cases.rollback_deployment import RollbackDeployment
from factoryai.application.use_cases.submit_feedback import SubmitFeedback
from factoryai.application.use_cases.train_model import TrainModel
from factoryai.domain.policies.validation import (
    AllowedColorModesRule,
    AllowedFormatRule,
    MaxFileSizeRule,
    ResolutionBoundsRule,
    ValidationChain,
)
from factoryai.domain.ports.detection import AnomalyDetector
from factoryai.domain.ports.imaging import ImageCodec
from factoryai.domain.ports.services import Clock, HardwareProbe, IdGenerator
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.ports.tracking import ExperimentTracker, ModelRegistry
from factoryai.domain.ports.versioning import VersionControl
from factoryai.domain.value_objects import Resolution
from tests.fakes import FakeUnitOfWork

_PERMISSIVE_CHAIN = ValidationChain(
    rules=(
        MaxFileSizeRule(max_bytes=25 * 1024 * 1024),
        AllowedFormatRule(frozenset({"png", "jpeg", "bmp", "tiff"})),
        ResolutionBoundsRule(minimum=Resolution(1, 1), maximum=Resolution(8192, 8192)),
        AllowedColorModesRule(frozenset({"RGB", "L", "RGBA"})),
    )
)
"""Bounds loose enough that a test's scripted :class:`DecodedImage` always passes them,
unless the test supplies its own ``validation_chain`` specifically to exercise a rule."""


def make_ingest_image_use_case(
    *,
    uow: FakeUnitOfWork,
    object_store: ObjectStore,
    image_codec: ImageCodec,
    clock: Clock,
    id_generator: IdGenerator,
    validation_chain: ValidationChain = _PERMISSIVE_CHAIN,
    raw_bucket: str = "factoryai-raw",
    duplicate_hamming_threshold: int = 3,
) -> IngestImage:
    """Build an :class:`IngestImage` use case wired to the given fakes."""
    return IngestImage(
        uow_factory=lambda: uow,
        object_store=object_store,
        image_codec=image_codec,
        validation_chain=validation_chain,
        clock=clock,
        id_generator=id_generator,
        raw_bucket=raw_bucket,
        duplicate_hamming_threshold=duplicate_hamming_threshold,
    )


def make_create_dataset_version_use_case(
    *,
    uow: FakeUnitOfWork,
    version_control: VersionControl,
    clock: Clock,
    id_generator: IdGenerator,
) -> CreateDatasetVersion:
    """Build a :class:`CreateDatasetVersion` use case wired to the given fakes."""
    return CreateDatasetVersion(
        uow_factory=lambda: uow,
        version_control=version_control,
        clock=clock,
        id_generator=id_generator,
    )


def make_train_model_use_case(
    *,
    uow: FakeUnitOfWork,
    object_store: ObjectStore,
    detector_factory: Callable[[str, str | None], AnomalyDetector],
    experiment_tracker: ExperimentTracker,
    model_registry: ModelRegistry,
    version_control: VersionControl,
    hardware_probe: HardwareProbe,
    clock: Clock,
    id_generator: IdGenerator,
    workdir: Path,
    mlflow_experiment_name: str = "factoryai-test",
) -> TrainModel:
    """Build a :class:`TrainModel` use case wired to the given fakes."""
    return TrainModel(
        uow_factory=lambda: uow,
        object_store=object_store,
        detector_factory=detector_factory,
        experiment_tracker=experiment_tracker,
        model_registry=model_registry,
        version_control=version_control,
        hardware_probe=hardware_probe,
        clock=clock,
        id_generator=id_generator,
        workdir=workdir,
        mlflow_experiment_name=mlflow_experiment_name,
    )


_DEFAULT_GATE = PromotionGate()


def make_promote_model_use_case(
    *,
    uow: FakeUnitOfWork,
    model_registry: ModelRegistry,
    clock: Clock,
    id_generator: IdGenerator,
    gate: PromotionGate = _DEFAULT_GATE,
) -> PromoteModel:
    """Build a :class:`PromoteModel` use case wired to the given fakes."""
    return PromoteModel(
        uow_factory=lambda: uow,
        model_registry=model_registry,
        gate=gate,
        clock=clock,
        id_generator=id_generator,
    )


def make_rollback_deployment_use_case(
    *,
    uow: FakeUnitOfWork,
    model_registry: ModelRegistry,
    clock: Clock,
    id_generator: IdGenerator,
) -> RollbackDeployment:
    """Build a :class:`RollbackDeployment` use case wired to the given fakes."""
    return RollbackDeployment(
        uow_factory=lambda: uow,
        model_registry=model_registry,
        clock=clock,
        id_generator=id_generator,
    )


def make_submit_feedback_use_case(
    *, uow: FakeUnitOfWork, clock: Clock, id_generator: IdGenerator
) -> SubmitFeedback:
    """Build a :class:`SubmitFeedback` use case wired to the given fakes."""
    return SubmitFeedback(uow_factory=lambda: uow, clock=clock, id_generator=id_generator)


def make_list_production_models_use_case(*, uow: FakeUnitOfWork) -> ListProductionModels:
    """Build a :class:`ListProductionModels` use case wired to the given fake."""
    return ListProductionModels(uow_factory=lambda: uow)


def make_list_predictions_use_case(*, uow: FakeUnitOfWork) -> ListPredictions:
    """Build a :class:`ListPredictions` use case wired to the given fake."""
    return ListPredictions(uow_factory=lambda: uow)


def make_list_feedback_queue_use_case(*, uow: FakeUnitOfWork) -> ListFeedbackQueue:
    """Build a :class:`ListFeedbackQueue` use case wired to the given fake."""
    return ListFeedbackQueue(uow_factory=lambda: uow)


def make_list_drift_reports_use_case(*, uow: FakeUnitOfWork) -> ListDriftReports:
    """Build a :class:`ListDriftReports` use case wired to the given fake."""
    return ListDriftReports(uow_factory=lambda: uow)


def make_list_dataset_versions_use_case(*, uow: FakeUnitOfWork) -> ListDatasetVersions:
    """Build a :class:`ListDatasetVersions` use case wired to the given fake."""
    return ListDatasetVersions(uow_factory=lambda: uow)


def make_list_training_runs_use_case(*, uow: FakeUnitOfWork) -> ListTrainingRuns:
    """Build a :class:`ListTrainingRuns` use case wired to the given fake."""
    return ListTrainingRuns(uow_factory=lambda: uow)


def make_list_model_versions_use_case(*, uow: FakeUnitOfWork) -> ListModelVersions:
    """Build a :class:`ListModelVersions` use case wired to the given fake."""
    return ListModelVersions(uow_factory=lambda: uow)


def make_list_deployments_use_case(*, uow: FakeUnitOfWork) -> ListDeployments:
    """Build a :class:`ListDeployments` use case wired to the given fake."""
    return ListDeployments(uow_factory=lambda: uow)


def make_get_defect_trend_use_case(*, uow: FakeUnitOfWork, clock: Clock) -> GetDefectTrend:
    """Build a :class:`GetDefectTrend` use case wired to the given fakes."""
    return GetDefectTrend(uow_factory=lambda: uow, clock=clock)
