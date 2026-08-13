"""Unit tests for the read-only use case behind ``GET /drift/reports``, against fakes."""

from __future__ import annotations

import uuid

import pytest

from factoryai.application.use_cases.list_drift_reports import ListDriftReportsCommand
from factoryai.domain.value_objects import ModelVersionId
from tests.builders import NOW, a_drift_report
from tests.fakes import FakeUnitOfWork
from tests.use_case_factory import make_list_drift_reports_use_case

pytestmark = pytest.mark.unit


class TestListDriftReports:
    async def test_reports_are_returned_newest_first(self) -> None:
        uow = FakeUnitOfWork()
        older = a_drift_report(created_at=NOW)
        newer = a_drift_report(created_at=NOW.replace(hour=13))
        await uow.drift_reports.add(older)
        await uow.drift_reports.add(newer)
        use_case = make_list_drift_reports_use_case(uow=uow)

        page = await use_case.execute(ListDriftReportsCommand())

        assert [r.id for r in page.items] == [newer.id, older.id]
        assert page.total == 2

    async def test_pagination_math_reflects_limit_and_offset(self) -> None:
        uow = FakeUnitOfWork()
        for hour in range(5):
            await uow.drift_reports.add(a_drift_report(created_at=NOW.replace(hour=hour)))
        use_case = make_list_drift_reports_use_case(uow=uow)

        page = await use_case.execute(ListDriftReportsCommand(limit=2, offset=1))

        assert len(page.items) == 2
        assert page.total == 5
        assert page.limit == 2
        assert page.offset == 1

    async def test_narrowing_by_model_version_id_excludes_other_models(self) -> None:
        uow = FakeUnitOfWork()
        target = ModelVersionId(uuid.uuid4())
        matching = a_drift_report(model_version_id=target)
        other = a_drift_report()
        await uow.drift_reports.add(matching)
        await uow.drift_reports.add(other)
        use_case = make_list_drift_reports_use_case(uow=uow)

        page = await use_case.execute(ListDriftReportsCommand(model_version_id=target))

        assert [r.id for r in page.items] == [matching.id]
        assert page.total == 1

    async def test_no_reports_returns_an_empty_page(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_list_drift_reports_use_case(uow=uow)

        page = await use_case.execute(ListDriftReportsCommand())

        assert page.items == []
        assert page.total == 0
