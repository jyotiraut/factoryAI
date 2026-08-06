"""Integration tests for :class:`SqlAlchemyDriftReportRepository` against real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from factoryai.domain.entities import DatasetMember
from factoryai.domain.value_objects import Checksum, DatasetSplit, DatasetVersionId, ModelVersionId
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.builders import (
    a_dataset,
    a_dataset_version,
    a_drift_report,
    a_drift_signal,
    a_model_version,
    an_experiment,
    an_image,
)

pytestmark = pytest.mark.integration


async def _seed_model(uow: SqlAlchemyUnitOfWork) -> tuple[ModelVersionId, DatasetVersionId]:
    image = an_image(checksum=Checksum("6" * 64))
    dataset = a_dataset()
    version = a_dataset_version(
        dataset_id=dataset.id, members=(DatasetMember(image.id, DatasetSplit.TRAIN),)
    )
    experiment = an_experiment(dataset_version_id=version.id)
    model = a_model_version(experiment_id=experiment.id)
    async with uow:
        await uow.images.add(image)
        await uow.datasets.add_dataset(dataset)
        await uow.datasets.add_version(version)
        await uow.experiments.add(experiment)
        await uow.models.add(model)
        await uow.commit()
    return model.id, version.id


async def test_add_then_latest_round_trips_signals(uow: SqlAlchemyUnitOfWork) -> None:
    model_id, version_id = await _seed_model(uow)
    report = a_drift_report(
        model_version_id=model_id,
        reference_dataset_version_id=version_id,
        signals=(a_drift_signal(statistic=0.2), a_drift_signal(name="other", statistic=0.05)),
    )

    async with uow:
        await uow.drift_reports.add(report)
        await uow.commit()

    async with uow:
        fetched = await uow.drift_reports.latest(model_id)
    assert fetched == report
    assert fetched is not None
    assert fetched.severity == report.severity


async def test_latest_returns_none_without_reports(uow: SqlAlchemyUnitOfWork) -> None:
    async with uow:
        result = await uow.drift_reports.latest(ModelVersionId(uuid.uuid4()))
    assert result is None


async def test_latest_picks_the_most_recent_report(uow: SqlAlchemyUnitOfWork) -> None:
    model_id, version_id = await _seed_model(uow)
    older = a_drift_report(
        model_version_id=model_id,
        reference_dataset_version_id=version_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer = a_drift_report(
        model_version_id=model_id,
        reference_dataset_version_id=version_id,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )

    async with uow:
        await uow.drift_reports.add(older)
        await uow.drift_reports.add(newer)
        await uow.commit()

    async with uow:
        fetched = await uow.drift_reports.latest(model_id)

    assert fetched is not None
    assert fetched.id == newer.id
