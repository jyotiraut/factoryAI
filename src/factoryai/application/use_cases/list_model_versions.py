"""The read-only use case behind ``GET /models/versions``.

Model lineage for the dashboard.
"""

from __future__ import annotations

from collections.abc import Callable

from factoryai.domain.entities import ModelVersion
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.value_objects import Category


class ListModelVersions:
    """Returns every registered model version for a category, newest first.

    Unpaginated, unlike the other dashboard list use cases: a category's registered
    versions are bounded by how many training runs it has ever had, not by unbounded
    production traffic — the same reasoning :meth:`~factoryai.domain.ports.repositories.
    ModelRepository.list_versions` already applies.
    """

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Initialise with the only collaborator this use case needs."""
        self._uow_factory = uow_factory

    async def execute(self, category: Category) -> list[ModelVersion]:
        """Return every registered version for ``category``, newest first."""
        async with self._uow_factory() as uow:
            return await uow.models.list_versions(category)
