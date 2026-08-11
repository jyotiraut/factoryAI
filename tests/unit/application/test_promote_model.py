"""Unit tests for the promotion use case, against fakes."""

from __future__ import annotations

import pytest

from factoryai.application.use_cases.promote_model import (
    PromoteModel,
    PromoteModelCommand,
    PromotionGate,
)
from factoryai.domain.errors import PromotionRejectedError
from factoryai.domain.value_objects import Category, ModelStage
from tests.builders import NOW, a_model_version, some_metrics
from tests.fakes import FakeClock, FakeIdGenerator, FakeModelRegistry, FakeUnitOfWork
from tests.use_case_factory import make_promote_model_use_case

pytestmark = pytest.mark.unit

_CATEGORY = Category("bottle")
_GATE = PromotionGate(min_auroc=0.95, improvement_margin=0.005, max_recall_regression=0.01)


def _use_case(
    uow: FakeUnitOfWork, registry: FakeModelRegistry | None = None
) -> tuple[PromoteModel, FakeModelRegistry]:
    registry = registry or FakeModelRegistry()
    use_case = make_promote_model_use_case(
        uow=uow,
        model_registry=registry,
        clock=FakeClock(NOW),
        id_generator=FakeIdGenerator(),
        gate=_GATE,
    )
    return use_case, registry


class TestFirstPromotion:
    async def test_a_qualifying_candidate_with_no_incumbent_is_promoted(self) -> None:
        uow = FakeUnitOfWork()
        candidate = a_model_version(metrics=some_metrics(image_auroc=0.98, recall=0.95))
        await uow.models.add(candidate)
        use_case, registry = _use_case(uow)

        result = await use_case.execute(
            PromoteModelCommand(category=_CATEGORY, candidate_model_version_id=candidate.id)
        )

        assert result.model_version_id == candidate.id
        assert result.previous_model_version_id is None
        promoted = await uow.models.get(candidate.id)
        assert promoted.stage is ModelStage.PRODUCTION
        assert registry.get_stage_version(name=candidate.registry_name, stage=ModelStage.PRODUCTION)

    async def test_below_the_absolute_floor_is_rejected(self) -> None:
        uow = FakeUnitOfWork()
        candidate = a_model_version(metrics=some_metrics(image_auroc=0.90, recall=0.95))
        await uow.models.add(candidate)
        use_case, _ = _use_case(uow)

        with pytest.raises(PromotionRejectedError) as exc_info:
            await use_case.execute(
                PromoteModelCommand(category=_CATEGORY, candidate_model_version_id=candidate.id)
            )

        assert "minimum" in exc_info.value.reasons[0]
        unchanged = await uow.models.get(candidate.id)
        assert unchanged.stage is ModelStage.DEVELOPMENT

    async def test_a_rejected_candidate_is_still_recorded_as_a_deployment(self) -> None:
        uow = FakeUnitOfWork()
        candidate = a_model_version(metrics=some_metrics(image_auroc=0.90, recall=0.95))
        await uow.models.add(candidate)
        use_case, _ = _use_case(uow)

        with pytest.raises(PromotionRejectedError):
            await use_case.execute(
                PromoteModelCommand(category=_CATEGORY, candidate_model_version_id=candidate.id)
            )

        deployments = await uow.models.list_deployments(_CATEGORY, environment="production")
        assert len(deployments) == 1
        assert deployments[0].action.value == "reject"
        assert deployments[0].comparison_report["passed"] is False


