"""Cross-cutting utilities: configuration, structured logging, errors and common types.

This package sits below every layer and must not import from ``domain``, ``application``,
``infrastructure`` or ``bootstrap`` — enforced by an import-linter contract.

Populated in Phase 1 (see ``docs/ROADMAP.md``).
"""
