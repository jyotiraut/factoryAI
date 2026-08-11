"""Integration tests for :class:`PromoteModel` against real PostgreSQL.

This is what caught a real bug during Phase 6's live verification that every unit test
against :class:`~tests.fakes.FakeUnitOfWork` missed: raising ``PromotionRejectedError``
*inside* the ``async with self._uow_factory() as uow:`` block, after already calling
``uow.commit()``, rolled the transaction back anyway — ``SqlAlchemyUnitOfWork.__aexit__``
only commits when ``exc is None``, and an exception propagating out of the block means it
is not. ``FakeUnitOfWork.__aexit__`` is a no-op regardless of exceptions, so it could never
have caught this; only a real transactional database can.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from factoryai.application.use_cases.promote_model import (
    PromoteModel,
    PromoteModelCommand,
    PromotionGate,
)
from factoryai.domain.entities import DatasetMember, DatasetVersion, ModelVersion
from factoryai.domain.errors import PromotionRejectedError
from factoryai.domain.value_objects import Category, DatasetSplit, DatasetVersionId, ModelStage
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.builders import a_dataset, a_model_version, an_experiment, an_image, some_metrics
from tests.fakes import FakeClock, FakeIdGenerator, FakeModelRegistry

pytestmark = pytest.mark.integration

_CATEGORY = Category("bottle")
_NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


async def _seed_model_version(
    uow: SqlAlchemyUnitOfWork, *, image_auroc: float, recall: float
) -> ModelVersion:
    """Seed the full real chain (image -> dataset version -> experiment) a model version needs."""
    async with uow:
        image = an_image()
        await uow.images.add(image)
        dataset = a_dataset(name=f"bottle-{uuid.uuid4()}")
        await uow.datasets.add_dataset(dataset)
        version = DatasetVersion(
            id=DatasetVersionId(uuid.uuid4()),
            dataset_id=dataset.id,
            version_tag=f"tag-{uuid.uuid4()}",
            dvc_hash="d" * 32,
            git_commit="a" * 40,
            members=(DatasetMember(image.id, DatasetSplit.TRAIN),),
            created_at=_NOW,
        )
        await uow.datasets.add_version(version)
        experiment = an_experiment(
            dataset_version_id=version.id, mlflow_run_id=f"run-{uuid.uuid4()}"
        )
        await uow.experiments.add(experiment)
        model = a_model_version(
            experiment_id=experiment.id,
            registry_name=f"factoryai-test-{uuid.uuid4()}",
            metrics=some_metrics(image_auroc=image_auroc, recall=recall),
        )
        await uow.models.add(model)
        await uow.commit()
    return model


class TestRejectionSurvivesTheTransaction:
    async def test_a_rejected_candidates_deployment_record_is_committed_not_rolled_back(
        self, uow: SqlAlchemyUnitOfWork
    ) -> None:
        weak_candidate = await _seed_model_version(uow, image_auroc=0.80, recall=0.60)
        use_case = PromoteModel(
            uow_factory=lambda: uow,
            model_registry=FakeModelRegistry(),
            gate=PromotionGate(),
            clock=FakeClock(_NOW),
            id_generator=FakeIdGenerator(),
        )

        with pytest.raises(PromotionRejectedError):
            await use_case.execute(
                PromoteModelCommand(
                    category=_CATEGORY, candidate_model_version_id=weak_candidate.id
                )
            )

        async with uow:
            deployments = await uow.models.list_deployments(_CATEGORY, environment="production")
            latest = await uow.audit.latest()

        assert any(d.action.value == "reject" for d in deployments)
        assert latest is not None
        assert latest.action == "model.reject"

        async with uow:
            unchanged = await uow.models.get(weak_candidate.id)
        assert unchanged.stage is ModelStage.DEVELOPMENT
