"""Unit tests for the read-only use case behind ``GET /models/versions``, against fakes."""

from __future__ import annotations

import pytest

from factoryai.domain.value_objects import Category
from tests.builders import NOW, a_model_version
from tests.fakes import FakeUnitOfWork
from tests.use_case_factory import make_list_model_versions_use_case

pytestmark = pytest.mark.unit


class TestListModelVersions:
    async def test_only_versions_for_the_requested_category_are_returned(self) -> None:
        uow = FakeUnitOfWork()
        bottle = a_model_version(category=Category("bottle"))
        cable = a_model_version(category=Category("cable"))
        await uow.models.add(bottle)
        await uow.models.add(cable)
        use_case = make_list_model_versions_use_case(uow=uow)

        versions = await use_case.execute(Category("bottle"))

        assert [v.id for v in versions] == [bottle.id]

    async def test_versions_are_returned_newest_first(self) -> None:
        uow = FakeUnitOfWork()
        older = a_model_version(category=Category("bottle"), created_at=NOW)
        newer = a_model_version(category=Category("bottle"), created_at=NOW.replace(hour=13))
        await uow.models.add(older)
        await uow.models.add(newer)
        use_case = make_list_model_versions_use_case(uow=uow)

        versions = await use_case.execute(Category("bottle"))

        assert [v.id for v in versions] == [newer.id, older.id]

    async def test_a_category_with_no_versions_returns_an_empty_list(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_list_model_versions_use_case(uow=uow)

        versions = await use_case.execute(Category("bottle"))

        assert versions == []
