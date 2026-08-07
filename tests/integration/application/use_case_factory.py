"""Builds a real, fully-wired ``IngestImage`` use case for integration tests.

Mirrors ``tests/use_case_factory.py``'s shape, but every collaborator here is the real
adapter — Pillow, the SQLAlchemy unit of work, the S3-compatible object store — so a test
using this is exercising the exact wiring :meth:`Container.ingest_image_use_case` would
produce in the running application, just pointed at test infrastructure.
"""

from __future__ import annotations

from factoryai.application.use_cases.ingest_image import IngestImage
from factoryai.domain.policies.validation import (
    AllowedColorModesRule,
    AllowedFormatRule,
    MaxFileSizeRule,
    ResolutionBoundsRule,
    ValidationChain,
)
from factoryai.domain.ports.services import IdGenerator, SystemClock, UuidGenerator
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.value_objects import Resolution
from factoryai.infrastructure.imaging.pillow_codec import PillowImageCodec
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork

_PERMISSIVE_CHAIN = ValidationChain(
    rules=(
        MaxFileSizeRule(max_bytes=25 * 1024 * 1024),
        AllowedFormatRule(frozenset({"png", "jpeg", "bmp", "tiff"})),
        ResolutionBoundsRule(minimum=Resolution(1, 1), maximum=Resolution(8192, 8192)),
        AllowedColorModesRule(frozenset({"RGB", "L", "RGBA"})),
    )
)


def build_ingest_image_use_case(
    *,
    uow: SqlAlchemyUnitOfWork,
    object_store: ObjectStore,
    validation_chain: ValidationChain = _PERMISSIVE_CHAIN,
    id_generator: IdGenerator | None = None,
    raw_bucket: str = "factoryai-test",
    duplicate_hamming_threshold: int = 3,
) -> IngestImage:
    """Build an ``IngestImage`` use case with real adapters, for integration tests."""
    return IngestImage(
        uow_factory=lambda: uow,
        object_store=object_store,
        image_codec=PillowImageCodec(),
        validation_chain=validation_chain,
        clock=SystemClock(),
        id_generator=id_generator or UuidGenerator(),
        raw_bucket=raw_bucket,
        duplicate_hamming_threshold=duplicate_hamming_threshold,
    )
