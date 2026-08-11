"""The read-only use case behind ``GET /models``: what is currently serving, and on what."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factoryai.domain.entities import EvaluationMetrics
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.value_objects import Category, ModelStage, ModelVersionId


@dataclass(frozen=True, slots=True)
class ModelSummary:
    """One category's currently-serving model, or the absence of one.

    Attributes:
        category: The product class this concerns.
        model_version_id: The production model version; absent if nothing is promoted yet.
        registry_name: Registry coordinate; absent alongside ``model_version_id``.
        registry_version: Registry coordinate; absent alongside ``model_version_id``.
        threshold: The calibrated decision boundary; absent alongside ``model_version_id``.
        metrics: Held-out evaluation results; absent alongside ``model_version_id``.
    """

    category: Category
    model_version_id: ModelVersionId | None
    registry_name: str | None
    registry_version: int | None
    threshold: float | None
    metrics: EvaluationMetrics | None


class ListProductionModels:
    """Reports the model currently serving each requested category, if any."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Initialise with the only collaborator this use case needs."""
        self._uow_factory = uow_factory

    async def execute(self, categories: list[Category]) -> list[ModelSummary]:
        """Return one summary per requested category, in the given order."""
        async with self._uow_factory() as uow:
            summaries = []
            for category in categories:
                model_version = await uow.models.find_by_stage(category, ModelStage.PRODUCTION)
                summaries.append(
                    ModelSummary(
                        category=category,
                        model_version_id=model_version.id if model_version else None,
                        registry_name=model_version.registry_name if model_version else None,
                        registry_version=(
                            model_version.registry_version if model_version else None
                        ),
                        threshold=model_version.threshold if model_version else None,
                        metrics=model_version.metrics if model_version else None,
                    )
                )
        return summaries
