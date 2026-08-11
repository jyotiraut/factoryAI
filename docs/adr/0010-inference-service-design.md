# ADR-0010 — Inference service: cache invalidation, health split, and backpressure

**Status:** accepted · **Date:** 2026-08-07

## Context

`factoryai.api` (FastAPI) serves the model `PromoteModel` puts into production. Three
decisions had no obvious single answer: how a running process learns a new model was
promoted without restarting, what "healthy" means for a process that depends on both a
database and MLflow, and how to keep one slow request from degrading every other one.

## Decisions

**Hot-reload piggybacks on the read the request needs anyway.** `PredictImage` already
reads the category's current production `ModelVersion` from PostgreSQL on every request —
ADR-0004 made PostgreSQL authoritative for stage decisions, not MLflow. `ModelCache.get`
compares that row's id against whichever detector is already loaded and only re-downloads
and reloads when they differ. No background poller, no MLflow round trip on the hot path,
and a promotion is visible to new requests within one request's latency of landing —
not on a fixed poll interval.

**Liveness never touches a dependency; readiness always does.** `/health/live` answers
"is the process alive" unconditionally — a database outage must never cause a container
orchestrator to kill and restart an otherwise-fine process, which is exactly what a
liveness probe that queries the database risks. `/health/ready` is the one that actually
checks PostgreSQL (a real query) and MLflow (its own `/health` endpoint) and reports
`503` when either is down, which is what a load balancer should route around.

**Backpressure is three independent, narrow mechanisms, not one generic limiter.** A
`Content-Length`-checking middleware rejects an oversized upload before it is buffered
into memory; an `asyncio.Semaphore` sized by `API_MAX_CONCURRENT_PREDICTIONS` bounds how
many CPU-bound inference calls run at once (each dispatched to a thread per ADR-0008, so
without a cap every concurrent request spawns a thread and they all contend for the same
CPU); `asyncio.timeout` bounds how long any single prediction is allowed to run. Each is
independently reasoned about and independently testable, rather than one "rate limiter"
component trying to be all three.

**Inference images are persisted but never validated or deduplicated.** `PredictImage` is
deliberately not `IngestImage` reused: ingestion's validation chain and duplicate
detection are training-data concerns. A camera repeatedly photographing the same nominal
product is normal production traffic, not a duplicate to reject — every prediction must be
scored and persisted (Phase 11's drift reference needs the full distribution), so the
image is stored with none of that gatekeeping and left in `PENDING` status, which already
keeps it out of `list_trainable` without inventing a new status value.

## Consequences

**Positive:** hot-reload requires no new infrastructure (no poller, no message queue) and
is trivially correct — the same row a request already needed to read is the same row that
answers "did anything change". The health split lets an orchestrator distinguish "restart
me" from "stop sending me traffic" instead of conflating them. Backpressure failures are
specific (413 for size, 504 for timeout, a queued wait for concurrency) rather than one
generic "too busy" response.

**Negative:** every request pays one extra `find_by_stage` + `get(experiment)` query
before it can even begin inference — cheap against an indexed table, but not free, and a
design that polled MLflow on a timer instead would have traded that per-request cost for
staleness up to the poll interval. `ModelCache` is process-local: a multi-worker deployment
(`API_WORKERS > 1`) reloads independently per worker, each paying its own download cost on
the first request after a promotion — acceptable at this scale, revisit with a shared cache
(Redis) if worker count grows enough for that to matter.
