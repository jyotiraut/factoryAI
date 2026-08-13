"""``/auth/*`` — login, refresh, logout, and administrator-only account creation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from factoryai.api.dependencies import get_container, get_current_user, require_permission
from factoryai.api.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterUserRequest,
    TokenResponse,
    UserResponse,
)
from factoryai.application.use_cases.login import LoginCommand
from factoryai.application.use_cases.register_user import RegisterUserCommand
from factoryai.bootstrap.container import Container
from factoryai.domain.entities import User
from factoryai.domain.errors import (
    AuthenticationError,
    EmailAlreadyRegisteredError,
    EntityNotFoundError,
    InactiveAccountError,
    TokenError,
)
from factoryai.domain.policies.permissions import Permission
from factoryai.domain.value_objects import UserRole

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, container: Container = Depends(get_container)
) -> TokenResponse:
    """Exchange an email and password for an access/refresh token pair.

    Raises:
        HTTPException: 401 if the credentials are wrong or the account is deactivated. The
            same status and a deliberately generic message cover both an unknown email and
            a wrong password (see :class:`~factoryai.domain.errors.AuthenticationError`).
    """
    use_case = container.login_use_case()
    command = LoginCommand(email=payload.email, password=payload.password)
    try:
        result = await use_case.execute(command)
    except (AuthenticationError, InactiveAccountError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc

    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=container.settings.auth.access_token_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest, container: Container = Depends(get_container)
) -> TokenResponse:
    """Exchange a still-valid refresh token for a new access token.

    Raises:
        HTTPException: 401 if the refresh token is malformed, expired, revoked, or its
            account no longer exists or has been deactivated.
    """
    use_case = container.refresh_access_token_use_case()
    try:
        result = await use_case.execute(payload.refresh_token)
    except (TokenError, EntityNotFoundError, InactiveAccountError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc

    return TokenResponse(
        access_token=result.access_token,
        refresh_token=None,
        expires_in=container.settings.auth.access_token_minutes * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: LogoutRequest, container: Container = Depends(get_container)) -> None:
    """Revoke a refresh token, ending that session immediately.

    Raises:
        HTTPException: 401 if the refresh token is malformed or has already expired.
    """
    use_case = container.logout_use_case()
    try:
        await use_case.execute(payload.refresh_token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(user: User = Depends(get_current_user)) -> UserResponse:
    """Return the authenticated caller's own identity and role.

    Phase 13's role-aware navigation reads this rather than decoding the access token's
    own ``role`` claim client-side — the same freshness argument
    :func:`~factoryai.api.dependencies.get_current_user` already makes for permission
    checks applies equally to what a nav bar renders: a role change should not wait for
    the client to notice its cached token is stale.
    """
    return UserResponse(user_id=str(user.id), email=user.email, role=user.role.value)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterUserRequest,
    container: Container = Depends(get_container),
    _actor: User = Depends(require_permission(Permission.MANAGE_USERS)),
) -> UserResponse:
    """Create a new account.

    Administrator-only — see ``factoryai user create`` for how the very first
    administrator is created before any admin exists to call this.

    Raises:
        HTTPException: 401/403 if the caller is not authenticated or lacks ``manage_users``;
            409 if the email is already registered.
    """
    use_case = container.register_user_use_case()
    try:
        result = await use_case.execute(
            RegisterUserCommand(
                email=payload.email,
                password=payload.password,
                role=UserRole(payload.role),
                display_name=payload.display_name,
            )
        )
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc

    return UserResponse(
        user_id=str(result.user_id), email=payload.email.strip().lower(), role=payload.role
    )
