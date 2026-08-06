"""Integration tests for :class:`SqlAlchemyExperimentRepository` against real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from factoryai.domain.entities import DatasetMember
from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.value_objects import (
    Checksum,
    DatasetSplit,
    DatasetVersionId,
    ExperimentId,
    ExperimentStatus,
)
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.builders import a_dataset, a_dataset_version, an_experiment, an_image, some_metrics

pytestmark = pytest.mark.integration


async def _seed_dataset_version(uow: SqlAlchemyUnitOfWork) -> DatasetVersionId:
    unique = uuid.uuid4().hex
    image = an_image(checksum=Checksum(unique + unique[:32]))
    dataset = a_dataset(name=f"bottle-production-{unique}")
    version = a_dataset_version(
        dataset_id=dataset.id,
        version_tag=f"v-{unique}",
        members=(DatasetMember(image.id, DatasetSplit.TRAIN),),
    )
    async with uow:
        await uow.images.add(image)
        await uow.datasets.add_dataset(dataset)
        await uow.datasets.add_version(version)
        await uow.commit()
    return version.id


async def test_add_then_get_round_trips_a_running_experiment(
    uow: SqlAlchemyUnitOfWork,
) -> None:
    version_id = await _seed_dataset_version(uow)
    experiment = an_experiment(dataset_version_id=version_id)

    async with uow:
        await uow.experiments.add(experiment)
        await uow.commit()

    async with uow:
        fetched = await uow.experiments.get(experiment.id)
    assert fetched == experiment


async def test_get_raises_when_missing(uow: SqlAlchemyUnitOfWork) -> None:
    async with uow:
        with pytest.raises(EntityNotFoundError):
            await uow.experiments.get(ExperimentId(uuid.uuid4()))


async def test_update_persists_completion_and_metrics(uow: SqlAlchemyUnitOfWork) -> None:
    version_id = await _seed_dataset_version(uow)
    experiment = an_experiment(dataset_version_id=version_id)
    async with uow:
        await uow.experiments.add(experiment)
        await uow.commit()

    completed = experiment.complete(some_metrics(), datetime(2026, 8, 5, 13, 0, tzinfo=UTC))
    async with uow:
        await uow.experiments.update(completed)
        await uow.commit()

    async with uow:
        fetched = await uow.experiments.get(experiment.id)
    assert fetched.status is ExperimentStatus.COMPLETED
    assert fetched.metrics == some_metrics()


async def test_list_for_dataset_version_filters_correctly(uow: SqlAlchemyUnitOfWork) -> None:
    version_id = await _seed_dataset_version(uow)
    other_version_id = await _seed_dataset_version(uow)
    matching = an_experiment(dataset_version_id=version_id, mlflow_run_id="run-a")
    other = an_experiment(dataset_version_id=other_version_id, mlflow_run_id="run-b")

    async with uow:
        await uow.experiments.add(matching)
        await uow.experiments.add(other)
        await uow.commit()

    async with uow:
        results = await uow.experiments.list_for_dataset_version(version_id)

    ids = {experiment.id for experiment in results}
    assert matching.id in ids
    assert other.id not in ids
