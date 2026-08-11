"""Entities: objects with identity, invariants and a lifecycle.

Unlike value objects, two entities with identical attributes are still different things if
their identifiers differ. All entities here are frozen dataclasses whose state transitions
return new instances, which keeps an entity handed to one component from mutating beneath
another and matches the append-only persistence model.
"""

from factoryai.domain.entities.audit import GENESIS_HASH, AuditEvent, verify_chain
from factoryai.domain.entities.dataset import Dataset, DatasetMember, DatasetVersion
from factoryai.domain.entities.experiment import (
    EvaluationMetrics,
    Experiment,
    HardwareInfo,
)
from factoryai.domain.entities.image import InspectionImage
from factoryai.domain.entities.job import Job
from factoryai.domain.entities.model import Deployment, ModelVersion
from factoryai.domain.entities.monitoring import DriftReport, DriftSignal
from factoryai.domain.entities.prediction import Feedback, Prediction
from factoryai.domain.entities.user import User

__all__ = [
    "GENESIS_HASH",
    "AuditEvent",
    "Dataset",
    "DatasetMember",
    "DatasetVersion",
    "Deployment",
    "DriftReport",
    "DriftSignal",
    "EvaluationMetrics",
    "Experiment",
    "Feedback",
    "HardwareInfo",
    "InspectionImage",
    "Job",
    "ModelVersion",
    "Prediction",
    "User",
    "verify_chain",
]
