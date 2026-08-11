"""FastAPI application factory for the inference service (Phase 7, ADR-0010).

Run with ``uvicorn factoryai.api.main:app`` (Linux) — on Windows, use ``factoryai serve``
instead (see ``cli.py``'s ``serve`` command docstring for why plain ``uvicorn ...:app``
breaks every database call there).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from factoryai import __version__
from factoryai.api.middleware import CorrelationIdMiddleware, MaxBodySizeMiddleware
from factoryai.api.routers import auth, feedback, health, jobs, models, predict
from factoryai.api.routers.metrics import router as metrics_router
from factoryai.bootstrap.container import Container, build_container
from factoryai.domain.value_objects import Category, ModelStage
from factoryai.shared.asyncio_compat import configure_event_loop_policy
from factoryai.shared.config import Settings, get_settings
from factoryai.shared.console import configure_stdio_encoding
from factoryai.shared.logging import configure_logging, get_logger

configure_event_loop_policy()
configure_stdio_encoding()


async def warm_up(container: Container) -> None:
    """Pre-load a detector for every enabled category with a production model.

    Best-effort: a category with no production model yet, or a registry that is
    temporarily unreachable, is logged and skipped rather than crashing startup —
    ``/health/ready`` is what reports that degradation, not a failed boot.
    """
    log = get_logger(__name__)
    try:
        enabled = [
            Category(code)
            for code, config in container.settings.categories().items()
            if config.enabled
        ]
    except Exception:
        log.warning("warm_up_categories_unavailable", exc_info=True)
        return

    for category in enabled:
        try:
            async with container.unit_of_work() as uow:
                model_version = await uow.models.find_by_stage(category, ModelStage.PRODUCTION)
                if model_version is None:
                    log.info("warm_up_skipped_no_production_model", category=category.code)
                    continue
                experiment = await uow.experiments.get(model_version.experiment_id)
            await container.model_cache.get(
                category,
                model_version_id=model_version.id,
                registry_name=model_version.registry_name,
                registry_version=model_version.registry_version,
                threshold=model_version.threshold,
                model_family=experiment.model_family,
                backbone=experiment.backbone,
            )
            log.info(
                "warm_up_loaded", category=category.code, model_version_id=str(model_version.id)
            )
        except Exception:
            log.warning("warm_up_failed", category=category.code, exc_info=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application, wired to one process-wide :class:`Container`."""
    resolved_settings = settings or get_settings()
    configure_logging(
        level=resolved_settings.log_level,
        log_format=resolved_settings.log_format,
        service="factoryai-api",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = build_container(resolved_settings)
        app.state.container = container
        app.state.prediction_semaphore = asyncio.Semaphore(
            resolved_settings.api.max_concurrent_predictions
        )
        await warm_up(container)
        try:
            yield
        finally:
            await container.dispose()

    app = FastAPI(
        title="FactoryAI Inference Service",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.api.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=resolved_settings.api.max_request_bytes)
    app.add_middleware(CorrelationIdMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(predict.router)
    app.include_router(models.router)
    app.include_router(feedback.router)
    app.include_router(jobs.router)
    app.include_router(metrics_router)
    return app


app = create_app()
