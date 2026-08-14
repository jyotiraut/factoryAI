# Capacity model

What one `factoryai-api` pod is sized for, the latency budget each endpoint is held to,
and how the Helm chart's autoscaling settings (`values.yaml`'s `api.autoscaling`) were
chosen — the ROADMAP's own cross-cutting practice: "latency budgets stated per endpoint
and asserted in tests" (`docs/ROADMAP.md`, "Cross-cutting, done continuously").

## Per-pod resource footprint

From `deploy/helm/factoryai/values.yaml`:

| Component | CPU request | CPU limit | Memory request | Memory limit |
|---|---|---|---|---|
| `api` | 250m | 1 | 512Mi | 2Gi |
| `worker` | 500m | 2 | 1Gi | 4Gi |

The `api` pod's request accounts for FastAPI/uvicorn plus a warm anomaly-detector cache
(`ModelCache`, Phase 11) for every enabled category — `warm_up()` (`api/main.py`) loads
one detector per category at startup specifically so the first real request does not pay
model-load latency, at the cost of holding that memory for the pod's whole lifetime.
`worker`'s higher ceiling accounts for a training run's actual working set (`anomalib`'s
feature extraction backbone plus PatchCore's memory bank), not just inference.

## Latency budgets

| Endpoint | Budget (p95) | Why |
|---|---|---|
| `GET /health/live` | 50ms | No I/O — process-local only. |
| `GET /health/ready` | 200ms | One DB round trip. |
| `GET /models`, `/predictions`, `/drift/reports`, etc. (Phase 13 dashboard reads) | 300ms | One paginated query each; `func.count()` runs in the same round trip as the page itself (ADR-0016), not a second query. |
| `POST /predict` | 2s | Dominated by inference, not the HTTP layer — `ApiSettings.request_timeout_seconds` (default 30s) is the hard ceiling; 2s is the target for a warm, cached detector on CPU. |
| `POST /feedback` | 300ms | One write, one audit-chain append. |

These are targets this document states, not (yet) gates a test enforces — Phase 7/13's
existing unit tests assert *behavioural* correctness (a 200, a correctly-shaped body), not
wall-clock latency; wiring a latency assertion into CI would need a dedicated, isolated
benchmark environment (shared CI runners have too much noise for a meaningful ms-level
gate) that does not exist yet. Real, tracked follow-up work, not a silent gap.

## Autoscaling

`api.autoscaling` (Helm chart): `minReplicas: 2`, `maxReplicas: 6` (`values.yaml`) /
`minReplicas: 3`, `maxReplicas: 10` (`values-production.yaml`), scaling on 65–70% CPU
utilization. Two is the floor everywhere, not one — `podDisruptionBudget.minAvailable: 1`
(dev) / `2` (production) only has teeth if there is more than one replica to begin with;
a single-replica deployment makes every rolling update and every voluntary node drain a
guaranteed availability gap.

`worker` has no HPA in this chart — Celery's own queue depth (`factoryai_jobs` gauge,
Phase 11, exposed at `GET /metrics`) is the signal that should drive worker scaling, not
CPU, since a worker can sit idle at 0% CPU with a deep backlog waiting on I/O. A
`HorizontalPodAutoscaler` driven by a custom metric (`external.metrics.k8s.io`, fed by
Prometheus Adapter reading that exact gauge) is the correct design — not built here,
since it needs Prometheus already reachable from inside the cluster the API's own gauges
are exposed to, a piece of cluster wiring out of this phase's scope. `worker.replicaCount`
is a static value an operator tunes by hand until that adapter exists.

## Load test

`deploy/loadtest/locustfile.py` drives the mix a real operator session actually produces:
mostly reads (`/health/live`, `/models`, `/predictions`), occasionally a real `/predict`
call. See that file's own docstring for how to run it, and ADR-0017's "Live verification"
section for what a real run against this environment did and did not confirm — a
short local run exercised the health/models/predictions paths successfully; `/predict`
specifically could not be driven end-to-end in this sandbox for the same reason a live
Postgres connection from the host could not be established elsewhere in this session (see
ADR-0014/ADR-0016), not a defect in the load test itself.
