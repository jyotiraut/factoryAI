"""Unit tests for the read-only use case behind ``GET /training/runs``, against fakes."""

from __future__ import annotations

import pytest

from factoryai.application.use_cases.list_training_runs import ListTrainingRunsCommand
from tests.builders import NOW, an_experiment
from tests.fakes import FakeUnitOfWork
from tests.use_case_factory import make_list_training_runs_use_case

pytestmark = pytest.mark.unit


class TestListTrainingRuns:
    async def test_runs_are_returned_newest_first(self) -> None:
        uow = FakeUnitOfWork()
        older = an_experiment(started_at=NOW)
        newer = an_experiment(started_at=NOW.replace(hour=13))
        await uow.experiments.add(older)
        await uow.experiments.add(newer)
        use_case = make_list_training_runs_use_case(uow=uow)

        page = await use_case.execute(ListTrainingRunsCommand())

        assert [e.id for e in page.items] == [newer.id, older.id]
        assert page.total == 2

    async def test_pagination_math_reflects_limit_and_offset(self) -> None:
        uow = FakeUnitOfWork()
        for hour in range(5):
            await uow.experiments.add(an_experiment(started_at=NOW.replace(hour=hour)))
        use_case = make_list_training_runs_use_case(uow=uow)

        page = await use_case.execute(ListTrainingRunsCommand(limit=2, offset=1))

        assert len(page.items) == 2
        assert page.total == 5
        assert page.limit == 2
        assert page.offset == 1

    async def test_no_runs_returns_an_empty_page(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_list_training_runs_use_case(uow=uow)

        page = await use_case.execute(ListTrainingRunsCommand())

        assert page.items == []
        assert page.total == 0
