"""``monitoring``: scheduled drift check against each enabled category's production model.

Generates and persists a :class:`~factoryai.domain.entities.monitoring.DriftReport`
(Phase 11, ADR-0014) on a daily schedule. Turning a breached signal into a Prometheus alert
is still the API's job (it polls ``DriftReportRepository.latest`` and exposes the result as
a gauge Alertmanager's rules watch), not this DAG's — see ADR-0014 for why alerting lives
there and not in a DAG callback. What this DAG does own, as of Phase 12 (ADR-0015), is
reacting to its own report: when :attr:`~factoryai.domain.entities.monitoring.DriftReport.
should_trigger_retraining` comes back true, it starts a ``retraining`` DAG run — the same
pipeline ``retraining_dag`` runs on demand, just triggered by drift instead of a person.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from airflow.api.common.trigger_dag import trigger_dag
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
    """Generate a drift report and, if it warrants one, start a retraining run."""

    @task(sla=timedelta(minutes=15))
    def check_drift(**context: dict) -> dict[str, object]:
        return run_drift_report({"category": context["params"]["category"]})

    @task(sla=timedelta(minutes=1))
    def trigger_retraining_if_needed(report: dict[str, object], **context: dict) -> None:
        category = context["params"]["category"]
        if not report["should_trigger_retraining"]:
            raise AirflowSkipException(
                f"category {category!r} drift severity {report['severity']!r} "
                "does not warrant retraining"
            )
        trigger_dag(
            dag_id="retraining",
            run_id=f"drift_triggered__{context['ts_nodash']}",
            conf={"category": category, "reason": f"drift-triggered ({report['severity']})"},
        )

    trigger_retraining_if_needed(check_drift())


monitoring_dag()
