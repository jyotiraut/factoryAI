"""FastAPI dependency providers.

Thin accessors only — resolving the actual use case is still the container's job (mirrors
``factoryai.cli``'s ``build_container(settings)`` call at the top of every command).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from factoryai.bootstrap.container import Container
from factoryai.domain.entities import User
from factoryai.domain.errors import EntityNotFoundError, TokenError
from factoryai.domain.policies.permissions import Permission, has_permission

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="a valid bearer token is required",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_container(request: Request) -> Container:
    """Return the process-wide container built once at startup (see ``api/main.py``)."""
    return request.app.state.container  # type: ignore[no-any-return]


async def get_current_user(request: Request, container: Container = Depends(get_container)) -> User:
    """Resolve the authenticated principal from the request's bearer access token.

    The role used for every downstream permission check is read fresh from the database,
    not from the token's own ``role`` claim — a role change or deactivation must take
    effect on the very next request, not wait for that user's current access token to
    expire.

    Raises:
        HTTPException: 401 if the header is missing, the token is invalid or expired, or
            the account it names no longer exists or has been deactivated.
    """
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _UNAUTHENTICATED
    try:
        claims = container.token_service.verify_access_token(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    async with container.unit_of_work() as uow:
        try:
            user = await uow.users.get(claims.user_id)
        except EntityNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="account no longer exists"
            ) from exc

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="account has been deactivated"
        )
    return user


def require_permission(permission: Permission) -> Callable[..., Coroutine[Any, Any, User]]:
    """Build a dependency that only lets a request through if it holds ``permission``.

    This is the one place a route's RBAC requirement is spelled out (mirrors
    :mod:`factoryai.domain.policies.permissions`'s own reasoning for keying the matrix by
    permission rather than by role) — a route depends on ``require_permission(...)``, never
    on a raw role comparison of its own.

    Raises:
        HTTPException: 403 if the authenticated user's role does not satisfy ``permission``.
    """

    async def guard(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"permission {permission.value!r} is required",
            )
        return user

    return guard


async def acquire_prediction_slot(request: Request) -> AsyncIterator[None]:
    """Bound how many predictions run concurrently (``API_MAX_CONCURRENT_PREDICTIONS``).

    Detector inference is CPU-bound and dispatched to a thread (ADR-0008); without a cap,
    an unbounded number of concurrent requests would each spawn a thread and contend for
    the same CPU, degrading every in-flight prediction's latency instead of queueing
    cleanly behind a fixed number of slots.
    """
    semaphore = request.app.state.prediction_semaphore
    async with semaphore:
        yield
