"""Celery tasks: the actual work behind each :class:`~factoryai.domain.value_objects.JobType`.

Every task shares one shape (see :func:`_run`): re-read the job's current payload from
PostgreSQL (never from the Celery message body — the message only ever carries a job id),
mark it running, execute the matching application use case, and record success or let the
exception propagate for Celery's own retry/backoff to handle. The job row, not the Celery
message, is this platform's source of truth for "did this actually happen" — the exact
property the audit-obsessed rest of the platform already relies on everywhere else.

Retry policy (ADR-0012): exponential backoff via ``retry_backoff``, capped attempts via
``max_retries``. Once retries are exhausted, :class:`JobTask.on_failure` marks the job
``failed`` and enqueues a small record onto the ``dead_letter`` queue — visible in Flower,
separate from any real work queue.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any

from celery import Task

from factoryai import pipeline_client
from factoryai.application.use_cases.predict_image import PredictImageCommand
from factoryai.bootstrap.container import Container, build_container
from factoryai.domain.entities import Job
from factoryai.domain.ports.services import SystemClock
from factoryai.domain.value_objects import (
    Category,
    JobId,
    JobStatus,
    JobType,
    StorageLocation,
    parse_uuid,
)
from factoryai.shared.asyncio_compat import configure_event_loop_policy
from factoryai.shared.config import get_settings
from factoryai.shared.logging import get_logger
from factoryai.worker.celery_app import celery_app

logger = get_logger(__name__)

_MAX_RETRIES = 5
_RETRY_BACKOFF_MAX_SECONDS = 600


@lru_cache(maxsize=1)
def _worker_container() -> Container:
    """Return this worker process's single, reused composition root.

    ``lru_cache(maxsize=1)`` makes this a per-process singleton: Celery's prefork pool
    forks one process per worker slot, each importing this module once, so every task
    executed by a given slot reuses the same database connection pool instead of opening a
    fresh one per task.
    """
    configure_event_loop_policy()
    return build_container(get_settings())


def _run(
    job_id: str, execute: Callable[[Container, Job], Awaitable[dict[str, Any]]]
) -> dict[str, Any]:
    """Drive one task attempt: load the job, run it, record the outcome.

    Returns:
        The use case's result, serialised to a plain dict.

    Raises:
        Exception: Whatever ``execute`` raised, unchanged — propagated so Celery's
            ``autoretry_for`` can schedule a retry. The job is left in ``running`` for a
            retryable failure; :class:`JobTask.on_failure` marks it ``failed`` only once
            retries are exhausted.
    """
    return asyncio.run(_run_async(JobId(parse_uuid(job_id)), execute))


async def _run_async(
    job_id: JobId, execute: Callable[[Container, Job], Awaitable[dict[str, Any]]]
) -> dict[str, Any]:
    container = _worker_container()
    clock = SystemClock()

    async with container.unit_of_work() as uow:
        job = await uow.jobs.get(job_id)
        if job.status.is_terminal:
            # Redelivered after already completing (e.g. the broker redelivered a message
            # whose ack was lost) — a Celery-crash-safety case, not a bug. Returning the
            # recorded result keeps this idempotent instead of redoing (or re-failing) work.
            return job.result or {"error": job.error}
        job = job.start(now=clock.now())
        await uow.jobs.update(job)
        await uow.commit()

    result = await execute(container, job)

    async with container.unit_of_work() as uow:
        job = await uow.jobs.get(job_id)
        job = job.succeed(result=result, now=clock.now())
        await uow.jobs.update(job)
        await uow.commit()
    return result


class JobTask(Task):  # type: ignore[misc]  # celery ships no stubs; Task is Any
    """Base class wiring a task's permanent failure to the job row and the dead-letter queue."""

    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        """Mark the job ``failed`` and record it for operator visibility.

        Called by Celery exactly once a task will not be retried again — either it raised
        something outside ``autoretry_for``, or ``max_retries`` is exhausted. Never called
        for an attempt that will still retry (see ``factoryai.worker.tasks`` module
        docstring).
        """
        job_id = kwargs.get("job_id") or (args[0] if args else None)
        if job_id is None:  # pragma: no cover - defensive, every task takes job_id
            return
        asyncio.run(_mark_failed(JobId(parse_uuid(job_id)), str(exc)))
        record_dead_letter.delay(
            job_id=job_id, task_name=self.name, error=str(exc), traceback=str(einfo)
        )


async def _mark_failed(job_id: JobId, error: str) -> None:
    container = _worker_container()
    async with container.unit_of_work() as uow:
        job = await uow.jobs.get(job_id)
        if job.status.is_terminal:
            return
        # A job that never reached `running` (the very first attempt failed before
        # `_run_async`'s own transaction committed) cannot go straight queued -> failed
        # under Job's transition table, so it is walked through running first.
        if job.status is JobStatus.QUEUED:
            job = job.start(now=SystemClock().now())
        job = job.fail(error=error, now=SystemClock().now())
        await uow.jobs.update(job)
        await uow.commit()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    base=JobTask,
    name="factoryai.worker.tasks.run_bulk_inference",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=_RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    max_retries=_MAX_RETRIES,
)
def run_bulk_inference(self: Task, job_id: str) -> dict[str, Any]:
    """Score every image referenced in the job's payload against the current production model.

    Payload shape: ``{"category": str, "images": [{"bucket": str, "key": str}, ...]}`` —
    references to already-uploaded raw images, not inline bytes: a 1000-image submission
    over Celery's message broker must stay small, which inline image bytes would not.
    """
    return _run(job_id, _execute_bulk_inference)


