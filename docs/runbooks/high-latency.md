# Runbook: high API latency (`FactoryAIHighLatency`)

## What fired

A route's 95th-percentile latency (`factoryai_http_request_latency_seconds`) has exceeded
2 seconds for 5 minutes. Phase 7's own live-verification budget for a single `/predict`
call on CPU is 2 seconds — this alert is that same budget, now enforced continuously
rather than checked once at launch.

## First steps

1. Open the **Service Health** dashboard's latency panel and confirm which `path` is slow.
2. If it is `/predict` or `/batch-predict`: check `factoryai_model_cache_hit_ratio` — a low
   ratio means the cache is reloading a detector (a real, multi-second cost) far more often
   than it should, usually from promotion thrashing.
3. Check `factoryai_system_cpu_percent` — CPU-bound inference sharing the host with a
   concurrent training run (`factoryai train`, a `training`/`retraining` DAG) will slow
   down every prediction on the same machine.
4. Check `API_MAX_CONCURRENT_PREDICTIONS` against actual concurrent traffic — the
   semaphore queues excess requests rather than rejecting them, which shows up as latency,
   not errors.

## Common causes and fixes

- **Concurrent training on the same host**: move training off the inference host, or
  schedule it for low-traffic windows.
- **Cache thrashing**: check recent `Deployment` records for rapid promote/rollback
  cycles; stop the thrashing at the source.
- **Genuine load beyond capacity**: raise `API_MAX_CONCURRENT_PREDICTIONS` only if the host
  has CPU headroom (`factoryai_system_cpu_percent` well under 100%); otherwise scale out.

## Escalation

Escalate if latency does not recover after ruling out concurrent training and cache
thrashing — likely a genuine capacity problem needing more hosts (Phase 14 scope).
