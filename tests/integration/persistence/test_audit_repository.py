"""Integration tests for :class:`SqlAlchemyAuditRepository` against real PostgreSQL.

The chain-integrity behaviour here is the point of the whole table (ADR: audit hash
chain), so it gets more scrutiny than a typical CRUD repository.
"""

from __future__ import annotations

import pytest

from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.value_objects import AuditSequence
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from factoryai.shared.errors import TransientError
from tests.builders import an_audit_event

pytestmark = pytest.mark.integration


async def test_latest_is_none_before_anything_is_appended(uow: SqlAlchemyUnitOfWork) -> None:
    async with uow:
        assert await uow.audit.latest() is None


async def test_append_the_genesis_event(uow: SqlAlchemyUnitOfWork) -> None:
    event = an_audit_event()
    async with uow:
        await uow.audit.append(event)
        await uow.commit()

    async with uow:
        latest = await uow.audit.latest()
    assert latest == event


async def test_a_correctly_chained_second_event_is_accepted(uow: SqlAlchemyUnitOfWork) -> None:
    first = an_audit_event()
    async with uow:
        await uow.audit.append(first)
        await uow.commit()

    second = an_audit_event(
        sequence=AuditSequence(2), action="model.promoted", prev_hash=first.row_hash()
    )
    async with uow:
        await uow.audit.append(second)
        await uow.commit()

    async with uow:
        latest = await uow.audit.latest()
    assert latest == second


async def test_append_rejects_a_stale_sequence(uow: SqlAlchemyUnitOfWork) -> None:
    """Simulates a caller that read the chain head, then someone else appended first."""
    first = an_audit_event()
    async with uow:
        await uow.audit.append(first)
        await uow.commit()

    stale = an_audit_event(sequence=AuditSequence(1), prev_hash=GENESIS_HASH)
    with pytest.raises(TransientError, match="chain head moved"):
        async with uow:
            await uow.audit.append(stale)
            await uow.commit()


async def test_append_rejects_a_mismatched_prev_hash(uow: SqlAlchemyUnitOfWork) -> None:
    first = an_audit_event()
    async with uow:
        await uow.audit.append(first)
        await uow.commit()

    wrong_link = an_audit_event(sequence=AuditSequence(2), prev_hash="f" * 64)
    with pytest.raises(TransientError):
        async with uow:
            await uow.audit.append(wrong_link)
            await uow.commit()


async def test_list_for_resource_filters_and_orders_newest_first(
    uow: SqlAlchemyUnitOfWork,
) -> None:
    first = an_audit_event(action="image.ingested", resource_type="image", resource_id="img-1")
    async with uow:
        await uow.audit.append(first)
        await uow.commit()

    second = an_audit_event(
        sequence=AuditSequence(2),
        action="image.quarantined",
        resource_type="image",
        resource_id="img-1",
        prev_hash=first.row_hash(),
    )
    unrelated = an_audit_event(
        sequence=AuditSequence(3),
        action="model.promoted",
        resource_type="model_version",
        resource_id="model-1",
        prev_hash=second.row_hash(),
    )
    async with uow:
        await uow.audit.append(second)
        await uow.commit()
    async with uow:
        await uow.audit.append(unrelated)
        await uow.commit()

    async with uow:
        trail = await uow.audit.list_for_resource("image", "img-1")

    assert [event.action for event in trail] == ["image.quarantined", "image.ingested"]
