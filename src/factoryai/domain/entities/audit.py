"""Immutable, hash-chained audit records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from typing import Any

from factoryai.domain.errors import InvariantViolationError
from factoryai.domain.value_objects import AuditSequence, UserId

GENESIS_HASH = "0" * 64
"""Predecessor hash of the first record in a chain."""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One tamper-evident record of something that happened.

    Each record hashes its own contents together with the hash of its predecessor, so the
    log forms a chain. Altering or removing any record invalidates every hash after it,
    which turns "the audit log was edited" from an undetectable event into an obvious one.
    A database trigger computes the chain, so the application cannot forge a link even if
    it wanted to.

    Attributes:
        sequence: Monotonic position in the chain, starting at 1.
        action: What happened, e.g. ``"model.promoted"``.
        resource_type: The kind of entity affected, e.g. ``"model_version"``.
        occurred_at: Timezone-aware timestamp.
        prev_hash: Hash of the preceding record, or :data:`GENESIS_HASH` for the first.
        actor_id: Who did it; absent for automated actions.
        resource_id: Identifier of the affected entity.
        payload: Structured detail. Must never contain secrets or raw credentials.
        correlation_id: Ties this record to the originating request.
    """

    sequence: AuditSequence
    action: str
    resource_type: str
    occurred_at: datetime
    prev_hash: str = GENESIS_HASH
    actor_id: UserId | None = None
    resource_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        """Validate the sequence, action, predecessor hash and timestamp.

        Raises:
            InvariantViolationError: If the sequence is not positive, the action or resource
                type is blank, the predecessor hash is malformed, or the timestamp is
                naive.
        """
        if self.sequence < 1:
            raise InvariantViolationError(
                "audit sequence must start at 1",
                code="audit.invalid_sequence",
                details={"sequence": self.sequence},
            )
        if not self.action.strip():
            raise InvariantViolationError("audit action must not be blank", code="audit.no_action")
        if not self.resource_type.strip():
            raise InvariantViolationError(
                "audit resource_type must not be blank", code="audit.no_resource_type"
            )
        if len(self.prev_hash) != len(GENESIS_HASH):
            raise InvariantViolationError(
                "prev_hash must be a 64-character digest",
                code="audit.malformed_prev_hash",
            )
        if self.occurred_at.tzinfo is None:
            raise InvariantViolationError(
                "occurred_at must be timezone-aware", code="audit.naive_timestamp"
            )

    def row_hash(self) -> str:
        """Compute this record's hash over its contents and its predecessor's.

        The payload is serialised with sorted keys so that two logically identical records
        always hash identically, regardless of dictionary insertion order.

        Returns:
            A 64-character lowercase hexadecimal digest.
        """
        canonical = json.dumps(
            {
                "sequence": int(self.sequence),
                "action": self.action,
                "resource_type": self.resource_type,
                "resource_id": self.resource_id,
                "actor_id": str(self.actor_id) if self.actor_id else None,
                "occurred_at": self.occurred_at.isoformat(),
                "payload": self.payload,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def follows(self, previous: AuditEvent) -> bool:
        """Return whether this record correctly chains onto ``previous``.

        Both the sequence numbering and the hash linkage must agree; checking only one
        would let a record be renumbered or re-parented undetected.
        """
        return self.sequence == previous.sequence + 1 and self.prev_hash == previous.row_hash()

    @property
    def is_automated(self) -> bool:
        """Return whether the platform performed this action without a human actor."""
        return self.actor_id is None


def verify_chain(events: list[AuditEvent]) -> int | None:
    """Verify that a sequence of audit records forms an unbroken chain.

    Args:
        events: Records in ascending sequence order.

    Returns:
        The sequence number of the first record that fails verification, or ``None`` if
        the whole chain is intact. An empty list verifies trivially.

    Raises:
        InvariantViolationError: If the first record does not start from the genesis hash,
            which indicates the caller passed a fragment rather than a full chain.
    """
    if not events:
        return None
    if events[0].prev_hash != GENESIS_HASH:
        raise InvariantViolationError(
            "chain verification must start from the genesis record",
            code="audit.partial_chain",
            details={"first_sequence": int(events[0].sequence)},
        )
    for previous, current in pairwise(events):
        if not current.follows(previous):
            return int(current.sequence)
    return None
