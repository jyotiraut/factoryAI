# Runbook: high host CPU or memory (`FactoryAIHighCPU` / `FactoryAIHighMemory`)

## What fired

`factoryai_system_cpu_percent` or `factoryai_system_memory_percent` has exceeded 90% for
10 minutes, sampled directly from the API process's host on every Prometheus scrape.

## First steps

1. Open the **Service Health** dashboard's CPU/memory/disk panel to see whether this is a
   sustained plateau (real load) or a spike (a specific job).
2. Check whether a training run (`factoryai train`, a `training`/`retraining` DAG) or a
   large `dataset_versioning`/bulk-inference job is running concurrently — all are
   CPU/memory-intensive by design (Anomalib on CPU) and are the most common real cause.
3. Check `factoryai_jobs{status="running"}` — several concurrent training jobs on one host
   will exhaust CPU quickly; this platform does not currently limit concurrent training
   jobs per host.

## Common causes and fixes

- **Expected**: a training run in progress. No action beyond confirming inference latency
  (see `high-latency.md`) stays within budget; the alert will clear once training finishes.
- **Unexpected sustained load with nothing running**: check for a runaway process
  (`docker stats`, or `ps` on the host) — a stuck detector load, a memory leak in a
  long-running worker process, or an external process unrelated to FactoryAI.
- **Memory specifically**: check MLflow/Postgres/MinIO container memory too
  (`docker stats`) — this gauge is the *host's* view, not scoped to the FactoryAI process
  alone, so a runaway container elsewhere on the same host also trips it.

## Escalation

Escalate if the host is pinned with no training or bulk job running — likely a leak or a
process outside FactoryAI's control needing infrastructure-team attention.
