"""The ingestion use case: validate, hash, store, record, audit — or reject with reasons.

Expected outcomes are values, not exceptions. A rejected or duplicate image is not a
programming error — it is the normal, anticipated result of validating untrusted input,
so :meth:`IngestImage.execute` returns an :class:`IngestImageResult` for it rather than
raising. That is what lets a batch ingest keep going after one bad file and still produce
a complete report at the end. Only genuine infrastructure failures (a lost database
connection, an audit-chain race) propagate as exceptions.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Any

from factoryai.domain.entities import AuditEvent, InspectionImage
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.errors import CorruptImageError
from factoryai.domain.policies.validation import ValidationChain
from factoryai.domain.ports.imaging import ImageCodec
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import Clock, IdGenerator
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.value_objects import (
    AuditSequence,
    Category,
    Checksum,
    ImageId,
    ImageLabel,
    ProcessingStatus,
    StorageLocation,
    UserId,
)

_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "BMP": "bmp", "TIFF": "tiff"}
_CONTENT_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}


def _extension_for(image_format: str) -> str:
    """Return a filename extension for a decoded format, falling back to its lower-case name."""
    return _EXTENSIONS.get(image_format, image_format.lower())


def _content_type_for(image_format: str) -> str | None:
    """Return a MIME type for a decoded format, or ``None`` if it is not one of the knowns."""
    return _CONTENT_TYPES.get(image_format)


@unique
class IngestOutcome(StrEnum):
    """What happened to one image offered for ingestion."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class IngestImageCommand:
    """One image to ingest.

    Attributes:
        category: The product class this image belongs to.
        filename: Original filename, used only for reporting — never trusted for content
            type or format.
        payload: Raw file bytes, exactly as uploaded.
        label: Ground truth, when the caller already knows it — a curated benchmark
            dataset (MVTec AD's ``train/good`` vs. ``test/broken_large``) states this
            directly; a production line camera feed will not, hence the default.
        uploaded_by: The user performing the ingestion; absent for automated ingestion
            (a seed script, a camera pipeline).
        correlation_id: Ties this ingestion to the request or job that triggered it.
        metadata: Free-form provenance to attach to the stored image (camera id, line id).
    """

    category: Category
    filename: str
    payload: bytes
    label: ImageLabel = ImageLabel.UNLABELED
    uploaded_by: UserId | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IngestImageResult:
    """The outcome of ingesting one image.

    Attributes:
        outcome: What happened.
        filename: Echoes :attr:`IngestImageCommand.filename`, for matching results back to
            inputs in a batch.
        image_id: Set when :attr:`outcome` is :attr:`~IngestOutcome.ACCEPTED`.
        location: Where the accepted image was stored.
        failures: Validation failure messages; set when :attr:`outcome` is
            :attr:`~IngestOutcome.REJECTED`.
        duplicate_of: The existing image this one matches; set when :attr:`outcome` is
            :attr:`~IngestOutcome.DUPLICATE`.
    """

    outcome: IngestOutcome
    filename: str
    image_id: ImageId | None = None
    location: StorageLocation | None = None
    failures: tuple[str, ...] = ()
    duplicate_of: ImageId | None = None


