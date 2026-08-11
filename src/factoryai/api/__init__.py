"""The inference service: a FastAPI presentation adapter (Phase 7, ADR-0010).

Like ``factoryai.cli``, this package resolves dependencies from the composition root
(:mod:`factoryai.bootstrap.container`) and calls application use cases — no business logic
lives here.
"""
