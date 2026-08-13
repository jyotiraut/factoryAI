"""``data_validation``: wait for staged images, then ingest and validate every one of them.

Airflow's counterpart to `factoryai ingest` (Phase 3, filesystem-sourced): a camera or
upload pipeline writes files into the raw bucket's `incoming/<category>/` prefix, and this
DAG picks them up on a schedule instead of a human running the CLI. See ADR-0013.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from airflow.decorators import dag, task
from airflow.sensors.python import PythonSensor
from common import (
    DEFAULT_ARGS,
    alert_on_failure,
    alert_on_sla_miss,
    check_staged_images,
    run_ingest,
)

_CATEGORY = "bottle"
_PREFIX = f"incoming/{_CATEGORY}/"


def _staged_images_exist() -> bool:
    """Return whether any object is waiting under the staging prefix.

    The sensor this backs is what makes "no long operation blocks" apply here too: an
    empty prefix reschedules the poke instead of running an ingestion batch over nothing.
    """
    return check_staged_images(prefix=_PREFIX)


@dag(
    dag_id="data_validation",
    schedule="@hourly",
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    default_args={**DEFAULT_ARGS, "on_failure_callback": alert_on_failure},
    sla_miss_callback=alert_on_sla_miss,
    tags=["factoryai", "ingestion"],
)
def data_validation_dag() -> None:
    """Wait for new staged images, then validate and ingest all of them."""
    wait_for_images = PythonSensor(
        task_id="wait_for_staged_images",
        python_callable=_staged_images_exist,
        poke_interval=60,
        timeout=timedelta(hours=1).total_seconds(),
        mode="reschedule",
        sla=timedelta(minutes=90),
    )

    @task(sla=timedelta(minutes=30))
    def ingest() -> dict[str, int]:
        return run_ingest(category=_CATEGORY, prefix=_PREFIX)

    wait_for_images >> ingest()


data_validation_dag()
