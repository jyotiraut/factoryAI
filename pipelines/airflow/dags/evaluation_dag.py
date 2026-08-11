"""``evaluation``: check a trained candidate against the gate's absolute floor.

Deliberately thin (see ``factoryai.pipeline_client.evaluate``'s docstring): the real,
auditable go/no-go decision — comparing against the current production incumbent — belongs
to ``deployment_dag`` alone. This DAG exists as its own triggerable unit so an operator (or
``retraining_dag``) can ask "is this candidate even worth attempting to promote" without
that attempt's side effects (a recorded rejection) if the answer is obviously no.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException
from common import DEFAULT_ARGS, alert_on_failure, alert_on_sla_miss, run_evaluate


@dag(
    dag_id="evaluation",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    default_args={**DEFAULT_ARGS, "on_failure_callback": alert_on_failure},
    sla_miss_callback=alert_on_sla_miss,
    tags=["factoryai", "training"],
    params={"model_version_id": ""},
)
def evaluation_dag() -> None:
    """Fail loudly if a candidate does not clear the absolute AUROC floor."""

    @task(sla=timedelta(minutes=10))
    def evaluate(**context: dict) -> dict[str, object]:
        result = run_evaluate(model_version_id=context["params"]["model_version_id"])
        if not result["passed"]:
            raise AirflowFailException(
                f"model {result['model_version_id']} scored image_auroc="
                f"{result['image_auroc']:.4f}, below the required minimum "
                f"{result['min_auroc']:.4f} — not worth attempting deployment"
            )
        return result

    evaluate()


evaluation_dag()
