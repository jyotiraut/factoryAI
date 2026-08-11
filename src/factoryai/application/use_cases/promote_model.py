"""The promotion use case: a candidate model earns production only by beating the incumbent.

A candidate is never trusted on its own numbers alone — it is compared against whatever is
currently serving production (if anything is), and the comparison, pass or fail, is
recorded as a :class:`Deployment`. A rejected candidate is exceptional (it raises
:class:`~factoryai.domain.errors.PromotionRejectedError`), but the rejection itself is not
lost: it is written down exactly like an accepted promotion, which is what makes "why did
we never ship that better-looking candidate" an answerable question later.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from factoryai.domain.entities import AuditEvent, Deployment, ModelVersion
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.errors import PromotionRejectedError
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import Clock, IdGenerator
from factoryai.domain.ports.tracking import ModelRegistry
from factoryai.domain.value_objects import (
    AuditSequence,
    Category,
    DeploymentAction,
    DeploymentId,
    ModelStage,
    ModelVersionId,
    UserId,
)

_ALLOWED_STAGING_SOURCES = frozenset({ModelStage.DEVELOPMENT, ModelStage.ARCHIVED})
"""Stages :meth:`advance_to_production` will pass a model through STAGING from.

A model already in STAGING or PRODUCTION does not need the intermediate hop.
"""


@dataclass(frozen=True, slots=True)
class PromotionGate:
    """The automated criteria a candidate must clear to reach production.

    Attributes:
        min_auroc: Absolute floor: no candidate below this is ever promotable, regardless
            of how the incumbent is performing.
        improvement_margin: How much better than the incumbent's image AUROC a candidate
            must be. Meaningless without an incumbent — first-ever promotion skips it.
        max_recall_regression: How much defect-recall a candidate may give up relative to
            the incumbent before it is rejected, even if its AUROC improved.
    """

    min_auroc: float = 0.95
    improvement_margin: float = 0.005
    max_recall_regression: float = 0.01


@dataclass(frozen=True, slots=True)
class PromoteModelCommand:
    """Promote one candidate model version to production, if it earns it.

    Attributes:
        category: Which product class's production slot this concerns.
        candidate_model_version_id: The version being evaluated for promotion.
        environment: Target environment recorded on the resulting deployment record.
        reason: Optional free-text justification.
        actor_id: The user requesting the promotion; absent for an automated trigger.
    """

    category: Category
    candidate_model_version_id: ModelVersionId
    environment: str = "production"
    reason: str = ""
    actor_id: UserId | None = None


@dataclass(frozen=True, slots=True)
class PromoteModelResult:
    """The outcome of a successful promotion.

    Attributes:
        model_version_id: The now-production model version.
        previous_model_version_id: What it replaced, if anything.
        comparison_report: The gate's numeric comparison, for display or audit.
    """

    model_version_id: ModelVersionId
    previous_model_version_id: ModelVersionId | None
    comparison_report: dict[str, Any]


def advance_to_production(model: ModelVersion) -> ModelVersion:
    """Return a copy of ``model`` in the PRODUCTION stage.

    Development and Archived models cannot move to Production directly (see
    ``domain/entities/model.py``'s ``_ALLOWED_STAGE_TRANSITIONS``) — they must pass through
    Staging first, which is where the promotion gate itself stands in for that review step.
    Shared between :class:`PromoteModel` and
    :class:`~factoryai.application.use_cases.rollback_deployment.RollbackDeployment`, since
    a rollback target is exactly a candidate that already earned production once.
    """
    if model.stage in _ALLOWED_STAGING_SOURCES:
        model = model.transition_to(ModelStage.STAGING)
    return model.transition_to(ModelStage.PRODUCTION)


def _evaluate_gate(
    candidate: ModelVersion, production: ModelVersion | None, gate: PromotionGate
) -> tuple[list[str], dict[str, Any]]:
    """Return the reasons a candidate fails the gate (empty if it passes) and the report."""
    reasons: list[str] = []
    candidate_auroc = candidate.metrics.image_auroc
    candidate_recall = candidate.metrics.recall

    if candidate_auroc < gate.min_auroc:
        reasons.append(
            f"image_auroc {candidate_auroc:.4f} is below the required minimum {gate.min_auroc:.4f}"
        )

    production_auroc = production.metrics.image_auroc if production else None
    production_recall = production.metrics.recall if production else None
    if production is not None:
        required_auroc = production.metrics.image_auroc + gate.improvement_margin
        if candidate_auroc < required_auroc:
            reasons.append(
                f"image_auroc {candidate_auroc:.4f} does not beat production "
                f"{production.metrics.image_auroc:.4f} by the required margin "
                f"{gate.improvement_margin:.4f}"
            )
        lowest_acceptable_recall = production.metrics.recall - gate.max_recall_regression
        if candidate_recall < lowest_acceptable_recall:
            reasons.append(
                f"recall {candidate_recall:.4f} regresses beyond tolerance "
                f"{gate.max_recall_regression:.4f} from production {production.metrics.recall:.4f}"
            )

    report = {
        "candidate_image_auroc": candidate_auroc,
        "candidate_recall": candidate_recall,
        "production_image_auroc": production_auroc,
        "production_recall": production_recall,
        "min_auroc": gate.min_auroc,
        "improvement_margin": gate.improvement_margin,
        "max_recall_regression": gate.max_recall_regression,
        "passed": not reasons,
    }
    return reasons, report


class PromoteModel:
    """Evaluates a candidate against the current production model and promotes or rejects it."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        model_registry: ModelRegistry,
        gate: PromotionGate,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        """Initialise with every collaborator this use case needs.

        Args:
            uow_factory: Builds a fresh unit of work per call.
            model_registry: Kept in sync with PostgreSQL's stage decision (ADR-0004:
                PostgreSQL is authoritative, MLflow mirrors it).
            gate: The automated promotion criteria to enforce.
            clock: Source of "now", for the deployment timestamp.
            id_generator: Source of the new deployment's identifier.
        """
        self._uow_factory = uow_factory
        self._model_registry = model_registry
        self._gate = gate
        self._clock = clock
        self._id_generator = id_generator

    async def execute(self, command: PromoteModelCommand) -> PromoteModelResult:
        """Promote the candidate if it clears the gate.

        Raises:
            EntityNotFoundError: If the candidate model version does not exist.
            PromotionRejectedError: If the candidate fails the gate. The rejection is
                still recorded as a :class:`Deployment` before this raises.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            candidate = await uow.models.get(command.candidate_model_version_id)
            production = await uow.models.find_by_stage(command.category, ModelStage.PRODUCTION)
            reasons, report = _evaluate_gate(candidate, production, self._gate)

            if reasons:
                # Committing here, inside the block, is what makes the rejection durable:
                # raising afterwards, still inside this `async with`, would propagate the
                # exception through `__aexit__` and roll the transaction back regardless of
                # having already called commit() — discarding the very record this is
                # supposed to make durable. See DeploymentAction.REJECT's docstring: a
                # rejection is recorded as deliberately as a promotion.
                await self._record(
                    uow,
                    action=DeploymentAction.REJECT,
                    model_version_id=candidate.id,
                    previous_model_version_id=production.id if production else None,
                    command=command,
                    report=report,
                    now=now,
                )
                await uow.commit()
            else:
                promoted = advance_to_production(candidate)
                self._model_registry.transition_stage(
                    name=promoted.registry_name,
                    version=promoted.registry_version,
                    stage=ModelStage.PRODUCTION,
                )
                await uow.models.update(promoted)

                if production is not None:
                    archived = production.transition_to(ModelStage.ARCHIVED)
                    self._model_registry.transition_stage(
                        name=archived.registry_name,
                        version=archived.registry_version,
                        stage=ModelStage.ARCHIVED,
                    )
                    await uow.models.update(archived)

                await self._record(
                    uow,
                    action=DeploymentAction.PROMOTE,
                    model_version_id=promoted.id,
                    previous_model_version_id=production.id if production else None,
                    command=command,
                    report=report,
                    now=now,
                )
                await uow.commit()

        if reasons:
            raise PromotionRejectedError(reasons, details=report)

        return PromoteModelResult(
            model_version_id=promoted.id,
            previous_model_version_id=production.id if production else None,
            comparison_report=report,
        )

    async def _record(
        self,
        uow: UnitOfWork,
        *,
        action: DeploymentAction,
        model_version_id: ModelVersionId,
        previous_model_version_id: ModelVersionId | None,
        command: PromoteModelCommand,
        report: dict[str, Any],
        now: datetime,
    ) -> None:
        """Append the deployment record and its audit event."""
        deployment = Deployment(
            id=DeploymentId(self._id_generator.new_id()),
            model_version_id=model_version_id,
            action=action,
            environment=command.environment,
            deployed_at=now,
            actor_id=command.actor_id,
            previous_model_version_id=previous_model_version_id,
            comparison_report=report,
            reason=command.reason,
        )
        await uow.models.add_deployment(deployment)

        latest = await uow.audit.latest()
        event = AuditEvent(
            sequence=AuditSequence((latest.sequence + 1) if latest else 1),
            action=f"model.{action.value}",
            resource_type="deployment",
            resource_id=str(deployment.id),
            occurred_at=now,
            prev_hash=latest.row_hash() if latest else GENESIS_HASH,
            actor_id=command.actor_id,
            payload={
                "model_version_id": str(model_version_id),
                "environment": command.environment,
                "passed": report.get("passed"),
            },
        )
        await uow.audit.append(event)
