"""Anomaly score, threshold and the verdict derived from them."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Self

from factoryai.domain.errors import InvariantViolationError


@dataclass(frozen=True, slots=True)
class AnomalyScore:
    """A raw anomaly score paired with the threshold it is judged against.

    Anomaly detectors produce unbounded, model-specific scores: a PatchCore score of 3.2
    means nothing without the threshold that model was calibrated to. Carrying the two
    together makes the verdict self-explanatory and prevents the common production bug of
    comparing a score against the *wrong* model's threshold.

    Confidence is derived from the distance to the threshold rather than being an
    independent number, which keeps it honest — a score sitting on the threshold is
    reported as maximally uncertain regardless of the model that produced it.

    Attributes:
        value: The raw score. Higher means more anomalous. Must be finite.
        threshold: The decision boundary this score is judged against. Must be finite.
        scale: Score span used to normalise confidence, defaulting to the threshold
            magnitude. A larger scale makes confidence grow more slowly with distance.
    """

    value: float
    threshold: float
    scale: float | None = None

    def __post_init__(self) -> None:
        """Validate that the score, threshold and scale are usable numbers.

        Raises:
            InvariantViolationError: If any value is NaN or infinite, or if ``scale`` is not
                strictly positive.
        """
        for name, number in (("value", self.value), ("threshold", self.threshold)):
            if not math.isfinite(number):
                raise InvariantViolationError(
                    f"anomaly score {name} must be a finite number",
                    code="anomaly_score.not_finite",
                    details={name: str(number)},
                )
        if self.scale is not None and (not math.isfinite(self.scale) or self.scale <= 0):
            raise InvariantViolationError(
                "anomaly score scale must be a positive finite number",
                code="anomaly_score.invalid_scale",
                details={"scale": str(self.scale)},
            )

    @property
    def is_anomalous(self) -> bool:
        """Return whether the score meets or exceeds the threshold.

        The comparison is inclusive: a score exactly on the threshold is treated as a
        defect. In inspection, the cost of a missed defect exceeds the cost of a false
        alarm, so ties are resolved towards flagging.
        """
        return self.value >= self.threshold

    @property
    def margin(self) -> float:
        """Return the signed distance from the threshold.

        Positive means anomalous, negative means nominal, and the magnitude indicates how
        far from the boundary the sample sits.
        """
        return self.value - self.threshold

    @property
    def effective_scale(self) -> float:
        """Return the normalisation scale, falling back to the threshold magnitude."""
        if self.scale is not None:
            return self.scale
        return abs(self.threshold) if self.threshold != 0 else 1.0

    @property
    def confidence(self) -> float:
        """Return confidence in the verdict, in ``[0, 1]``.

        Zero means the score sits exactly on the threshold; the value approaches one as
        the score moves away from it in either direction. The mapping is a saturating
        ``|margin| / (|margin| + scale)`` curve, chosen because it is monotonic, bounded
        and needs no per-model calibration.

        This is a measure of *decision certainty*, not a calibrated probability, and is
        documented as such wherever it is surfaced to operators.
        """
        distance = abs(self.margin)
        return distance / (distance + self.effective_scale)

    def rescaled(self, threshold: float, *, scale: float | None = None) -> Self:
        """Return a copy judged against a different threshold.

        Used when a model is recalibrated: historical scores keep their raw value while
        their verdict is recomputed under the new boundary.
        """
        return type(self)(value=self.value, threshold=threshold, scale=scale)

    def __str__(self) -> str:
        """Return a compact human-readable summary."""
        verdict = "anomalous" if self.is_anomalous else "nominal"
        return f"{self.value:.4f} ({verdict}, threshold {self.threshold:.4f})"
