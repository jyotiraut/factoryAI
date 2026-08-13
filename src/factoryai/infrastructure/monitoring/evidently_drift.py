"""Evidently-backed drift detection (Phase 11, ADR-0014).

Uses Evidently's statistical-test registry directly (`evidently.legacy.calculations.
stattests`) rather than its newer `Report`/`Dataset` orchestration layer: the
:class:`~factoryai.domain.ports.monitoring.DriftDetector` port only needs one number and a
pass/fail per named distribution pair, and the registry is exactly that — a statistic and a
threshold, with no HTML report, no dashboard, and no opinion about what a "column" is.
"""

from __future__ import annotations

import pandas as pd
from evidently.legacy.calculations.stattests import get_stattest
from evidently.legacy.core import ColumnType

from factoryai.domain.entities import DriftSignal
from factoryai.domain.ports.monitoring import DistributionSample, DriftDetector

_DEFAULT_METHOD = "wasserstein"
"""Wasserstein distance: sensitive to a shift in the whole distribution's shape and
location, not just its mean — appropriate for anomaly scores and confidence values, which
are bounded and often skewed rather than normally distributed."""


class EvidentlyDriftDetector(DriftDetector):
    """Compares named distributions with Evidently's Wasserstein-distance stat test."""

    def compare(
        self,
        *,
        reference: list[DistributionSample],
        current: list[DistributionSample],
        thresholds: dict[str, float],
    ) -> list[DriftSignal]:
        """Measure drift between two sets of distributions.

        Raises:
            KeyError: If ``thresholds`` has no ``"default"`` entry and a comparable
                distribution has no name-specific threshold either.
        """
        current_by_name = {sample.name: sample for sample in current}
        signals = []
        for ref_sample in reference:
            cur_sample = current_by_name.get(ref_sample.name)
            if cur_sample is None:
                continue
            threshold = thresholds.get(ref_sample.name, thresholds["default"])
            signals.append(self._compare_one(ref_sample, cur_sample, threshold))
        return signals

    def _compare_one(
        self, reference: DistributionSample, current: DistributionSample, threshold: float
    ) -> DriftSignal:
        """Compute one named distribution pair's drift statistic."""
        ref_series = pd.Series(reference.values)
        cur_series = pd.Series(current.values)
        stattest = get_stattest(ref_series, cur_series, ColumnType.Numerical, _DEFAULT_METHOD)
        statistic, _ = stattest.func(ref_series, cur_series, ColumnType.Numerical, threshold)
        return DriftSignal(
            name=reference.name,
            statistic=float(statistic),
            threshold=threshold,
            method=_DEFAULT_METHOD,
        )
