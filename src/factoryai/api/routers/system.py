"""``GET /system/health`` — the system-health dashboard view (Phase 13).

A JSON sibling of ``GET /metrics``, for the one caller Prometheus text exposition is the
wrong shape for: a browser chart. Reuses the exact same live-sampling approach ADR-0014
already established for ``/metrics`` (gauges recomputed on every read, no background
refresh loop) rather than inventing a second one — see that router's own module docstring.
"""

from __future__ import annotations

import shutil

import psutil
from fastapi import APIRouter, Depends

from factoryai.api.dependencies import get_container, require_permission
from factoryai.api.schemas import SystemHealthResponse
from factoryai.bootstrap.container import Container
from factoryai.domain.policies.permissions import Permission

router = APIRouter(tags=["system"])


@router.get(
    "/system/health",
    response_model=SystemHealthResponse,
    dependencies=[Depends(require_permission(Permission.VIEW_SYSTEM_HEALTH))],
)
async def get_system_health(container: Container = Depends(get_container)) -> SystemHealthResponse:
    """Return a live snapshot of host resource usage, job-queue depth and cache health."""
    cache = container.model_cache
    total = cache.hits + cache.misses
    async with container.unit_of_work() as uow:
        jobs_by_status = await uow.jobs.count_by_status()
    return SystemHealthResponse(
        cpu_percent=psutil.cpu_percent(interval=None),
        memory_percent=psutil.virtual_memory().percent,
        disk_percent=shutil.disk_usage("/").used / shutil.disk_usage("/").total * 100,
        jobs_by_status=jobs_by_status,
        model_cache_hit_ratio=cache.hits / total if total else 1.0,
    )
