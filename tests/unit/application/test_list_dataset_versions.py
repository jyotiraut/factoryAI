"""Unit tests for the read-only use case behind ``GET /datasets/versions``, against fakes."""

from __future__ import annotations

import pytest

from factoryai.application.use_cases.list_dataset_versions import ListDatasetVersionsCommand
from tests.builders import NOW, a_dataset_version
from tests.fakes import FakeUnitOfWork
from tests.use_case_factory import make_list_dataset_versions_use_case

pytestmark = pytest.mark.unit


class TestListDatasetVersions:
    async def test_versions_are_returned_newest_first_across_datasets(self) -> None:
        uow = FakeUnitOfWork()
        older = a_dataset_version(created_at=NOW)
        newer = a_dataset_version(created_at=NOW.replace(hour=13))
        await uow.datasets.add_version(older)
        await uow.datasets.add_version(newer)
        use_case = make_list_dataset_versions_use_case(uow=uow)

        page = await use_case.execute(ListDatasetVersionsCommand())

        assert [v.id for v in page.items] == [newer.id, older.id]
        assert page.total == 2

    async def test_pagination_math_reflects_limit_and_offset(self) -> None:
        uow = FakeUnitOfWork()
        for hour in range(5):
            await uow.datasets.add_version(a_dataset_version(created_at=NOW.replace(hour=hour)))
        use_case = make_list_dataset_versions_use_case(uow=uow)

        page = await use_case.execute(ListDatasetVersionsCommand(limit=2, offset=1))

        assert len(page.items) == 2
        assert page.total == 5
        assert page.limit == 2
        assert page.offset == 1

    async def test_no_versions_returns_an_empty_page(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_list_dataset_versions_use_case(uow=uow)

        page = await use_case.execute(ListDatasetVersionsCommand())

        assert page.items == []
        assert page.total == 0
