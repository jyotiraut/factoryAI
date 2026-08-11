"""Background jobs: long-running work tracked outside the HTTP request cycle."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from factoryai.domain.errors import IllegalStateTransitionError, InvariantViolationError
from factoryai.domain.value_objects import JobId, JobStatus, JobType, UserId

_TERMINAL_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset({JobStatus.RUNNING, JobStatus.SUCCEEDED, JobStatus.FAILED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
}
"""Allowed status transitions. ``RUNNING -> RUNNING`` covers a retry attempt restarting the
same job rather than minting a new one."""


@dataclass(frozen=True, slots=True)
class Job:
    """One unit of background work submitted to a Celery queue.

    A job's ``idempotency_key`` is what lets a client retry a submission (a flaky network
    call, a double-click) without starting the work twice: submitting the same key returns
    the existing job instead of enqueueing a second one. ``progress`` is a coarse
    ``(completed, total)`` pair a task updates as it works through a batch — enough for a
    client polling ``GET /jobs/{id}`` to show a progress bar, not a per-item log.

    Attributes:
        id: Unique identifier, also used as the Celery task id.
        job_type: What kind of work this job performs.
        status: Current lifecycle state.
        idempotency_key: Caller-supplied deduplication key, unique across all jobs.
        payload: The task's input, e.g. a category and a list of image references.
        submitted_by: The user who requested this job, if any.
        created_at: When the job was submitted.
        started_at: When a worker began executing it, if it has.
        finished_at: When it reached a terminal status, if it has.
        attempts: How many times a worker has picked this job up.
        progress_completed: Items processed so far, for jobs that process a batch.
        progress_total: Total items expected, for jobs that process a batch.
        result: Structured output, present only once :attr:`status` is ``succeeded``.
        error: A human-readable failure reason, present only once ``failed``.
    """

    id: JobId
    job_type: JobType
    status: JobStatus
    idempotency_key: str
    payload: dict[str, Any]
    created_at: datetime
    submitted_by: UserId | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    progress_completed: int = 0
    progress_total: int = 0
    result: dict[str, Any] | None = field(default=None)
    error: str | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate the idempotency key, timestamps and progress counters.

        Raises:
            InvariantViolationError: If the idempotency key is blank, ``created_at`` is
                naive, or progress counters are negative or inconsistent.
        """
        if not self.idempotency_key.strip():
            raise InvariantViolationError(
                "job idempotency_key must not be blank", code="job.no_idempotency_key"
            )
        if self.created_at.tzinfo is None:
            raise InvariantViolationError(
                "created_at must be timezone-aware", code="job.naive_timestamp"
            )
        if self.progress_completed < 0 or self.progress_total < 0:
            raise InvariantViolationError(
                "progress counters must not be negative", code="job.negative_progress"
            )
        if self.progress_completed > self.progress_total > 0:
            raise InvariantViolationError(
                "progress_completed cannot exceed progress_total",
                code="job.progress_overflow",
                details={"completed": self.progress_completed, "total": self.progress_total},
            )

    def can_transition_to(self, target: JobStatus) -> bool:
        """Return whether this job may move from its current status to ``target``."""
        return target in _TERMINAL_TRANSITIONS[self.status]

    def _transition(self, target: JobStatus, **changes: Any) -> Job:
        """Return a copy moved to ``target``, applying ``changes`` alongside the status.

        Raises:
            IllegalStateTransitionError: If :meth:`can_transition_to` forbids ``target``.
        """
        if not self.can_transition_to(target):
            raise IllegalStateTransitionError("Job", self.status, target)
        return dataclasses.replace(self, status=target, **changes)

    def start(self, *, now: datetime) -> Job:
        """Move to ``running``, recording the first attempt's start time.

        ``started_at`` is only set once: a retry re-enters :attr:`~JobStatus.RUNNING` from
        ``running`` itself (see :data:`_TERMINAL_TRANSITIONS`), and should not overwrite
        when the job *first* began.
        """
        return self._transition(
            JobStatus.RUNNING,
            attempts=self.attempts + 1,
            started_at=self.started_at or now,
        )

    def report_progress(self, *, completed: int, total: int) -> Job:
        """Return a copy with updated progress counters, status unchanged."""
        return dataclasses.replace(self, progress_completed=completed, progress_total=total)

    def succeed(self, *, result: dict[str, Any], now: datetime) -> Job:
        """Move to ``succeeded``, recording the result and finish time."""
        return self._transition(JobStatus.SUCCEEDED, result=result, finished_at=now)

    def fail(self, *, error: str, now: datetime) -> Job:
        """Move to ``failed``, recording the error and finish time."""
        return self._transition(JobStatus.FAILED, error=error, finished_at=now)
