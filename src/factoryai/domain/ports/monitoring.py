"""The drift detection port (Phase 11)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from factoryai.domain.entities import DriftSignal


@dataclass(frozen=True, slots=True)
class DistributionSample:
    """A distribution summarised for comparison.

    Raw feature vectors are not carried across this boundary: an embedding drift check over
    a day of production traffic would otherwise mean moving gigabytes into the domain. The
    adapter summarises first and passes the summary.

    Attributes:
        name: What the values describe, e.g. ``"anomaly_score"``.
        values: The observations, or a representative sample of them.
        metadata: Context such as the sampling rate or the window boundaries.
    """

    name: str
    values: tuple[float, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        """Return the number of observations."""
        return len(self.values)


class DriftDetector(ABC):
    """Compares a production window against the distribution a model was trained on."""

    @abstractmethod
    def compare(
        self,
        *,
        reference: list[DistributionSample],
        current: list[DistributionSample],
        thresholds: dict[str, float],
    ) -> list[DriftSignal]:
        """Measure drift between two sets of distributions.

        Args:
            reference: Distributions from the training data.
            current: Distributions from the production window.
            thresholds: Per-signal limits, keyed by sample name, with a ``"default"`` entry
                used for any signal without its own.

        Returns:
            One signal per comparable distribution pair. Names present on only one side are
            skipped rather than reported as infinite drift, since a missing signal is a
            configuration problem and not evidence about the data.
        """
