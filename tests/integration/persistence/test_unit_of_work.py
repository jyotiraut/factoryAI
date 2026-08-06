"""Integration tests for :class:`SqlAlchemyUnitOfWork` transactional semantics."""

from __future__ import annotations

import pytest

from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.value_objects import Checksum
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.builders import an_image

pytestmark = pytest.mark.integration


async def test_commit_persists_across_transactions(uow: SqlAlchemyUnitOfWork) -> None:
    image = an_image()
    async with uow:
        await uow.images.add(image)
        await uow.commit()

    async with uow:
        await uow.images.get(image.id)  # raises if not persisted


async def test_forgetting_commit_rolls_back(uow: SqlAlchemyUnitOfWork) -> None:
    image = an_image()
    async with uow:
        await uow.images.add(image)
        # deliberately no commit()

    async with uow:
        with pytest.raises(EntityNotFoundError):
            await uow.images.get(image.id)


async def test_an_exception_rolls_back_even_after_a_commit_call(
    uow: SqlAlchemyUnitOfWork,
) -> None:
    """An exception after commit() must still discard the transaction.

    ``commit()`` only flushes and marks intent; ``__aexit__`` sees the exception and rolls
    back regardless — this is what makes "commit, then something else in the block blows
    up" safe rather than a half-applied write.
    """
    image = an_image()
    with pytest.raises(RuntimeError, match="boom"):
        async with uow:
            await uow.images.add(image)
            await uow.commit()
            raise RuntimeError("boom")

    async with uow:
        with pytest.raises(EntityNotFoundError):
            await uow.images.get(image.id)


async def test_explicit_rollback_discards_pending_changes_within_the_block(
    uow: SqlAlchemyUnitOfWork,
) -> None:
    image = an_image()
    async with uow:
        await uow.images.add(image)
        await uow.rollback()

    async with uow:
        with pytest.raises(EntityNotFoundError):
            await uow.images.get(image.id)


async def test_using_the_unit_of_work_outside_a_context_block_raises(
    uow: SqlAlchemyUnitOfWork,
) -> None:
    with pytest.raises(RuntimeError, match="async with"):
        await uow.commit()


async def test_two_sequential_transactions_are_independent(uow: SqlAlchemyUnitOfWork) -> None:
    first = an_image()
    second = an_image(checksum=Checksum("2" * 64))

    async with uow:
        await uow.images.add(first)
        await uow.commit()

    async with uow:
        await uow.images.add(second)
        await uow.commit()

    async with uow:
        await uow.images.get(first.id)
        await uow.images.get(second.id)
