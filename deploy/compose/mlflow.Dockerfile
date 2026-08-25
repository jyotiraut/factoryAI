# The upstream MLflow image has no Postgres or S3 client, so it cannot use the backend
# store and artifact store ADR-0004 chose. This image adds exactly those two drivers.
FROM python:3.11-slim

RUN pip install --no-cache-dir \
    "mlflow>=2.16,<3" \
    "psycopg2-binary>=2.9,<3" \
    "boto3>=1.35,<2"

# Found live (Phase 14 CI hardening): Trivy's `DS-0002` check flags any Dockerfile with no
# `USER` at all as running root by default — real here, unlike `factoryai.Dockerfile` and
# `airflow.Dockerfile`, which both already drop root.
RUN useradd --create-home --uid 10001 mlflow
USER mlflow

EXPOSE 5000
