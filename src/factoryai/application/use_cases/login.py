"""The login use case: exchange a password for a token pair (Phase 8, ADR-0011)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from factoryai.domain.entities import AuditEvent
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.errors import AuthenticationError, InactiveAccountError
from factoryai.domain.ports.auth import PasswordHasher, TokenService
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import Clock
from factoryai.domain.value_objects import AuditSequence, UserId, UserRole

_GENERIC_FAILURE = "invalid email or password"
"""Deliberately identical whether the email is unknown or the password is wrong — see
:class:`~factoryai.domain.errors.AuthenticationError`'s docstring for why."""


@dataclass(frozen=True, slots=True)
class LoginCommand:
    """Credentials presented for authentication."""

    email: str
    password: str


@dataclass(frozen=True, slots=True)
class LoginResult:
    """A freshly issued token pair."""

    user_id: UserId
    role: UserRole
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


class Login:
    """Verifies a password and issues a token pair."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        password_hasher: PasswordHasher,
        token_service: TokenService,
        clock: Clock,
    ) -> None:
        """Initialise with every collaborator this use case needs."""
        self._uow_factory = uow_factory
        self._password_hasher = password_hasher
        self._token_service = token_service
        self._clock = clock

    async def execute(self, command: LoginCommand) -> LoginResult:
        """Authenticate and issue tokens.

        Raises:
            AuthenticationError: If the email is unknown or the password does not match.
            InactiveAccountError: If the credentials are correct but the account has been
                deactivated.
        """
        now = self._clock.now()
        email = command.email.strip().lower()
        async with self._uow_factory() as uow:
            user = await uow.users.find_by_email(email)
            if user is None:
                raise AuthenticationError(_GENERIC_FAILURE)

            password_hash = await uow.users.get_password_hash(user.id)
            if password_hash is None or not self._password_hasher.verify(
                command.password, password_hash
            ):
                raise AuthenticationError(_GENERIC_FAILURE)

            if not user.is_active:
                raise InactiveAccountError(f"account {email!r} has been deactivated")

            issued = self._token_service.issue(user_id=user.id, role=user.role)

            latest = await uow.audit.latest()
            event = AuditEvent(
                sequence=AuditSequence((latest.sequence + 1) if latest else 1),
                action="user.logged_in",
                resource_type="user",
                resource_id=str(user.id),
                actor_id=user.id,
                occurred_at=now,
                prev_hash=latest.row_hash() if latest else GENESIS_HASH,
            )
            await uow.audit.append(event)
            await uow.commit()

        return LoginResult(
            user_id=user.id,
            role=user.role,
            access_token=issued.access_token,
            refresh_token=issued.refresh_token,
            access_expires_at=issued.access_expires_at,
            refresh_expires_at=issued.refresh_expires_at,
        )
