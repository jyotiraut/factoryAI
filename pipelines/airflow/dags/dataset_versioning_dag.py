"""``dataset_versioning``: freeze a new dataset version on demand.

Triggerable manually (an operator wants a fresh snapshot right now) or by
``retraining_dag`` — this file and that one call the identical
``common.run_version_dataset``, never their own copy of the logic (ADR-0005, ADR-0013).
The dag run's ``conf`` supplies everything :class:`~factoryai.application.use_cases.
create_dataset_version.CreateDatasetVersionCommand` needs; there is no sensible schedule
for "version whatever is trainable right now", so this DAG has none — it waits to be
triggered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from airflow.decorators import dag, task
from common import DEFAULT_ARGS, alert_on_failure, alert_on_sla_miss, run_version_dataset


@dag(
    dag_id="dataset_versioning",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    default_args={**DEFAULT_ARGS, "on_failure_callback": alert_on_failure},
    sla_miss_callback=alert_on_sla_miss,
    tags=["factoryai", "dataset"],
    params={
        "dataset_name": "bottle",
        "category": "bottle",
        "version_tag": "",
        "seed": 42,
        "note": "",
    },
)
def dataset_versioning_dag() -> None:
    """Freeze the current trainable set into a new dataset version."""

    @task(sla=timedelta(minutes=15))
    def version_dataset(**context: dict) -> dict[str, object]:
        params = context["params"]
        return run_version_dataset(
            {
                "dataset_name": params["dataset_name"],
                "category": params["category"],
                "version_tag": params["version_tag"] or f"auto-{context['ts_nodash']}",
                "seed": params["seed"],
                "note": params["note"],
            }
        )

    version_dataset()


dataset_versioning_dag()
