"""Unit tests for the rollback use case, against fakes."""

from __future__ import annotations

import pytest

from factoryai.application.use_cases.rollback_deployment import (
    NoPriorProductionVersionError,
    NothingToRollBackError,
    RollbackDeployment,
    RollbackDeploymentCommand,
)
from factoryai.domain.value_objects import Category, DeploymentAction, ModelStage
from tests.builders import NOW, a_deployment, a_model_version
from tests.fakes import FakeClock, FakeIdGenerator, FakeModelRegistry, FakeUnitOfWork
from tests.use_case_factory import make_rollback_deployment_use_case

pytestmark = pytest.mark.unit

_CATEGORY = Category("bottle")


def _use_case(
    uow: FakeUnitOfWork, registry: FakeModelRegistry | None = None
) -> tuple[RollbackDeployment, FakeModelRegistry]:
    registry = registry or FakeModelRegistry()
    use_case = make_rollback_deployment_use_case(
        uow=uow, model_registry=registry, clock=FakeClock(NOW), id_generator=FakeIdGenerator()
    )
    return use_case, registry


class TestExplicitTarget:
    async def test_the_named_target_is_restored_and_the_current_one_archived(self) -> None:
        uow = FakeUnitOfWork()
        current = (
            a_model_version(registry_version=2)
            .transition_to(ModelStage.STAGING)
            .transition_to(ModelStage.PRODUCTION)
        )
        target = (
            a_model_version(registry_version=1)
            .transition_to(ModelStage.STAGING)
            .transition_to(ModelStage.PRODUCTION)
            .transition_to(ModelStage.ARCHIVED)
        )
        await uow.models.add(current)
        await uow.models.add(target)
        use_case, registry = _use_case(uow)

        result = await use_case.execute(
            RollbackDeploymentCommand(category=_CATEGORY, target_model_version_id=target.id)
        )

        assert result.model_version_id == target.id
        assert result.previous_model_version_id == current.id
        assert (await uow.models.get(target.id)).stage is ModelStage.PRODUCTION
        assert (await uow.models.get(current.id)).stage is ModelStage.ARCHIVED
        assert registry.get_stage_version(name=target.registry_name, stage=ModelStage.PRODUCTION)

    async def test_a_rollback_appends_an_audit_event(self) -> None:
        uow = FakeUnitOfWork()
        current = (
            a_model_version().transition_to(ModelStage.STAGING).transition_to(ModelStage.PRODUCTION)
        )
        target = a_model_version().transition_to(ModelStage.STAGING)
        await uow.models.add(current)
        await uow.models.add(target)
        use_case, _ = _use_case(uow)

        await use_case.execute(
            RollbackDeploymentCommand(category=_CATEGORY, target_model_version_id=target.id)
        )

        latest = await uow.audit.latest()
        assert latest is not None
        assert latest.action == "model.rollback"


class TestNothingToRollBack:
    async def test_no_current_production_model_raises(self) -> None:
        uow = FakeUnitOfWork()
        use_case, _ = _use_case(uow)

        with pytest.raises(NothingToRollBackError):
            await use_case.execute(RollbackDeploymentCommand(category=_CATEGORY))


class TestResolvingTheDefaultTarget:
    async def test_the_previously_displaced_version_is_found_from_history(self) -> None:
        uow = FakeUnitOfWork()
        first = (
            a_model_version()
            .transition_to(ModelStage.STAGING)
            .transition_to(ModelStage.PRODUCTION)
            .transition_to(ModelStage.ARCHIVED)
        )
        second = (
            a_model_version().transition_to(ModelStage.STAGING).transition_to(ModelStage.PRODUCTION)
        )
        await uow.models.add(first)
        await uow.models.add(second)
        # `first` was promoted (no incumbent), then `second` displaced it.
        await uow.models.add_deployment(
            a_deployment(model_version_id=first.id, action=DeploymentAction.PROMOTE)
        )
        await uow.models.add_deployment(
            a_deployment(
                model_version_id=second.id,
                action=DeploymentAction.PROMOTE,
                previous_model_version_id=first.id,
            )
        )
        use_case, _ = _use_case(uow)

        result = await use_case.execute(RollbackDeploymentCommand(category=_CATEGORY))

        assert result.model_version_id == first.id
        assert result.previous_model_version_id == second.id

    async def test_no_history_at_all_raises_no_prior_version(self) -> None:
        uow = FakeUnitOfWork()
        current = (
            a_model_version().transition_to(ModelStage.STAGING).transition_to(ModelStage.PRODUCTION)
        )
        await uow.models.add(current)
        use_case, _ = _use_case(uow)

        with pytest.raises(NoPriorProductionVersionError):
            await use_case.execute(RollbackDeploymentCommand(category=_CATEGORY))
