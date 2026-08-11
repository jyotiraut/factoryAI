"""Unit tests for the ``Login`` use case, against fakes."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from factoryai.application.use_cases.login import Login, LoginCommand
from factoryai.domain.entities import User
from factoryai.domain.errors import AuthenticationError, InactiveAccountError
from factoryai.domain.value_objects import UserId, UserRole
from factoryai.infrastructure.auth.argon2_hasher import Argon2PasswordHasher
from factoryai.infrastructure.auth.jwt_tokens import JwtTokenService
from tests.builders import NOW, a_user
from tests.fakes import FakeClock, FakeUnitOfWork

pytestmark = pytest.mark.unit

_SECRET = "unit-test-secret-key-at-least-32-bytes"


async def _seed_user(
    uow: FakeUnitOfWork, hasher: Argon2PasswordHasher, *, password: str, **overrides: Any
) -> User:
    user = a_user(id=UserId(uuid.uuid4()), **overrides)
    await uow.users.add(user)
    await uow.users.set_password_hash(user.id, hasher.hash(password))
    return user


def _use_case(uow: FakeUnitOfWork, hasher: Argon2PasswordHasher) -> Login:
    return Login(
        uow_factory=lambda: uow,
        password_hasher=hasher,
        token_service=JwtTokenService(
            secret_key=_SECRET, algorithm="HS256", access_token_minutes=30, refresh_token_days=7
        ),
        clock=FakeClock(NOW),
    )


class TestLogin:
    async def test_correct_credentials_issue_a_token_pair(self) -> None:
        uow = FakeUnitOfWork()
        hasher = Argon2PasswordHasher()
        user = await _seed_user(uow, hasher, password="correct-password", role=UserRole.OPERATOR)
        use_case = _use_case(uow, hasher)

        result = await use_case.execute(LoginCommand(email=user.email, password="correct-password"))

        assert result.user_id == user.id
        assert result.access_token
        assert result.refresh_token

    async def test_an_unknown_email_is_rejected(self) -> None:
        uow = FakeUnitOfWork()
        hasher = Argon2PasswordHasher()
        use_case = _use_case(uow, hasher)

        with pytest.raises(AuthenticationError):
            await use_case.execute(LoginCommand(email="nobody@factory.example", password="x"))

    async def test_a_wrong_password_is_rejected(self) -> None:
        uow = FakeUnitOfWork()
        hasher = Argon2PasswordHasher()
        user = await _seed_user(uow, hasher, password="correct-password")
        use_case = _use_case(uow, hasher)

        with pytest.raises(AuthenticationError):
            await use_case.execute(LoginCommand(email=user.email, password="wrong-password"))

    async def test_a_deactivated_account_is_rejected(self) -> None:
        uow = FakeUnitOfWork()
        hasher = Argon2PasswordHasher()
        user = await _seed_user(uow, hasher, password="correct-password")
        await uow.users.update(user.deactivate())
        use_case = _use_case(uow, hasher)

        with pytest.raises(InactiveAccountError):
            await use_case.execute(LoginCommand(email=user.email, password="correct-password"))

    async def test_an_audit_event_is_appended_on_success(self) -> None:
        uow = FakeUnitOfWork()
        hasher = Argon2PasswordHasher()
        user = await _seed_user(uow, hasher, password="correct-password")
        use_case = _use_case(uow, hasher)

        await use_case.execute(LoginCommand(email=user.email, password="correct-password"))

        latest = await uow.audit.latest()
        assert latest is not None
        assert latest.action == "user.logged_in"
