"""The read-only use case behind ``GET /models/deployments``.

Deployment history for the dashboard.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factoryai.domain.entities import Deployment
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.value_objects import Category


@dataclass(frozen=True, slots=True)
class ListDeploymentsCommand:
    """Which environment's deployment history to return, and how far back.

    Attributes:
        category: The product class to report on.
        environment: Which deployment target's history to read.
        limit: How many records to return, newest first.
    """

    category: Category
    environment: str = "production"
    limit: int = 50


class ListDeployments:
    """Returns a category's deployment history for one environment, newest first."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Initialise with the only collaborator this use case needs."""
        self._uow_factory = uow_factory

    async def execute(self, command: ListDeploymentsCommand) -> list[Deployment]:
        """Return the requested deployment history."""
        async with self._uow_factory() as uow:
            return await uow.models.list_deployments(
                command.category, environment=command.environment, limit=command.limit
            )
