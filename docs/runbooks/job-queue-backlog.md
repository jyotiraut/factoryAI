# Runbook: background job queue backlog (`FactoryAIJobQueueBacklog`)

## What fired

More than 50 jobs (`factoryai_jobs{status="queued"}`) have sat queued for 15 minutes,
computed from the `jobs` table's own status counts (`JobRepository.count_by_status`), not
from Celery's internal queue depth.

## First steps

1. Open the **Data Pipeline** Grafana dashboard and check the `queued`/`running` trend —
   is `running` also near zero (worker down or stalled) or normal (worker just behind)?
2. Confirm at least one `factoryai worker` process is actually running
   (`ps` on the host, or check for a Flower UI response if running).
3. Check the worker process's own logs for repeated task failures — a task stuck retrying
   with exponential backoff (`JobTask`, ADR-0012) holds a slot without completing.
4. Check Redis is reachable (`redis-cli ping` against the configured
   `CELERY_BROKER_URL`) — a Celery worker that lost its broker connection stops pulling
   new tasks entirely without necessarily crashing.

## Common causes and fixes

- **No worker running**: start one — `factoryai worker --pool=solo` (Windows) or
  `--pool=prefork` (Linux/macOS).
- **Worker running but stuck**: check `factoryai_jobs{status="running"}` isn't itself
  growing without bound (a hung task never completing) — restart the worker process; the
  in-flight task, once redelivered (Phase 9's `task_acks_late`), resumes from its own job
  row rather than losing work.
- **Redis unreachable**: restart the `redis` container; the worker reconnects
  automatically once it recovers.
- **Genuinely too much load**: scale out worker processes (`--concurrency`), or
  investigate whether an upstream system is submitting far more jobs than expected.

## Escalation

Escalate if restarting the worker does not drain the backlog within 30 minutes.
