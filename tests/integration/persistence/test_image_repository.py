"""Integration tests for :class:`SqlAlchemyImageRepository` against real PostgreSQL."""

from __future__ import annotations

import uuid

import pytest

from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.value_objects import Category, Checksum, ImageId, ProcessingStatus
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.builders import an_image

pytestmark = pytest.mark.integration


async def test_add_then_get_round_trips(uow: SqlAlchemyUnitOfWork) -> None:
    image = an_image()
    async with uow:
        await uow.images.add(image)
        await uow.commit()

    async with uow:
        fetched = await uow.images.get(image.id)
    assert fetched == image


async def test_get_raises_when_missing(uow: SqlAlchemyUnitOfWork) -> None:
    async with uow:
        with pytest.raises(EntityNotFoundError):
            await uow.images.get(ImageId(uuid.uuid4()))


async def test_update_persists_a_status_transition(uow: SqlAlchemyUnitOfWork) -> None:
    image = an_image()
    async with uow:
        await uow.images.add(image)
        await uow.commit()

    validating = image.transition_to(ProcessingStatus.VALIDATING)
    async with uow:
        await uow.images.update(validating)
        await uow.commit()

    async with uow:
        fetched = await uow.images.get(image.id)
    assert fetched.status is ProcessingStatus.VALIDATING


async def test_rollback_discards_an_uncommitted_add(uow: SqlAlchemyUnitOfWork) -> None:
    image = an_image()
    async with uow:
        await uow.images.add(image)
        # No commit() call: exiting the block must roll back.

    async with uow:
        with pytest.raises(EntityNotFoundError):
            await uow.images.get(image.id)


async def test_find_by_checksum_locates_an_exact_duplicate(uow: SqlAlchemyUnitOfWork) -> None:
    image = an_image()
    async with uow:
        await uow.images.add(image)
        await uow.commit()

    async with uow:
        found = await uow.images.find_by_checksum(image.checksum)
        missing = await uow.images.find_by_checksum(Checksum("f" * 64))
    assert found == image
    assert missing is None


async def test_checksum_uniqueness_is_enforced_by_the_database(uow: SqlAlchemyUnitOfWork) -> None:
    """The unique constraint on checksum_sha256 is the real backstop for duplicate detection."""
    checksum = Checksum("a" * 64)
    first = an_image(checksum=checksum)
    second = an_image(checksum=checksum)

    async with uow:
        await uow.images.add(first)
        await uow.commit()

    with pytest.raises(Exception, match=r"unique|duplicate"):
        async with uow:
            await uow.images.add(second)
            await uow.commit()


async def test_find_near_duplicates_uses_hamming_distance(uow: SqlAlchemyUnitOfWork) -> None:
    close = an_image(checksum=Checksum("1" * 64), perceptual_hash="ff00")
    far = an_image(checksum=Checksum("2" * 64), perceptual_hash="0000")

    async with uow:
        await uow.images.add(close)
        await uow.images.add(far)
        await uow.commit()

    async with uow:
        matches = await uow.images.find_near_duplicates("ff01", max_distance=2)

    matched_ids = {match.id for match in matches}
    assert close.id in matched_ids
    assert far.id not in matched_ids


async def test_list_trainable_returns_only_valid_images_in_category(
    uow: SqlAlchemyUnitOfWork,
) -> None:
    trainable = an_image(checksum=Checksum("1" * 64), status=ProcessingStatus.VALID)
    pending = an_image(checksum=Checksum("2" * 64), status=ProcessingStatus.PENDING)

    async with uow:
        await uow.images.add(trainable)
        await uow.images.add(pending)
        await uow.commit()

    async with uow:
        images = await uow.images.list_trainable(Category("bottle"))

    ids = {image.id for image in images}
    assert trainable.id in ids
    assert pending.id not in ids


async def test_count_by_status_is_zero_filled_for_absent_statuses(
    uow: SqlAlchemyUnitOfWork,
) -> None:
    async with uow:
        await uow.images.add(an_image(checksum=Checksum("3" * 64)))
        await uow.commit()

    async with uow:
        counts = await uow.images.count_by_status(Category("bottle"))

    assert counts[ProcessingStatus.PENDING.value] == 1
    assert counts[ProcessingStatus.VALID.value] == 0
    assert set(counts) == {status.value for status in ProcessingStatus}
