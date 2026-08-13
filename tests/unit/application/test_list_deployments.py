"""Unit tests for the read-only use case behind ``GET /models/deployments``, against fakes."""

from __future__ import annotations

import pytest

from factoryai.application.use_cases.list_deployments import ListDeploymentsCommand
from factoryai.domain.value_objects import Category
from tests.builders import NOW, a_deployment, a_model_version
from tests.fakes import FakeUnitOfWork
from tests.use_case_factory import make_list_deployments_use_case

pytestmark = pytest.mark.unit


class TestListDeployments:
    async def test_deployments_for_the_category_are_returned_newest_first(self) -> None:
        uow = FakeUnitOfWork()
        model = a_model_version(category=Category("bottle"))
        await uow.models.add(model)
        older = a_deployment(model_version_id=model.id, environment="production", deployed_at=NOW)
        newer = a_deployment(
            model_version_id=model.id,
            environment="production",
            deployed_at=NOW.replace(hour=13),
        )
        await uow.models.add_deployment(older)
        await uow.models.add_deployment(newer)
        use_case = make_list_deployments_use_case(uow=uow)

        deployments = await use_case.execute(ListDeploymentsCommand(category=Category("bottle")))

        assert [d.id for d in deployments] == [newer.id, older.id]

    async def test_a_different_environment_is_excluded(self) -> None:
        uow = FakeUnitOfWork()
        model = a_model_version(category=Category("bottle"))
        await uow.models.add(model)
        staging_deployment = a_deployment(model_version_id=model.id, environment="staging")
        await uow.models.add_deployment(staging_deployment)
        use_case = make_list_deployments_use_case(uow=uow)

        deployments = await use_case.execute(
            ListDeploymentsCommand(category=Category("bottle"), environment="production")
        )

        assert deployments == []

    async def test_a_category_with_no_deployments_returns_an_empty_list(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_list_deployments_use_case(uow=uow)

        deployments = await use_case.execute(ListDeploymentsCommand(category=Category("bottle")))

        assert deployments == []
