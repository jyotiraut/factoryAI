"""Unit tests for the ``Logout`` use case, against fakes."""

from __future__ import annotations

import uuid

import pytest

from factoryai.application.use_cases.logout import Logout
from factoryai.application.use_cases.refresh_access_token import RefreshAccessToken
from factoryai.domain.errors import TokenError
from factoryai.domain.value_objects import UserId
from factoryai.infrastructure.auth.jwt_tokens import JwtTokenService
from tests.builders import NOW, a_user
from tests.fakes import FakeClock, FakeTokenRevocationList, FakeUnitOfWork

pytestmark = pytest.mark.unit

_SECRET = "unit-test-secret-key-at-least-32-bytes"


def _service() -> JwtTokenService:
    return JwtTokenService(
        secret_key=_SECRET, algorithm="HS256", access_token_minutes=30, refresh_token_days=7
    )


class TestLogout:
    async def test_logging_out_revokes_the_refresh_token(self) -> None:
        uow = FakeUnitOfWork()
        user = a_user(id=UserId(uuid.uuid4()))
        await uow.users.add(user)
        token_service = _service()
        revocation_list = FakeTokenRevocationList()
        issued = token_service.issue(user_id=user.id, role=user.role)
        use_case = Logout(
            uow_factory=lambda: uow,
            token_service=token_service,
            revocation_list=revocation_list,
            clock=FakeClock(NOW),
        )

        await use_case.execute(issued.refresh_token)

        refresh_use_case = RefreshAccessToken(
            uow_factory=lambda: uow, token_service=token_service, revocation_list=revocation_list
        )
        with pytest.raises(TokenError):
            await refresh_use_case.execute(issued.refresh_token)

    async def test_an_audit_event_is_appended(self) -> None:
        uow = FakeUnitOfWork()
        user = a_user(id=UserId(uuid.uuid4()))
        await uow.users.add(user)
        token_service = _service()
        issued = token_service.issue(user_id=user.id, role=user.role)
        use_case = Logout(
            uow_factory=lambda: uow,
            token_service=token_service,
            revocation_list=FakeTokenRevocationList(),
            clock=FakeClock(NOW),
        )

        await use_case.execute(issued.refresh_token)

        latest = await uow.audit.latest()
        assert latest is not None
        assert latest.action == "user.logged_out"
