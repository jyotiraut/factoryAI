"""Unit tests for the ``VerifyAuditChain`` use case, against fakes."""

from __future__ import annotations

import dataclasses
from typing import cast

import pytest

from factoryai.application.use_cases.verify_audit_chain import VerifyAuditChain
from factoryai.domain.entities import AuditEvent
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.value_objects import AuditSequence
from tests.builders import NOW
from tests.fakes import FakeAuditRepository, FakeUnitOfWork

pytestmark = pytest.mark.unit


async def _append(uow: FakeUnitOfWork, action: str) -> AuditEvent:
    latest = await uow.audit.latest()
    event = AuditEvent(
        sequence=AuditSequence((latest.sequence + 1) if latest else 1),
        action=action,
        resource_type="user",
        occurred_at=NOW,
        prev_hash=latest.row_hash() if latest else GENESIS_HASH,
    )
    await uow.audit.append(event)
    return event


class TestVerifyAuditChain:
    async def test_an_empty_chain_is_intact(self) -> None:
        uow = FakeUnitOfWork()
        use_case = VerifyAuditChain(uow_factory=lambda: uow)

        result = await use_case.execute()

        assert result.total_events == 0
        assert result.is_intact is True
        assert result.first_broken_sequence is None

    async def test_an_untouched_chain_is_intact(self) -> None:
        uow = FakeUnitOfWork()
        await _append(uow, "user.registered")
        await _append(uow, "user.logged_in")
        await _append(uow, "user.logged_out")
        use_case = VerifyAuditChain(uow_factory=lambda: uow)

        result = await use_case.execute()

        assert result.total_events == 3
        assert result.is_intact is True

    async def test_tampering_a_non_tip_record_is_detected(self) -> None:
        uow = FakeUnitOfWork()
        await _append(uow, "user.registered")
        await _append(uow, "user.logged_in")
        await _append(uow, "user.logged_out")
        # Simulate a row edited after the fact (bypassing the immutability trigger a real
        # database enforces) by replacing it in the fake's in-memory list directly.
        audit = cast(FakeAuditRepository, uow.audit)
        audit._events[1] = dataclasses.replace(audit._events[1], action="user.tampered")
        use_case = VerifyAuditChain(uow_factory=lambda: uow)

        result = await use_case.execute()

        assert result.is_intact is False
        assert result.first_broken_sequence == 3
