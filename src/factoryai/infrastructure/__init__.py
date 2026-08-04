"""Infrastructure layer: concrete adapters implementing the domain ports.

Grouped by technology — ``persistence`` (PostgreSQL, SQLAlchemy, Alembic), ``storage``
(MinIO, S3, Azure, GCS, local), ``tracking`` (MLflow), ``models`` (Anomalib plugin
registry), ``monitoring`` (Evidently, Prometheus) and ``messaging`` (Celery).

Nothing outside this package may import a third-party infrastructure library directly.

Populated in Phase 2 onwards (see ``docs/ROADMAP.md``).
"""
