"""Integration tests for the Phase 8 auth persistence, against real PostgreSQL.

Covers what a fake structurally cannot: the real ``password_hash`` column round-trips
through an actual ``UPDATE``, and :class:`SqlAlchemyTokenRevocationList` opens its own
sessions independently of any unit of work.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.value_objects import UserId, parse_uuid
from factoryai.infrastructure.persistence.repositories import SqlAlchemyTokenRevocationList
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.builders import a_user

pytestmark = pytest.mark.integration


class TestPasswordHashRoundTrip:
    async def test_a_password_hash_survives_a_real_commit_and_reload(
        self, uow: SqlAlchemyUnitOfWork
    ) -> None:
        user = a_user()
        async with uow:
            await uow.users.add(user)
            await uow.users.set_password_hash(user.id, "argon2id$fake-hash-value")
            await uow.commit()

        async with uow:
            password_hash = await uow.users.get_password_hash(user.id)
        assert password_hash == "argon2id$fake-hash-value"

    async def test_a_user_with_no_password_set_yet_has_none(
        self, uow: SqlAlchemyUnitOfWork
    ) -> None:
        user = a_user()
        async with uow:
            await uow.users.add(user)
            await uow.commit()

        async with uow:
            password_hash = await uow.users.get_password_hash(user.id)
        assert password_hash is None

    async def test_setting_a_password_hash_for_an_unknown_user_raises(
        self, uow: SqlAlchemyUnitOfWork
    ) -> None:
        async with uow:
            with pytest.raises(EntityNotFoundError):
                await uow.users.set_password_hash(
                    UserId(parse_uuid("00000000-0000-0000-0000-000000000000")), "irrelevant"
                )

    async def test_a_role_change_does_not_wipe_the_password_hash(
        self, uow: SqlAlchemyUnitOfWork
    ) -> None:
        """Regression test for a real bug found while writing this file.

        ``update()`` used to copy every column from a freshly built row, including
        ``password_hash`` — which the mapper never sets — silently overwriting a real hash
        with ``NULL`` on the very next role change or deactivation. Only a real Postgres
        round trip catches this: a fake's ``update()`` just replaces the dict entry with
        the entity it was given, which never carries a hash to lose in the first place.
        """
        user = a_user()
        async with uow:
            await uow.users.add(user)
            await uow.users.set_password_hash(user.id, "argon2id$fake-hash-value")
            await uow.commit()

        async with uow:
            await uow.users.update(user.deactivate())
            await uow.commit()

        async with uow:
            password_hash = await uow.users.get_password_hash(user.id)
        assert password_hash == "argon2id$fake-hash-value"


class TestTokenRevocationList:
    async def test_a_revoked_token_is_reported_as_revoked(self, engine: AsyncEngine) -> None:
        revocation_list = SqlAlchemyTokenRevocationList(async_sessionmaker(engine))
        jti = "test-jti-revoked"

        assert await revocation_list.is_revoked(jti) is False
        await revocation_list.revoke(jti, expires_at=datetime.now(UTC) + timedelta(days=1))
        assert await revocation_list.is_revoked(jti) is True

    async def test_revoking_the_same_jti_twice_does_not_raise(self, engine: AsyncEngine) -> None:
        revocation_list = SqlAlchemyTokenRevocationList(async_sessionmaker(engine))
        jti = "test-jti-idempotent"
        expires_at = datetime.now(UTC) + timedelta(days=1)

        await revocation_list.revoke(jti, expires_at=expires_at)
        await revocation_list.revoke(jti, expires_at=expires_at)

        assert await revocation_list.is_revoked(jti) is True
