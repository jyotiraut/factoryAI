"""ASGI middleware for request-level tracing and backpressure (ADR-0010)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from factoryai.shared.correlation import correlation_scope

CORRELATION_HEADER = "X-Correlation-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Binds a correlation id for the request's duration and echoes it in the response.

    Adopts an inbound ``X-Correlation-ID`` if the caller sent one (tracing a request that
    already started upstream), otherwise generates a fresh one — see
    ``factoryai.shared.correlation``.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Bind the correlation id, run the request, and echo it back."""
        with correlation_scope(request.headers.get(CORRELATION_HEADER)) as correlation_id:
            response = await call_next(request)
            response.headers[CORRELATION_HEADER] = correlation_id
            return response


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Rejects a request whose declared body size exceeds ``API_MAX_REQUEST_BYTES``.

    Checked against the ``Content-Length`` header rather than by reading the body — the
    point is to refuse an oversized upload before it is ever buffered into memory.
    """

    def __init__(self, app: object, *, max_bytes: int) -> None:
        """Wrap ``app``, rejecting any request declaring a body over ``max_bytes``."""
        super().__init__(app)  # type: ignore[arg-type]
        self._max_bytes = max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Reject an oversized request before it reaches any route handler."""
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > self._max_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": f"request body exceeds {self._max_bytes} bytes"},
            )
        return await call_next(request)
