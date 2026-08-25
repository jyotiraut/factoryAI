"""Local override: these tests exercise the real MLflow server, never a database.

The parent ``tests/integration/conftest.py`` makes ``_clean_database`` autouse for every
test under ``tests/integration/``, which pulls in a session-scoped Postgres container even
for tests that never touch it. Redefining the fixture here — nearest conftest wins — keeps
this directory decoupled from Docker/testcontainers entirely; it talks to the MLflow
server the docker-compose stack already runs on ``localhost:5000`` instead.
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

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


def _mlflow_server_reachable(uri: str, *, timeout: float = 2.0) -> bool:
    """Return whether something is actually listening at ``uri``'s host/port."""
    parsed = urlparse(uri)
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session", autouse=True)
def _require_mlflow_server() -> None:
    """Skip this whole directory fast if the compose stack's MLflow server isn't up.

    Found live (first real CI run): without this, every test here ran the real MLflow
    client against an unreachable `localhost:5000` and burned ~16 minutes per file on the
    client's own connection retries before finally failing — not a code defect, just an
    environment this suite was never meant to run against (see this module's own
    docstring). CI has no docker-compose stack; a developer running `make up` still gets
    these tests exactly as before.
    """
    if not _mlflow_server_reachable(MLFLOW_TRACKING_URI):
        pytest.skip(
            f"MLflow server not reachable at {MLFLOW_TRACKING_URI} — these tests exercise "
            "the docker-compose stack's real MLflow server (`make up`), not a "
            "testcontainer; start it locally to run this directory."
        )
