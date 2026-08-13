"""The read-only use case behind ``GET /datasets/versions``.

Dataset history for the dashboard.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factoryai.application.pagination import Page
from factoryai.domain.entities import DatasetVersion
from factoryai.domain.ports.repositories import UnitOfWork


@dataclass(frozen=True, slots=True)
class ListDatasetVersionsCommand:
    """What page of dataset-version history to return.

    Attributes:
        limit: Page size.
        offset: Rows to skip before this page.
    """

    limit: int = 50
    offset: int = 0


class ListDatasetVersions:
    """Returns a page of dataset versions across every dataset, newest first."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Initialise with the only collaborator this use case needs."""
        self._uow_factory = uow_factory

    async def execute(self, command: ListDatasetVersionsCommand) -> Page[DatasetVersion]:
        """Return the requested page of dataset-version history."""
        async with self._uow_factory() as uow:
            items, total = await uow.datasets.list_all_versions(
                limit=command.limit, offset=command.offset
            )
        return Page(items=items, total=total, limit=command.limit, offset=command.offset)
