"""The read-only use case behind ``GET /predictions``: prediction history for the dashboard."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factoryai.application.pagination import Page
from factoryai.domain.entities import Prediction
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.value_objects import ModelVersionId


@dataclass(frozen=True, slots=True)
class ListPredictionsCommand:
    """What page of which model version's prediction history to return.

    Attributes:
        model_version_id: Narrow to one model version; absent returns every model.
        limit: Page size.
        offset: Rows to skip before this page.
    """

    model_version_id: ModelVersionId | None = None
    limit: int = 50
    offset: int = 0


class ListPredictions:
    """Returns a page of served predictions, newest first."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Initialise with the only collaborator this use case needs."""
        self._uow_factory = uow_factory

    async def execute(self, command: ListPredictionsCommand) -> Page[Prediction]:
        """Return the requested page of prediction history."""
        async with self._uow_factory() as uow:
            items, total = await uow.predictions.list_recent(
                model_version_id=command.model_version_id,
                limit=command.limit,
                offset=command.offset,
            )
        return Page(items=items, total=total, limit=command.limit, offset=command.offset)
