"""Unit tests for the JWT issuance/verification adapter."""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

from factoryai.domain.errors import TokenError
from factoryai.domain.value_objects import UserId, UserRole
from factoryai.infrastructure.auth.jwt_tokens import JwtTokenService

pytestmark = pytest.mark.unit

_SECRET = "unit-test-secret-key-at-least-32-bytes"


def _service(**overrides: int) -> JwtTokenService:
    defaults = {"access_token_minutes": 30, "refresh_token_days": 7}
    return JwtTokenService(secret_key=_SECRET, algorithm="HS256", **{**defaults, **overrides})


class TestIssueAndVerify:
    def test_a_freshly_issued_access_token_verifies(self) -> None:
        service = _service()
        user_id = UserId(uuid.uuid4())
        issued = service.issue(user_id=user_id, role=UserRole.OPERATOR)

        claims = service.verify_access_token(issued.access_token)

        assert claims.user_id == user_id
        assert claims.role == UserRole.OPERATOR

    def test_a_freshly_issued_refresh_token_verifies(self) -> None:
        service = _service()
        user_id = UserId(uuid.uuid4())
        issued = service.issue(user_id=user_id, role=UserRole.OPERATOR)

        claims = service.verify_refresh_token(issued.refresh_token)

        assert claims.user_id == user_id
        assert claims.jti == issued.refresh_jti


class TestTokenTypeIsolation:
    def test_an_access_token_is_rejected_where_a_refresh_token_is_expected(self) -> None:
        service = _service()
        issued = service.issue(user_id=UserId(uuid.uuid4()), role=UserRole.OPERATOR)

        with pytest.raises(TokenError):
            service.verify_refresh_token(issued.access_token)

    def test_a_refresh_token_is_rejected_where_an_access_token_is_expected(self) -> None:
        service = _service()
        issued = service.issue(user_id=UserId(uuid.uuid4()), role=UserRole.OPERATOR)

        with pytest.raises(TokenError):
            service.verify_access_token(issued.refresh_token)


class TestExpiryAndTampering:
    def test_an_expired_access_token_is_rejected(self) -> None:
        service = _service(access_token_minutes=1)
        # PyJWT compares against wall-clock time; a token minted to already be in the past
        # is the deterministic way to force expiry without sleeping in a unit test.
        expired = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "role": "operator",
                "type": "access",
                "exp": time.time() - 60,
            },
            _SECRET,
            algorithm="HS256",
        )

        with pytest.raises(TokenError):
            service.verify_access_token(expired)

    def test_a_token_signed_with_a_different_secret_is_rejected(self) -> None:
        service = _service()
        forger = JwtTokenService(
            secret_key="a-completely-different-secret-key-value",
            algorithm="HS256",
            access_token_minutes=30,
            refresh_token_days=7,
        )
        forged = forger.issue(user_id=UserId(uuid.uuid4()), role=UserRole.ADMINISTRATOR)

        with pytest.raises(TokenError):
            service.verify_access_token(forged.access_token)

    def test_a_malformed_token_is_rejected(self) -> None:
        service = _service()
        with pytest.raises(TokenError):
            service.verify_access_token("not-a-real-jwt")


class TestAccessTokenAlone:
    def test_issuing_an_access_token_alone_does_not_mint_a_refresh_token(self) -> None:
        service = _service()
        token, expires_at = service.issue_access_token(
            user_id=UserId(uuid.uuid4()), role=UserRole.VIEWER
        )

        claims = service.verify_access_token(token)
        # JWT's numeric `exp` claim is whole seconds; round-tripping through it loses the
        # sub-second precision `expires_at` was minted with.
        assert abs((claims.expires_at - expires_at).total_seconds()) < 1
