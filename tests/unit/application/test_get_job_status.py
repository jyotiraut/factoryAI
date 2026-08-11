"""Unit tests for the ``GetJobStatus`` use case, against fakes."""

from __future__ import annotations

import uuid

import pytest

from factoryai.application.use_cases.get_job_status import GetJobStatus
from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.value_objects import JobId, JobStatus
from tests.builders import NOW, a_job
from tests.fakes import FakeUnitOfWork

pytestmark = pytest.mark.unit


class TestGetJobStatus:
    async def test_returns_the_stored_job(self) -> None:
        uow = FakeUnitOfWork()
        job = a_job()
        await uow.jobs.add(job)
        use_case = GetJobStatus(uow_factory=lambda: uow)

        found = await use_case.execute(job.id)

        assert found.id == job.id
        assert found.status is JobStatus.QUEUED

    async def test_reflects_progress_recorded_by_a_worker(self) -> None:
        uow = FakeUnitOfWork()
        job = a_job().start(now=NOW).report_progress(completed=4, total=10)
        await uow.jobs.add(job)
        use_case = GetJobStatus(uow_factory=lambda: uow)

        found = await use_case.execute(job.id)

        assert (found.progress_completed, found.progress_total) == (4, 10)

    async def test_an_unknown_job_id_raises(self) -> None:
        uow = FakeUnitOfWork()
        use_case = GetJobStatus(uow_factory=lambda: uow)

        with pytest.raises(EntityNotFoundError):
            await use_case.execute(JobId(uuid.uuid4()))
