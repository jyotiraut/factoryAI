"""The audit tamper-detection use case (Phase 8's explicit exit criterion).

Reads the *entire* chain and recomputes every link — see
:func:`~factoryai.domain.entities.audit.verify_chain` for the algorithm. This catches a
tampered or deleted row anywhere except the current chain tip: altering or removing
record N invalidates record N+1's ``prev_hash``, but the very last record has no successor
to check it against, the same limitation any hash chain (including git's own commit
history) has without an external anchor. In practice the tip does not stay the tip for
long — every subsequent event re-anchors it — so this is a deliberate, documented gap, not
an oversight (see ``docs/adr/0011-jwt-auth-and-rbac.md``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factoryai.domain.entities.audit import verify_chain
from factoryai.domain.ports.repositories import UnitOfWork


@dataclass(frozen=True, slots=True)
class AuditChainVerificationResult:
    """The outcome of walking the entire audit chain.

    Attributes:
        total_events: How many records were examined.
        is_intact: Whether every link recomputed correctly.
        first_broken_sequence: The earliest sequence number whose link is broken, or
            ``None`` if the chain is intact.
    """

    total_events: int
    is_intact: bool
    first_broken_sequence: int | None


class VerifyAuditChain:
    """Walks the whole audit log and reports the first broken link, if any."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Initialise with the unit-of-work factory this use case reads through."""
        self._uow_factory = uow_factory

    async def execute(self) -> AuditChainVerificationResult:
        """Verify the chain end to end."""
        async with self._uow_factory() as uow:
            events = await uow.audit.list_all()
        broken_at = verify_chain(events)
        return AuditChainVerificationResult(
            total_events=len(events), is_intact=broken_at is None, first_broken_sequence=broken_at
        )