async def _execute_bulk_inference(container: Container, job: Job) -> dict[str, Any]:
    category = Category.parse(job.payload["category"])
    image_refs: list[dict[str, str]] = job.payload["images"]
    use_case = container.predict_image_use_case()

    chunk_size = container.settings.api.max_batch_size
    predicted = 0
    anomalous = 0
    for start in range(0, len(image_refs), chunk_size):
        chunk = image_refs[start : start + chunk_size]
        commands = []
        for ref in chunk:
            payload = await container.object_store.get(
                StorageLocation(bucket=ref["bucket"], key=ref["key"])
            )
            commands.append(PredictImageCommand(category=category, payload=payload))
        results = await use_case.execute_batch(commands)
        predicted += len(results)
        anomalous += sum(1 for result in results if result.is_anomalous)

        async with container.unit_of_work() as uow:
            current = await uow.jobs.get(job.id)
            current = current.report_progress(completed=predicted, total=len(image_refs))
            await uow.jobs.update(current)
            await uow.commit()

    return {"predicted": predicted, "anomalous": anomalous}


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    base=JobTask,
    name="factoryai.worker.tasks.run_retraining",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=_RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    max_retries=_MAX_RETRIES,
)
def run_retraining(self: Task, job_id: str) -> dict[str, Any]:
    """Run one training pass, exactly as ``factoryai train`` would, via :class:`TrainModel`."""
    return _run(job_id, _execute_retraining)


async def _execute_retraining(container: Container, job: Job) -> dict[str, Any]:
    return await pipeline_client.train(container, job.payload, started_by=job.submitted_by)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    base=JobTask,
    name="factoryai.worker.tasks.run_dataset_versioning",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=_RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    max_retries=_MAX_RETRIES,
)
def run_dataset_versioning(self: Task, job_id: str) -> dict[str, Any]:
    """Freeze a new dataset version, exactly as ``factoryai dataset version`` would."""
    return _run(job_id, _execute_dataset_versioning)


async def _execute_dataset_versioning(container: Container, job: Job) -> dict[str, Any]:
    return await pipeline_client.version_dataset(
        container, job.payload, created_by=job.submitted_by
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    base=JobTask,
    name="factoryai.worker.tasks.run_drift_report",
    # No autoretry: drift detection has no implementation to retry into (see body below).
    max_retries=0,
)
def run_drift_report(self: Task, job_id: str) -> dict[str, Any]:
    """Generate a drift report.

    Raises:
        NotImplementedError: Always. Drift detection (Evidently, reference-window
            comparison) is Phase 11 scope — this task exists so the job infrastructure has
            every :class:`~factoryai.domain.value_objects.JobType` wired end-to-end, with
            the one type nothing can implement yet failing loudly and immediately rather
            than silently, as a documented scope cut (``docs/ROADMAP.md`` Phase 9).
        Exception: Whatever :func:`_run` raises while marking the job started.
    """
    return _run(job_id, _execute_drift_report)


async def _execute_drift_report(container: Container, job: Job) -> dict[str, Any]:
    raise NotImplementedError(
        "drift report generation requires the drift detector built in Phase 11"
    )


_TASK_BY_JOB_TYPE: dict[JobType, Task] = {
    JobType.BULK_INFERENCE: run_bulk_inference,
    JobType.RETRAINING: run_retraining,
    JobType.DATASET_VERSIONING: run_dataset_versioning,
    JobType.DRIFT_REPORT: run_drift_report,
}


def dispatch(job: Job) -> None:
    """Enqueue the Celery task matching ``job.job_type``.

    Called by the API and CLI immediately after :class:`~factoryai.application.use_cases.
    submit_job.SubmitJob` records a *new* job — never for one an idempotency-key lookup
    found already existing, which would enqueue the same work a second time.

    Uses ``job.id`` as the Celery task id, not merely as an argument: it makes ``GET
    /jobs/{id}`` and Celery's own task inspection agree on what "the same task" means, and
    lets a redelivered message land on the identical task id rather than minting a new one.
    """
    _TASK_BY_JOB_TYPE[job.job_type].apply_async(kwargs={"job_id": str(job.id)}, task_id=str(job.id))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="factoryai.worker.tasks.record_dead_letter"
)
def record_dead_letter(job_id: str, task_name: str, error: str, traceback: str) -> None:
    """Log a permanently failed task for operator visibility via Flower.

    Deliberately does nothing beyond logging: the job row itself already carries the
    authoritative failure record (:meth:`JobTask.on_failure`); this task's only purpose is
    to exist on the ``dead_letter`` queue so a human watching Flower sees dead work land
    somewhere distinct from a queue real jobs are still flowing through.
    """
    logger.error(
        "job.dead_letter",
        job_id=job_id,
        task_name=task_name,
        error=error,
        traceback=traceback,
    )
