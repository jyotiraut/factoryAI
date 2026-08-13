"""``retraining``: the full version → train → evaluate → deploy pipeline, one DAG run.

This is the reusable pipeline Phase 12 will trigger from a drift alert instead of a
schedule or a manual run — only the trigger source differs; the steps are identical, and
each one calls the exact same ``common.run_*`` helper its own standalone DAG
(``dataset_versioning``, ``training``, ``evaluation``, ``deployment``) calls, so there is
nothing to keep in sync between "the composite pipeline" and "the four independent DAGs"
beyond that one shared helper module (ADR-0013).

Deliberately four tasks in one DAG rather than four ``TriggerDagRunOperator`` calls across
the standalone DAGs: passing a dynamically computed value (the just-trained
``model_version_id``) into a triggered DAG's ``conf`` needs either cross-DAG XCom lookups
or a rendered Jinja expression against another DAG's run — solvable, but native TaskFlow
XCom within one DAG is simpler and is what a genuinely single logical pipeline calls for.
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
    run_evaluate,
    run_train,
    run_version_dataset,
)


@dag(
    dag_id="retraining",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    default_args={**DEFAULT_ARGS, "on_failure_callback": alert_on_failure},
    sla_miss_callback=alert_on_sla_miss,
    tags=["factoryai", "training", "deployment"],
    params={
        "dataset_name": "bottle",
        "category": "bottle",
        "model_name": "patchcore",
        "seed": 42,
        "reason": "automated retraining pipeline",
    },
)
def retraining_dag() -> None:
    """Version the current trainable set, train on it, evaluate, then attempt deployment."""

    @task(sla=timedelta(minutes=15))
    def version_dataset(**context: dict) -> dict[str, object]:
        params = context["params"]
        return run_version_dataset(
            {
                "dataset_name": params["dataset_name"],
                "category": params["category"],
                "version_tag": f"auto-{context['ts_nodash']}",
                "seed": params["seed"],
                "note": "retraining pipeline",
            }
        )

    @task(sla=timedelta(hours=2))
    def train(version: dict[str, object], **context: dict) -> dict[str, object]:
        params = context["params"]
        return run_train(
            {
                "dataset_name": params["dataset_name"],
                "dataset_version_tag": version["version_tag"],
                "category": params["category"],
                "model_name": params["model_name"],
                "seed": params["seed"],
                "note": "retraining pipeline",
            }
        )

    @task(sla=timedelta(minutes=10))
    def evaluate(trained: dict[str, object]) -> dict[str, object]:
        result = run_evaluate(model_version_id=trained["model_version_id"])
        if not result["passed"]:
            raise AirflowSkipException(
                f"model {result['model_version_id']} scored image_auroc="
                f"{result['image_auroc']:.4f}, below the required minimum "
                f"{result['min_auroc']:.4f} — stopping before a deployment attempt"
            )
        return result

    @task(sla=timedelta(minutes=10))
    def deploy(evaluated: dict[str, object], **context: dict) -> dict[str, object]:
        params = context["params"]
        try:
            return run_deploy(
                category=params["category"],
                model_version_id=evaluated["model_version_id"],
                reason=params["reason"],
            )
        except PromotionRejectedError as exc:
            raise AirflowSkipException(f"promotion rejected: {exc.message}") from exc

    deploy(evaluate(train(version_dataset())))


retraining_dag()
