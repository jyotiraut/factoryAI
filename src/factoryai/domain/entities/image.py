"""The inspection image entity and its lifecycle."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Self

from factoryai.domain.errors import IllegalStateTransitionError, InvariantViolationError
from factoryai.domain.value_objects import (
    Category,
    Checksum,
    ImageId,
    ImageLabel,
    ProcessingStatus,
    Resolution,
    StorageLocation,
)

_ALLOWED_TRANSITIONS: dict[ProcessingStatus, frozenset[ProcessingStatus]] = {
    ProcessingStatus.PENDING: frozenset({ProcessingStatus.VALIDATING, ProcessingStatus.REJECTED}),
    ProcessingStatus.VALIDATING: frozenset(
        {ProcessingStatus.VALID, ProcessingStatus.REJECTED, ProcessingStatus.QUARANTINED}
    ),
    ProcessingStatus.VALID: frozenset({ProcessingStatus.QUARANTINED, ProcessingStatus.ARCHIVED}),
    ProcessingStatus.QUARANTINED: frozenset({ProcessingStatus.VALID, ProcessingStatus.ARCHIVED}),
    ProcessingStatus.REJECTED: frozenset(),
    ProcessingStatus.ARCHIVED: frozenset(),
}
"""Permitted status transitions.

Rejected and archived are terminal. Quarantine is reversible: an image pulled out of
service for review can return to :attr:`~ProcessingStatus.VALID` once an engineer clears
it, which is how a false-positive duplicate detection gets corrected.
"""


@dataclass(frozen=True, slots=True)
class InspectionImage:
    """A single photograph of a product, with its provenance and lifecycle state.

    Instances are immutable: every state change returns a new instance rather than
    mutating in place. This keeps an entity that has been handed to another component from
    changing underneath it, and it makes the append-only persistence model
    (``docs/DATA_MODEL.md`` §2) a natural fit rather than a constraint fought against.

    Attributes:
        id: Unique identifier.
        category: The product class photographed.
        checksum: SHA-256 of the image bytes; unique across the platform.
        resolution: Pixel dimensions.
        size_bytes: Size of the stored object.
        location: Where the bytes live in object storage.
        uploaded_at: Timezone-aware ingestion timestamp.
        status: Current lifecycle state.
        label: Ground truth, when known.
        perceptual_hash: Hash used for near-duplicate detection; absent until computed.
        metadata: Free-form provenance — camera id, line id, defect subtype, batch.
    """

    id: ImageId
    category: Category
    checksum: Checksum
    resolution: Resolution
    size_bytes: int
    location: StorageLocation
    uploaded_at: datetime
    status: ProcessingStatus = ProcessingStatus.PENDING
    label: ImageLabel = ImageLabel.UNLABELED
    perceptual_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate size and timestamp invariants.

        Raises:
            InvariantViolationError: If the size is not positive or the timestamp is naive.
        """
        if self.size_bytes <= 0:
            raise InvariantViolationError(
                "image size must be positive",
                code="image.invalid_size",
                details={"size_bytes": self.size_bytes},
            )
        if self.uploaded_at.tzinfo is None:
            raise InvariantViolationError(
                "uploaded_at must be timezone-aware",
                code="image.naive_timestamp",
            )

    def transition_to(self, status: ProcessingStatus) -> Self:
        """Return a copy in a new lifecycle state.

        Args:
            status: The requested state.

        Returns:
            A new instance in ``status``, or ``self`` if already in it.

        Raises:
            IllegalStateTransitionError: If the move is not permitted from the current state.
        """
        if status is self.status:
            return self
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise IllegalStateTransitionError("InspectionImage", self.status, status)
        return dataclasses.replace(self, status=status)

    def mark_valid(self) -> Self:
        """Return a copy accepted into the dataset."""
        return self.transition_to(ProcessingStatus.VALID)

    def mark_rejected(self) -> Self:
        """Return a copy permanently rejected by validation."""
        return self.transition_to(ProcessingStatus.REJECTED)

    def quarantine(self) -> Self:
        """Return a copy withheld from training pending human review."""
        return self.transition_to(ProcessingStatus.QUARANTINED)

    def archive(self) -> Self:
        """Return a copy retired from active use but retained for audit."""
        return self.transition_to(ProcessingStatus.ARCHIVED)

    def relabel(self, label: ImageLabel) -> Self:
        """Return a copy with a corrected ground-truth label.

        Operator corrections flow through here, which is how feedback reaches the next
        training run (Phase 12).
        """
        return dataclasses.replace(self, label=label)

    def with_perceptual_hash(self, perceptual_hash: str) -> Self:
        """Return a copy carrying the computed perceptual hash."""
        return dataclasses.replace(self, perceptual_hash=perceptual_hash)

    def with_metadata(self, **entries: Any) -> Self:
        """Return a copy with additional metadata merged in.

        Existing keys are overwritten; the original instance is untouched.
        """
        return dataclasses.replace(self, metadata={**self.metadata, **entries})

    @property
    def is_trainable(self) -> bool:
        """Return whether this image may be included in a dataset version."""
        return self.status.is_usable_for_training

    @property
    def is_nominal(self) -> bool:
        """Return whether this image is labelled defect-free.

        PatchCore trains on nominal samples only, so this is the predicate that builds a
        training split.
        """
        return self.label is ImageLabel.GOOD
