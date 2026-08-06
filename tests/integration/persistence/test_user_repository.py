"""Integration tests for :class:`SqlAlchemyUserRepository` against real PostgreSQL."""

from __future__ import annotations

import uuid

import pytest

from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.value_objects import UserId, UserRole
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.builders import a_user

pytestmark = pytest.mark.integration


async def test_add_then_get_round_trips(uow: SqlAlchemyUnitOfWork) -> None:
    user = a_user()
    async with uow:
        await uow.users.add(user)
        await uow.commit()

    async with uow:
        fetched = await uow.users.get(user.id)
    assert fetched == user


async def test_get_raises_when_missing(uow: SqlAlchemyUnitOfWork) -> None:
    async with uow:
        with pytest.raises(EntityNotFoundError):
            await uow.users.get(UserId(uuid.uuid4()))


async def test_find_by_email(uow: SqlAlchemyUnitOfWork) -> None:
    user = a_user(email="engineer@factory.example")
    async with uow:
        await uow.users.add(user)
        await uow.commit()

    async with uow:
        found = await uow.users.find_by_email("engineer@factory.example")
        missing = await uow.users.find_by_email("nobody@factory.example")
    assert found == user
    assert missing is None


async def test_update_persists_role_and_deactivation(uow: SqlAlchemyUnitOfWork) -> None:
    user = a_user()
    async with uow:
        await uow.users.add(user)
        await uow.commit()

    changed = user.assign_role(UserRole.ADMINISTRATOR).deactivate()
    async with uow:
        await uow.users.update(changed)
        await uow.commit()

    async with uow:
        fetched = await uow.users.get(user.id)
    assert fetched.role is UserRole.ADMINISTRATOR
    assert not fetched.is_active