class TestReplacingAnIncumbent:
    async def test_a_better_candidate_replaces_and_archives_the_incumbent(self) -> None:
        uow = FakeUnitOfWork()
        incumbent = (
            a_model_version(registry_version=1, metrics=some_metrics(image_auroc=0.96, recall=0.90))
            .transition_to(ModelStage.STAGING)
            .transition_to(ModelStage.PRODUCTION)
        )
        await uow.models.add(incumbent)
        candidate = a_model_version(
            registry_version=2, metrics=some_metrics(image_auroc=0.975, recall=0.92)
        )
        await uow.models.add(candidate)
        use_case, registry = _use_case(uow)

        result = await use_case.execute(
            PromoteModelCommand(category=_CATEGORY, candidate_model_version_id=candidate.id)
        )

        assert result.previous_model_version_id == incumbent.id
        assert (await uow.models.get(candidate.id)).stage is ModelStage.PRODUCTION
        assert (await uow.models.get(incumbent.id)).stage is ModelStage.ARCHIVED
        assert registry.get_stage_version(name=candidate.registry_name, stage=ModelStage.PRODUCTION)
        assert registry.get_stage_version(name=incumbent.registry_name, stage=ModelStage.ARCHIVED)

    async def test_insufficient_improvement_margin_is_rejected(self) -> None:
        uow = FakeUnitOfWork()
        incumbent = (
            a_model_version(registry_version=1, metrics=some_metrics(image_auroc=0.96, recall=0.90))
            .transition_to(ModelStage.STAGING)
            .transition_to(ModelStage.PRODUCTION)
        )
        await uow.models.add(incumbent)
        # Above the incumbent, but not by the required 0.005 margin.
        candidate = a_model_version(
            registry_version=2, metrics=some_metrics(image_auroc=0.962, recall=0.90)
        )
        await uow.models.add(candidate)
        use_case, _ = _use_case(uow)

        with pytest.raises(PromotionRejectedError) as exc_info:
            await use_case.execute(
                PromoteModelCommand(category=_CATEGORY, candidate_model_version_id=candidate.id)
            )

        assert "margin" in exc_info.value.reasons[0]

    async def test_a_recall_regression_rejects_even_with_higher_auroc(self) -> None:
        uow = FakeUnitOfWork()
        incumbent = (
            a_model_version(registry_version=1, metrics=some_metrics(image_auroc=0.95, recall=0.95))
            .transition_to(ModelStage.STAGING)
            .transition_to(ModelStage.PRODUCTION)
        )
        await uow.models.add(incumbent)
        candidate = a_model_version(
            registry_version=2, metrics=some_metrics(image_auroc=0.99, recall=0.80)
        )
        await uow.models.add(candidate)
        use_case, _ = _use_case(uow)

        with pytest.raises(PromotionRejectedError) as exc_info:
            await use_case.execute(
                PromoteModelCommand(category=_CATEGORY, candidate_model_version_id=candidate.id)
            )

        assert any("regresses" in reason for reason in exc_info.value.reasons)
        assert (await uow.models.get(incumbent.id)).stage is ModelStage.PRODUCTION


class TestArchivedCandidateCanBeRestored:
    async def test_an_archived_candidate_can_be_promoted_directly(self) -> None:
        uow = FakeUnitOfWork()
        candidate = (
            a_model_version(metrics=some_metrics(image_auroc=0.99, recall=0.95))
            .transition_to(ModelStage.STAGING)
            .transition_to(ModelStage.ARCHIVED)
        )
        await uow.models.add(candidate)
        use_case, _ = _use_case(uow)

        await use_case.execute(
            PromoteModelCommand(category=_CATEGORY, candidate_model_version_id=candidate.id)
        )

        assert (await uow.models.get(candidate.id)).stage is ModelStage.PRODUCTION


class TestAuditTrail:
    async def test_a_successful_promotion_appends_an_audit_event(self) -> None:
        uow = FakeUnitOfWork()
        candidate = a_model_version(metrics=some_metrics(image_auroc=0.98, recall=0.95))
        await uow.models.add(candidate)
        use_case, _ = _use_case(uow)

        await use_case.execute(
            PromoteModelCommand(category=_CATEGORY, candidate_model_version_id=candidate.id)
        )

        latest = await uow.audit.latest()
        assert latest is not None
        assert latest.action == "model.promote"
