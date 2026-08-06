"""Drift reports produced by monitoring the production prediction stream."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from factoryai.domain.errors import InvariantViolationError
from factoryai.domain.value_objects import (
    DatasetVersionId,
    DriftReportId,
    DriftSeverity,
    ModelVersionId,
)


@dataclass(frozen=True, slots=True)
class DriftSignal:
    """One measured drift statistic and whether it breached its threshold.

    Attributes:
        name: What was measured, e.g. ``"anomaly_score_distribution"``.
        statistic: The computed distance or divergence.
        threshold: The configured limit for this signal.
        method: The test used, e.g. ``"wasserstein"`` or ``"psi"``.
    """

    name: str
    statistic: float
    threshold: float
    method: str

    @property
    def breached(self) -> bool:
        """Return whether the statistic exceeds its threshold."""
        return self.statistic > self.threshold

    @property
    def exceedance(self) -> float:
        """Return how far past the threshold the statistic sits, relative to it.

        Zero when within limits. A value of ``0.5`` means the statistic is 50% above its
        threshold, which is what severity is graded on.
        """
        if not self.breached or self.threshold <= 0:
            return 0.0
        return (self.statistic - self.threshold) / self.threshold


@dataclass(frozen=True, slots=True)
class DriftReport:
    """The outcome of comparing a production window against the training reference.

    Severity is derived from the signals rather than stored independently, so a report
    cannot claim to be healthy while carrying breached signals.

    Attributes:
        id: Unique identifier.
        model_version_id: The model being monitored.
        reference_dataset_version_id: The training data used as the reference distribution.
        window_start: Timezone-aware start of the observation window.
        window_end: Timezone-aware end of the observation window.
        sample_count: Number of predictions in the window.
        signals: The individual measurements.
        created_at: Timezone-aware timestamp of the analysis.
        min_samples: Minimum window size for the result to be considered meaningful.
    """

    id: DriftReportId
    model_version_id: ModelVersionId
    reference_dataset_version_id: DatasetVersionId
    window_start: datetime
    window_end: datetime
    sample_count: int
    signals: tuple[DriftSignal, ...]
    created_at: datetime
    min_samples: int = 200

    def __post_init__(self) -> None:
        """Validate the window, sample count and timestamps.

        Raises:
            InvariantViolationError: If the window is inverted, a timestamp is naive, or the
                sample count is negative.
        """
        for name, moment in (
            ("window_start", self.window_start),
            ("window_end", self.window_end),
            ("created_at", self.created_at),
        ):
            if moment.tzinfo is None:
                raise InvariantViolationError(
                    f"{name} must be timezone-aware", code="drift.naive_timestamp"
                )
        if self.window_end < self.window_start:
            raise InvariantViolationError(
                "window_end must not precede window_start", code="drift.inverted_window"
            )
        if self.sample_count < 0:
            raise InvariantViolationError(
                "sample_count must not be negative", code="drift.invalid_sample_count"
            )

    @property
    def is_conclusive(self) -> bool:
        """Return whether the window held enough samples to trust the result.

        An underpowered window is reported as inconclusive rather than as "no drift" — the
        distinction matters, because the second would silently suppress retraining on a
        quiet shift.
        """
        return self.sample_count >= self.min_samples

    @property
    def breached_signals(self) -> tuple[DriftSignal, ...]:
        """Return only the signals that exceeded their thresholds."""
        return tuple(signal for signal in self.signals if signal.breached)

    @property
    def severity(self) -> DriftSeverity:
        """Return severity derived from how many signals broke and by how much.

        Grading: nothing breached, or an inconclusive window, is
        :attr:`~DriftSeverity.NONE`. Otherwise severity rises with the worst exceedance and
        with the number of independent signals agreeing that something changed — two
        signals breaching together is stronger evidence than one breaching twice as hard.
        """
        if not self.is_conclusive:
            return DriftSeverity.NONE
        breached = self.breached_signals
        if not breached:
            return DriftSeverity.NONE

        worst = max(signal.exceedance for signal in breached)
        high_exceedance = 1.0
        medium_exceedance = 0.25
        multi_signal = 2

        if worst >= high_exceedance or len(breached) >= multi_signal + 1:
            return DriftSeverity.HIGH
        if worst >= medium_exceedance or len(breached) >= multi_signal:
            return DriftSeverity.MEDIUM
        return DriftSeverity.LOW

    @property
    def drift_detected(self) -> bool:
        """Return whether any signal breached its threshold in a conclusive window."""
        return self.is_conclusive and bool(self.breached_signals)

    @property
    def should_trigger_retraining(self) -> bool:
        """Return whether this report warrants launching the retraining workflow."""
        return self.severity.requires_retraining
