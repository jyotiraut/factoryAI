"""Platform users and their roles."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Self

from factoryai.domain.errors import InvariantViolationError
from factoryai.domain.value_objects import UserId, UserRole


@dataclass(frozen=True, slots=True)
class User:
    """An authenticated principal.

    The domain deliberately holds no password, hash or token: credential handling is an
    infrastructure concern (Phase 8), and keeping it out of the entity means a ``User``
    can be logged, serialised and passed around without any risk of leaking a secret.

    Attributes:
        id: Unique identifier.
        email: Login identifier, stored lowercase.
        role: Assigned role.
        created_at: Timezone-aware creation timestamp.
        display_name: Optional human-readable name.
        is_active: Whether the account may authenticate.
    """

    id: UserId
    email: str
    role: UserRole
    created_at: datetime
    display_name: str = ""
    is_active: bool = True

    def __post_init__(self) -> None:
        """Validate the email and timestamp.

        Raises:
            InvariantViolationError: If the email is blank, is not lowercase, lacks an ``@``,
                or the timestamp is naive.
        """
        if not self.email.strip():
            raise InvariantViolationError("user email must not be blank", code="user.no_email")
        if "@" not in self.email:
            raise InvariantViolationError(
                f"user email {self.email!r} is not a valid address", code="user.invalid_email"
            )
        if self.email != self.email.lower():
            raise InvariantViolationError(
                "user email must be stored lowercase to keep lookups unambiguous",
                code="user.email_not_normalised",
            )
        if self.created_at.tzinfo is None:
            raise InvariantViolationError(
                "created_at must be timezone-aware", code="user.naive_timestamp"
            )

    def can(self, required: UserRole) -> bool:
        """Return whether this user satisfies a role requirement.

        A deactivated account satisfies nothing, regardless of its role — which is what
        makes deactivation an effective revocation rather than a cosmetic flag.
        """
        return self.is_active and self.role.can_act_as(required)

    def deactivate(self) -> Self:
        """Return a copy that can no longer authenticate."""
        return dataclasses.replace(self, is_active=False)

    def reactivate(self) -> Self:
        """Return a copy restored to active use."""
        return dataclasses.replace(self, is_active=True)

    def assign_role(self, role: UserRole) -> Self:
        """Return a copy with a different role."""
        return dataclasses.replace(self, role=role)
