"""Async SQLAlchemy engine and session factory construction."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from factoryai.shared.config import DatabaseSettings


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Build an async engine from database settings.

    Uses the ``postgresql+psycopg`` driver: psycopg 3 supports asyncio natively, so the
    same driver string serves both the sync Alembic migrations and the async application
    engine — only the engine constructor (``create_engine`` vs. ``create_async_engine``)
    differs.
    """
    return create_async_engine(
        settings.dsn(driver="postgresql+psycopg"),
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        echo=settings.echo_sql,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to ``engine``.

    ``expire_on_commit=False`` so that entities mapped from a committed row remain
    readable afterwards without triggering a lazy refresh — the mapper layer copies data
    out into domain entities immediately, but intermediate ORM row access after commit
    (e.g. logging) should not silently re-hit the database.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
