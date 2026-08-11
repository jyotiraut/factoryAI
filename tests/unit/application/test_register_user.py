"""Unit tests for the ``RegisterUser`` use case, against fakes."""

from __future__ import annotations

import pytest

from factoryai.application.use_cases.register_user import RegisterUser, RegisterUserCommand
from factoryai.domain.errors import EmailAlreadyRegisteredError
from factoryai.domain.value_objects import UserRole
from factoryai.infrastructure.auth.argon2_hasher import Argon2PasswordHasher
from tests.builders import NOW
from tests.fakes import FakeClock, FakeIdGenerator, FakeUnitOfWork

pytestmark = pytest.mark.unit


def _use_case(uow: FakeUnitOfWork) -> RegisterUser:
    return RegisterUser(
        uow_factory=lambda: uow,
        password_hasher=Argon2PasswordHasher(),
        clock=FakeClock(NOW),
        id_generator=FakeIdGenerator(),
    )


class TestRegisterUser:
    async def test_a_new_user_is_created_with_a_hashed_password(self) -> None:
        uow = FakeUnitOfWork()
        use_case = _use_case(uow)

        result = await use_case.execute(
            RegisterUserCommand(
                email="New.Operator@Factory.Example",
                password="a-strong-password",
                role=UserRole.OPERATOR,
            )
        )

        user = await uow.users.get(result.user_id)
        assert user.email == "new.operator@factory.example"
        assert user.role == UserRole.OPERATOR
        password_hash = await uow.users.get_password_hash(result.user_id)
        assert password_hash is not None
        assert password_hash != "a-strong-password"

    async def test_an_audit_event_is_appended(self) -> None:
        uow = FakeUnitOfWork()
        use_case = _use_case(uow)

        await use_case.execute(
            RegisterUserCommand(
                email="operator@factory.example", password="pw", role=UserRole.OPERATOR
            )
        )

        latest = await uow.audit.latest()
        assert latest is not None
        assert latest.action == "user.registered"

    async def test_a_duplicate_email_is_rejected(self) -> None:
        uow = FakeUnitOfWork()
        use_case = _use_case(uow)
        command = RegisterUserCommand(
            email="operator@factory.example", password="pw", role=UserRole.OPERATOR
        )
        await use_case.execute(command)

        with pytest.raises(EmailAlreadyRegisteredError):
            await use_case.execute(command)
