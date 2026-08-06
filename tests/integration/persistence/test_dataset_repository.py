"""Integration tests for :class:`SqlAlchemyDatasetRepository` against real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from factoryai.domain.entities import DatasetMember
from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.value_objects import Checksum, DatasetId, DatasetSplit, ImageId
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.builders import a_dataset, a_dataset_version, an_image

pytestmark = pytest.mark.integration


async def _seed_images(uow: SqlAlchemyUnitOfWork, count: int) -> list[ImageId]:
    async with uow:
        images = [an_image(checksum=Checksum(f"{index:064x}")) for index in range(count)]
        for image in images:
            await uow.images.add(image)
        await uow.commit()
    return [image.id for image in images]


async def test_add_dataset_then_get_round_trips(uow: SqlAlchemyUnitOfWork) -> None:
    dataset = a_dataset()
    async with uow:
        await uow.datasets.add_dataset(dataset)
        await uow.commit()

    async with uow:
        fetched = await uow.datasets.get_dataset(dataset.id)
    assert fetched == dataset


async def test_get_dataset_raises_when_missing(uow: SqlAlchemyUnitOfWork) -> None:
    async with uow:
        with pytest.raises(EntityNotFoundError):
            await uow.datasets.get_dataset(DatasetId(uuid.uuid4()))


async def test_find_dataset_by_name(uow: SqlAlchemyUnitOfWork) -> None:
    dataset = a_dataset(name="bottle-production-v2")
    async with uow:
        await uow.datasets.add_dataset(dataset)
        await uow.commit()

    async with uow:
        found = await uow.datasets.find_dataset_by_name("bottle-production-v2")
        missing = await uow.datasets.find_dataset_by_name("does-not-exist")
    assert found == dataset
    assert missing is None


async def test_add_version_computes_and_persists_the_content_checksum(
    uow: SqlAlchemyUnitOfWork,
) -> None:
    image_ids = await _seed_images(uow, 3)
    dataset = a_dataset()
    version = a_dataset_version(
        dataset_id=dataset.id,
        members=(
            DatasetMember(image_ids[0], DatasetSplit.TRAIN),
            DatasetMember(image_ids[1], DatasetSplit.VAL),
            DatasetMember(image_ids[2], DatasetSplit.TEST),
        ),
    )

    async with uow:
        await uow.datasets.add_dataset(dataset)
        await uow.datasets.add_version(version)
        await uow.commit()

    async with uow:
        fetched = await uow.datasets.get_version(version.id)
    assert fetched.members == version.members
    assert fetched.image_count == 3


async def test_find_version_by_tag(uow: SqlAlchemyUnitOfWork) -> None:
    image_ids = await _seed_images(uow, 1)
    dataset = a_dataset()
    version = a_dataset_version(
        dataset_id=dataset.id,
        version_tag="bottle-v7",
        members=(DatasetMember(image_ids[0], DatasetSplit.TRAIN),),
    )

    async with uow:
        await uow.datasets.add_dataset(dataset)
        await uow.datasets.add_version(version)
        await uow.commit()

    async with uow:
        found = await uow.datasets.find_version_by_tag(dataset.id, "bottle-v7")
        missing = await uow.datasets.find_version_by_tag(dataset.id, "does-not-exist")
    assert found is not None
    assert found.id == version.id
    assert missing is None


async def test_list_versions_returns_newest_first(uow: SqlAlchemyUnitOfWork) -> None:
    image_ids = await _seed_images(uow, 2)
    dataset = a_dataset()
    early = a_dataset_version(
        dataset_id=dataset.id,
        version_tag="v1",
        members=(DatasetMember(image_ids[0], DatasetSplit.TRAIN),),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    late = a_dataset_version(
        dataset_id=dataset.id,
        version_tag="v2",
        members=(DatasetMember(image_ids[1], DatasetSplit.TRAIN),),
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )

    async with uow:
        await uow.datasets.add_dataset(dataset)
        await uow.datasets.add_version(early)
        await uow.datasets.add_version(late)
        await uow.commit()

    async with uow:
        versions = await uow.datasets.list_versions(dataset.id)

    assert [version.version_tag for version in versions] == ["v2", "v1"]
