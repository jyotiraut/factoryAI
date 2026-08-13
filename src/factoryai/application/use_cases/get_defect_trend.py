"""The read-only use case behind ``GET /analytics/defect-trend``: daily defect rate for the
dashboard.

Reuses :meth:`~factoryai.domain.ports.repositories.PredictionRepository.list_in_window`
(Phase 11's own reference-window query) rather than a new SQL aggregation — bucketing a
few thousand predictions by day in Python is cheap enough for a dashboard chart, and this
phase does not add a new query method just to move that grouping into the database. A
category with enough daily traffic to make the Python-side bucketing itself the bottleneck
is real, tracked future work, not a silently accepted risk: see this use case's own
``docs/adr`` entry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, timedelta

from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import Clock
from factoryai.domain.value_objects import Category, ModelStage

_MAX_SAMPLE = 5_000
"""Upper bound on predictions read per trend request — a deliberate scope cut (see module
docstring); a category this active needs a SQL-side aggregation, not a larger constant."""


@dataclass(frozen=True, slots=True)
class DefectTrendPoint:
    """One day's defect rate.

    Attributes:
        day: The calendar day this point summarises, in UTC.
        total: Predictions served that day.
        defective: How many of those were flagged anomalous.
    """

    day: date
    total: int
    defective: int

    @property
    def rate(self) -> float:
        """Return the fraction of predictions flagged anomalous, or 0.0 for a quiet day."""
        return self.defective / self.total if self.total else 0.0


class GetDefectTrend:
    """Buckets a category's production model's recent predictions into a daily defect rate."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        """Initialise with every collaborator this use case needs."""
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, category: Category, *, days: int = 30) -> list[DefectTrendPoint]:
        """Return one point per day over the trailing window, oldest first.

        An empty list means the category has no production model yet — not an error, since
        a category can legitimately be mid-onboarding.
        """
        now = self._clock.now()
        start = now - timedelta(days=days)
        async with self._uow_factory() as uow:
            model = await uow.models.find_by_stage(category, ModelStage.PRODUCTION)
            if model is None:
                return []
            predictions = await uow.predictions.list_in_window(
                model.id, start=start, end=now, limit=_MAX_SAMPLE
            )

        buckets: dict[date, list[int]] = {}
        for prediction in predictions:
            day = prediction.predicted_at.astimezone(UTC).date()
            total, defective = buckets.setdefault(day, [0, 0])
            buckets[day][0] = total + 1
            buckets[day][1] = defective + (1 if prediction.is_anomalous else 0)

        return [
            DefectTrendPoint(day=day, total=counts[0], defective=counts[1])
            for day, counts in sorted(buckets.items())
        ]
