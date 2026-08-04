"""Composition root: wires concrete adapters to domain ports exactly once, at startup.

The API process, the Celery workers, the CLI and the Airflow tasks all build the same
container from the same settings, which is what allows them to share use cases without
duplicating configuration logic.

Populated in Phase 2 (see ``docs/ROADMAP.md``).
"""
