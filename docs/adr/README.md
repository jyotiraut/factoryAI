# Architecture Decision Records

Each ADR captures one decision: the context that forced it, the options considered, what
was chosen, and what it costs. ADRs are immutable — a decision that changes gets a new ADR
that supersedes the old one.

Format: [MADR](https://adr.github.io/madr/)-lite. Status is one of `proposed`, `accepted`,
`superseded by ADR-XXXX`.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-clean-architecture.md) | Clean Architecture with ports and adapters | accepted |
| [0002](0002-anomalib-patchcore.md) | Anomalib PatchCore as the default detector | accepted |
| [0003](0003-minio-object-storage.md) | MinIO locally behind a cloud-agnostic port | accepted |
| [0004](0004-mlflow-tracking-registry.md) | MLflow for experiment tracking and model registry | accepted |
| [0005](0005-airflow-celery-split.md) | Airflow for scheduled workflows, Celery for request-triggered work | accepted |
| [0006](0006-dvc-dataset-versioning.md) | DVC for dataset versioning | accepted |
| [0007](0007-python-311.md) | Pin Python 3.11 | accepted |
| [0008](0008-synchronous-compute-ports.md) | Compute-bound ports are synchronous | accepted |
| [0009](0009-training-pipeline-steps.md) | Training as a fixed sequence of single-responsibility steps | accepted |
| [0010](0010-inference-service-design.md) | Inference service: cache invalidation, health split, and backpressure | accepted |
| [0011](0011-jwt-auth-and-rbac.md) | JWT authentication, permission-keyed RBAC, and audit tamper detection | accepted |
| [0012](0012-background-job-design.md) | Background job design: idempotency, retry/backoff, and the dead-letter queue | accepted |
