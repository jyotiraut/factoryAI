"""Application layer: use cases orchestrating the domain through ports.

Each use case is a class with a single public ``execute`` method and receives its
collaborators via constructor injection. Use cases are the only place that open
transactions, emit audit events, and translate domain errors into application results.

Populated in Phase 3 onwards (see ``docs/ROADMAP.md``).
"""
