"""Unit tests for the IngestImage use case, against in-memory fakes.

No Pillow, no database, no MinIO: :class:`~tests.fakes.FakeImageCodec` returns scripted
structural metadata, so these tests are entirely about the use case's own orchestration —
validate, hash, check for duplicates, store, record, audit, compensate on failure.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from factoryai.application.use_cases.ingest_image import (
    BatchIngestReport,
    IngestImageCommand,
    IngestImageResult,
    IngestOutcome,
)
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.errors import InvariantViolationError
from factoryai.domain.policies.validation import AllowedFormatRule, ValidationChain
from factoryai.domain.value_objects import Category, DecodedImage, ImageLabel, Resolution
from tests.fakes import FakeClock, FakeIdGenerator, FakeImageCodec, FakeObjectStore, FakeUnitOfWork
from tests.use_case_factory import make_ingest_image_use_case

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
FIXED_IMAGE_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _good_decoded_image() -> DecodedImage:
    return DecodedImage(resolution=Resolution(512, 512), image_format="PNG", color_mode="RGB")


class TestAccepted:
    async def test_a_valid_image_is_stored_and_recorded(self) -> None:
        object_store = FakeObjectStore()
        uow = FakeUnitOfWork()
        use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=object_store,
            image_codec=FakeImageCodec(_good_decoded_image(), hash_value="0" * 16),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(FIXED_IMAGE_ID),
        )

        result = await use_case.execute(
            IngestImageCommand(category=Category("bottle"), filename="a.png", payload=b"x" * 10)
        )

        assert result.outcome is IngestOutcome.ACCEPTED
        assert result.image_id is not None
        assert str(result.image_id) == str(FIXED_IMAGE_ID)
        assert result.location is not None
        assert result.location.bucket == "factoryai-raw"
        assert result.location.key.startswith("bottle/2026/08/")
        assert result.location.key.endswith(".png")

    async def test_the_stored_bytes_match_the_payload_exactly(self) -> None:
        object_store = FakeObjectStore()
        uow = FakeUnitOfWork()
        use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=object_store,
            image_codec=FakeImageCodec(_good_decoded_image()),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(FIXED_IMAGE_ID),
        )
        payload = b"exact bytes of the image" * 5

        result = await use_case.execute(
            IngestImageCommand(category=Category("bottle"), filename="a.png", payload=payload)
        )

        assert result.location is not None
        assert await object_store.get(result.location) == payload

    async def test_the_image_row_is_persisted_as_valid(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=FakeImageCodec(_good_decoded_image()),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(FIXED_IMAGE_ID),
        )

        result = await use_case.execute(
            IngestImageCommand(category=Category("bottle"), filename="a.png", payload=b"x" * 10)
        )

        assert result.image_id is not None
        stored = await uow.images.get(result.image_id)
        assert stored.is_trainable
        assert stored.category == Category("bottle")

    async def test_an_audit_event_is_appended_as_the_genesis_record(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=FakeImageCodec(_good_decoded_image()),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(FIXED_IMAGE_ID),
        )

        result = await use_case.execute(
            IngestImageCommand(category=Category("bottle"), filename="a.png", payload=b"x" * 10)
        )

        latest = await uow.audit.latest()
        assert latest is not None
        assert latest.action == "image.ingested"
        assert latest.resource_id == str(result.image_id)
        assert latest.prev_hash == GENESIS_HASH

    async def test_a_second_ingestion_chains_onto_the_first_audit_event(self) -> None:
        # Two distinct FakeImageCodec instances: reusing one would give both images the
        # same scripted perceptual hash and the second would be flagged a near-duplicate
        # of the first, never reaching the audit append this test is about.
        uow = FakeUnitOfWork()
        first_use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=FakeImageCodec(_good_decoded_image(), hash_value="0" * 16),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )
        second_use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=FakeImageCodec(_good_decoded_image(), hash_value="f" * 16),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        await first_use_case.execute(
            IngestImageCommand(category=Category("bottle"), filename="a.png", payload=b"a" * 10)
        )
        second = await second_use_case.execute(
            IngestImageCommand(category=Category("bottle"), filename="b.png", payload=b"b" * 10)
        )

        assert second.outcome is IngestOutcome.ACCEPTED
        latest = await uow.audit.latest()
        assert latest is not None
        assert int(latest.sequence) == 2

    async def test_metadata_is_merged_with_decoded_structural_info(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=FakeImageCodec(_good_decoded_image()),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(FIXED_IMAGE_ID),
        )

        result = await use_case.execute(
            IngestImageCommand(
                category=Category("bottle"),
                filename="a.png",
                payload=b"x" * 10,
                metadata={"camera": "cam-1"},
            )
        )

        assert result.image_id is not None
        stored = await uow.images.get(result.image_id)
        assert stored.metadata["camera"] == "cam-1"
        assert stored.metadata["color_mode"] == "RGB"
        assert stored.metadata["format"] == "PNG"

    async def test_a_supplied_ground_truth_label_is_persisted(self) -> None:
        """Verify a caller-supplied label survives, instead of being silently downgraded.

        A curated dataset (MVTec's train/good) states the label directly; it must not be
        overwritten just because production traffic usually cannot state one.
        """
        uow = FakeUnitOfWork()
        use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=FakeImageCodec(_good_decoded_image()),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(FIXED_IMAGE_ID),
        )

        result = await use_case.execute(
            IngestImageCommand(
                category=Category("bottle"),
                filename="a.png",
                payload=b"x" * 10,
                label=ImageLabel.DEFECT,
            )
        )

        assert result.image_id is not None
        stored = await uow.images.get(result.image_id)
        assert stored.label is ImageLabel.DEFECT

    async def test_the_default_label_is_unlabeled(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=FakeImageCodec(_good_decoded_image()),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(FIXED_IMAGE_ID),
        )

        result = await use_case.execute(
            IngestImageCommand(category=Category("bottle"), filename="a.png", payload=b"x" * 10)
        )

        assert result.image_id is not None
        stored = await uow.images.get(result.image_id)
        assert stored.label is ImageLabel.UNLABELED


class TestRejected:
    async def test_a_corrupt_image_is_rejected_without_touching_storage(self) -> None:
        object_store = FakeObjectStore()
        codec = FakeImageCodec(_good_decoded_image())
        codec.corrupt = True
        use_case = make_ingest_image_use_case(
            uow=FakeUnitOfWork(),
            object_store=object_store,
            image_codec=codec,
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        result = await use_case.execute(
            IngestImageCommand(category=Category("bottle"), filename="bad.png", payload=b"junk")
        )

        assert result.outcome is IngestOutcome.REJECTED
        assert result.failures[0].startswith("decode:")
        assert object_store.deleted == []

    async def test_a_disallowed_format_is_rejected_with_a_named_reason(self) -> None:
        chain = ValidationChain(rules=(AllowedFormatRule(frozenset({"jpeg"})),))
        use_case = make_ingest_image_use_case(
            uow=FakeUnitOfWork(),
            object_store=FakeObjectStore(),
            image_codec=FakeImageCodec(_good_decoded_image()),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
            validation_chain=chain,
        )

        result = await use_case.execute(
            IngestImageCommand(category=Category("bottle"), filename="a.png", payload=b"x" * 10)
        )

        assert result.outcome is IngestOutcome.REJECTED
        assert any(f.startswith("allowed_format:") for f in result.failures)

    async def test_a_rejected_image_is_never_persisted(self) -> None:
        uow = FakeUnitOfWork()
        chain = ValidationChain(rules=(AllowedFormatRule(frozenset({"jpeg"})),))
        use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=FakeImageCodec(_good_decoded_image()),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
            validation_chain=chain,
        )

        await use_case.execute(
            IngestImageCommand(category=Category("bottle"), filename="a.png", payload=b"x" * 10)
        )

        assert await uow.audit.latest() is None


class TestDuplicate:
    async def test_an_exact_checksum_match_is_reported_as_a_duplicate(self) -> None:
        uow = FakeUnitOfWork()
        object_store = FakeObjectStore()
        use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=object_store,
            image_codec=FakeImageCodec(_good_decoded_image()),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(uuid.uuid4(), uuid.uuid4()),
        )
        command = IngestImageCommand(
            category=Category("bottle"), filename="a.png", payload=b"identical bytes"
        )

        first = await use_case.execute(command)
        second = await use_case.execute(command)

        assert first.outcome is IngestOutcome.ACCEPTED
        assert second.outcome is IngestOutcome.DUPLICATE
        assert second.duplicate_of == first.image_id

    async def test_a_duplicate_does_not_write_a_second_object(self) -> None:
        uow = FakeUnitOfWork()
        object_store = FakeObjectStore()
        use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=object_store,
            image_codec=FakeImageCodec(_good_decoded_image()),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(uuid.uuid4(), uuid.uuid4()),
        )
        command = IngestImageCommand(
            category=Category("bottle"), filename="a.png", payload=b"identical bytes"
        )

        await use_case.execute(command)
        await use_case.execute(command)

        stored = await uow.images.list_trainable(Category("bottle"))
        assert len(stored) == 1

    async def test_a_near_duplicate_perceptual_hash_is_reported_as_a_duplicate(self) -> None:
        uow = FakeUnitOfWork()
        first_codec = FakeImageCodec(_good_decoded_image(), hash_value="0" * 16)
        use_case_one = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=first_codec,
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(uuid.uuid4()),
        )
        first = await use_case_one.execute(
            IngestImageCommand(category=Category("bottle"), filename="a.png", payload=b"a" * 10)
        )

        near_codec = FakeImageCodec(_good_decoded_image(), hash_value="1" + "0" * 15)
        use_case_two = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=near_codec,
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(uuid.uuid4()),
            duplicate_hamming_threshold=4,
        )
        second = await use_case_two.execute(
            IngestImageCommand(category=Category("bottle"), filename="b.png", payload=b"b" * 10)
        )

        assert second.outcome is IngestOutcome.DUPLICATE
        assert second.duplicate_of == first.image_id

    async def test_a_hash_outside_the_threshold_is_not_treated_as_a_duplicate(self) -> None:
        uow = FakeUnitOfWork()
        first_codec = FakeImageCodec(_good_decoded_image(), hash_value="0" * 16)
        use_case_one = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=first_codec,
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(uuid.uuid4()),
        )
        await use_case_one.execute(
            IngestImageCommand(category=Category("bottle"), filename="a.png", payload=b"a" * 10)
        )

        far_codec = FakeImageCodec(_good_decoded_image(), hash_value="f" * 16)
        use_case_two = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=far_codec,
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(uuid.uuid4()),
            duplicate_hamming_threshold=2,
        )
        second = await use_case_two.execute(
            IngestImageCommand(category=Category("bottle"), filename="b.png", payload=b"b" * 10)
        )

        assert second.outcome is IngestOutcome.ACCEPTED


class TestCompensatingDelete:
    async def test_a_commit_failure_deletes_the_already_uploaded_object(self) -> None:
        uow = FakeUnitOfWork()
        uow.fail_on_commit = InvariantViolationError("simulated audit chain conflict")
        object_store = FakeObjectStore()
        use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=object_store,
            image_codec=FakeImageCodec(_good_decoded_image()),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(FIXED_IMAGE_ID),
        )

        with pytest.raises(InvariantViolationError):
            await use_case.execute(
                IngestImageCommand(category=Category("bottle"), filename="a.png", payload=b"x" * 10)
            )

        assert len(object_store.deleted) == 1
        assert object_store.deleted[0].key.startswith("bottle/2026/08/")

    async def test_a_failed_commit_leaves_the_transaction_uncommitted(self) -> None:
        """Verify the fake reports "not committed" after a failed commit.

        The fake does not roll back writes already made (see its docstring) — what it can
        honestly demonstrate is that the transaction was never marked committed, which is
        what a real unit of work uses to decide whether to commit or roll back on exit.
        """
        uow = FakeUnitOfWork()
        uow.fail_on_commit = InvariantViolationError("simulated failure")
        use_case = make_ingest_image_use_case(
            uow=uow,
            object_store=FakeObjectStore(),
            image_codec=FakeImageCodec(_good_decoded_image()),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(FIXED_IMAGE_ID),
        )

        with pytest.raises(InvariantViolationError):
            await use_case.execute(
                IngestImageCommand(category=Category("bottle"), filename="a.png", payload=b"x" * 10)
            )

        assert uow.committed is False


class TestBatchIngestReport:
    def test_counts_each_outcome(self) -> None:
        report = BatchIngestReport(
            results=(
                IngestImageResult(outcome=IngestOutcome.ACCEPTED, filename="a.png"),
                IngestImageResult(outcome=IngestOutcome.ACCEPTED, filename="b.png"),
                IngestImageResult(outcome=IngestOutcome.REJECTED, filename="c.png"),
                IngestImageResult(outcome=IngestOutcome.DUPLICATE, filename="d.png"),
            )
        )
        assert report.total == 4
        assert report.count(IngestOutcome.ACCEPTED) == 2
        assert report.count(IngestOutcome.REJECTED) == 1
        assert report.count(IngestOutcome.DUPLICATE) == 1

    def test_summary_mentions_every_count(self) -> None:
        report = BatchIngestReport(
            results=(IngestImageResult(outcome=IngestOutcome.ACCEPTED, filename="a.png"),)
        )
        summary = report.summary()
        assert "1 processed" in summary
        assert "1 accepted" in summary

    def test_to_json_round_trips_the_essentials(self) -> None:
        image_id = uuid.uuid4()
        report = BatchIngestReport(
            results=(
                IngestImageResult(
                    outcome=IngestOutcome.ACCEPTED, filename="a.png", image_id=image_id  # type: ignore[arg-type]
                ),
            )
        )
        payload = json.loads(report.to_json())
        assert payload["total"] == 1
        assert payload["accepted"] == 1
        assert payload["items"][0]["image_id"] == str(image_id)

    def test_an_empty_batch_reports_zero_everything(self) -> None:
        report = BatchIngestReport(results=())
        assert report.total == 0
        assert "0 processed" in report.summary()
