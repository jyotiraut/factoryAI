"""The read-only use case behind ``GET /drift/reports``: drift history for the dashboard."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factoryai.application.pagination import Page
from factoryai.domain.entities import DriftReport
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.value_objects import ModelVersionId


@dataclass(frozen=True, slots=True)
class ListDriftReportsCommand:
    """What page of which model version's drift history to return.

    Attributes:
        model_version_id: Narrow to one model version; absent returns every model.
        limit: Page size.
        offset: Rows to skip before this page.
    """

    model_version_id: ModelVersionId | None = None
    limit: int = 50
    offset: int = 0


class ListDriftReports:
    """Returns a page of drift reports, newest first."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Initialise with the only collaborator this use case needs."""
        self._uow_factory = uow_factory

    async def execute(self, command: ListDriftReportsCommand) -> Page[DriftReport]:
        """Return the requested page of drift history."""
        async with self._uow_factory() as uow:
            items, total = await uow.drift_reports.list_recent(
                model_version_id=command.model_version_id,
                limit=command.limit,
                offset=command.offset,
            )
        return Page(items=items, total=total, limit=command.limit, offset=command.offset)
