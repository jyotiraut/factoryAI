"""``monitoring``: scheduled drift check.

Scope cut, documented rather than silently skipped (``docs/ROADMAP.md`` Phase 10, ADR-0013,
matching the identical cut in ``factoryai.worker.tasks.run_drift_report``): drift detection
does not exist until Phase 11. This DAG is wired end-to-end — schedule, SLA, failure
callback — with nothing behind its one task yet; it skips itself every run rather than
failing, since "not yet implemented" is not the same claim as "broken".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from airflow.decorators import dag, task
from airflow.exceptions import AirflowSkipException
from common import DEFAULT_ARGS, alert_on_failure, alert_on_sla_miss, run_drift_report


@dag(
    dag_id="monitoring",
    schedule="@daily",
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    default_args={**DEFAULT_ARGS, "on_failure_callback": alert_on_failure},
    sla_miss_callback=alert_on_sla_miss,
    tags=["factoryai", "monitoring"],
    params={"category": "bottle"},
)
def monitoring_dag() -> None:
    """Check for drift — currently always skips; see the module docstring."""

    @task(sla=timedelta(minutes=15))
    def check_drift(**context: dict) -> None:
        try:
            run_drift_report({"category": context["params"]["category"]})
        except NotImplementedError as exc:
            raise AirflowSkipException(str(exc)) from exc

    check_drift()


monitoring_dag()
