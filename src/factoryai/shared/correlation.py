"""Correlation identifiers for tracing one logical operation across processes.

A correlation ID is created at the edge of the system — an HTTP request, a CLI invocation,
an Airflow task — and then travels with the work: into Celery message headers, into every
log line, and into the ``correlation_id`` column of predictions and audit records.

The value lives in a :class:`~contextvars.ContextVar`, so it is inherited by ``asyncio``
tasks automatically and does not leak between concurrent requests the way a module-level
global would.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_CORRELATION_ID: ContextVar[str | None] = ContextVar("factoryai_correlation_id", default=None)

UNSET = "-"
"""Placeholder used in log records when no correlation ID has been bound."""


def new_correlation_id() -> str:
    """Generate a fresh correlation identifier."""
    return uuid.uuid4().hex


def get_correlation_id() -> str:
    """Return the current correlation ID, or :data:`UNSET` if none is bound."""
    return _CORRELATION_ID.get() or UNSET


def set_correlation_id(correlation_id: str) -> Token[str | None]:
    """Bind a correlation ID to the current context.

    Args:
        correlation_id: The identifier to bind, typically taken from an inbound
            ``X-Correlation-ID`` header or generated with :func:`new_correlation_id`.

    Returns:
        A token that can be passed to :func:`reset_correlation_id` to restore the
        previous value.
    """
    return _CORRELATION_ID.set(correlation_id)


def reset_correlation_id(token: Token[str | None]) -> None:
    """Restore the correlation ID that was bound before ``token`` was issued."""
    _CORRELATION_ID.reset(token)


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Iterator[str]:
    """Bind a correlation ID for the duration of a block.

    Args:
        correlation_id: An existing identifier to adopt — for example one propagated from
            an upstream service. A new one is generated when omitted.

    Yields:
        The correlation ID that is bound inside the block.

    Example:
        >>> with correlation_scope() as cid:
        ...     assert get_correlation_id() == cid
        >>> get_correlation_id()
        '-'
    """
    token = set_correlation_id(correlation_id or new_correlation_id())
    try:
        yield get_correlation_id()
    finally:
        reset_correlation_id(token)
