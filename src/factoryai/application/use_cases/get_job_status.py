"""The job status use case: what ``GET /jobs/{id}`` and ``factoryai job status`` read."""

from __future__ import annotations

from collections.abc import Callable

from factoryai.domain.entities import Job
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.value_objects import JobId


class GetJobStatus:
    """Reads a job's current status, progress and result."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Initialise with the unit-of-work factory this use case reads through."""
        self._uow_factory = uow_factory

    async def execute(self, job_id: JobId) -> Job:
        """Return the current state of one job.

        Raises:
            EntityNotFoundError: If no such job exists.
        """
        async with self._uow_factory() as uow:
            return await uow.jobs.get(job_id)
