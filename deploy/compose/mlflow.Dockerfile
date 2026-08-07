# The upstream MLflow image has no Postgres or S3 client, so it cannot use the backend
# store and artifact store ADR-0004 chose. This image adds exactly those two drivers.
FROM python:3.11-slim

RUN pip install --no-cache-dir \
    "mlflow>=2.16,<3" \
    "psycopg2-binary>=2.9,<3" \
    "boto3>=1.35,<2"

EXPOSE 5000
