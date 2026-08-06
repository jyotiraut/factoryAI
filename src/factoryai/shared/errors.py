"""Base exception hierarchy for the whole platform.

Every exception raised deliberately by FactoryAI derives from :class:`FactoryAIError`, so
process boundaries (the HTTP exception handler, the Celery error handler, the CLI) can
distinguish "a rule we wrote said no" from "something unexpected broke".

Each error carries a stable machine-readable ``code`` and a ``details`` mapping. The code
is what clients and dashboards branch on; the message is for humans and may change.

Layer-specific errors extend this hierarchy:

- :mod:`factoryai.domain.errors` for business-rule violations.
- Infrastructure adapters wrap third-party failures in their own subclasses so that no
  ``botocore`` or ``sqlalchemy`` exception ever escapes into the application layer.
"""

from __future__ import annotations

from typing import Any


class FactoryAIError(Exception):
    """Root of the FactoryAI exception hierarchy.

    Attributes:
        message: Human-readable description. May change between releases.
        code: Stable machine-readable identifier, e.g. ``"image.duplicate"``.
        details: Structured context safe to log and to return to a trusted caller.
    """

    default_code = "factoryai.error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialise with a human message and an optional machine code and context."""
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.details: dict[str, Any] = details or {}

    def __repr__(self) -> str:
        """Return an unambiguous representation including the error code."""
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"

    def to_dict(self) -> dict[str, Any]:
        """Return a serialisable form suitable for API responses and audit payloads."""
        return {"code": self.code, "message": self.message, "details": self.details}


class ConfigurationError(FactoryAIError):
    """Raised when settings are missing, malformed or mutually inconsistent.

    This is always a deployment or developer mistake, never a user input problem, and it
    should fail the process at startup rather than at first use.
    """

    default_code = "config.invalid"


class InfrastructureError(FactoryAIError):
    """Raised when an external system fails in a way the caller cannot correct.

    Adapters raise this (or a subclass) instead of leaking driver-specific exceptions,
    which keeps the application layer free of infrastructure imports.
    """

    default_code = "infrastructure.failure"


class TransientError(InfrastructureError):
    """An infrastructure failure that is worth retrying.

    Retry policies key off this type, so adapters must only raise it for genuinely
    transient conditions — timeouts, connection resets, throttling — and never for
    permanent ones such as a malformed request or a missing bucket.
    """

    default_code = "infrastructure.transient"