class IngestImage:
    """Validates, stores and records one inspection image, with a full audit trail."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        object_store: ObjectStore,
        image_codec: ImageCodec,
        validation_chain: ValidationChain,
        clock: Clock,
        id_generator: IdGenerator,
        raw_bucket: str,
        duplicate_hamming_threshold: int,
    ) -> None:
        """Initialise with every collaborator this use case needs.

        Args:
            uow_factory: Builds a fresh unit of work per call — see
                :meth:`~factoryai.bootstrap.container.Container.unit_of_work`.
            object_store: Where accepted image bytes are written.
            image_codec: Decodes bytes and computes the perceptual hash.
            validation_chain: The composed structural rules (ADR: rules are declarative).
            clock: Source of "now", for the upload timestamp and storage key.
            id_generator: Source of the new image's identifier.
            raw_bucket: Bucket accepted images are written to.
            duplicate_hamming_threshold: Maximum perceptual-hash distance still treated as
                a near-duplicate.
        """
        self._uow_factory = uow_factory
        self._object_store = object_store
        self._image_codec = image_codec
        self._validation_chain = validation_chain
        self._clock = clock
        self._id_generator = id_generator
        self._raw_bucket = raw_bucket
        self._duplicate_hamming_threshold = duplicate_hamming_threshold

    async def execute(self, command: IngestImageCommand) -> IngestImageResult:
        """Ingest one image.

        Returns:
            The outcome — never raises for a rejected or duplicate image.

        Raises:
            Exception: Propagated from the object store or database on a genuine
                infrastructure failure. Any object already written to storage is deleted
                before the exception propagates (the compensating action), so a failed
                ingestion never leaves an orphaned blob behind.
        """
        size_bytes = len(command.payload)
        checksum = Checksum.from_bytes(command.payload)

        try:
            decoded = self._image_codec.decode(command.payload)
        except CorruptImageError as exc:
            return IngestImageResult(
                outcome=IngestOutcome.REJECTED,
                filename=command.filename,
                failures=(f"decode: {exc.message}",),
            )

        failures = self._validation_chain.run(decoded, size_bytes=size_bytes)
        if failures:
            return IngestImageResult(
                outcome=IngestOutcome.REJECTED,
                filename=command.filename,
                failures=failures,
            )

        async with self._uow_factory() as uow:
            existing = await uow.images.find_by_checksum(checksum)
            if existing is not None:
                return IngestImageResult(
                    outcome=IngestOutcome.DUPLICATE,
                    filename=command.filename,
                    duplicate_of=existing.id,
                )

            perceptual_hash = self._image_codec.perceptual_hash(command.payload)
            near_duplicates = await uow.images.find_near_duplicates(
                perceptual_hash, max_distance=self._duplicate_hamming_threshold
            )
            if near_duplicates:
                return IngestImageResult(
                    outcome=IngestOutcome.DUPLICATE,
                    filename=command.filename,
                    duplicate_of=near_duplicates[0].id,
                )

            now = self._clock.now()
            image_id = ImageId(self._id_generator.new_id())
            location = StorageLocation(
                self._raw_bucket,
                f"{command.category.code}/{now:%Y}/{now:%m}/"
                f"{checksum.value}.{_extension_for(decoded.image_format)}",
            )

            await self._object_store.put(
                location, command.payload, content_type=_content_type_for(decoded.image_format)
            )
            try:
                image = (
                    InspectionImage(
                        id=image_id,
                        category=command.category,
                        checksum=checksum,
                        resolution=decoded.resolution,
                        size_bytes=size_bytes,
                        location=location,
                        uploaded_at=now,
                        label=command.label,
                        perceptual_hash=perceptual_hash,
                        metadata={
                            **command.metadata,
                            "color_mode": decoded.color_mode,
                            "format": decoded.image_format,
                        },
                    )
                    .transition_to(ProcessingStatus.VALIDATING)
                    .mark_valid()
                )
                await uow.images.add(image)

                latest = await uow.audit.latest()
                event = AuditEvent(
                    sequence=AuditSequence((latest.sequence + 1) if latest else 1),
                    action="image.ingested",
                    resource_type="image",
                    resource_id=str(image_id),
                    occurred_at=now,
                    prev_hash=latest.row_hash() if latest else GENESIS_HASH,
                    actor_id=command.uploaded_by,
                    payload={
                        "checksum": checksum.value,
                        "category": command.category.code,
                        "filename": command.filename,
                    },
                    correlation_id=command.correlation_id,
                )
                await uow.audit.append(event)
                await uow.commit()
            except Exception:
                await self._object_store.delete(location)
                raise

        return IngestImageResult(
            outcome=IngestOutcome.ACCEPTED,
            filename=command.filename,
            image_id=image_id,
            location=location,
        )


@dataclass(frozen=True, slots=True)
class BatchIngestReport:
    """A summary of ingesting many images, for the CLI and — later — a bulk API endpoint.

    Attributes:
        results: Every per-image outcome, in the order they were processed.
    """

    results: tuple[IngestImageResult, ...]

    @property
    def total(self) -> int:
        """Return the number of images processed."""
        return len(self.results)

    def count(self, outcome: IngestOutcome) -> int:
        """Return how many results had a given outcome."""
        return sum(1 for result in self.results if result.outcome is outcome)

    def to_json(self) -> str:
        """Serialise the full report, one entry per image, for machine consumption."""
        payload = {
            "total": self.total,
            "accepted": self.count(IngestOutcome.ACCEPTED),
            "rejected": self.count(IngestOutcome.REJECTED),
            "duplicate": self.count(IngestOutcome.DUPLICATE),
            "items": [
                {
                    "filename": result.filename,
                    "outcome": result.outcome.value,
                    "image_id": str(result.image_id) if result.image_id else None,
                    "failures": list(result.failures),
                    "duplicate_of": str(result.duplicate_of) if result.duplicate_of else None,
                }
                for result in self.results
            ],
        }
        return json.dumps(payload, indent=2)

    def summary(self) -> str:
        """Render a short, human-readable summary line."""
        return (
            f"{self.total} processed: "
            f"{self.count(IngestOutcome.ACCEPTED)} accepted, "
            f"{self.count(IngestOutcome.REJECTED)} rejected, "
            f"{self.count(IngestOutcome.DUPLICATE)} duplicate"
        )
