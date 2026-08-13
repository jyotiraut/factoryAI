"""The read-only use case behind ``GET /predictions/feedback-queue``.

What an operator still owes a verdict on.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factoryai.application.pagination import Page
from factoryai.domain.entities import Prediction
from factoryai.domain.ports.repositories import UnitOfWork


@dataclass(frozen=True, slots=True)
class ListFeedbackQueueCommand:
    """What page of the feedback queue to return.

    Attributes:
        limit: Page size.
        offset: Rows to skip before this page.
    """

    limit: int = 50
    offset: int = 0


class ListFeedbackQueue:
    """Returns a page of predictions no operator has reviewed yet, newest first."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Initialise with the only collaborator this use case needs."""
        self._uow_factory = uow_factory

    async def execute(self, command: ListFeedbackQueueCommand) -> Page[Prediction]:
        """Return the requested page of the feedback queue."""
        async with self._uow_factory() as uow:
            items, total = await uow.predictions.list_needing_feedback(
                limit=command.limit, offset=command.offset
            )
        return Page(items=items, total=total, limit=command.limit, offset=command.offset)
