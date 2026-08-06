"""Integration tests for :class:`SqlAlchemyModelRepository` against real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from factoryai.domain.entities import DatasetMember
from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.value_objects import (
    Category,
    Checksum,
    DatasetSplit,
    DeploymentAction,
    ExperimentId,
    ModelStage,
    ModelVersionId,
)
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.builders import (
    a_dataset,
    a_dataset_version,
    a_deployment,
    a_model_version,
    an_experiment,
    an_image,
)

pytestmark = pytest.mark.integration


async def _seed_experiment(uow: SqlAlchemyUnitOfWork) -> ExperimentId:
    image = an_image(checksum=Checksum("8" * 64))
    dataset = a_dataset()
    version = a_dataset_version(
        dataset_id=dataset.id, members=(DatasetMember(image.id, DatasetSplit.TRAIN),)
    )
    experiment = an_experiment(dataset_version_id=version.id)
    async with uow:
        await uow.images.add(image)
        await uow.datasets.add_dataset(dataset)
        await uow.datasets.add_version(version)
        await uow.experiments.add(experiment)
        await uow.commit()
    return experiment.id


async def test_add_then_get_round_trips(uow: SqlAlchemyUnitOfWork) -> None:
    experiment_id = await _seed_experiment(uow)
    model = a_model_version(experiment_id=experiment_id)

    async with uow:
        await uow.models.add(model)
        await uow.commit()

    async with uow:
        fetched = await uow.models.get(model.id)
    assert fetched == model


async def test_get_raises_when_missing(uow: SqlAlchemyUnitOfWork) -> None:
    async with uow:
        with pytest.raises(EntityNotFoundError):
            await uow.models.get(ModelVersionId(uuid.uuid4()))


async def test_update_persists_a_stage_transition(uow: SqlAlchemyUnitOfWork) -> None:
    experiment_id = await _seed_experiment(uow)
    model = a_model_version(experiment_id=experiment_id)
    async with uow:
        await uow.models.add(model)
        await uow.commit()

    staged = model.transition_to(ModelStage.STAGING)
    async with uow:
        await uow.models.update(staged)
        await uow.commit()

    async with uow:
        fetched = await uow.models.get(model.id)
    assert fetched.stage is ModelStage.STAGING


async def test_find_by_stage_returns_the_current_occupant(uow: SqlAlchemyUnitOfWork) -> None:
    experiment_id = await _seed_experiment(uow)
    model = a_model_version(experiment_id=experiment_id).transition_to(ModelStage.STAGING)
    promoted = model.transition_to(ModelStage.PRODUCTION)

    async with uow:
        await uow.models.add(model)
        await uow.commit()
    async with uow:
        await uow.models.update(promoted)
        await uow.commit()

    async with uow:
        production = await uow.models.find_by_stage(Category("bottle"), ModelStage.PRODUCTION)
        staging = await uow.models.find_by_stage(Category("bottle"), ModelStage.STAGING)

    assert production is not None
    assert production.id == model.id
    assert staging is None


async def test_list_versions_orders_newest_first(uow: SqlAlchemyUnitOfWork) -> None:
    experiment_id = await _seed_experiment(uow)
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    first = a_model_version(experiment_id=experiment_id, registry_version=1, created_at=now)
    second = a_model_version(
        experiment_id=experiment_id, registry_version=2, created_at=now + timedelta(minutes=1)
    )

    async with uow:
        await uow.models.add(first)
        await uow.commit()
    async with uow:
        await uow.models.add(second)
        await uow.commit()

    async with uow:
        versions = await uow.models.list_versions(Category("bottle"))

    assert [version.registry_version for version in versions] == [2, 1]


async def test_deployment_history_filters_by_environment_and_category(
    uow: SqlAlchemyUnitOfWork,
) -> None:
    experiment_id = await _seed_experiment(uow)
    model = a_model_version(experiment_id=experiment_id)
    deployment = a_deployment(
        model_version_id=model.id, action=DeploymentAction.PROMOTE, environment="production"
    )

    async with uow:
        await uow.models.add(model)
        await uow.models.add_deployment(deployment)
        await uow.commit()

    async with uow:
        history = await uow.models.list_deployments(Category("bottle"), environment="production")
        empty = await uow.models.list_deployments(Category("bottle"), environment="staging")

    assert [entry.id for entry in history] == [deployment.id]
    assert empty == []
