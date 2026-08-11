"""JWT issuance and verification backed by PyJWT (Phase 8, ADR-0011)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from factoryai.domain.errors import TokenError
from factoryai.domain.ports.auth import (
    AccessTokenClaims,
    IssuedTokenPair,
    RefreshTokenClaims,
    TokenService,
)
from factoryai.domain.value_objects import UserId, UserRole, parse_uuid

_ACCESS_TOKEN_TYPE = "access"
_REFRESH_TOKEN_TYPE = "refresh"


class JwtTokenService(TokenService):
    """HMAC-signed JWTs, one claim set for access tokens and another for refresh tokens.

    Both token types share a signing secret and algorithm but carry a ``type`` claim so
    that an access token can never be replayed where a refresh token is expected, and
    vice versa — without it, a leaked access token (routinely sent on every request, so
    more exposed) would be just as good as a refresh token for minting new access tokens.
    """

    def __init__(
        self,
        *,
        secret_key: str,
        algorithm: str,
        access_token_minutes: int,
        refresh_token_days: int,
    ) -> None:
        """Initialise with the signing secret and both tokens' lifetimes."""
        self._secret_key = secret_key
        self._algorithm = algorithm
        self._access_token_lifetime = timedelta(minutes=access_token_minutes)
        self._refresh_token_lifetime = timedelta(days=refresh_token_days)

    def issue(self, *, user_id: UserId, role: UserRole) -> IssuedTokenPair:
        """Mint a fresh access/refresh token pair."""
        access_token, access_expires_at = self.issue_access_token(user_id=user_id, role=role)
        refresh_jti = str(uuid.uuid4())
        refresh_expires_at = datetime.now(UTC) + self._refresh_token_lifetime
        refresh_token = jwt.encode(
            {
                "sub": str(user_id),
                "jti": refresh_jti,
                "type": _REFRESH_TOKEN_TYPE,
                "exp": refresh_expires_at,
            },
            self._secret_key,
            algorithm=self._algorithm,
        )
        return IssuedTokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            refresh_jti=refresh_jti,
            refresh_expires_at=refresh_expires_at,
            access_expires_at=access_expires_at,
        )

    def issue_access_token(self, *, user_id: UserId, role: UserRole) -> tuple[str, datetime]:
        """Mint a new access token alone."""
        expires_at = datetime.now(UTC) + self._access_token_lifetime
        token = jwt.encode(
            {
                "sub": str(user_id),
                "role": role.value,
                "type": _ACCESS_TOKEN_TYPE,
                "exp": expires_at,
            },
            self._secret_key,
            algorithm=self._algorithm,
        )
        return token, expires_at

    def verify_access_token(self, token: str) -> AccessTokenClaims:
        """Decode and validate an access token.

        Raises:
            TokenError: If the token is malformed, has an invalid signature, has expired,
                or is a refresh token presented where an access token was expected.
        """
        claims = self._decode(token, expected_type=_ACCESS_TOKEN_TYPE)
        try:
            return AccessTokenClaims(
                user_id=UserId(parse_uuid(claims["sub"])),
                role=UserRole(claims["role"]),
                expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
            )
        except (KeyError, ValueError) as exc:
            raise TokenError("access token is missing required claims") from exc

    def verify_refresh_token(self, token: str) -> RefreshTokenClaims:
        """Decode and validate a refresh token.

        Raises:
            TokenError: If the token is malformed, has an invalid signature, has expired,
                or is an access token presented where a refresh token was expected.
        """
        claims = self._decode(token, expected_type=_REFRESH_TOKEN_TYPE)
        try:
            return RefreshTokenClaims(
                user_id=UserId(parse_uuid(claims["sub"])),
                jti=claims["jti"],
                expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
            )
        except (KeyError, ValueError) as exc:
            raise TokenError("refresh token is missing required claims") from exc

    def _decode(self, token: str, *, expected_type: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(token, self._secret_key, algorithms=[self._algorithm])
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("token has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise TokenError("token is malformed or has an invalid signature") from exc
        if claims.get("type") != expected_type:
            raise TokenError(f"expected a {expected_type!r} token, got {claims.get('type')!r}")
        return claims


__all__ = ["JwtTokenService"]
