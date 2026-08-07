"""Event loop policy fix-up for process entry points.

``psycopg`` (the async driver every database adapter uses — see
``factoryai.infrastructure.persistence.engine``) refuses to run under Windows' default
``ProactorEventLoop``: ``Psycopg cannot use the 'ProactorEventLoop' to run in async mode``.
This must be applied before the first event loop is created, which on every entry point
means before the first ``asyncio.run()`` call — by the time an adapter is constructed
inside that loop, it is already too late to change the policy.

Every process entry point that touches the database (the CLI today; a Celery worker or the
FastAPI app in later phases) must call :func:`configure_event_loop_policy` first, before
anything else runs.
"""

from __future__ import annotations

import asyncio
import sys


def configure_event_loop_policy() -> None:
    """Select an event loop policy compatible with async ``psycopg``.

    A no-op on every platform except Windows, where the default policy is replaced with
    :class:`asyncio.WindowsSelectorEventLoopPolicy`.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
