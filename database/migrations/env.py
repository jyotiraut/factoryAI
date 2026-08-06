"""Alembic environment: wires migrations to the same settings the application reads.

The database URL is never duplicated in ``alembic.ini`` — it comes from
:func:`factoryai.shared.config.get_settings`, the same source the async application
engine reads, so migrations and the running app can never point at different databases by
accident.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from factoryai.infrastructure.persistence.orm import Base
from factoryai.shared.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the migration target from application settings.

    Alembic itself runs synchronously, so the sync ``psycopg`` driver is used here even
    though the application engine (:mod:`factoryai.infrastructure.persistence.engine`)
    is async — same driver package, same credentials, just a different SQLAlchemy
    engine constructor.

    ``FACTORYAI_TEST_DATABASE_URL`` is a test-only escape hatch: integration tests migrate
    a disposable testcontainers Postgres whose port is assigned at random, which settings
    read from the environment have no way to know in advance.
    """
    override = os.environ.get("FACTORYAI_TEST_DATABASE_URL")
    if override:
        return override
    return get_settings().database.dsn(driver="postgresql+psycopg")


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live database connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database connection."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
