"""The job submission use case: record a background job, safely on retry.

Only the record is created here — dispatching the Celery task that actually performs the
work is an infrastructure concern (``factoryai.worker``), kept out of this layer by the
same rule that keeps every other use case free of concrete infrastructure (ADR-0001).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from factoryai.domain.entities import Job
from factoryai.domain.errors import JobIdempotencyKeyExistsError
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import Clock, IdGenerator
from factoryai.domain.value_objects import JobId, JobStatus, JobType, UserId


@dataclass(frozen=True, slots=True)
class SubmitJobCommand:
    """A request to run one unit of background work.

    Attributes:
        job_type: What kind of work to perform.
        idempotency_key: Caller-supplied deduplication key. A client that resends this
            command after a timeout gets back the job already created for the same key
            rather than starting the work twice.
        payload: The task's input; shape depends on ``job_type``.
        submitted_by: The user requesting the job, absent for an automated trigger.
    """

    job_type: JobType
    idempotency_key: str
    payload: dict[str, Any]
    submitted_by: UserId | None = None


@dataclass(frozen=True, slots=True)
class SubmitJobResult:
    """The outcome of a submission.

    Attributes:
        job: The job record — either newly created, or the pre-existing one matching
            ``idempotency_key``.
        is_new: Whether this call created ``job`` or a prior call already had.
            :class:`~factoryai.worker.tasks` dispatch on this: a duplicate submission must
            not enqueue a second Celery task.
    """

    job: Job
    is_new: bool


class SubmitJob:
    """Records a background job, deduplicating on its idempotency key."""

    def __init__(
        self, *, uow_factory: Callable[[], UnitOfWork], clock: Clock, id_generator: IdGenerator
    ) -> None:
        """Initialise with every collaborator this use case needs."""
        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator

    async def execute(self, command: SubmitJobCommand) -> SubmitJobResult:
        """Create a job record, or return the one already submitted under this key."""
        async with self._uow_factory() as uow:
            existing = await uow.jobs.find_by_idempotency_key(command.idempotency_key)
            if existing is not None:
                return SubmitJobResult(job=existing, is_new=False)

            job = Job(
                id=JobId(self._id_generator.new_id()),
                job_type=command.job_type,
                status=JobStatus.QUEUED,
                idempotency_key=command.idempotency_key,
                payload=dict(command.payload),
                submitted_by=command.submitted_by,
                created_at=self._clock.now(),
            )
            try:
                await uow.jobs.add(job)
                await uow.commit()
            except JobIdempotencyKeyExistsError:
                # A concurrent submission won the race between our find and our add. The
                # failed insert leaves this unit of work's transaction unusable for further
                # queries, so the re-read below opens a fresh one rather than reusing it.
                pass
            else:
                return SubmitJobResult(job=job, is_new=True)

        async with self._uow_factory() as uow:
            existing = await uow.jobs.find_by_idempotency_key(command.idempotency_key)
            if existing is None:  # pragma: no cover - defensive, see comment above
                raise JobIdempotencyKeyExistsError(
                    f"a job with idempotency key {command.idempotency_key!r} already exists",
                    details={"idempotency_key": command.idempotency_key},
                )
            return SubmitJobResult(job=existing, is_new=False)
