"""The refresh use case: mint a new access token from a still-valid refresh token."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from factoryai.domain.errors import InactiveAccountError, TokenError
from factoryai.domain.ports.auth import TokenRevocationList, TokenService
from factoryai.domain.ports.repositories import UnitOfWork


@dataclass(frozen=True, slots=True)
class RefreshAccessTokenResult:
    """A freshly minted access token."""

    access_token: str
    expires_at: datetime


class RefreshAccessToken:
    """Exchanges a refresh token for a new access token, without rotating the refresh token."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        token_service: TokenService,
        revocation_list: TokenRevocationList,
    ) -> None:
        """Initialise with every collaborator this use case needs."""
        self._uow_factory = uow_factory
        self._token_service = token_service
        self._revocation_list = revocation_list

    async def execute(self, refresh_token: str) -> RefreshAccessTokenResult:
        """Verify a refresh token and issue a new access token.

        Raises:
            TokenError: If the token is malformed, expired, or has been revoked.
            EntityNotFoundError: If the token's user no longer exists.
            InactiveAccountError: If the user's account has since been deactivated.
        """
        claims = self._token_service.verify_refresh_token(refresh_token)
        if await self._revocation_list.is_revoked(claims.jti):
            raise TokenError("refresh token has been revoked")

        async with self._uow_factory() as uow:
            user = await uow.users.get(claims.user_id)

        if not user.is_active:
            raise InactiveAccountError(f"account {user.email!r} has been deactivated")

        access_token, expires_at = self._token_service.issue_access_token(
            user_id=user.id, role=user.role
        )
        return RefreshAccessTokenResult(access_token=access_token, expires_at=expires_at)
