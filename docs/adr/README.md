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
