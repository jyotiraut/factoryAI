"""``deployment``: attempt promotion — the real, auditable go/no-go call.

A rejection is not a bug (see ``PromoteModel.execute``'s docstring: it is recorded exactly
as deliberately as a promotion), so this task turns a rejection into a *skip*, not a
failure — Airflow's status vocabulary has no built-in "the answer was legitimately no"
outcome closer than that, and marking it a hard failure would page an on-call operator for
something the gate is working exactly as designed to catch. ``common.PromotionRejectedError``,
not the domain exception of the same name: this file runs in Airflow's own Python process,
which cannot import ``factoryai`` at all (ADR-0013's "Consequences") — see
``common.py``'s module docstring.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from airflow.decorators import dag, task
from airflow.exceptions import AirflowSkipException
from common import (
    DEFAULT_ARGS,
    PromotionRejectedError,
    alert_on_failure,
    alert_on_sla_miss,
    run_deploy,
)


@dag(
    dag_id="deployment",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    default_args={**DEFAULT_ARGS, "on_failure_callback": alert_on_failure},
    sla_miss_callback=alert_on_sla_miss,
    tags=["factoryai", "deployment"],
    params={"category": "bottle", "model_version_id": "", "reason": "airflow deployment DAG"},
)
def deployment_dag() -> None:
    """Attempt to promote a candidate model version to production."""

    @task(sla=timedelta(minutes=10))
    def deploy(**context: dict) -> dict[str, object]:
        params = context["params"]
        try:
            return run_deploy(
                category=params["category"],
                model_version_id=params["model_version_id"],
                reason=params["reason"],
            )
        except PromotionRejectedError as exc:
            raise AirflowSkipException(f"promotion rejected: {exc.message}") from exc

    deploy()


deployment_dag()
