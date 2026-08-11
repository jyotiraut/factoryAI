"""Ports: interfaces the domain owns and infrastructure implements.

The direction of ownership is the whole point. The domain declares what it needs — "store
these bytes", "fit a model" — and infrastructure conforms. Nothing here imports boto3,
SQLAlchemy, MLflow or Anomalib, which is what makes each of them replaceable.

I/O-bound ports are ``async``; compute-bound ports are synchronous (ADR-0008).
"""

from factoryai.domain.ports.auth import (
    AccessTokenClaims,
    IssuedTokenPair,
    PasswordHasher,
    RefreshTokenClaims,
    TokenRevocationList,
    TokenService,
)
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
from factoryai.domain.ports.imaging import ImageCodec
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
    HardwareProbe,
    IdGenerator,
    SystemClock,
    UuidGenerator,
)
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.ports.tracking import ExperimentTracker, ModelRegistry
from factoryai.domain.ports.versioning import VersionControl

__all__ = [
    "AccessTokenClaims",
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
    "HardwareProbe",
    "IdGenerator",
    "ImageCodec",
    "ImageRepository",
    "IssuedTokenPair",
    "ModelRegistry",
    "ModelRepository",
    "ObjectStore",
    "PasswordHasher",
    "PredictionRepository",
    "RawPrediction",
    "RefreshTokenClaims",
    "SystemClock",
    "TokenRevocationList",
    "TokenService",
    "TrainedModel",
    "TrainingRequest",
    "UnitOfWork",
    "UserRepository",
    "UuidGenerator",
    "VersionControl",
    "available_detectors",
    "get_detector_class",
    "register_detector",
]
