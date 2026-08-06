# FactoryAI — Incremental Build Roadmap

This document breaks the platform into **14 phases**. Each phase is independently
demonstrable, ends in a working system (never a half-wired one), and has explicit exit
criteria. Nothing is built "to be finished later" — every phase ships with tests, docs and
a runnable path.

**Rule for every phase:** type hints, docstrings, structured logging, Pydantic settings,
tests, and a docs update land *with* the feature, not after it.

---

## Phase map

```mermaid
flowchart TD
    P0[P0 Foundations] --> P1[P1 Domain & config core]
    P1 --> P2[P2 Storage + DB adapters]
    P2 --> P3[P3 Ingestion & validation]
    P3 --> P4[P4 Dataset versioning DVC]
    P4 --> P5[P5 Training pipeline + MLflow]
    P5 --> P6[P6 Model registry & promotion]
    P6 --> P7[P7 Inference API]
    P7 --> P8[P8 Auth, RBAC, audit]
    P8 --> P9[P9 Background jobs Celery]
    P9 --> P10[P10 Airflow orchestration]
    P10 --> P11[P11 Monitoring & drift]
    P11 --> P12[P12 Auto-retraining + feedback]
    P12 --> P13[P13 Frontend dashboard]
    P13 --> P14[P14 K8s, Terraform, CI/CD hardening]
```

Phases 0–7 build the vertical slice: *image in → prediction out, fully traceable*.
Phases 8–14 make it operable, observable and deployable.

---

## Phase 0 — Foundations & documentation *(complete)*

**Goal:** a repository that already looks like an enterprise project before a line of ML
is written.

**Scope**
- Repository skeleton, `README`, `ARCHITECTURE`, `DATA_MODEL`, `ROADMAP`, ADR directory.
- Tooling: `pyproject.toml` (uv/poetry), Ruff, Black, Mypy strict, pre-commit, pytest.
- `.env.example`, `Makefile` task interface, `.gitignore`, `.dockerignore`.
- Git repository initialised with Conventional Commits.
- CI skeleton: lint + typecheck + unit tests on PR.

**Exit criteria**
- `make lint`, `make typecheck`, `make test` all pass on an empty-but-structured repo.
- ADR-0001..0005 recorded (architecture style, model library, storage, orchestration, DB).
- A reader can understand the whole system from `docs/` without reading code.

---

## Phase 1 — Domain model & configuration core *(complete)*

**Goal:** the vocabulary of the system, dependency-free.

**Scope**
- `domain/entities`: `InspectionImage`, `Dataset`, `DatasetVersion`, `Experiment`,
  `ModelVersion`, `Prediction`, `Feedback`, `Deployment`, `AuditEvent`.
- `domain/value_objects`: `Checksum`, `Resolution`, `AnomalyScore`, `Category`,
  `ModelStage`, `ProcessingStatus`.
- `domain/ports`: `ImageRepository`, `ObjectStore`, `ExperimentTracker`, `ModelRegistry`,
  `AnomalyDetector`, `DriftDetector`, `Clock`, `UnitOfWork`.
- `shared/config`: layered Pydantic `Settings` (defaults → YAML → env → CLI).
- `shared/logging`: structured JSON logging with correlation IDs.
- `shared/errors`: domain exception hierarchy mapped later to HTTP codes.

**Exit criteria**
- `domain/` imports nothing outside stdlib + Pydantic. Enforced by an import-linter rule in CI.
- 100% unit test coverage on domain invariants (e.g. a `Checksum` cannot be malformed).
- Category config for all 15 MVTec classes exists; only `bottle` is enabled.

**Delivered.** The domain ended up stricter than planned: it depends on the standard
library alone (no Pydantic — frozen dataclasses validate in `__post_init__`), which is
now its own import-linter contract. 238 unit tests, 88% coverage, all five layer
contracts held. Notable adapter-facing decision made while wiring `shared/config.py`:
`STORAGE_BACKEND=minio` defaults to MinIO's well-known local credentials so a fresh
checkout runs with zero configuration, while `s3`/`azure`/`gcs` still fail closed without
an explicit key — see the validators on `StorageSettings`.

