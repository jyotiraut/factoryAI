"""The registration use case: create a new platform account (Phase 8, ADR-0011).

Deliberately not exposed as an open, self-service HTTP endpoint (see ``docs/adr/
0011-jwt-auth-and-rbac.md``): a factory floor's user list is small and managed by an
administrator, not the public, so this is reached from ``factoryai user create`` and from
``POST /auth/register`` guarded to administrators only — never anonymously.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factoryai.domain.entities import AuditEvent, User
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.errors import EmailAlreadyRegisteredError
from factoryai.domain.ports.auth import PasswordHasher
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import Clock, IdGenerator
from factoryai.domain.value_objects import AuditSequence, UserId, UserRole


@dataclass(frozen=True, slots=True)
class RegisterUserCommand:
    """A new account to create.

    Attributes:
        email: Login identifier. Normalised to lowercase before storage.
        password: The plaintext password, hashed before it ever reaches persistence.
        role: The role to assign.
        display_name: Optional human-readable name.
    """

    email: str
    password: str
    role: UserRole
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class RegisterUserResult:
    """The outcome of registering a user.

    Attributes:
        user_id: The newly created account's identifier.
    """

    user_id: UserId


class RegisterUser:
    """Creates a new account with a hashed password."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        password_hasher: PasswordHasher,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        """Initialise with every collaborator this use case needs."""
        self._uow_factory = uow_factory
        self._password_hasher = password_hasher
        self._clock = clock
        self._id_generator = id_generator

    async def execute(self, command: RegisterUserCommand) -> RegisterUserResult:
        """Create a new account.

        Raises:
            EmailAlreadyRegisteredError: If the email is already on file.
            InvariantViolationError: If the email is malformed.
        """
        now = self._clock.now()
        email = command.email.strip().lower()
        async with self._uow_factory() as uow:
            if await uow.users.find_by_email(email) is not None:
                raise EmailAlreadyRegisteredError(f"email {email!r} is already registered")

            user = User(
                id=UserId(self._id_generator.new_id()),
                email=email,
                role=command.role,
                created_at=now,
                display_name=command.display_name,
            )
            await uow.users.add(user)
            await uow.users.set_password_hash(user.id, self._password_hasher.hash(command.password))

            latest = await uow.audit.latest()
            event = AuditEvent(
                sequence=AuditSequence((latest.sequence + 1) if latest else 1),
                action="user.registered",
                resource_type="user",
                resource_id=str(user.id),
                occurred_at=now,
                prev_hash=latest.row_hash() if latest else GENESIS_HASH,
                payload={"email": email, "role": command.role.value},
            )
            await uow.audit.append(event)
            await uow.commit()

        return RegisterUserResult(user_id=user.id)
