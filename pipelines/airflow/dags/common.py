"""Shared plumbing every DAG in this directory imports — never business logic.

ADR-0005's split ("Airflow DAG files ... contain no business logic — a DAG task is a
use-case invocation and nothing else") is enforced by convention, not by a linter rule, so
this module exists to make the convention easy to follow: every DAG file below calls
exactly one ``run_*``/``check_staged_images`` function per task, and reports failures
through :func:`alert_on_failure`. Nothing here decides what a task *does* — only how it is
wired into Airflow.

None of this module imports ``factoryai`` directly (ADR-0013's "Consequences", discovered
while actually building the image, not predicted in the abstract): every Airflow 2.x
release pins ``SQLAlchemy==1.4.54`` in its own constraints file, and this platform requires
``sqlalchemy>=2.0``, so ``factoryai`` cannot live in Airflow's own Python environment.
``airflow.Dockerfile`` instead builds a second, independent virtualenv
(``/opt/factoryai-venv``) with ``factoryai`` installed free of Airflow's constraints; every
``run_*`` function here shells out to that interpreter running
:mod:`factoryai.pipeline_runner`, which is the only thing that actually imports
``factoryai``.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

logger = logging.getLogger("factoryai.airflow")


class PromotionRejectedError(Exception):
    """Raised by :func:`_call` when a subprocess reports a business rejection.

    Not imported from :mod:`factoryai.domain.errors` — this module deliberately never
    imports ``factoryai`` at all (see the module docstring); ``deployment_dag`` and
    ``retraining_dag`` catch this local class instead of the domain one.
    """

    def __init__(self, message: str) -> None:
        """Initialise with the rejection reason reported by the subprocess."""
        super().__init__(message)
        self.message = message


FACTORYAI_PYTHON = "/opt/factoryai-venv/bin/python"
"""The isolated interpreter every ``run_*`` function below shells out to."""

_EXIT_REJECTED = 3
_EXIT_NOT_IMPLEMENTED = 4

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


def _call(*args: str) -> dict[str, Any]:
    """Run one ``factoryai.pipeline_runner`` subcommand and return its parsed JSON result.

    Raises:
        PromotionRejectedError: If the subprocess exited with the "business rejection"
            code — reconstructed here, on this side of the process boundary, since a
            Python exception cannot itself cross it.
        NotImplementedError: If the subprocess exited with the "not implemented yet" code.
        subprocess.CalledProcessError: For any other non-zero exit — a genuine failure,
            left for Celery-style ``retries``/``on_failure_callback`` to handle exactly
            like any other task exception.
    """
    result = subprocess.run(
        [FACTORYAI_PYTHON, "-m", "factoryai.pipeline_runner", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return dict(json.loads(result.stdout))
    if result.returncode == _EXIT_REJECTED:
        raise PromotionRejectedError(json.loads(result.stdout)["message"])
    if result.returncode == _EXIT_NOT_IMPLEMENTED:
        raise NotImplementedError(json.loads(result.stdout)["message"])
    raise subprocess.CalledProcessError(
        result.returncode, result.args, output=result.stdout, stderr=result.stderr
    )


def run_ingest(*, category: str, prefix: str, label: str = "unlabeled") -> dict[str, Any]:
    """Task body shared by ``data_validation_dag`` and ``retraining_dag``."""
    return _call("ingest", "--category", category, "--prefix", prefix, "--label", label)


def check_staged_images(*, prefix: str) -> bool:
    """Return whether any object is waiting under ``prefix`` in the raw bucket.

    Backs ``data_validation_dag``'s sensor: an empty prefix reschedules the poke instead of
    running an ingestion batch over nothing.
    """
    return bool(_call("staged-images-exist", "--prefix", prefix)["exists"])


def run_version_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    """Task body shared by ``dataset_versioning_dag`` and ``retraining_dag``."""
    return _call("version-dataset", "--payload", json.dumps(payload))


def run_train(payload: dict[str, Any]) -> dict[str, Any]:
    """Task body shared by ``training_dag`` and ``retraining_dag``."""
    return _call("train", "--payload", json.dumps(payload))


def run_evaluate(*, model_version_id: str) -> dict[str, Any]:
    """Task body shared by ``evaluation_dag`` and ``retraining_dag``."""
    return _call("evaluate", "--model-version-id", model_version_id)


def run_deploy(*, category: str, model_version_id: str, reason: str = "") -> dict[str, Any]:
    """Task body shared by ``deployment_dag`` and ``retraining_dag``.

    Raises:
        PromotionRejectedError: If the candidate fails the gate — see :func:`_call`. The
            rejection is still durably recorded; only this process's view of the outcome
            is an exception.
    """
    return _call(
        "deploy", "--category", category, "--model-version-id", model_version_id, "--reason", reason
    )


def run_drift_report(payload: dict[str, Any]) -> dict[str, Any]:
    """Task body for ``monitoring_dag``.

    Raises:
        NotImplementedError: Always — see :func:`factoryai.pipeline_client.
            generate_drift_report`. ``monitoring_dag`` catches this and skips rather than
            fails.
    """
    return _call("drift-report", "--payload", json.dumps(payload))


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
        "task_failed dag_id=%s task_id=%s run_id=%s exception=%s",
        context["dag"].dag_id if context.get("dag") else None,
        task_instance.task_id if task_instance else None,
        context.get("run_id"),
        context.get("exception"),
    )


def alert_on_sla_miss(
    dag: Any, task_list: Any, blocking_task_list: Any, slas: Any, blocking_tis: Any
) -> None:
    """Log an SLA miss — same extension point and same reasoning as :func:`alert_on_failure`."""
    logger.warning(
        "sla_missed dag_id=%s task_list=%s slas=%s",
        dag.dag_id if dag is not None else None,
        task_list,
        slas,
    )
