"""Shared plumbing every DAG in this directory imports — never business logic.

ADR-0005's split ("Airflow DAG files ... contain no business logic — a DAG task is a
use-case invocation and nothing else") is enforced by convention, not by a linter rule, so
this module exists to make the convention easy to follow: every DAG file below builds a
container with :func:`container`, calls exactly one :mod:`factoryai.pipeline_client`
function per task, and reports failures through :func:`alert_on_failure`. Nothing here
decides what a task *does* — only how it is wired into Airflow.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from typing import Any, TypeVar

from factoryai import pipeline_client
from factoryai.bootstrap.container import Container, build_container
from factoryai.shared.asyncio_compat import configure_event_loop_policy
from factoryai.shared.config import get_settings
from factoryai.shared.logging import configure_logging, get_logger

_T = TypeVar("_T")

logger = get_logger(__name__)

DEFAULT_ARGS: dict[str, Any] = {
    "owner": "factoryai",
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
}
"""Applied to every task via each DAG's own ``default_args``.

Exponential backoff, not a fixed delay: a task failing because Postgres or MinIO is
mid-restart should back off, not hammer an already-struggling dependency three times in
quick succession.
"""


def container() -> Container:
    """Build a fresh composition root from this process's environment.

    Not cached across calls the way :func:`factoryai.worker.tasks._worker_container` caches
    per Celery worker process: Airflow's ``LocalExecutor`` runs each task instance in its
    own forked process, so there is no cross-task process to cache a container in, and
    building one is cheap relative to the training/ingestion work every task actually does.
    """
    configure_event_loop_policy()
    settings = get_settings()
    configure_logging(level=settings.log_level, log_format=settings.log_format, service="airflow")
    return build_container(settings)


def run(coroutine_factory: Callable[[Container], Awaitable[_T]]) -> _T:
    """Run one pipeline-client call to completion inside a fresh event loop.

    Args:
        coroutine_factory: A callable taking the container this call builds and returning
            the awaitable to run — a callable rather than a bare coroutine so the container
            is constructed *inside* this function, after the event loop policy is set.
    """
    return asyncio.run(coroutine_factory(container()))


def run_ingest(*, category: str, prefix: str, label: str = "unlabeled") -> dict[str, Any]:
    """Task body shared by ``data_validation_dag`` and ``retraining_dag``."""
    return run(
        lambda c: pipeline_client.ingest_from_object_store(
            c, category=category, prefix=prefix, label=label
        )
    )


def run_version_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    """Task body shared by ``dataset_versioning_dag`` and ``retraining_dag``."""
    return run(lambda c: pipeline_client.version_dataset(c, payload))


def run_train(payload: dict[str, Any]) -> dict[str, Any]:
    """Task body shared by ``training_dag`` and ``retraining_dag``."""
    return run(lambda c: pipeline_client.train(c, payload))


def run_evaluate(*, model_version_id: str) -> dict[str, Any]:
    """Task body shared by ``evaluation_dag`` and ``retraining_dag``."""
    return run(lambda c: pipeline_client.evaluate(c, model_version_id=model_version_id))


def run_deploy(*, category: str, model_version_id: str, reason: str = "") -> dict[str, Any]:
    """Task body shared by ``deployment_dag`` and ``retraining_dag``."""
    return run(
        lambda c: pipeline_client.deploy(
            c, category=category, model_version_id=model_version_id, reason=reason
        )
    )


def run_drift_report(payload: dict[str, Any]) -> dict[str, Any]:
    """Task body shared by ``monitoring_dag``.

    Raises:
        NotImplementedError: Always — see :func:`factoryai.pipeline_client.
            generate_drift_report`. ``monitoring_dag`` catches this and skips rather than
            fails; every other caller lets it propagate.
    """
    return run(lambda c: pipeline_client.generate_drift_report(c, payload))


def alert_on_failure(context: Mapping[str, Any]) -> None:
    """Log a task failure with enough context to act on.

    This is the extension point a real deployment wires a Slack or PagerDuty webhook into
    (ADR-0013) — structured logging is what ships here, since this project has no
    third-party alerting credentials to integrate against. Anything reading these logs
    (a log-based alert in the eventual Phase 11 monitoring stack, an operator's `grep`) gets
    the same fields either way.
    """
    task_instance = context.get("task_instance")
    logger.error(
        "airflow.task_failed",
        dag_id=context.get("dag").dag_id if context.get("dag") else None,
        task_id=task_instance.task_id if task_instance else None,
        run_id=context.get("run_id"),
        exception=str(context.get("exception")),
    )


def alert_on_sla_miss(
    dag: Any, task_list: Any, blocking_task_list: Any, slas: Any, blocking_tis: Any
) -> None:
    """Log an SLA miss — same extension point and same reasoning as :func:`alert_on_failure`."""
    logger.warning(
        "airflow.sla_missed",
        dag_id=dag.dag_id if dag is not None else None,
        task_list=str(task_list),
        slas=str(slas),
    )
