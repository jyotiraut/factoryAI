"""The read-only use case behind ``GET /training/runs``: training history for the
dashboard.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factoryai.application.pagination import Page
from factoryai.domain.entities import Experiment
from factoryai.domain.ports.repositories import UnitOfWork


@dataclass(frozen=True, slots=True)
class ListTrainingRunsCommand:
    """What page of training-run history to return.

    Attributes:
        limit: Page size.
        offset: Rows to skip before this page.
    """

    limit: int = 50
    offset: int = 0


class ListTrainingRuns:
    """Returns a page of training runs across every dataset version, newest first."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Initialise with the only collaborator this use case needs."""
        self._uow_factory = uow_factory

    async def execute(self, command: ListTrainingRunsCommand) -> Page[Experiment]:
        """Return the requested page of training-run history."""
        async with self._uow_factory() as uow:
            items, total = await uow.experiments.list_recent(
                limit=command.limit, offset=command.offset
            )
        return Page(items=items, total=total, limit=command.limit, offset=command.offset)
