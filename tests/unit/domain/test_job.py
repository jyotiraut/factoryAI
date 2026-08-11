"""Unit tests for the ``Job`` entity's invariants and state transitions."""

from __future__ import annotations

import pytest

from factoryai.domain.errors import IllegalStateTransitionError, InvariantViolationError
from factoryai.domain.value_objects import JobStatus
from tests.builders import NOW, a_job

pytestmark = pytest.mark.unit


class TestJobInvariants:
    def test_a_blank_idempotency_key_is_rejected(self) -> None:
        with pytest.raises(InvariantViolationError):
            a_job(idempotency_key="   ")

    def test_a_naive_timestamp_is_rejected(self) -> None:
        with pytest.raises(InvariantViolationError):
            a_job(created_at=NOW.replace(tzinfo=None))

    def test_negative_progress_is_rejected(self) -> None:
        with pytest.raises(InvariantViolationError):
            a_job(progress_completed=-1)

    def test_progress_completed_cannot_exceed_total(self) -> None:
        with pytest.raises(InvariantViolationError):
            a_job(progress_completed=5, progress_total=1)

    def test_progress_completed_may_equal_total(self) -> None:
        job = a_job(progress_completed=5, progress_total=5)
        assert job.progress_completed == job.progress_total == 5


class TestJobTransitions:
    def test_queued_starts_into_running(self) -> None:
        job = a_job()

        started = job.start(now=NOW)

        assert started.status is JobStatus.RUNNING
        assert started.attempts == 1
        assert started.started_at == NOW

    def test_a_retry_does_not_overwrite_started_at(self) -> None:
        job = a_job().start(now=NOW)
        later = NOW.replace(hour=13)

        retried = job.start(now=later)

        assert retried.attempts == 2
        assert retried.started_at == NOW

    def test_running_succeeds_with_a_result(self) -> None:
        job = a_job().start(now=NOW)

        succeeded = job.succeed(result={"predicted": 3}, now=NOW)

        assert succeeded.status is JobStatus.SUCCEEDED
        assert succeeded.result == {"predicted": 3}
        assert succeeded.finished_at == NOW

    def test_running_fails_with_an_error(self) -> None:
        job = a_job().start(now=NOW)

        failed = job.fail(error="boom", now=NOW)

        assert failed.status is JobStatus.FAILED
        assert failed.error == "boom"

    def test_a_queued_job_cannot_succeed_directly(self) -> None:
        job = a_job()

        with pytest.raises(IllegalStateTransitionError):
            job.succeed(result={}, now=NOW)

    def test_a_terminal_job_cannot_transition_again(self) -> None:
        job = a_job().start(now=NOW).succeed(result={}, now=NOW)

        with pytest.raises(IllegalStateTransitionError):
            job.fail(error="too late", now=NOW)

    def test_progress_can_be_reported_without_changing_status(self) -> None:
        job = a_job().start(now=NOW)

        updated = job.report_progress(completed=2, total=10)

        assert updated.status is JobStatus.RUNNING
        assert (updated.progress_completed, updated.progress_total) == (2, 10)
