"""Immutable, self-validating values with no identity of their own.

Two value objects with equal attributes are the same value — there is no "which one" to
ask about. They validate in their constructor, so an invalid instance cannot exist, and
they are frozen, so a valid one cannot become invalid later.
"""

from factoryai.domain.value_objects.anomaly_score import AnomalyScore
from factoryai.domain.value_objects.category import MVTEC_CATEGORIES, Category
from factoryai.domain.value_objects.checksum import Checksum
from factoryai.domain.value_objects.enums import (
    DatasetSplit,
    DeploymentAction,
    DriftSeverity,
    ExperimentStatus,
    FeedbackVerdict,
    ImageLabel,
    ModelStage,
    ProcessingStatus,
    UserRole,
)
from factoryai.domain.value_objects.identifiers import (
    AuditSequence,
    DatasetId,
    DatasetVersionId,
    DeploymentId,
    DriftReportId,
    ExperimentId,
    FeedbackId,
    ImageId,
    ModelVersionId,
    PredictionId,
    UserId,
    parse_uuid,
)
from factoryai.domain.value_objects.resolution import Resolution
from factoryai.domain.value_objects.storage_location import StorageLocation

__all__ = [
    "MVTEC_CATEGORIES",
    "AnomalyScore",
    "AuditSequence",
    "Category",
    "Checksum",
    "DatasetId",
    "DatasetSplit",
    "DatasetVersionId",
    "DeploymentAction",
    "DeploymentId",
    "DriftReportId",
    "DriftSeverity",
    "ExperimentId",
    "ExperimentStatus",
    "FeedbackId",
    "FeedbackVerdict",
    "ImageId",
    "ImageLabel",
    "ModelStage",
    "ModelVersionId",
    "PredictionId",
    "ProcessingStatus",
    "Resolution",
    "StorageLocation",
    "UserId",
    "UserRole",
    "parse_uuid",
]