---

## Phase 2 — Infrastructure adapters: object storage + database *(complete)*

**Goal:** real persistence behind the Phase 1 ports.

**Scope**
- PostgreSQL schema + Alembic migrations for the tables in [DATA_MODEL.md](DATA_MODEL.md).
- SQLAlchemy 2.0 repositories implementing the domain ports; Unit-of-Work transaction boundary.
- `ObjectStore` port with a MinIO/S3 adapter (boto3) and a local-filesystem adapter for tests.
- `docker-compose` stack: postgres, minio, adminer.
- Testcontainers-based integration tests.

**Exit criteria**
- `make up` starts postgres + minio; `make migrate` applies a clean schema.
- Swapping `STORAGE_BACKEND=minio|s3|local` changes no application code.
- Integration tests green against real containers in CI.

**Delivered.** 13 tables (schema mirrors `DATA_MODEL.md` with three documented,
deliberate deviations — see `orm.py`'s module docstring: category enablement stays
config, not a DB table; `password_hash` arrives with Phase 8; `audit_logs` immutability
is a trigger, not application discipline). All 8 Phase-1 ports have SQLAlchemy
implementations behind one `SqlAlchemyUnitOfWork`. Both `ObjectStore` adapters pass the
same contract test suite. 65 integration tests run against real Postgres and MinIO via
testcontainers; combined with the unit suite, coverage is 95% (gate: 80%, enforced only
on the combined run — see `docs/CONTRIBUTING.md` for why unit-only coverage doesn't work
once infrastructure adapters exist). Two real bugs surfaced and were fixed while wiring
this up: (1) SQLAlchemy only orders a flush's INSERTs by FK dependency between mapped
classes that have an ORM `relationship()` — a bare FK column is not enough — so two
related rows added in one transaction without a declared relationship could flush in the
wrong order and trip a spurious FK violation; fixed by flushing immediately after every
`add()` (see `repositories.py`'s `_add` helper). (2) `psycopg`'s async mode refuses to run
under Windows' default `ProactorEventLoop`; integration tests select
`WindowsSelectorEventLoopPolicy` on that platform only.

---

## Phase 3 — Ingestion & validation pipeline

**Goal:** nothing enters the dataset unvalidated.

**Scope**
- Validation chain (composable rules): file type, decodability, resolution bounds,
  aspect ratio, colour mode, EXIF sanity, perceptual-hash duplicate detection,
  checksum collision, required metadata.
- `IngestImage` use case: validate → hash → store to object storage → record metadata row →
  emit audit event, all in one transaction with compensating delete on failure.
- Validation report artifact (JSON + human-readable summary) per batch.
- CLI: `factoryai ingest --path ... --category bottle --dataset raw`.

**Exit criteria**
- Corrupt, duplicate and out-of-spec images are rejected with actionable reasons.
- Ingesting the full MVTec `bottle` train+test set produces a complete metadata table.
- Rules are declarative — adding a rule requires no change to the use case.

---

## Phase 4 — Dataset versioning with DVC

**Goal:** every dataset state is addressable and reproducible.

**Scope**
- DVC initialised with MinIO as remote; `datasets/` tracked.
- `CreateDatasetVersion` use case: snapshot the metadata query → materialise a manifest →
  `dvc add` + push → persist `DatasetVersion` row with the DVC hash and Git commit.
- Deterministic train/val/test splits pinned by seed and recorded in the manifest.
- CLI: `factoryai dataset version --name bottle-v1 --note "..."`.

**Exit criteria**
- `factoryai dataset checkout bottle-v1` reproduces byte-identical data on a clean machine.
- A dataset version records: image count, class balance, checksum-of-checksums, Git SHA.

---

## Phase 5 — Training pipeline & experiment tracking

**Goal:** configurable, reproducible training with full lineage.

**Scope**
- `AnomalyDetector` port + Anomalib adapter; plugin registry keyed by model name so
  `model.name: patchcore|padim|fastflow|reverse_distillation|autoencoder` selects an implementation.
- Pipeline steps: load dataset version → validate → build datamodule → fit → evaluate →
  log → persist artifacts. Each step is a class with a single responsibility.
- MLflow logging: dataset version, Git commit, config hash, backbone, hyperparameters,
  memory-bank size, training/inference time, image & pixel AUROC, PRO, precision, recall,
  F1, confusion matrix, threshold, hardware fingerprint (CPU/GPU/RAM/driver).
- Artifacts: model weights, threshold, sample heatmaps, evaluation report.

**Exit criteria**
- `factoryai train --config configs/bottle/patchcore.yaml` produces an MLflow run with all
  metrics above and a reproducible artifact set.
- Re-running with the same config + dataset version reproduces metrics within tolerance.
- Switching to PaDiM is a one-line config change.

---

## Phase 6 — Model registry & promotion gates

**Goal:** models move between stages only when they earn it.

**Scope**
- MLflow Model Registry adapter; stages Development → Staging → Production → Archived.
- `PromoteModel` use case with an automated gate: candidate must beat the current
  production model on held-out AUROC by a configurable margin *and* not regress recall
  on the defect classes beyond tolerance.
- `RollbackDeployment` use case restoring a prior version.
- `Deployment` records with actor, reason, comparison metrics, timestamp.

**Exit criteria**
- A worse candidate is rejected with a machine-readable comparison report.
- Rollback to any previous production version is one command and fully audited.

---

## Phase 7 — Inference service

**Goal:** production FastAPI service serving the registered production model.

**Scope**
- Endpoints: `POST /predict`, `POST /batch-predict`, `GET /models`, `GET /metrics`,
  `GET /health` (liveness/readiness split), `POST /feedback`.
- Response: anomaly score, binary prediction, heatmap (PNG in object storage + signed URL),
  inference time, model version, dataset version, confidence, request id.
- Model cache with warm-up on startup; hot-reload on registry change without restart.
- Every prediction persisted for later drift analysis.
- Backpressure: request size limits, timeouts, concurrency caps.

**Exit criteria**
- p95 latency budget documented and met for a single image on CPU.
- `/health` correctly reports degraded when the registry or DB is unreachable.
- OpenAPI schema published; contract tests pin the response shape.

---

## Phase 8 — Authentication, RBAC and audit logging

**Goal:** the platform is safe to expose inside a factory network.

**Scope**
- JWT auth (access + refresh), password hashing (argon2), token revocation list.
- Roles: Administrator, ML Engineer, Operator, Viewer — permission matrix in code and docs.
- Route-level dependency guards; deny by default.
- Immutable audit log: append-only table, hash-chained rows, covering user actions,
  deployments, rollbacks, retraining, dataset changes and prediction requests.

**Exit criteria**
- An Operator cannot promote a model; a Viewer cannot submit feedback. Tested.
- Audit chain verification script detects any tampered or deleted row.

---

## Phase 9 — Background processing

**Goal:** no long operation blocks an HTTP request.

**Scope**
- Celery + Redis; separate queues for `training`, `inference`, `reports`.
- Jobs: bulk inference, retraining, dataset versioning, drift report generation.
- Job status API (`GET /jobs/{id}`), idempotency keys, retry/backoff policy, dead-letter queue.
- Flower for queue visibility.

**Exit criteria**
- Submitting a 1000-image batch returns immediately with a job id and streams progress.
- A worker crash mid-job does not lose or duplicate work.

---

## Phase 10 — Workflow orchestration with Airflow

**Goal:** the platform runs itself on a schedule.

**Scope**
- DAGs: `data_validation`, `dataset_versioning`, `training`, `evaluation`, `deployment`,
  `monitoring`, `retraining`.
- DAGs call application use cases via a thin client — no business logic in DAG files.
- Sensors, retries with exponential backoff, SLA misses, failure alerting.

**Exit criteria**
- Full pipeline runs end-to-end from an Airflow trigger with zero manual steps.
- A failing task retries and then alerts rather than silently stalling.

---

## Phase 11 — Monitoring & drift detection

**Goal:** know the system's health and the model's health separately.

**Scope**
- Prometheus instrumentation: request rate, latency histograms, error rate, throughput,
  CPU/memory/disk, GPU utilisation when present, queue depth, model cache hit rate.
- Grafana dashboards: Service Health, Inference Performance, Model Quality, Data Pipeline.
- Evidently: data drift, prediction drift, feature (embedding) drift, confidence and
  anomaly-score distribution shift, computed on rolling windows against the training reference.
- Alertmanager rules with thresholds per signal; alerts carry a runbook link.

**Exit criteria**
- Injecting shifted images (brightness/blur perturbation) raises a drift alert.
- Every alert has a corresponding runbook in `docs/runbooks/`.

---

## Phase 12 — Automatic retraining & human feedback loop

**Goal:** the loop closes.

**Scope**
- Drift alert → Airflow retraining DAG → new dataset version including recent production
  images and operator-corrected labels → train → evaluate → promotion gate (Phase 6) →
  deploy or reject with a report.
- Operator feedback UI/API: mark a prediction Correct / Incorrect (+ optional region).
- Feedback weighting in evaluation: corrected samples form a growing regression suite.

**Exit criteria**
- A simulated drift event produces either a deployed better model or a documented rejection,
  with no human intervention.
- Feedback demonstrably changes the evaluation set of the next training run.

---

## Phase 13 — Frontend dashboard

**Goal:** an interface a plant engineer would accept.

**Scope**
- React + TypeScript + Vite, component library, dark industrial theme.
- Views: Live Inspection (image + heatmap overlay + score), Prediction History with filters,
  Defect Trends, Model Versions & stages, Dataset Versions, Training Runs, Drift Status,
  System Health, Deployment History, Feedback queue.
- Role-aware navigation; optimistic feedback submission; websocket/SSE live updates.

**Exit criteria**
- An operator can review a prediction and submit feedback in under three interactions.
- All dashboard data comes from the public API — no backdoor queries.

---

## Phase 14 — Kubernetes, cloud and CI/CD hardening

**Goal:** deployable beyond a laptop.

**Scope**
- Kubernetes: Deployments, Services, ConfigMaps, Secrets, Ingress, PVCs, HPA, probes,
  resource requests/limits, PodDisruptionBudgets. Helm chart with per-environment values.
- Terraform modules for the cloud-agnostic pieces (object storage, managed Postgres,
  container registry, secrets) with AWS as the reference implementation.
- CI/CD: lint → typecheck → unit → integration → build → Trivy scan → SBOM → push →
  deploy to staging → smoke tests → manual gate → production.
- Load testing (Locust) and a documented capacity model.

**Exit criteria**
- `helm install factoryai` brings up the full stack on a kind cluster.
- A failing test or a HIGH/CRITICAL CVE blocks the pipeline.

---

## Cross-cutting, done continuously

| Concern | Practice |
|---|---|
| Testing | Unit (domain, fast) → integration (testcontainers) → e2e (compose). Coverage gate 85%. |
| Docs | Every phase updates `docs/`; every non-obvious decision gets an ADR. |
| Security | Secrets never in Git; least-privilege DB roles; dependency scanning in CI. |
| Performance | Latency budgets stated per endpoint and asserted in tests. |
| Observability | Correlation ID from HTTP request through Celery job into logs and metrics. |

---

## Known constraints & decisions to revisit

1. **Python version.** Anomalib supports 3.10–3.12; the local interpreter is 3.13.9.
   The project pins **3.11** in Docker and in `pyproject.toml`. Local development outside
   Docker requires a 3.11 virtualenv.
2. **GPU.** PatchCore trains fine on CPU for a single MVTec category, but inference latency
   targets assume CPU-only; GPU paths are optional and feature-flagged.
3. **MVTec AD licence** is non-commercial research use. Documented in `docs/DATA_SOURCES.md`
   before any distribution of derived artifacts.
