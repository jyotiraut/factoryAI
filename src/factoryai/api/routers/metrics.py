"""``GET /metrics`` — Prometheus text exposition.

Gauges are recomputed on every scrape rather than by a background loop (see
``api/metrics.py``'s module docstring, ADR-0014): Prometheus's own pull model already
re-reads this endpoint on its configured interval, so a live query here is exactly as fresh
as a cached one would be, without a second process-lifetime task to keep alive.
"""

from __future__ import annotations

import shutil
from collections.abc import Awaitable, Callable

import psutil
from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from factoryai.api.dependencies import get_container
from factoryai.api.metrics import (
    _SEVERITY_VALUES,
    DRIFT_SEVERITY,
    DRIFT_SIGNAL_BREACHED,
    DRIFT_SIGNAL_STATISTIC,
    JOBS_BY_STATUS,
    MODEL_CACHE_HIT_RATIO,
    SYSTEM_CPU_PERCENT,
    SYSTEM_DISK_PERCENT,
    SYSTEM_GPU_UTILIZATION_PERCENT,
    SYSTEM_MEMORY_PERCENT,
)
from factoryai.bootstrap.container import Container
from factoryai.domain.value_objects import Category, ModelStage
from factoryai.shared.logging import get_logger

router = APIRouter(tags=["metrics"])
logger = get_logger(__name__)


@router.get("/metrics")
async def get_metrics(container: Container = Depends(get_container)) -> Response:
    """Refresh every live gauge, then return all collectors in Prometheus text format.

    A scrape must never come back empty just because one dependency is down: CPU/memory
    and the cache ratio (no I/O at all) always refresh; the two DB-backed gauge groups are
    each wrapped so a Postgres outage degrades to "those two gauges go stale," not to a 500
    that makes Prometheus mark the *entire* target down and lose every metric — including
    the exact system-health ones an operator most needs while the database is unreachable.
    """
    _refresh_system_gauges()
    _refresh_cache_gauge(container)
    await _try_refresh(_refresh_job_gauges, container, label="job gauges")
    await _try_refresh(_refresh_drift_gauges, container, label="drift gauges")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def _try_refresh(
    refresh: Callable[[Container], Awaitable[None]], container: Container, *, label: str
) -> None:
    """Run one DB-backed gauge refresh, logging and swallowing any failure."""
    try:
        await refresh(container)
    except Exception:
        logger.warning("metrics_refresh_failed", gauge_group=label, exc_info=True)


def _refresh_system_gauges() -> None:
    """Sample host CPU, memory, disk and (if present) GPU utilisation."""
    SYSTEM_CPU_PERCENT.set(psutil.cpu_percent(interval=None))
    SYSTEM_MEMORY_PERCENT.set(psutil.virtual_memory().percent)
    SYSTEM_DISK_PERCENT.set(shutil.disk_usage("/").used / shutil.disk_usage("/").total * 100)
    _refresh_gpu_gauge()


def _refresh_gpu_gauge() -> None:
    """Sample GPU utilisation per device, if this process has CUDA available.

    Imported lazily (ADR-0001's established pattern for ``torch``, see
    ``bootstrap/container.py``): a CPU-only deployment never needs it importable, and the
    gauge simply never gets a value — an absent series, not a zero, which is the honest
    representation of "no GPU here" for a metric that only otherwise means "0% busy".
    """
    try:
        import torch
    except ImportError:
        return
    if not torch.cuda.is_available():
        return
    for device in range(torch.cuda.device_count()):
        try:
            utilization = torch.cuda.utilization(device)
        except RuntimeError:
            # nvidia-smi (what torch.cuda.utilization shells out to) can be present in the
            # CUDA runtime but absent from PATH inside a minimal container — a real,
            # observed gap, not a hypothetical one, so it is caught rather than crashing
            # every single scrape.
            continue
        SYSTEM_GPU_UTILIZATION_PERCENT.labels(device=str(device)).set(utilization)


def _refresh_cache_gauge(container: Container) -> None:
    """Compute the model cache's hit ratio since process start."""
    cache = container.model_cache
    total = cache.hits + cache.misses
    MODEL_CACHE_HIT_RATIO.set(cache.hits / total if total else 1.0)


async def _refresh_job_gauges(container: Container) -> None:
    """Read current job counts per status from the ``jobs`` table."""
    async with container.unit_of_work() as uow:
        counts = await uow.jobs.count_by_status()
    for status, count in counts.items():
        JOBS_BY_STATUS.labels(status=status).set(count)


async def _refresh_drift_gauges(container: Container) -> None:
    """Read the latest drift report for each enabled category's production model."""
    try:
        enabled = [
            Category(code)
            for code, config in container.settings.categories().items()
            if config.enabled
        ]
    except Exception:
        return

    async with container.unit_of_work() as uow:
        for category in enabled:
            model = await uow.models.find_by_stage(category, ModelStage.PRODUCTION)
            if model is None:
                continue
            report = await uow.drift_reports.latest(model.id)
            if report is None:
                continue
            severity = _SEVERITY_VALUES[report.severity.value]
            DRIFT_SEVERITY.labels(category=category.code).set(severity)
            for signal in report.signals:
                DRIFT_SIGNAL_STATISTIC.labels(category=category.code, signal=signal.name).set(
                    signal.statistic
                )
                DRIFT_SIGNAL_BREACHED.labels(category=category.code, signal=signal.name).set(
                    1 if signal.breached else 0
                )
