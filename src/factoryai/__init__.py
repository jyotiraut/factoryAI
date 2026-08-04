"""FactoryAI — Industrial Visual Inspection Platform with End-to-End MLOps.

The package is organised into Clean Architecture layers (see ADR-0001):

- :mod:`factoryai.domain` — entities, value objects, ports and policies. No external deps.
- :mod:`factoryai.application` — use cases orchestrating the domain through ports.
- :mod:`factoryai.infrastructure` — adapters implementing the ports.
- :mod:`factoryai.bootstrap` — the composition root wiring adapters to ports.
- :mod:`factoryai.shared` — cross-cutting utilities that depend on nothing else here.

Dependencies point inwards only; the rule is enforced by import-linter in CI.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
