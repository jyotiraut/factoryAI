# ADR-0014 — Monitoring and drift detection: reference windows, pull-model gauges, and Evidently

**Status:** accepted · **Date:** 2026-08-13

## Context

Phase 1 already scaffolded the shape this phase has to fill in: a `DriftDetector` port, a
`DriftReport`/`DriftSignal` entity pair with severity grading already written, and
`DriftSettings` with two threshold knobs. Phases 9 and 10 both left a documented
`NotImplementedError` behind `run_drift_report`/`generate_drift_report`, waiting for this
phase. Three questions had no answer already on record: what a drift *reference*
distribution actually is when nothing persists per-training-image scores; how a metric
computed by a periodic batch job (Airflow, Celery) becomes something Prometheus — a pull-
based system — can alert on; and which of Evidently's two very different API surfaces
(the modern `Report`/`Dataset` orchestration layer, or the lower-level statistical-test
registry) this platform actually needs.

## Decisions

**The reference distribution is a model's own earliest predictions, not a re-scored
training set.** Phase 2's `Prediction` entity docstring already committed to this: "the
distribution of all scores is the reference signal drift detection compares against."
`GenerateDriftReport` reads two windows through the *same* `PredictionRepository.
list_in_window` method — the earliest `reference_sample_size` predictions this production
model has ever served (via `production.created_at` as the window's start, ascending order,
`limit` applied) as reference, and the last `window_hours` as current. This needed no new
infrastructure: no detector reload, no object-store access, no second CPU-bound inference
pass over the training set on every drift check. The `reference_dataset_version_id` field
on `DriftReport` is still populated (via the model's experiment), preserving that lineage
fact, even though the reference *distribution* comes from predictions, not from re-scoring
the dataset version's images.

**Two signals, not four.** The ROADMAP names data drift, prediction drift, feature/
embedding drift, and confidence/anomaly-score shift as in scope. `AnomalyDetector.predict`
returns a score and a heatmap, not a raw feature vector — extracting genuine embeddings
would mean widening that port, a real, separate piece of work. What ships: `anomaly_score`
(the direct "prediction drift" signal) and `confidence` (a data-quality-adjacent signal
computed from the same score via `AnomalyScore.confidence`). `DriftSettings.
prediction_threshold` gates the first, `data_threshold` gates the second — a deliberate,
documented interpretation of two generically-named config knobs, not left ambiguous.
Feature/embedding-level drift is out of scope until `AnomalyDetector` exposes raw
features — real, tracked future work, not silently dropped.

**Evidently's statistical-test registry (`evidently.legacy.calculations.stattests`), not
its `Report`/`Dataset` layer.** `DriftDetector.compare()` only needs one statistic and a
pass/fail per named distribution pair — no HTML report, no notion of a "column" or a
dataset schema. The registry's `get_stattest(reference, current, column_type, method)`
gives exactly that, via Wasserstein distance (sensitive to a distribution's whole shape and
location, appropriate for scores that are bounded and often skewed rather than normal).
Verified directly against synthetic same-distribution and shifted-distribution samples
before writing the use case around it — the same-distribution case does occasionally
report a small statistic near the configured threshold, which is expected sampling noise
at n≈200, not a detector bug.

**Live gauges are recomputed on every Prometheus scrape — no background refresh loop.**
CPU/memory/disk, model-cache hit ratio, job-queue depth by status, and drift severity per
category are all *read fresh* inside the `/metrics` handler itself (`psutil` calls, a
`JobRepository.count_by_status()` query, `DriftReportRepository.latest()` per enabled
category) rather than maintained by a periodic asyncio task started at process startup.
Prometheus's own pull model already re-scrapes on its configured interval (15s here), so a
live query on each scrape is exactly as fresh as a cached value updated on the same
cadence would be — without a second long-lived task whose failure mode (silently stops
updating, gauge goes stale but keeps reporting an old value) is worse than a slow query
occasionally adding scrape latency.

**`JobRepository` gained `count_by_status()`, not a reuse of `list_by_status(...,
limit=...)`.** Counting via a limited list fetch would silently undercount past the limit —
exactly the kind of gauge bug that looks fine in a demo (small job counts) and lies in
production (real backlog). A dedicated grouped-count query, mirroring `ImageRepository.
count_by_status`'s already-established pattern, avoids that trap entirely.

**`ModelCache` tracks hits/misses as two plain integers, not a Prometheus counter.**
`ModelCache` lives in the application layer, which stays free of concrete instrumentation
tech the same way it stays free of `sqlalchemy`/`fastapi` (ADR-0001) — `self.hits`/
`self.misses` are exactly the kind of fact the presentation layer is allowed to know how to
expose (a Gauge, computed as a ratio on each scrape) without the use-case layer needing to
import `prometheus_client` to produce it.

**A business rejection stays a rejection; a route error becomes an HTTP-request-level
metric, not a route-specific one.** `PrometheusMiddleware` records every request under its
matched route *template* (`request.scope["route"].path`, read after `call_next` returns —
Starlette only populates it once routing resolves), not the raw path — a raw
`/jobs/<uuid>` would mint one label series per job id and grow Prometheus's cardinality
without bound for as long as the platform kept running.

## Consequences

- `pipeline_client.generate_drift_report` and `factoryai.worker.tasks.run_drift_report`
  are real now — the `NotImplementedError` scope cut Phases 9 and 10 both documented (and
  the Celery task's `max_retries=0` special-case) is gone; the task retries with the same
  exponential backoff every other job type gets. `monitoring_dag` (Airflow) generates and
  persists a real report on its daily schedule instead of skipping every run.
- Alerting is Prometheus/Alertmanager's job, not Airflow's: `monitoring_dag` only measures
  and records (via `common.run_drift_report`); nothing in it pages anyone. This is a
  narrower scope than a first read of ADR-0013's `alert_on_failure` extension point might
  suggest — that callback is for Airflow's *own* task failures and SLA misses, a different
  concern from "the domain signal this platform measures crossed a threshold," which
  belongs on the metric itself.
- Automatically triggering the `retraining` DAG from a high-severity drift alert is
  explicitly **not** built here — Phase 12 ("Automatic retraining & human feedback loop")
  owns that connection. This phase stops at "the alert fires and a runbook exists,"
  matching the ROADMAP's own phase boundary.
- Every alert rule's `runbook_url` annotation is a repo-relative path
  (`docs/runbooks/*.md`), not a resolvable URL — this project has no public docs host to
  point at yet. A real deployment replaces the prefix; the six runbooks themselves
  (drift, error rate, latency, resource usage, job backlog, cache hit ratio) are written
  to be useful regardless of where they end up being served from.
- Alertmanager's `default` receiver has no `slack_configs`/`webhook_configs` — the
  identical credentials gap ADR-0013 already documented for Airflow's own callbacks.
  Alerts are visible in Alertmanager's UI and group/deduplicate correctly; wiring a real
  channel is a one-block config change, not a design change.

## Live verification

Once Docker was available, `prometheus`/`alertmanager`/`grafana` were brought up against
the real compose stack and a real `factoryai serve` process on the host. Confirmed
directly, not assumed: Prometheus loaded `prometheus.yml` and all 8 alert rules across 3
groups with zero parse errors; Alertmanager loaded its config and reported cluster
`"status":"ready"`; all 4 Grafana dashboards were discoverable via `GET /api/search`
immediately after provisioning; Prometheus's `factoryai-api` scrape target reported
`health: "up"`, and `factoryai_system_cpu_percent` was queryable through Prometheus's own
`/api/v1/query` with a real, live value — the full pull-model path (API exposes → Prometheus
scrapes → queryable) working end to end, not merely configured.

Two real bugs surfaced during that run, neither hypothetical:

1. **`GET /metrics` returned a bare 500 the moment the database was unreachable** — a
   genuinely bad failure mode for a health-signal endpoint, since Prometheus marking the
   *entire* scrape target down loses the CPU/memory gauges an operator needs *most* while
   the database is down, not only the two gauges that actually depend on it. Fixed by
   wrapping the two DB-backed gauge groups (`_refresh_job_gauges`, `_refresh_drift_gauges`)
   so either one failing logs a warning and leaves its own gauges stale, while system
   gauges (no I/O at all) and the cache-hit ratio still refresh and the scrape still
   returns `200`. A regression test (`test_a_database_outage_degrades_instead_of_500ing`)
   simulates the outage directly rather than relying on live infrastructure staying broken.
2. **This session's own `.env` carried a stale `POSTGRES_PORT=5555`** left over from an
   earlier port remap that was never reconciled with the compose file's actual default
   (`5432`), and separately, this host has a native Postgres service also bound to `5432`,
   which — bound before Docker's own port-forwarding claims it — silently won the race for
   some connection attempts and lost it for others depending on address family, producing
   inconsistent, confusing "password authentication failed" errors that had nothing to do
   with the actual password. Neither is a defect in this phase's code; both are recorded
   here because they were real, live-discovered environment gaps, and the fix (remapping
   the compose Postgres to host port `5433` via `POSTGRES_HOST_PORT`, the identical pattern
   already used for the Redis port conflict in Phase 9/10's own live verification) is the
   kind of thing a future session on this same host will hit again without this note.
