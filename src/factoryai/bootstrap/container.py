"""The composition root: builds concrete adapters from settings, once per process.

Every process shape — the API, a Celery worker, the CLI, an Airflow task — constructs one
:class:`Container` from the same :class:`~factoryai.shared.config.Settings` and gets back
the same wiring. This is what lets ``STORAGE_BACKEND=s3`` change every caller's behaviour
with a single environment variable instead of an edit at every call site.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.storage import ObjectStore
from factoryai.infrastructure.persistence.engine import create_engine, create_session_factory
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from factoryai.infrastructure.storage.local import LocalObjectStore
from factoryai.infrastructure.storage.s3_compatible import S3CompatibleObjectStore
from factoryai.shared.config import Settings
from factoryai.shared.errors import ConfigurationError


@dataclass(frozen=True)
class Container:
    """Holds settings and lazily builds the adapters that depend on them.

    Adapters are built once and cached (:func:`functools.cached_property`): the database
    engine in particular is meant to be a single, long-lived connection pool per process,
    not something re-created on every use case call.

    Not ``slots=True``: :func:`functools.cached_property` caches by writing to the
    instance ``__dict__``, which slots removes entirely.
    """

    settings: Settings

    @cached_property
    def engine(self) -> AsyncEngine:
        """The application's async database engine."""
        return create_engine(self.settings.database)

    @cached_property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """The session factory every unit of work opens a transaction from."""
        return create_session_factory(self.engine)

    def unit_of_work(self) -> UnitOfWork:
        """Return a fresh unit of work.

        Deliberately not cached: a unit of work is one transaction, and hand out a new
        instance is what lets one process serve many independent requests concurrently.
        """
        return SqlAlchemyUnitOfWork(self.session_factory)

    @cached_property
    def object_store(self) -> ObjectStore:
        """The configured object store, selected by ``STORAGE_BACKEND``.

        Raises:
            ConfigurationError: If the backend is not yet implemented (``azure``, ``gcs``
                — ADR-0003 records these as adapters written when a phase needs them).
        """
        storage = self.settings.storage
        if storage.backend == "local":
            return LocalObjectStore(storage.local_root)
        if storage.backend in {"minio", "s3"}:
            return S3CompatibleObjectStore(
                endpoint_url=storage.endpoint,
                access_key=storage.access_key.get_secret_value(),
                secret_key=storage.secret_key.get_secret_value(),
                region=storage.region,
                use_ssl=storage.use_ssl,
            )
        raise ConfigurationError(
            f"no ObjectStore adapter is implemented yet for backend {storage.backend!r}",
            code="config.storage_backend_unimplemented",
            details={"backend": storage.backend},
        )

    async def dispose(self) -> None:
        """Release the database connection pool.

        Call once, at process shutdown. Only meaningful if :attr:`engine` was ever
        accessed — disposing an engine that was never built would create one just to
        immediately tear it down.
        """
        if "engine" in self.__dict__:
            await self.engine.dispose()


def build_container(settings: Settings) -> Container:
    """Build the composition root for a process.

    Args:
        settings: The process's configuration, typically
            :func:`factoryai.shared.config.get_settings`.

    Returns:
        A container ready to hand out units of work and an object store.
    """
    return Container(settings=settings)
