"""Unit tests for the ``RefreshAccessToken`` use case, against fakes."""

from __future__ import annotations

import uuid

import pytest

from factoryai.application.use_cases.refresh_access_token import RefreshAccessToken
from factoryai.domain.errors import InactiveAccountError, TokenError
from factoryai.domain.value_objects import UserId
from factoryai.infrastructure.auth.jwt_tokens import JwtTokenService
from tests.builders import a_user
from tests.fakes import FakeTokenRevocationList, FakeUnitOfWork

pytestmark = pytest.mark.unit

_SECRET = "unit-test-secret-key-at-least-32-bytes"


def _service() -> JwtTokenService:
    return JwtTokenService(
        secret_key=_SECRET, algorithm="HS256", access_token_minutes=30, refresh_token_days=7
    )


class TestRefreshAccessToken:
    async def test_a_valid_refresh_token_issues_a_new_access_token(self) -> None:
        uow = FakeUnitOfWork()
        user = a_user(id=UserId(uuid.uuid4()))
        await uow.users.add(user)
        token_service = _service()
        issued = token_service.issue(user_id=user.id, role=user.role)
        use_case = RefreshAccessToken(
            uow_factory=lambda: uow,
            token_service=token_service,
            revocation_list=FakeTokenRevocationList(),
        )

        result = await use_case.execute(issued.refresh_token)

        claims = token_service.verify_access_token(result.access_token)
        assert claims.user_id == user.id

    async def test_a_revoked_refresh_token_is_rejected(self) -> None:
        uow = FakeUnitOfWork()
        user = a_user(id=UserId(uuid.uuid4()))
        await uow.users.add(user)
        token_service = _service()
        issued = token_service.issue(user_id=user.id, role=user.role)
        revocation_list = FakeTokenRevocationList()
        await revocation_list.revoke(issued.refresh_jti, expires_at=issued.refresh_expires_at)
        use_case = RefreshAccessToken(
            uow_factory=lambda: uow, token_service=token_service, revocation_list=revocation_list
        )

        with pytest.raises(TokenError):
            await use_case.execute(issued.refresh_token)

    async def test_a_deactivated_users_refresh_token_is_rejected(self) -> None:
        uow = FakeUnitOfWork()
        user = a_user(id=UserId(uuid.uuid4()))
        await uow.users.add(user)
        token_service = _service()
        issued = token_service.issue(user_id=user.id, role=user.role)
        await uow.users.update(user.deactivate())
        use_case = RefreshAccessToken(
            uow_factory=lambda: uow,
            token_service=token_service,
            revocation_list=FakeTokenRevocationList(),
        )

        with pytest.raises(InactiveAccountError):
            await use_case.execute(issued.refresh_token)
