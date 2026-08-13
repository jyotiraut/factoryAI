# Runbook: high API error rate (`FactoryAIHighErrorRate`)

## What fired

More than 5% of `factoryai_http_requests_total` over the last 5 minutes carried a `5xx`
status code.

## First steps

1. Open the **Service Health** Grafana dashboard's error-rate panel and check whether the
   spike is isolated to one route (`path` label) or spread across all of them.
2. Check `GET /health/ready` directly — if it reports `degraded`, the underlying cause is
   almost certainly Postgres or MLflow being unreachable, not application code.
3. Check the API process's own logs (structured JSON, correlation-id tagged) for the
   correlation ids of failing requests around the alert's start time.
4. If the errors are concentrated on `/predict` or `/batch-predict`: check whether a model
   promotion happened around the same time (`ModelCache` reload failures surface here) and
   whether MinIO/the model registry is reachable.

## Common causes and fixes

- **Postgres or MinIO down/unreachable**: restart the affected container
  (`docker compose restart postgres` / `minio`); confirm `/health/ready` recovers.
- **A bad model promotion**: `factoryai model rollback --category <category>` restores the
  previous production version.
- **Request validation errors miscounted as 5xx**: check the route — 4xx (bad input) should
  not trigger this alert; if it is, the route's error handling has a bug misclassifying a
  client error as a server error.

## Escalation

Page the on-call engineer if `/health/ready` does not recover within 10 minutes of a
dependency restart.
