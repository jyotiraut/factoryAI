"""Local override: these tests exercise the real MLflow server, never a database.

The parent ``tests/integration/conftest.py`` makes ``_clean_database`` autouse for every
test under ``tests/integration/``, which pulls in a session-scoped Postgres container even
for tests that never touch it. Redefining the fixture here — nearest conftest wins — keeps
this directory decoupled from Docker/testcontainers entirely; it talks to the MLflow
server the docker-compose stack already runs on ``localhost:5000`` instead.
"""

from __future__ import annotations

import os

import pytest

MLFLOW_TRACKING_URI = "http://localhost:5000"
"""The docker-compose stack's MLflow server (``make up``) — see ADR-0004.

Not a testcontainer: MLflow itself has no official one, and standing up Postgres + MinIO +
an MLflow server per test session for this alone would duplicate what the compose stack
already provides for real, manual verification."""

# MLflow's client constructs its own boto3 S3 client for artifact I/O, reading these from
# the process environment rather than accepting injected credentials — the same env vars
# Container._configure_mlflow_s3_env sets for the running application.
os.environ.setdefault("MLFLOW_S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")


@pytest.fixture(autouse=True)
def _clean_database() -> None:
    """No-op: nothing here uses a database."""
