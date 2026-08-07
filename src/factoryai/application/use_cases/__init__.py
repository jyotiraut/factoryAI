"""Use cases: one class per business operation, orchestrating the domain through ports.

Each use case receives its collaborators through its constructor — no service locator, no
global state — and exposes a single ``execute`` method. See ``docs/ARCHITECTURE.md`` §2.2.
"""
