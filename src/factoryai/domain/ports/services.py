"""Ambient services injected so that time and randomness stay testable."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime


class Clock(ABC):
    """Source of the current time.

    Injected rather than calling :func:`datetime.now` directly, so that tests can pin
    "now" and assert on timestamps without sleeping or tolerating jitter.
    """

    @abstractmethod
    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""


class SystemClock(Clock):
    """The real clock, reading UTC from the operating system."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(UTC)


class IdGenerator(ABC):
    """Source of new entity identifiers."""

    @abstractmethod
    def new_id(self) -> uuid.UUID:
        """Return a fresh identifier."""


class UuidGenerator(IdGenerator):
    """Random UUID version 4 identifiers."""

    def new_id(self) -> uuid.UUID:
        """Return a random UUID."""
        return uuid.uuid4()
