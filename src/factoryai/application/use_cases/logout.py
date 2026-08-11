"""The logout use case: revoke a refresh token before it would naturally expire."""

from __future__ import annotations

from collections.abc import Callable

from factoryai.domain.entities import AuditEvent
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.ports.auth import TokenRevocationList, TokenService
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import Clock
from factoryai.domain.value_objects import AuditSequence


class Logout:
    """Revokes a refresh token, ending that session immediately."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        token_service: TokenService,
        revocation_list: TokenRevocationList,
        clock: Clock,
    ) -> None:
        """Initialise with every collaborator this use case needs."""
        self._uow_factory = uow_factory
        self._token_service = token_service
        self._revocation_list = revocation_list
        self._clock = clock

    async def execute(self, refresh_token: str) -> None:
        """Revoke a refresh token and record the logout.

        Raises:
            TokenError: If the token is malformed or has already expired.
        """
        claims = self._token_service.verify_refresh_token(refresh_token)
        await self._revocation_list.revoke(claims.jti, expires_at=claims.expires_at)

        now = self._clock.now()
        async with self._uow_factory() as uow:
            latest = await uow.audit.latest()
            event = AuditEvent(
                sequence=AuditSequence((latest.sequence + 1) if latest else 1),
                action="user.logged_out",
                resource_type="user",
                resource_id=str(claims.user_id),
                actor_id=claims.user_id,
                occurred_at=now,
                prev_hash=latest.row_hash() if latest else GENESIS_HASH,
            )
            await uow.audit.append(event)
            await uow.commit()
