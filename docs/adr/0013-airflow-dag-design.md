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

**Airflow gets its own image; `factoryai` gets a second, independent virtualenv inside it —
not installed into Airflow's own Python environment.** Phase 9 chose not to containerise
`factoryai serve`/`factoryai worker` because there was no application image yet, and
building one is Phase 14 scope. Airflow is different: it is a third-party application with
no `factoryai`-shipped host entry point, and `LocalExecutor` runs every task in the
scheduler's own process, so a container is unavoidable regardless of whether the wider app
is containerised. The first version of this ADR planned to `pip install
factoryai[storage,imaging,versioning,ml]` directly into that container — live-verifying it
(building the image for real, once Docker was available) proved that impossible:
**every Airflow 2.x release pins `SQLAlchemy==1.4.54` in its own published constraints
file**, a hard conflict with this platform's `sqlalchemy>=2.0` requirement that a
version bump cannot fix either — Airflow does not gain SQLAlchemy 2.0 support until 3.2+,
and *that* constraints file pins `numpy>=2`, which conflicts with Anomalib's `numpy<2`
requirement the same way. No single Airflow version satisfies both constraints
simultaneously. The fix actually shipped: `airflow.Dockerfile` builds `/opt/factoryai-venv`,
a completely separate virtualenv with no constraint file at all, free to resolve modern
SQLAlchemy and NumPy independently of whatever Airflow's own environment pins. DAG tasks
never import `factoryai`; `pipelines/airflow/dags/common.py` shells out to
`/opt/factoryai-venv/bin/python -m factoryai.pipeline_runner`, a new CLI bridge
(`src/factoryai/pipeline_runner.py`) that is the only thing that actually imports
`factoryai.pipeline_client`. Business outcomes still cross the process boundary
deliberately: exit code `3` means "business rejection" (`PromotionRejectedError`,
reconstructed as a lightweight *local* exception in `common.py` — not imported from
`factoryai.domain.errors`, since this file runs in Airflow's own process, which cannot
import `factoryai` at all), exit code `4` means "not implemented yet"
(`NotImplementedError`, Phase 11's drift detector); anything else propagates as a genuine
`subprocess.CalledProcessError` for Celery-style retries to handle like any other failure.

## Consequences

- `pipelines/airflow/` is linted by `ruff` (`make lint` now covers it) but is **not**
  type-checked by `mypy` — Airflow's TaskFlow decorators and the `**context` kwargs pattern
  every task callable receives are not well-typed enough to fight `--strict` over, and
  Airflow is not installed in this project's own `.venv` for the identical dependency-
  conflict reason it is not installed into its own Docker image. `pyproject.toml`'s
  per-file ruff ignores for this directory are scoped narrowly to the patterns Airflow's
  own API imposes (missing return-type annotations on `@task`-decorated closures, fixed
  callback signatures), not a blanket relaxation.
- **Live-verified once Docker became available in this environment**, and it is what
  surfaced the SQLAlchemy conflict above — this ADR originally called that verification a
  manual follow-up, written before Docker could actually be exercised, and the plan it
  described did not survive contact with a real build. What was actually confirmed working
  against the full compose stack: all seven DAGs parse with zero import errors
  (`airflow dags list-import-errors`); `airflow-init` migrated Airflow's own metadata
  schema and created the admin user; `pipeline_runner evaluate` read the real, already-
  promoted `factoryai-bottle` production model (image AUROC 1.0, exactly the Phase 5/6/8
  figure) from Postgres inside `/opt/factoryai-venv` and correctly passed it; `pipeline_
  runner deploy` against a real weak development-stage candidate correctly reproduced
  `PromoteModel`'s full rejection report and exited `3`. Also found and fixed live, not
  predicted: the Airflow containers' environment initially set `POSTGRES_HOST` but not
  `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` — `factoryai`'s own settings have no
  safe password default (unlike `STORAGE_*`'s local-MinIO fallback), so every DB-touching
  command failed with "no password supplied" until the compose file's `airflow-env` anchor
  was corrected to set them explicitly.
- **Not verified**: `dataset_versioning`/`training`/`retraining` DAGs, which need `git` and
  `dvc` CLI binaries plus a real `.git`-tracked checkout — neither exists inside
  `airflow.Dockerfile`'s image, which only copies `pyproject.toml`/`README.md`/`src` for
  the pip build, not the repository's version-control metadata. Attempting
  `dataset_versioning` live failed exactly there (`PermissionError: git`), a real,
  understood gap rather than a silent one: `DvcGitVersionControl` (ADR-0006) is not
  optional for that use case. Installing `git`/`dvc` and mounting or cloning a real
  checkout into the image is real follow-up work, tracked here rather than papered over.
- `monitoring_dag` ran on a real daily schedule showing "skipped" on every run (the drift
  detector it called did not exist yet) until Phase 11 landed one — see ADR-0014 for the
  real implementation that replaced the stub.
