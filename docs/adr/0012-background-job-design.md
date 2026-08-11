# ADR-0012 — Background job design: idempotency, retry/backoff, and the dead-letter queue

**Status:** accepted · **Date:** 2026-08-11

## Context

ADR-0005 already drew the line: request-triggered, single-unit work goes to Celery. Phase 9
has to turn that boundary into a real, operable system. Three questions had no answer
already written down: what a client polling for a submitted job's status actually reads
(Celery's own result backend, or something this platform owns and can query in SQL like
everything else); how "submitting the same batch twice" is prevented without the caller
having to track anything itself; and what happens once a task has genuinely exhausted its
retries, since "the worker logs an exception" is not the same as "an operator can see, in
one place, everything that permanently failed."

## Decisions

**The `jobs` table, not Celery's result backend, is the source of truth for status.**
Celery's own result backend (Redis) answers "what did this specific task invocation
return", which is the wrong question the moment a task is retried, redelivered after a
worker crash, or resubmitted under the same idempotency key — none of those map cleanly
onto "one task id, one result." A `jobs` row is a first-class entity with its own
lifecycle (`queued → running → succeeded | failed`), the same way every other unit of work
on this platform is: queryable in SQL, covered by `GET /jobs/{id}` like everything else in
`api/schemas.py`, and outliving whatever Celery message happened to carry it. Every task
re-reads its payload from this row rather than from the Celery message body, and a
redelivered message that finds the job already terminal returns the recorded result
instead of redoing (or re-failing) the work — see `factoryai.worker.tasks`'s module
docstring.

**Idempotency is a client-supplied key, not a platform-inferred one.** There is no way to
infer "this is the same submission" from a training config or an image batch — two
genuinely different jobs can have byte-identical payloads (retrain the same config twice on
purpose), and two retries of the same logical submission might not (a client that
regenerates a request id per attempt). `SubmitJob` takes an opaque `idempotency_key` and
returns the existing job if one was already submitted under it, backed by a real unique
constraint on `jobs.idempotency_key` (not just an application-level check, which would race
under concurrent submission) — `SqlAlchemyJobRepository.add` translates the resulting
`IntegrityError` into `JobIdempotencyKeyExistsError`, and the use case re-reads on that
path rather than trusting its own prior `find_by_idempotency_key`.

**Retry policy is Celery's own exponential backoff, not a hand-rolled scheduler.**
`autoretry_for=(Exception,)` with `retry_backoff=True`, `retry_jitter=True` and a capped
`max_retries` on every task that calls real infrastructure (inference, training, dataset
versioning). The one exception is `run_drift_report`: it raises `NotImplementedError`
unconditionally (Phase 11 has not built a drift detector yet — see `docs/ROADMAP.md` Phase
9's scope-cut note), and retrying into a guaranteed failure would only delay an operator
noticing, so it carries `max_retries=0` and no `autoretry_for`.

**A permanently failed task marks its job `failed` and is recorded on a `dead_letter`
queue, not silently dropped.** `JobTask.on_failure` — called by Celery exactly once retries
are exhausted, never on an attempt that will retry again — does two things: writes the
job's terminal state (so `GET /jobs/{id}` reflects it immediately, no result-backend lookup
required) and enqueues a small record onto a fourth Celery queue, `dead_letter`, routed
away from `training`/`inference`/`reports` specifically so an operator watching Flower sees
dead work land somewhere distinct from a queue real jobs are still flowing through. The
`dead_letter` task itself does nothing but log — the job row is already the authoritative
record; the queue exists for visibility, not storage.

**Bulk inference references already-uploaded images, never inline bytes.** A 1000-image
submission (the ROADMAP's own exit-criterion number) must keep the Celery message small;
`BulkInferenceJobRequest` carries `{bucket, key}` pairs, and the task fetches each image
from the object store at execution time. This also means the caller is responsible for
having uploaded the batch first — a real constraint, not an oversight, and one Phase 13's
dashboard will need an upload step ahead of job submission to satisfy.

**Job dispatch is a `Container` method, not a router-level import of `factoryai.worker`.**
`api.routers.jobs` calls `container.dispatch_job(job)`; the container lazily imports
`factoryai.worker.tasks.dispatch` (mirroring `detector_factory`'s and `experiment_tracker`'s
existing lazy-import pattern for `torch`/`mlflow`), so a request that never submits a job
never needs `celery` importable, and a test's `FakeContainer` can record dispatched jobs
without touching a real broker at all.

## Consequences

- Two systems now track a submitted job's lifecycle by design: the `jobs` table (this
  platform's view) and Celery's task id (used only as a dispatch handle — `dispatch()`
  passes `job.id` as the Celery `task_id` specifically so the two never disagree about which
  task is "the same" one). Flower shows Celery's view; `GET /jobs/{id}` shows the
  platform's. An operator debugging a stuck job needs both.
- A worker crash between a use case's own commit and this task's `succeed()` update leaves
  a job stuck `running` with the underlying work actually done (e.g. a model genuinely
  trained and registered, but the job row never told). This is a narrow window, not
  eliminated here — a stuck-job reconciler that reads `jobs.list_by_status(RUNNING)` past
  some age threshold is real future work, not built in this phase.
- `factoryai worker` defaults to Celery's `solo` pool because `prefork` depends on
  `os.fork`, which Windows does not have — the same class of platform gap
  `shared/asyncio_compat.py` and `shared/console.py` already exist for. `solo` processes
  one task at a time; real concurrency requires `--pool=prefork` on Linux/macOS (the
  containerised deployment target, once Phase 14 builds an application image).
- The worker and Flower are not containerised in `deploy/compose/docker-compose.yml` for
  the same reason the API process is not (Phase 7): there is no application image yet.
  Both run from the host against the compose stack's `redis`/`postgres`/`minio`/`mlflow`,
  exactly like `factoryai serve` already does.
