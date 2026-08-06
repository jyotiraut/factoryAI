"""Ports: interfaces the domain owns and infrastructure implements.

The direction of ownership is the whole point. The domain declares what it needs — "store
these bytes", "fit a model" — and infrastructure conforms. Nothing here imports boto3,
SQLAlchemy, MLflow or Anomalib, which is what makes each of them replaceable.

I/O-bound ports are ``async``; compute-bound ports are synchronous (ADR-0008).
"""

from factoryai.domain.ports.detection import (
    AnomalyDetector,
    DetectorNotLoadedError,
    RawPrediction,
    TrainedModel,
    TrainingRequest,
    available_detectors,
    get_detector_class,
    register_detector,
)
from factoryai.domain.ports.monitoring import DistributionSample, DriftDetector
from factoryai.domain.ports.repositories import (
    AuditRepository,
    DatasetRepository,
    DriftReportRepository,
    ExperimentRepository,
    ImageRepository,
    ModelRepository,
    PredictionRepository,
    UnitOfWork,
    UserRepository,
)
from factoryai.domain.ports.services import (
    Clock,
    IdGenerator,
    SystemClock,
    UuidGenerator,
)
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.ports.tracking import ExperimentTracker, ModelRegistry

__all__ = [
    "AnomalyDetector",
    "AuditRepository",
    "Clock",
    "DatasetRepository",
    "DetectorNotLoadedError",
    "DistributionSample",
    "DriftDetector",
    "DriftReportRepository",
    "ExperimentRepository",
    "ExperimentTracker",
    "IdGenerator",
    "ImageRepository",
    "ModelRegistry",
    "ModelRepository",
    "ObjectStore",
    "PredictionRepository",
    "RawPrediction",
    "SystemClock",
    "TrainedModel",
    "TrainingRequest",
    "UnitOfWork",
    "UserRepository",
    "UuidGenerator",
    "available_detectors",
    "get_detector_class",
    "register_detector",
]
