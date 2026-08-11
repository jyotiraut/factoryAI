"""Integration tests for job persistence, against real PostgreSQL.

Covers what a fake structurally cannot: the real ``idempotency_key`` unique constraint
translating into :class:`JobIdempotencyKeyExistsError` through an actual ``IntegrityError``,
not a Python-level ``dict`` membership check.
"""

from __future__ import annotations

import pytest

from factoryai.domain.errors import EntityNotFoundError, JobIdempotencyKeyExistsError
from factoryai.domain.value_objects import JobId, JobStatus, parse_uuid
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.builders import NOW, a_job

pytestmark = pytest.mark.integration


class TestJobPersistence:
    async def test_a_job_survives_a_real_commit_and_reload(self, uow: SqlAlchemyUnitOfWork) -> None:
        job = a_job()
        async with uow:
            await uow.jobs.add(job)
            await uow.commit()

        async with uow:
            reloaded = await uow.jobs.get(job.id)
        assert reloaded.idempotency_key == job.idempotency_key
        assert reloaded.status is JobStatus.QUEUED

    async def test_a_duplicate_idempotency_key_is_rejected_by_a_real_constraint(
        self, uow: SqlAlchemyUnitOfWork
    ) -> None:
        first = a_job(idempotency_key="shared-key")
        async with uow:
            await uow.jobs.add(first)
            await uow.commit()

        second = a_job(idempotency_key="shared-key")
        async with uow:
            with pytest.raises(JobIdempotencyKeyExistsError):
                await uow.jobs.add(second)
                await uow.commit()

    async def test_a_status_transition_round_trips(self, uow: SqlAlchemyUnitOfWork) -> None:
        job = a_job()
        async with uow:
            await uow.jobs.add(job)
            await uow.commit()

        async with uow:
            running = (await uow.jobs.get(job.id)).start(now=NOW)
            await uow.jobs.update(running)
            await uow.commit()

        async with uow:
            succeeded = (await uow.jobs.get(job.id)).succeed(result={"predicted": 7}, now=NOW)
            await uow.jobs.update(succeeded)
            await uow.commit()

        async with uow:
            reloaded = await uow.jobs.get(job.id)
        assert reloaded.status is JobStatus.SUCCEEDED
        assert reloaded.result == {"predicted": 7}
        assert reloaded.attempts == 1

    async def test_find_by_idempotency_key_returns_none_when_absent(
        self, uow: SqlAlchemyUnitOfWork
    ) -> None:
        async with uow:
            assert await uow.jobs.find_by_idempotency_key("never-submitted") is None

    async def test_getting_an_unknown_job_raises(self, uow: SqlAlchemyUnitOfWork) -> None:
        async with uow:
            with pytest.raises(EntityNotFoundError):
                await uow.jobs.get(
                    JobId(parse_uuid("00000000-0000-0000-0000-000000000000"))
                )
