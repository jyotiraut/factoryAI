# ADR-0013 — Airflow DAG design: the thin client, sensors, and business-outcome vocabulary

**Status:** accepted · **Date:** 2026-08-11

## Context

ADR-0005 drew the Airflow/Celery boundary and promised both sides call "the *same*
application use cases through a thin client" with "no business logic" in either scheduler's
own files. Phase 10 has to turn that promise into working DAGs, and three questions had no
answer already written down: what that thin client actually is (a new module, or something
already sitting in `factoryai.worker`); how a scheduled DAG discovers new work without a
human triggering it; and how Airflow's own success/failure vocabulary — which only knows
"succeeded" or "failed" — should represent an outcome this platform already treats as a
third thing: a *correct rejection* (`PromoteModel`'s reject path, `Job.status.FAILED` after
retries are exhausted, and now a candidate that doesn't clear the evaluation floor).

## Decisions

**`factoryai.pipeline_client` is the one thin client, shared with Celery.** Rather than
write Airflow-specific wrapper functions duplicating what `factoryai.worker.tasks`
already did for retraining and dataset-versioning jobs, that logic moved *out* of
`worker/tasks.py` into a new top-level module, `factoryai.pipeline_client` — sibling to
`api`/`cli`/`worker`, not one of the four import-linter-governed layers. Every function
takes a structurally-typed `Container` (a `Protocol`, not the concrete
`bootstrap.container.Container` dataclass — see the module's own docstring for why a
`Protocol` is what lets both the real container and a test's duck-typed fake satisfy the
same signature) and a plain dict payload, and returns a plain dict result.
`worker/tasks.py`'s Celery tasks now call it too; ADR-0005's claim is no longer aspirational.

**Data validation is Airflow-sourced from object storage, not the filesystem.**
`factoryai ingest --path ...` (Phase 3) reads a host directory — meaningless for a
scheduled job with no host to speak of. `pipeline_client.ingest_from_object_store` scans a
`incoming/<category>/` prefix in the raw bucket instead, which is what a real camera or
upload pipeline would already be writing into. `data_validation_dag`'s sensor
(`PythonSensor`, `mode="reschedule"`) polls that prefix and only runs the ingestion task
once something is actually there — an empty prefix reschedules the poke rather than
running `IngestImage` over nothing, keeping "no long operation blocks" true here as well
as it is in Phase 9's job-submission routes.

**"Evaluation" is real but deliberately narrow.** The ROADMAP names it as its own DAG;
Phase 6's `PromoteModel` already computes the full gate (incumbent comparison included) as
part of one atomic, auditable promotion attempt, so a second, separate "evaluation" step
duplicating that comparison would either repeat logic or trust stale data. What
`evaluation_dag` (and the `evaluate` step inside `retraining_dag`) actually checks is the
gate's *absolute floor* alone — `meets_minimum_bar`, pulled out of `promote_model.py`'s
private `_evaluate_gate` specifically so this step and `PromoteModel` share one
implementation of "is this candidate even worth attempting" rather than two. Failing that
floor stops a retraining run *before* a real promotion attempt would record a rejection —
cheap, and skippable rather than alertable (see below), because "the model wasn't good
enough" is not an infrastructure problem.

**A business rejection is an Airflow *skip*, not a *failure*.** `PromoteModelRejectedError`
surfacing from `deployment_dag` (or the `deploy` step in `retraining_dag`) is caught and
re-raised as `AirflowSkipException`; the identical NotImplementedError from
`generate_drift_report` in `monitoring_dag` gets the same treatment. Airflow's own
vocabulary — every task is "succeeded" or "failed," full stop — doesn't distinguish "the
gate correctly said no" from "Postgres was unreachable," and treating the former as a hard
failure would alert an on-call operator for the gate doing exactly its job. The rejection
itself is still durably recorded (`PromoteModel.execute`'s own guarantee, unchanged); only
Airflow's *task status* is softened, not the audit trail.

**`retraining_dag` is one DAG with four TaskFlow tasks, not four chained
`TriggerDagRunOperator` calls.** Passing the just-trained `model_version_id` from a
`training` DAG run into a triggered `evaluation` run's `conf` needs either cross-DAG XCom
lookups or a Jinja expression reaching into another DAG's run — solvable, but native
TaskFlow XCom within one DAG is simpler for what is, in this platform's own terms, one
logical pipeline (dataset version → train → evaluate → deploy). The four steps
(`dataset_versioning`, `training`, `evaluation`, `deployment`) still exist as independently
triggerable DAGs for the ad-hoc case ("just re-run promotion on a candidate I already
trained") — both call the identical `common.run_*` helpers, so there is exactly one place
each step's Airflow wiring lives.

**Failure and SLA-miss callbacks log; they do not page anyone.** `alert_on_failure` and
`alert_on_sla_miss` (`pipelines/airflow/dags/common.py`) are the extension point a real
deployment wires a Slack or PagerDuty webhook into — this project holds no such
credentials, so structured logging is what ships. Every DAG's `default_args` and
`sla_miss_callback` point at them uniformly, so wiring a real notifier later is a one-file
change, not a per-DAG one.

**Airflow gets its own image; it is not run from the host the way the API and worker are.**
Phase 9 chose not to containerise `factoryai serve`/`factoryai worker` because there was no
application image yet, and building one is Phase 14 scope. Airflow is different: it is a
third-party application with no `factoryai`-shipped host entry point, and `LocalExecutor`
(the executor `.env.example` already named in Phase 0) runs every task in the scheduler's
own process — so whatever container runs the scheduler must have `factoryai[storage,
imaging,versioning,ml]` installed inside it regardless of whether the wider app is
containerised yet. `airflow.Dockerfile` does exactly that, pinned to Airflow's own
published constraints file for the chosen version/Python combination, the same reasoning
`mlflow.Dockerfile` already established for keeping a third-party image's dependency set
under this project's control rather than whatever the upstream image happened to ship.

## Consequences

- `pipelines/airflow/` is linted by `ruff` (`make lint` now covers it) but is **not**
  type-checked by `mypy` — Airflow's TaskFlow decorators and the `**context` kwargs pattern
  every task callable receives are not well-typed enough to fight `--strict` over, and
  Airflow is not installed in this project's own `.venv` (installing it there risks
  resolving to dependency versions older than this platform's own `sqlalchemy>=2.0`
  requirement — the exact reason it gets its own image instead). `pyproject.toml`'s
  per-file ruff ignores for this directory are scoped narrowly to the patterns Airflow's
  own API imposes (missing return-type annotations on `@task`-decorated closures, fixed
  callback signatures), not a blanket relaxation.
- Live verification — building `airflow.Dockerfile`, bringing up the scheduler and
  webserver, triggering `retraining_dag` end-to-end against the real compose stack, and
  confirming a worker-side crash mid-task redelivers rather than duplicates work — is a
  manual follow-up, for the identical reason Phase 9's live Celery verification was: this
  environment's Docker daemon was not running while this phase's code was written. DAG
  syntax, Ruff/Black compliance and the compose file's own YAML anchors were all verified
  directly; parsing under a real Airflow scheduler (`airflow dags list-import-errors`) was
  not.
- `monitoring_dag` and the `generate_drift_report` path it (and nothing else) calls run on
  a real daily schedule and will show as "skipped" in the Airflow UI on every run until
  Phase 11 lands a real drift detector — a visible, honest placeholder rather than a DAG
  quietly absent from the schedule.
