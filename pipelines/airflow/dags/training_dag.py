"""``training``: run one training pass, triggerable manually or from ``retraining_dag``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from airflow.decorators import dag, task
from common import DEFAULT_ARGS, alert_on_failure, alert_on_sla_miss, run_train


@dag(
    dag_id="training",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    default_args={**DEFAULT_ARGS, "on_failure_callback": alert_on_failure},
    sla_miss_callback=alert_on_sla_miss,
    tags=["factoryai", "training"],
    params={
        "dataset_name": "bottle",
        "dataset_version_tag": "",
        "category": "bottle",
        "model_name": "patchcore",
        "backbone": None,
        "seed": 42,
        "device": "auto",
        "note": "",
    },
)
def training_dag() -> None:
    """Train one model version from a named dataset version."""

    @task(sla=timedelta(hours=2))
    def train(**context: dict) -> dict[str, object]:
        params = context["params"]
        return run_train(
            {
                "dataset_name": params["dataset_name"],
                "dataset_version_tag": params["dataset_version_tag"],
                "category": params["category"],
                "model_name": params["model_name"],
                "backbone": params["backbone"],
                "seed": params["seed"],
                "device": params["device"],
                "note": params["note"],
            }
        )

    train()


training_dag()
