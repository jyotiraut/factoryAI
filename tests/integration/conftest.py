"""Fixtures for integration tests: real PostgreSQL and MinIO via testcontainers.

A single Postgres container is started once per test session and migrated once with
Alembic; each test gets a clean database via a post-test ``TRUNCATE ... CASCADE`` rather
than a savepoint-per-test scheme, which is simpler to reason about and fast enough at this
table count. A single MinIO container is likewise shared for the session.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from testcontainers.community.minio import MinioContainer
from testcontainers.community.postgres import PostgresContainer

from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from factoryai.infrastructure.storage.local import LocalObjectStore
from factoryai.infrastructure.storage.s3_compatible import S3CompatibleObjectStore
from factoryai.shared.asyncio_compat import configure_event_loop_policy

# Must run before pytest-asyncio creates the first event loop — see the module's
# docstring for why this cannot be deferred into a fixture.
configure_event_loop_policy()

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "database" / "alembic.ini"

_TABLES = (
    "feedback",
    "predictions",
    "drift_reports",
    "deployments",
    "model_versions",
    "experiments",
    "dataset_version_images",
    "dataset_versions",
    "datasets",
    "audit_logs",
    "images",
    "users",
)
"""Every table this schema owns, in an order TRUNCATE ... CASCADE doesn't need to care
about — CASCADE handles the foreign keys regardless of listing order."""


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Start one Postgres container for the whole test session."""
    with PostgresContainer(
        "postgres:16-alpine", username="factoryai", password="factoryai", dbname="factoryai"
    ) as container:
        yield container


@pytest.fixture(scope="session")
def _migrated(postgres_container: PostgresContainer) -> None:
    """Apply every migration to the test container, once per session.

    ``FACTORYAI_TEST_DATABASE_URL`` is Alembic's ``env.py`` test-only override — see its
    docstring — so this runs against the container without touching real settings.
    """
    os.environ["FACTORYAI_TEST_DATABASE_URL"] = postgres_container.get_connection_url(
        driver="psycopg"
    )
    config = AlembicConfig(str(ALEMBIC_INI))
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
async def engine(
    postgres_container: PostgresContainer, _migrated: None
) -> AsyncIterator[AsyncEngine]:
    """A single async engine shared across the test session."""
    async_engine = create_async_engine(postgres_container.get_connection_url(driver="psycopg"))
    yield async_engine
    await async_engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_database(engine: AsyncEngine) -> AsyncIterator[None]:
    """Truncate every table after each integration test, so tests do not leak state."""
    yield
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def uow(engine: AsyncEngine) -> SqlAlchemyUnitOfWork:
    """A fresh unit of work factory bound to the shared engine.

    Not a single shared unit of work: each ``async with uow:`` block in a test opens its
    own session and transaction, exactly as it would in the running application.
    """
    return SqlAlchemyUnitOfWork(async_sessionmaker(engine, expire_on_commit=False))


@pytest.fixture(scope="session")
def minio_container() -> Iterator[MinioContainer]:
    """Start one MinIO container for the whole test session."""
    with MinioContainer("minio/minio:latest") as container:
        yield container


@pytest.fixture
async def s3_object_store(minio_container: MinioContainer) -> S3CompatibleObjectStore:
    """An :class:`S3CompatibleObjectStore` against the shared MinIO container.

    Ensures its own test bucket exists — this is provisioning
    (:meth:`S3CompatibleObjectStore.ensure_bucket`), not something the port itself does.
    """
    config = minio_container.get_config()
    store = S3CompatibleObjectStore(
        endpoint_url=f"http://{config['endpoint']}",
        access_key=config["access_key"],
        secret_key=config["secret_key"],
        region="us-east-1",
        use_ssl=False,
    )
    await store.ensure_bucket("factoryai-test")
    return store


@pytest.fixture
def local_object_store(tmp_path: Path) -> LocalObjectStore:
    """A :class:`LocalObjectStore` rooted at a fresh temporary directory."""
    return LocalObjectStore(tmp_path)
