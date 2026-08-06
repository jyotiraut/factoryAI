"""Registered model versions and the deployments that move them between stages."""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

from factoryai.domain.entities.experiment import EvaluationMetrics
from factoryai.domain.errors import IllegalStateTransitionError, InvariantViolationError
from factoryai.domain.value_objects import (
    Category,
    DeploymentAction,
    DeploymentId,
    ExperimentId,
    ModelStage,
    ModelVersionId,
    StorageLocation,
    UserId,
)

_ALLOWED_STAGE_TRANSITIONS: dict[ModelStage, frozenset[ModelStage]] = {
    ModelStage.DEVELOPMENT: frozenset({ModelStage.STAGING, ModelStage.ARCHIVED}),
    ModelStage.STAGING: frozenset(
        {ModelStage.PRODUCTION, ModelStage.DEVELOPMENT, ModelStage.ARCHIVED}
    ),
    ModelStage.PRODUCTION: frozenset({ModelStage.STAGING, ModelStage.ARCHIVED}),
    ModelStage.ARCHIVED: frozenset({ModelStage.STAGING}),
}
"""Permitted stage transitions.

Development cannot jump straight to production — a candidate must pass through staging,
where the promotion gate evaluates it. Archived models can return to staging, which is
what makes rollback possible without re-training.
"""


@dataclass(frozen=True, slots=True)
class ModelVersion:
    """A trained, registered model artifact.

    The threshold lives here rather than in the serving configuration because it is a
    property *of this model*: serving a model with another version's threshold produces
    silently wrong verdicts, which is among the easier production mistakes to make and the
    harder ones to notice.

    Attributes:
        id: Unique identifier.
        experiment_id: The run that produced this artifact.
        category: The product class this model inspects.
        registry_name: Name under which it is registered.
        registry_version: Monotonic version number within that registry name.
        stage: Current lifecycle stage.
        threshold: Calibrated decision boundary.
        artifact_location: Where the weights are stored.
        metrics: Held-out evaluation results.
        created_at: Timezone-aware registration timestamp.
        tags: Free-form annotations.
    """

    id: ModelVersionId
    experiment_id: ExperimentId
    category: Category
    registry_name: str
    registry_version: int
    threshold: float
    artifact_location: StorageLocation
    metrics: EvaluationMetrics
    created_at: datetime
    stage: ModelStage = ModelStage.DEVELOPMENT
    tags: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate registry version, name and timestamp.

        Raises:
            InvariantViolationError: If the registry version is not positive, the name is
                blank, or the timestamp is naive.
        """
        if self.registry_version < 1:
            raise InvariantViolationError(
                "registry version must be a positive integer",
                code="model.invalid_version",
                details={"registry_version": self.registry_version},
            )
        if not self.registry_name.strip():
            raise InvariantViolationError("registry name must not be blank", code="model.no_name")
        if self.created_at.tzinfo is None:
            raise InvariantViolationError(
                "created_at must be timezone-aware", code="model.naive_timestamp"
            )

    def transition_to(self, stage: ModelStage) -> Self:
        """Return a copy in a new lifecycle stage.

        Args:
            stage: The requested stage.

        Returns:
            A new instance in ``stage``, or ``self`` if already there.

        Raises:
            IllegalStateTransitionError: If the move is not permitted from the current stage.
        """
        if stage is self.stage:
            return self
        if stage not in _ALLOWED_STAGE_TRANSITIONS[self.stage]:
            raise IllegalStateTransitionError("ModelVersion", self.stage, stage)
        return dataclasses.replace(self, stage=stage)

    def recalibrate(self, threshold: float) -> Self:
        """Return a copy with a new decision threshold.

        Raises:
            InvariantViolationError: If the threshold is not a real number.
        """
        if not math.isfinite(threshold):
            raise InvariantViolationError(
                "threshold must be a finite number", code="model.invalid_threshold"
            )
        return dataclasses.replace(self, threshold=threshold)

    @property
    def is_servable(self) -> bool:
        """Return whether the inference service may load this version."""
        return self.stage.is_servable

    @property
    def reference(self) -> str:
        """Return the registry coordinate, e.g. ``"factoryai-bottle/7"``."""
        return f"{self.registry_name}/{self.registry_version}"


@dataclass(frozen=True, slots=True)
class Deployment:
    """An immutable record of a stage change, or of a refusal to make one.

    Rejections are recorded as deliberately as promotions: an audit that only shows what
    was deployed cannot answer why a known-better candidate never shipped.

    Attributes:
        id: Unique identifier.
        model_version_id: The model the action concerns.
        action: What happened.
        environment: Target environment, e.g. ``"production"``.
        actor_id: Who performed it; absent for automated actions.
        deployed_at: Timezone-aware timestamp.
        previous_model_version_id: The version displaced, when there was one.
        comparison_report: Gate metrics that justified the decision.
        reason: Human-readable justification.
    """

    id: DeploymentId
    model_version_id: ModelVersionId
    action: DeploymentAction
    environment: str
    deployed_at: datetime
    actor_id: UserId | None = None
    previous_model_version_id: ModelVersionId | None = None
    comparison_report: dict[str, Any] = dataclasses.field(default_factory=dict)
    reason: str = ""

    def __post_init__(self) -> None:
        """Validate the environment, timestamp and rollback consistency.

        Raises:
            InvariantViolationError: If the environment is blank, the timestamp is naive, or a
                rollback names no version to roll back to.
        """
        if not self.environment.strip():
            raise InvariantViolationError(
                "deployment environment must not be blank", code="deployment.no_environment"
            )
        if self.deployed_at.tzinfo is None:
            raise InvariantViolationError(
                "deployed_at must be timezone-aware", code="deployment.naive_timestamp"
            )
        if self.action is DeploymentAction.ROLLBACK and self.previous_model_version_id is None:
            raise InvariantViolationError(
                "a rollback must record the version it replaced",
                code="deployment.incomplete_rollback",
            )

    @property
    def is_automated(self) -> bool:
        """Return whether the platform performed this action without a human actor."""
        return self.actor_id is None

    @property
    def changed_production(self) -> bool:
        """Return whether this record altered what production is serving."""
        return self.action in {DeploymentAction.PROMOTE, DeploymentAction.ROLLBACK}
