"""Integration tests for :class:`IngestImage` against real PostgreSQL and MinIO.

Everything here is real: Pillow decodes real PNG bytes, the checksum and near-duplicate
checks run real SQL against a real Postgres, and accepted bytes really land in MinIO. This
is what the unit tests (against fakes) cannot prove on their own.
"""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from factoryai.application.use_cases.ingest_image import IngestImageCommand, IngestOutcome
from factoryai.domain.policies.validation import (
    AllowedColorModesRule,
    AllowedFormatRule,
    MaxFileSizeRule,
    ResolutionBoundsRule,
    ValidationChain,
)
from factoryai.domain.value_objects import Category, ImageId, Resolution
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from factoryai.infrastructure.storage.s3_compatible import S3CompatibleObjectStore
from tests.builders import an_image
from tests.fakes import FakeIdGenerator
from tests.integration.application.use_case_factory import build_ingest_image_use_case

pytestmark = pytest.mark.integration


def _png_bytes(
    width: int = 512, height: int = 512, color: tuple[int, int, int] = (10, 20, 30)
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_a_valid_image_is_stored_in_minio_and_recorded_in_postgres(
    uow: SqlAlchemyUnitOfWork, s3_object_store: S3CompatibleObjectStore
) -> None:
    use_case = build_ingest_image_use_case(uow=uow, object_store=s3_object_store)

    result = await use_case.execute(
        IngestImageCommand(
            category=Category("bottle"), filename="bottle-001.png", payload=_png_bytes()
        )
    )

    assert result.outcome is IngestOutcome.ACCEPTED
    assert result.image_id is not None
    assert result.location is not None
    assert await s3_object_store.exists(result.location)

    async with uow:
        stored = await uow.images.get(result.image_id)
    assert stored.resolution == Resolution(512, 512)
    assert stored.category == Category("bottle")

    async with uow:
        latest = await uow.audit.latest()
    assert latest is not None
    assert latest.action == "image.ingested"


async def test_ingesting_the_same_bytes_twice_is_reported_as_a_duplicate(
    uow: SqlAlchemyUnitOfWork, s3_object_store: S3CompatibleObjectStore
) -> None:
    use_case = build_ingest_image_use_case(uow=uow, object_store=s3_object_store)
    payload = _png_bytes(color=(99, 88, 77))
    command = IngestImageCommand(category=Category("bottle"), filename="dup.png", payload=payload)

    first = await use_case.execute(command)
    second = await use_case.execute(command)

    assert first.outcome is IngestOutcome.ACCEPTED
    assert second.outcome is IngestOutcome.DUPLICATE
    assert second.duplicate_of == first.image_id


async def test_a_recompressed_copy_is_caught_by_the_near_duplicate_check(
    uow: SqlAlchemyUnitOfWork, s3_object_store: S3CompatibleObjectStore
) -> None:
    use_case = build_ingest_image_use_case(uow=uow, object_store=s3_object_store)
    original = _png_bytes(color=(40, 40, 40))
    first = await use_case.execute(
        IngestImageCommand(category=Category("bottle"), filename="a.png", payload=original)
    )
    assert first.outcome is IngestOutcome.ACCEPTED

    buffer = io.BytesIO()
    Image.open(io.BytesIO(original)).save(buffer, format="JPEG", quality=97)
    recompressed = buffer.getvalue()

    second = await use_case.execute(
        IngestImageCommand(
            category=Category("bottle"), filename="a-recompressed.jpg", payload=recompressed
        )
    )

    assert second.outcome is IngestOutcome.DUPLICATE
    assert second.duplicate_of == first.image_id


async def test_a_corrupt_payload_is_rejected(
    uow: SqlAlchemyUnitOfWork, s3_object_store: S3CompatibleObjectStore
) -> None:
    use_case = build_ingest_image_use_case(uow=uow, object_store=s3_object_store)

    result = await use_case.execute(
        IngestImageCommand(
            category=Category("bottle"), filename="bad.png", payload=b"not an image at all"
        )
    )

    assert result.outcome is IngestOutcome.REJECTED
    assert result.failures[0].startswith("decode:")

    async with uow:
        assert await uow.audit.latest() is None


async def test_an_image_below_the_minimum_resolution_is_rejected(
    uow: SqlAlchemyUnitOfWork, s3_object_store: S3CompatibleObjectStore
) -> None:
    chain = ValidationChain(
        rules=(
            MaxFileSizeRule(max_bytes=25 * 1024 * 1024),
            AllowedFormatRule(frozenset({"png"})),
            ResolutionBoundsRule(minimum=Resolution(256, 256), maximum=Resolution(4096, 4096)),
            AllowedColorModesRule(frozenset({"RGB"})),
        )
    )
    use_case = build_ingest_image_use_case(
        uow=uow, object_store=s3_object_store, validation_chain=chain
    )

    result = await use_case.execute(
        IngestImageCommand(
            category=Category("bottle"), filename="tiny.png", payload=_png_bytes(64, 64)
        )
    )

    assert result.outcome is IngestOutcome.REJECTED
    assert any(f.startswith("resolution_bounds:") for f in result.failures)


async def test_a_database_failure_after_upload_deletes_the_orphaned_object(
    uow: SqlAlchemyUnitOfWork, s3_object_store: S3CompatibleObjectStore
) -> None:
    """Verify the compensating delete against real infrastructure, not just fakes.

    Forces a real primary-key collision: the pre-seeded row makes the use case's own id
    generator produce an id that already
    exists, so its insert fails with a genuine Postgres integrity error *after* the bytes
    are already sitting in MinIO — exactly the failure window the compensating delete
    exists for.
    """
    collision_id = ImageId(uuid.uuid4())
    async with uow:
        await uow.images.add(an_image(id=collision_id))
        await uow.commit()

    use_case = build_ingest_image_use_case(
        uow=uow, object_store=s3_object_store, id_generator=FakeIdGenerator(collision_id)
    )
    keys_before = {key async for key in s3_object_store.list_keys("factoryai-test")}

    with pytest.raises(Exception, match=r"(?i)duplicate|unique|integrity"):
        await use_case.execute(
            IngestImageCommand(
                category=Category("bottle"),
                filename="colliding.png",
                payload=_png_bytes(color=(1, 2, 3)),
            )
        )

    keys_after = {key async for key in s3_object_store.list_keys("factoryai-test")}
    assert keys_after == keys_before, "the uploaded object must not survive the failed insert"
