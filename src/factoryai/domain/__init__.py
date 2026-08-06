"""Domain layer: entities, value objects, ports and policies.

This package is free of every framework and infrastructure import — it depends on the
standard library alone, which is stricter than ADR-0001 requires and enforced by the
import-linter contracts in ``pyproject.toml``.

The layout:

- :mod:`~factoryai.domain.value_objects` — immutable, self-validating values.
- :mod:`~factoryai.domain.entities` — objects with identity and a lifecycle.
- :mod:`~factoryai.domain.ports` — interfaces infrastructure must satisfy.
- :mod:`~factoryai.domain.errors` — business-rule violations.
"""
