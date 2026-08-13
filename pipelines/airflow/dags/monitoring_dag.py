"""``monitoring``: scheduled drift check against each enabled category's production model.

Generates and persists a :class:`~factoryai.domain.entities.monitoring.DriftReport`
(Phase 11, ADR-0014) on a daily schedule. The report itself is the only output this DAG
produces — turning a breached signal into a Prometheus alert is the API's job (it polls
``DriftReportRepository.latest`` and exposes the result as a gauge Alertmanager's rules
watch), not Airflow's; see ADR-0014 for why alerting lives there and not in a DAG
callback. Automatically triggering a retraining run from a drift alert is Phase 12 scope —
this DAG only measures and records.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from airflow.decorators import dag, task
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
    """Generate a drift report for the category's current production model."""

    @task(sla=timedelta(minutes=15))
    def check_drift(**context: dict) -> dict[str, object]:
        return run_drift_report({"category": context["params"]["category"]})

    check_drift()


monitoring_dag()
