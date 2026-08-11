"""``GET /health/live`` and ``GET /health/ready`` — liveness/readiness split (ADR-0010).

Liveness answers "is the process alive" — it never touches the database or MLflow, so a
degraded dependency never causes a healthy process to be killed and restarted for no
reason. Readiness answers "can this instance actually serve a request right now" — it
does check dependencies, and is what a load balancer or Kubernetes readiness probe should
poll instead.
"""

from __future__ import annotations

import asyncio

import requests
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from factoryai.api.dependencies import get_container
from factoryai.api.schemas import HealthResponse
from factoryai.bootstrap.container import Container

router = APIRouter(tags=["health"])

_MLFLOW_PING_TIMEOUT_SECONDS = 3.0


@router.get("/health/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    """Report that the process is up. Never checks a dependency."""
    return HealthResponse(status="ok", checks={})


@router.get("/health/ready")
async def readiness(container: Container = Depends(get_container)) -> JSONResponse:
    """Report whether this instance can actually serve a request right now."""
    checks = {
        "database": await _check_database(container),
        "model_registry": await asyncio.to_thread(_check_model_registry, container),
    }
    healthy = all(checks.values())
    body = HealthResponse(status="ok" if healthy else "degraded", checks=checks)
    return JSONResponse(status_code=200 if healthy else 503, content=body.model_dump())


async def _check_database(container: Container) -> bool:
    """Return whether a real query against PostgreSQL succeeds."""
    try:
        async with container.unit_of_work() as uow:
            await uow.audit.latest()
    except Exception:
        # A health check reports failure; it must never itself raise one.
        return False
    return True


def _check_model_registry(container: Container) -> bool:
    """Return whether the MLflow tracking server's own health endpoint responds.

    Runs synchronously (``requests``, not ``httpx``) and is dispatched via
    :func:`asyncio.to_thread` by the caller — consistent with every other network call
    this codebase makes from inside a sync client (ADR-0008).
    """
    try:
        response = requests.get(
            f"{container.settings.mlflow.tracking_uri}/health", timeout=_MLFLOW_PING_TIMEOUT_SECONDS
        )
    except requests.exceptions.RequestException:
        return False
    else:
        return response.status_code == requests.codes.ok
