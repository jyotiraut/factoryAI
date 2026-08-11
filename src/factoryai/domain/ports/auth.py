"""Ports for authentication: password verification, token issuance, and revocation.

Every implementation lives in infrastructure (argon2, PyJWT) — the domain declares only
the shapes it needs, per ADR-0001. See ADR-0011 for why credentials still never touch the
:class:`~factoryai.domain.entities.user.User` entity itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from factoryai.domain.value_objects import UserId, UserRole


class PasswordHasher(ABC):
    """One-way password hashing and verification."""

    @abstractmethod
    def hash(self, plain_password: str) -> str:
        """Return a salted hash of ``plain_password``, safe to persist."""

    @abstractmethod
    def verify(self, plain_password: str, password_hash: str) -> bool:
        """Return whether ``plain_password`` matches a previously hashed value."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """What a verified access token asserts about its bearer."""

    user_id: UserId
    role: UserRole
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RefreshTokenClaims:
    """What a verified refresh token asserts about its bearer.

    Carries no role: a role change between issuance and refresh must be picked up from the
    current user record, not trusted from a token minted under the old role.
    """

    user_id: UserId
    jti: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedTokenPair:
    """The two tokens returned by a successful login or refresh."""

    access_token: str
    refresh_token: str
    refresh_jti: str
    refresh_expires_at: datetime
    access_expires_at: datetime


class TokenService(ABC):
    """JWT issuance and verification."""

    @abstractmethod
    def issue(self, *, user_id: UserId, role: UserRole) -> IssuedTokenPair:
        """Mint a fresh access/refresh token pair for a just-authenticated user."""

    @abstractmethod
    def issue_access_token(self, *, user_id: UserId, role: UserRole) -> tuple[str, datetime]:
        """Mint a new access token alone, e.g. when refreshing without rotating."""

    @abstractmethod
    def verify_access_token(self, token: str) -> AccessTokenClaims:
        """Decode and validate an access token.

        Raises:
            TokenError: If the token is malformed, has an invalid signature, or has expired.
        """

    @abstractmethod
    def verify_refresh_token(self, token: str) -> RefreshTokenClaims:
        """Decode and validate a refresh token.

        Raises:
            TokenError: If the token is malformed, has an invalid signature, or has expired.
        """


class TokenRevocationList(ABC):
    """Tracks refresh tokens invalidated before their natural expiry (logout).

    Access tokens are short-lived by design and are never individually revocable — only
    their refresh token is, which caps how long a logout takes to fully take effect at one
    access-token lifetime.
    """

    @abstractmethod
    async def revoke(self, jti: str, *, expires_at: datetime) -> None:
        """Blacklist a refresh token's identifier until it would have expired anyway."""

    @abstractmethod
    async def is_revoked(self, jti: str) -> bool:
        """Return whether a refresh token identifier has been revoked."""
