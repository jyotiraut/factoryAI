"""Local override: these tests exercise real ``git``/``dvc`` only, never a database.

The parent ``tests/integration/conftest.py`` makes ``_clean_database`` autouse for every
test under ``tests/integration/``, which pulls in a session-scoped Postgres container even
for tests that never touch it. Redefining the fixture here — nearest conftest wins — keeps
this directory decoupled from Docker/testcontainers entirely.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_database() -> None:
    """No-op: nothing here uses a database."""
