# Airflow's own official image has no `factoryai` package inside it, and — because
# LocalExecutor (ADR-0005: no separate Celery-for-Airflow layer) runs every task in the
# same container as the scheduler — every DAG task that calls `factoryai.pipeline_client`
# needs the full stack that client depends on: `storage` (Postgres/MinIO), `imaging`
# (Pillow/imagehash), `versioning` (DVC/Git) and `ml` (Anomalib/PyTorch/MLflow) for the
# training and evaluation DAGs. This mirrors `mlflow.Dockerfile`'s reasoning — the upstream
# image is deliberately minimal, so anything a DAG task actually calls has to be added here,
# not assumed.
#
# Pinned to Airflow's own published constraints file for this exact version/Python
# combination, the same reason `pyproject.toml`'s own dependency groups pin ranges: Airflow
# 2.x's dependency set (older Flask/SQLAlchemy-adjacent packages in some releases) can
# otherwise silently downgrade something this image needs.
FROM apache/airflow:2.10.4-python3.11

USER airflow

COPY pyproject.toml /tmp/factoryai/pyproject.toml
COPY src /tmp/factoryai/src

RUN pip install --no-cache-dir \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.4/constraints-3.11.txt" \
    "/tmp/factoryai[storage,imaging,versioning,ml]"
