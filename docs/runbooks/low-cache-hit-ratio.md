# Runbook: low model cache hit ratio (`FactoryAILowModelCacheHitRatio`)

## What fired

`factoryai_model_cache_hit_ratio` — the fraction of `ModelCache.get()` calls served from an
already-loaded detector rather than triggering a download-and-load — has been below 50%
for 15 minutes.

## What it means

`ModelCache` (`factoryai.application.services.model_cache`) reloads a category's detector
only when the production `ModelVersion.id` it reads from Postgres differs from what is
already loaded. A low hit ratio means production is changing (or being read as changing)
far more often than real promotions should — each reload costs a real download from the
model registry plus a real model load, which is what makes this worth alerting on before
it shows up as inference latency (see `high-latency.md`).

## First steps

1. Check recent `Deployment` records (`GET /models/{category}` or the `deployments` table)
   for the affected category — an unusually high rate of promotions/rollbacks in a short
   window (promotion thrashing) is the most direct cause.
2. Check whether more than one category shares this symptom — if so, suspect the cache
   itself (a bug causing spurious reloads) rather than promotion activity for any one
   category.
3. Check the API process's own restart history — a process restart clears the cache
   entirely and is expected to show one reload per category after startup, not a
   sustained low ratio.

## Common causes and fixes

- **Promotion thrashing** (a training/evaluation loop repeatedly promoting and rejecting
  candidates): pause automated retraining triggers until the underlying model quality
  issue is fixed; stop manually re-promoting/rolling back in quick succession.
- **A process restart loop**: if the API keeps restarting (crash loop), fix the crash —
  the low ratio here is a symptom, not the problem.
- **A `ModelCache` bug**: if neither of the above applies, check
  `application/services/model_cache.py` for a regression in the equality check between
  the cached and current `model_version_id`.

## Escalation

Escalate to the ML engineer on rotation if promotion activity looks intentional but the
ratio does not recover — the retraining/promotion process itself may need throttling.
