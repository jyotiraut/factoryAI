"""The drift-monitoring use case: has production behaviour moved since a model went live.

Reference and current are both drawn from the *same* source — persisted predictions
(``Prediction.score``) — rather than by re-scoring the training set through the detector.
This was the design Phase 2 already anticipated (see ``Prediction``'s own docstring: "the
distribution of all scores is the reference signal drift detection compares against") and
it avoids a second, CPU-bound inference pass over the training data on every drift check:
the reference window is simply this model's *earliest* predictions, and the current window
is its most recent ones. What moved between the two is exactly what an operator watching a
model over time would ask about.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from factoryai.domain.entities import DriftReport, DriftSignal
from factoryai.domain.errors import NoProductionModelError
from factoryai.domain.ports.monitoring import DistributionSample, DriftDetector
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import Clock, IdGenerator
from factoryai.domain.value_objects import Category, DriftReportId, ModelStage


@dataclass(frozen=True, slots=True)
class GenerateDriftReportCommand:
    """A request to check one category's current production model for drift.

    Attributes:
        category: Which category's production model to monitor.
        window_hours: How far back "current" reaches from now.
        reference_sample_size: How many of the model's earliest predictions form the
            reference distribution.
        min_samples: The current window must hold at least this many predictions for the
            result to be conclusive — see ``DriftReport.is_conclusive``.
        data_threshold: Threshold applied to the ``confidence`` signal.
        prediction_threshold: Threshold applied to the ``anomaly_score`` signal.
    """

    category: Category
    window_hours: int = 24
    reference_sample_size: int = 200
    min_samples: int = 200
    data_threshold: float = 0.15
    prediction_threshold: float = 0.10


class GenerateDriftReport:
    """Compares a production model's recent predictions against its earliest ones."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        drift_detector: DriftDetector,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        """Initialise with every collaborator this use case needs."""
        self._uow_factory = uow_factory
        self._drift_detector = drift_detector
        self._clock = clock
        self._id_generator = id_generator

    async def execute(self, command: GenerateDriftReportCommand) -> DriftReport:
        """Generate and persist a drift report for the category's production model.

        Raises:
            NoProductionModelError: If the category has no production model.
        """
        now = self._clock.now()
        window_start = now - timedelta(hours=command.window_hours)

        async with self._uow_factory() as uow:
            production = await uow.models.find_by_stage(command.category, ModelStage.PRODUCTION)
            if production is None:
                raise NoProductionModelError(
                    f"category {command.category.code!r} has no production model",
                    details={"category": command.category.code},
                )
            experiment = await uow.experiments.get(production.experiment_id)

            current = await uow.predictions.list_in_window(
                production.id, start=window_start, end=now
            )
            sample_count = len(current)

            signals: tuple[DriftSignal, ...] = ()
            if sample_count >= command.min_samples:
                reference = await uow.predictions.list_in_window(
                    production.id,
                    start=production.created_at,
                    end=now,
                    limit=command.reference_sample_size,
                )
                reference_scores = DistributionSample(
                    name="anomaly_score", values=tuple(p.score.value for p in reference)
                )
                reference_confidence = DistributionSample(
                    name="confidence", values=tuple(p.score.confidence for p in reference)
                )
                current_scores = DistributionSample(
                    name="anomaly_score", values=tuple(p.score.value for p in current)
                )
                current_confidence = DistributionSample(
                    name="confidence", values=tuple(p.score.confidence for p in current)
                )
                signals = tuple(
                    self._drift_detector.compare(
                        reference=[reference_scores, reference_confidence],
                        current=[current_scores, current_confidence],
                        thresholds={
                            "anomaly_score": command.prediction_threshold,
                            "confidence": command.data_threshold,
                            "default": command.prediction_threshold,
                        },
                    )
                )

            report = DriftReport(
                id=DriftReportId(self._id_generator.new_id()),
                model_version_id=production.id,
                reference_dataset_version_id=experiment.dataset_version_id,
                window_start=window_start,
                window_end=now,
                sample_count=sample_count,
                signals=signals,
                created_at=now,
                min_samples=command.min_samples,
            )
            await uow.drift_reports.add(report)
            await uow.commit()

        return report
