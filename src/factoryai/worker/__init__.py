"""The Celery worker process: background execution of long-running jobs (Phase 9).

Sits alongside :mod:`factoryai.api` and :mod:`factoryai.cli` rather than inside the four
import-linter-governed layers (ADR-0001) — like both of those, it is a presentation/
composition adapter, not a layer: it depends on :mod:`factoryai.bootstrap` to build a
:class:`~factoryai.bootstrap.container.Container` and drives application use cases through
it, exactly as the API and CLI do. See ADR-0012 for the background-processing design.
"""
