"""The rollback use case: restore a prior production version, fully audited.

Unlike :class:`~factoryai.application.use_cases.promote_model.PromoteModel`, rollback is
an operator decision, not a candidate earning its place — the gate does not run again. The
version being restored already cleared it once, when it was first promoted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from factoryai.application.use_cases.promote_model import advance_to_production
from factoryai.domain.entities import AuditEvent, Deployment, ModelVersion
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.errors import DomainError
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


class NoPriorProductionVersionError(DomainError):
    """No earlier production version exists to roll back to."""

    default_code = "deployment.no_prior_version"


class NothingToRollBackError(DomainError):
    """The category has no current production model to roll back from."""

    default_code = "deployment.nothing_to_roll_back"


@dataclass(frozen=True, slots=True)
class RollbackDeploymentCommand:
    """Restore a prior production version for one category.

    Attributes:
        category: Which product class's production slot this concerns.
        environment: Target environment recorded on the resulting deployment record.
        target_model_version_id: The version to restore; when absent, the most recently
            displaced production version is used.
        reason: Optional free-text justification.
        actor_id: The user requesting the rollback; absent for an automated trigger.
    """

    category: Category
    environment: str = "production"
    target_model_version_id: ModelVersionId | None = None
    reason: str = ""
    actor_id: UserId | None = None


@dataclass(frozen=True, slots=True)
class RollbackDeploymentResult:
    """The outcome of a rollback.

    Attributes:
        model_version_id: The now-production model version.
        previous_model_version_id: What it replaced.
    """

    model_version_id: ModelVersionId
    previous_model_version_id: ModelVersionId


class RollbackDeployment:
    """Restores a prior production version, displacing whatever is serving now."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        model_registry: ModelRegistry,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        """Initialise with every collaborator this use case needs."""
        self._uow_factory = uow_factory
        self._model_registry = model_registry
        self._clock = clock
        self._id_generator = id_generator

    async def execute(self, command: RollbackDeploymentCommand) -> RollbackDeploymentResult:
        """Restore a prior production version.

        Raises:
            NothingToRollBackError: If the category has no current production model.
            NoPriorProductionVersionError: If no target was given and none can be found
                from deployment history.
            EntityNotFoundError: If an explicit target does not exist.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            current = await uow.models.find_by_stage(command.category, ModelStage.PRODUCTION)
            if current is None:
                raise NothingToRollBackError(
                    f"category {command.category.code!r} has no current production model",
                    details={"category": command.category.code},
                )

            target = await self._resolve_target(uow, command, current)

            restored = advance_to_production(target)
            self._model_registry.transition_stage(
                name=restored.registry_name,
                version=restored.registry_version,
                stage=ModelStage.PRODUCTION,
            )
            await uow.models.update(restored)

            displaced = current.transition_to(ModelStage.ARCHIVED)
            self._model_registry.transition_stage(
                name=displaced.registry_name,
                version=displaced.registry_version,
                stage=ModelStage.ARCHIVED,
            )
            await uow.models.update(displaced)

            await self._record(uow, restored.id, displaced.id, command, now)
            await uow.commit()

        return RollbackDeploymentResult(
            model_version_id=restored.id, previous_model_version_id=displaced.id
        )

    async def _resolve_target(
        self, uow: UnitOfWork, command: RollbackDeploymentCommand, current: ModelVersion
    ) -> ModelVersion:
        """Return the model version to restore.

        Raises:
            NoPriorProductionVersionError: If no target was given and deployment history
                names no earlier production version to fall back to.
        """
        if command.target_model_version_id is not None:
            return await uow.models.get(command.target_model_version_id)

        history = await uow.models.list_deployments(
            command.category, environment=command.environment
        )
        for deployment in history:
            if deployment.changed_production and deployment.model_version_id != current.id:
                return await uow.models.get(deployment.model_version_id)
        raise NoPriorProductionVersionError(
            f"no prior production version found for category {command.category.code!r}",
            details={"category": command.category.code},
        )

    async def _record(
        self,
        uow: UnitOfWork,
        model_version_id: ModelVersionId,
        previous_model_version_id: ModelVersionId,
        command: RollbackDeploymentCommand,
        now: datetime,
    ) -> None:
        """Append the deployment record and its audit event."""
        deployment = Deployment(
            id=DeploymentId(self._id_generator.new_id()),
            model_version_id=model_version_id,
            action=DeploymentAction.ROLLBACK,
            environment=command.environment,
            deployed_at=now,
            actor_id=command.actor_id,
            previous_model_version_id=previous_model_version_id,
            reason=command.reason,
        )
        await uow.models.add_deployment(deployment)

        latest = await uow.audit.latest()
        event = AuditEvent(
            sequence=AuditSequence((latest.sequence + 1) if latest else 1),
            action="model.rollback",
            resource_type="deployment",
            resource_id=str(deployment.id),
            occurred_at=now,
            prev_hash=latest.row_hash() if latest else GENESIS_HASH,
            actor_id=command.actor_id,
            payload={
                "model_version_id": str(model_version_id),
                "previous_model_version_id": str(previous_model_version_id),
                "environment": command.environment,
            },
        )
        await uow.audit.append(event)
