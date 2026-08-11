"""Unit tests for the ``SubmitJob`` use case, against fakes."""

from __future__ import annotations

import uuid

import pytest

from factoryai.application.use_cases.submit_job import SubmitJob, SubmitJobCommand
from factoryai.domain.value_objects import JobStatus, JobType, UserId
from tests.builders import NOW
from tests.fakes import FakeClock, FakeIdGenerator, FakeUnitOfWork

pytestmark = pytest.mark.unit


def _use_case(uow: FakeUnitOfWork) -> SubmitJob:
    return SubmitJob(uow_factory=lambda: uow, clock=FakeClock(NOW), id_generator=FakeIdGenerator())


class TestSubmitJob:
    async def test_a_new_submission_creates_a_queued_job(self) -> None:
        uow = FakeUnitOfWork()
        use_case = _use_case(uow)

        result = await use_case.execute(
            SubmitJobCommand(
                job_type=JobType.BULK_INFERENCE,
                idempotency_key="key-1",
                payload={"category": "bottle", "images": []},
            )
        )

        assert result.is_new is True
        assert result.job.status is JobStatus.QUEUED
        assert result.job.job_type is JobType.BULK_INFERENCE
        stored = await uow.jobs.get(result.job.id)
        assert stored.idempotency_key == "key-1"

    async def test_resubmitting_the_same_key_returns_the_existing_job(self) -> None:
        uow = FakeUnitOfWork()
        use_case = _use_case(uow)
        command = SubmitJobCommand(
            job_type=JobType.RETRAINING, idempotency_key="key-2", payload={"a": 1}
        )

        first = await use_case.execute(command)
        second = await use_case.execute(command)

        assert second.is_new is False
        assert second.job.id == first.job.id

    async def test_a_different_key_creates_a_different_job(self) -> None:
        uow = FakeUnitOfWork()
        use_case = _use_case(uow)

        first = await use_case.execute(
            SubmitJobCommand(
                job_type=JobType.DATASET_VERSIONING, idempotency_key="key-3", payload={}
            )
        )
        second = await use_case.execute(
            SubmitJobCommand(
                job_type=JobType.DATASET_VERSIONING, idempotency_key="key-4", payload={}
            )
        )

        assert first.job.id != second.job.id

    async def test_the_submitting_user_is_recorded(self) -> None:
        uow = FakeUnitOfWork()
        use_case = _use_case(uow)
        submitted_by = UserId(uuid.uuid4())

        result = await use_case.execute(
            SubmitJobCommand(
                job_type=JobType.BULK_INFERENCE,
                idempotency_key="key-5",
                payload={},
                submitted_by=submitted_by,
            )
        )

        assert result.job.submitted_by == submitted_by
