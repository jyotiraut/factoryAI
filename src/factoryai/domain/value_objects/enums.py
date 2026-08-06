"""Enumerations shared across the domain.

Every member's value is the exact string persisted in PostgreSQL and returned by the API,
so renaming one is a breaking change requiring a migration. They are ``str`` subclasses,
which makes them directly serialisable and comparable to raw strings at boundaries.
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class ProcessingStatus(StrEnum):
    """Lifecycle of an inspection image inside the platform.

    Images are never deleted; they move to :attr:`QUARANTINED` or :attr:`ARCHIVED` instead,
    which keeps the audit trail complete (see ``docs/DATA_MODEL.md`` §2).
    """

    PENDING = "pending"
    VALIDATING = "validating"
    VALID = "valid"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    ARCHIVED = "archived"

    @property
    def is_terminal(self) -> bool:
        """Return whether no further transition is possible from this status."""
        return self in {ProcessingStatus.REJECTED, ProcessingStatus.ARCHIVED}

    @property
    def is_usable_for_training(self) -> bool:
        """Return whether an image in this status may be included in a dataset version."""
        return self is ProcessingStatus.VALID


@unique
class ImageLabel(StrEnum):
    """Ground-truth label of an inspection image.

    PatchCore trains on :attr:`GOOD` samples only; :attr:`UNLABELED` is the default for
    images arriving from the production line before an operator has reviewed them.
    The specific defect subtype, when known, lives in the image metadata rather than here.
    """

    GOOD = "good"
    DEFECT = "defect"
    UNLABELED = "unlabeled"


@unique
class DatasetSplit(StrEnum):
    """Partition an image belongs to within a dataset version."""

    TRAIN = "train"
    VAL = "val"
    TEST = "test"


@unique
class ModelStage(StrEnum):
    """Model registry lifecycle stage (ADR-0004)."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    ARCHIVED = "archived"

    @property
    def is_servable(self) -> bool:
        """Return whether the inference service may load a model in this stage."""
        return self in {ModelStage.STAGING, ModelStage.PRODUCTION}


@unique
class ExperimentStatus(StrEnum):
    """Outcome of a training run."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"

    @property
    def is_finished(self) -> bool:
        """Return whether the run has reached a terminal state."""
        return self is not ExperimentStatus.RUNNING


@unique
class DeploymentAction(StrEnum):
    """What a deployment record represents.

    :attr:`REJECT` is recorded as deliberately as a promotion: knowing which candidates
    were refused, and why, is part of the audit story.
    """

    PROMOTE = "promote"
    ROLLBACK = "rollback"
    ARCHIVE = "archive"
    REJECT = "reject"


@unique
class FeedbackVerdict(StrEnum):
    """An operator's judgement of a prediction."""

    CORRECT = "correct"
    INCORRECT = "incorrect"


@unique
class DriftSeverity(StrEnum):
    """Severity assigned to a drift report."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def requires_retraining(self) -> bool:
        """Return whether this severity should trigger the retraining workflow."""
        return self in {DriftSeverity.MEDIUM, DriftSeverity.HIGH}


@unique
class UserRole(StrEnum):
    """Platform roles, ordered from least to most privileged (Phase 8)."""

    VIEWER = "viewer"
    OPERATOR = "operator"
    ML_ENGINEER = "ml_engineer"
    ADMINISTRATOR = "administrator"

    @property
    def rank(self) -> int:
        """Return the privilege rank, where a higher number means more privilege."""
        return _ROLE_RANKS[self]

    def can_act_as(self, required: UserRole) -> bool:
        """Return whether this role satisfies a requirement for ``required``.

        Roles are hierarchical: an administrator satisfies every requirement, a viewer
        only its own. Permissions that are not hierarchical are expressed as explicit
        checks in the application layer rather than being forced into this ordering.
        """
        return self.rank >= required.rank


_ROLE_RANKS: dict[UserRole, int] = {
    UserRole.VIEWER: 0,
    UserRole.OPERATOR: 1,
    UserRole.ML_ENGINEER: 2,
    UserRole.ADMINISTRATOR: 3,
}
