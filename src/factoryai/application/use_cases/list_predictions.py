"""The read-only use case behind ``GET /predictions``: prediction history for the dashboard."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factoryai.application.pagination import Page
from factoryai.domain.entities import Prediction
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.value_objects import ModelVersionId, StorageLocation


@dataclass(frozen=True, slots=True)
class PredictionWithImage:
    """A prediction bundled with its source image's storage location.

    The dashboard needs a presignable URL for the actual image a prediction was made on,
    not just its opaque ``image_id`` — this is what let a reviewer see only numbers, never
    the picture (ADR-0016's own disclosed gap). Resolved here, inside the use case, rather
    than the API router reaching into :class:`~factoryai.domain.ports.repositories.
    ImageRepository` directly: routers stay a thin translation layer, only use cases talk
    to a :class:`~factoryai.domain.ports.repositories.UnitOfWork`. One extra
    ``ImageRepository.get`` per item (bounded by the endpoint's own ``limit<=200``), not a
    batch fetch — no batch-get method exists on the port, and a dashboard page is not a hot
    enough path to justify adding one for this alone.
    """

    prediction: Prediction
    image_location: StorageLocation


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

    async def execute(self, command: ListPredictionsCommand) -> Page[PredictionWithImage]:
        """Return the requested page of prediction history, newest first."""
        async with self._uow_factory() as uow:
            items, total = await uow.predictions.list_recent(
                model_version_id=command.model_version_id,
                limit=command.limit,
                offset=command.offset,
            )
            enriched = [
                PredictionWithImage(
                    prediction=item, image_location=(await uow.images.get(item.image_id)).location
                )
                for item in items
            ]
        return Page(items=enriched, total=total, limit=command.limit, offset=command.offset)
