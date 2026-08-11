"""The Celery application instance every worker process and every dispatcher shares.

Three queues separate the platform's long-running work by resource profile (ADR-0012):
``training`` (GPU/CPU-heavy, minutes to hours), ``inference`` (bulk scoring, seconds to
minutes) and ``reports`` (dataset versioning, drift analysis — I/O-bound, seconds). A
fourth, ``dead_letter``, holds nothing but records of tasks that exhausted their retries —
routing it separately is what lets an operator watch it in Flower without it being mixed
into a real work queue.
"""

from __future__ import annotations

from celery import Celery

from factoryai.shared.config import get_settings

celery_app = Celery("factoryai")


def _configure(app: Celery) -> None:
    """Apply this process's settings to the shared Celery app.

    Kept as its own function, called once at import time, so a worker started via
    ``celery -A factoryai.worker.celery_app worker`` and one started via
    :func:`factoryai.worker.tasks.dispatch` observe identical configuration — nothing here
    is set only by whichever entry point happens to run first.
    """
    celery = get_settings().celery
    app.conf.update(
        broker_url=celery.broker_url,
        result_backend=celery.result_backend,
        task_time_limit=celery.task_time_limit_seconds,
        task_track_started=True,
        # A task that survives a worker crash mid-execution must be redelivered rather than
        # silently dropped — the Phase 9 exit criterion "a worker crash mid-job does not
        # lose or duplicate work" rests on this pair: late acks plus a broker configured
        # to redeliver on lost visibility (Redis's own default is to hold the message
        # until it's acked, matching this without extra configuration).
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_default_queue="inference",
        task_routes={
            "factoryai.worker.tasks.run_bulk_inference": {"queue": "inference"},
            "factoryai.worker.tasks.run_retraining": {"queue": "training"},
            "factoryai.worker.tasks.run_dataset_versioning": {"queue": "reports"},
            "factoryai.worker.tasks.run_drift_report": {"queue": "reports"},
            "factoryai.worker.tasks.record_dead_letter": {"queue": "dead_letter"},
        },
    )


_configure(celery_app)
