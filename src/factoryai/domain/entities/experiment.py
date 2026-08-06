"""Training runs, their metrics and the hardware that produced them."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Self

from factoryai.domain.errors import IllegalStateTransitionError, InvariantViolationError
from factoryai.domain.value_objects import (
    DatasetVersionId,
    ExperimentId,
    ExperimentStatus,
)


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    """Quality metrics for an anomaly detection model.

    Image-level AUROC answers "did we flag the right products"; pixel-level AUROC and the
    PRO score answer "did we point at the right part of the product". A model can score
    well on the first and poorly on the others, which is exactly the failure an operator
    notices first — so the promotion gate reads all of them.

    Attributes:
        image_auroc: Image-level area under the ROC curve.
        pixel_auroc: Pixel-level AUROC; absent for models without localisation.
        pro_score: Per-region overlap score; absent when ground-truth masks are missing.
        precision: Precision at the operating threshold.
        recall: Recall at the operating threshold.
        f1: F1 at the operating threshold.
        threshold: The operating threshold these metrics were computed at.
        confusion_matrix: ``(true_negative, false_positive, false_negative, true_positive)``.
    """

    image_auroc: float
    precision: float
    recall: float
    f1: float
    threshold: float
    pixel_auroc: float | None = None
    pro_score: float | None = None
    confusion_matrix: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        """Validate that every rate lies in ``[0, 1]``.

        Raises:
            InvariantViolationError: If any metric falls outside the unit interval, or a
                confusion matrix entry is negative.
        """
        rates = {
            "image_auroc": self.image_auroc,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "pixel_auroc": self.pixel_auroc,
            "pro_score": self.pro_score,
        }
        for name, value in rates.items():
            if value is not None and not 0.0 <= value <= 1.0:
                raise InvariantViolationError(
                    f"{name} must lie in [0, 1]",
                    code="metrics.out_of_range",
                    details={name: value},
                )
        if self.confusion_matrix and any(count < 0 for count in self.confusion_matrix):
            raise InvariantViolationError(
                "confusion matrix counts must not be negative",
                code="metrics.invalid_confusion_matrix",
            )

    @property
    def false_positive_rate(self) -> float | None:
        """Return the false alarm rate, or ``None`` without a confusion matrix.

        This is the number a plant manager cares about: how often the line stops for a
        product that was fine.
        """
        true_negative, false_positive, _, _ = self.confusion_matrix or (0, 0, 0, 0)
        denominator = true_negative + false_positive
        return false_positive / denominator if denominator else None

    def to_dict(self) -> dict[str, Any]:
        """Return a serialisable mapping for MLflow and the metrics column."""
        return {key: value for key, value in dataclasses.asdict(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class HardwareInfo:
    """The machine an experiment ran on.

    Recorded because inference-time metrics are meaningless without it, and because a
    result that only reproduces on one machine needs to be identifiable as such.

    Attributes:
        cpu_model: Processor description.
        cpu_count: Logical core count.
        memory_gb: Total system memory in gibibytes.
        gpu_model: GPU description, absent on CPU-only runs.
        gpu_memory_gb: GPU memory in gibibytes, when applicable.
        driver_version: GPU driver or CUDA version, when applicable.
    """

    cpu_model: str
    cpu_count: int
    memory_gb: float
    gpu_model: str | None = None
    gpu_memory_gb: float | None = None
    driver_version: str | None = None

    @property
    def has_gpu(self) -> bool:
        """Return whether the run had a GPU available."""
        return self.gpu_model is not None


@dataclass(frozen=True, slots=True)
class Experiment:
    """One training run, from launch to terminal state.

    The four lineage fields — dataset version, Git commit, config hash and hyperparameters
    — are what make a run replayable. An experiment that cannot name all four is not
    reproducible, so they are required at construction rather than filled in afterwards.

    Attributes:
        id: Unique identifier.
        mlflow_run_id: The corresponding MLflow run.
        dataset_version_id: The exact data used.
        model_family: Registered detector name, e.g. ``"patchcore"``.
        backbone: Feature extractor, e.g. ``"wide_resnet50_2"``.
        hyperparameters: Full model configuration.
        config_hash: Hash of the effective settings at launch.
        git_commit: Commit SHA of the code that ran.
        started_at: Timezone-aware launch timestamp.
        status: Current run state.
        finished_at: Timezone-aware completion timestamp; set on terminal states.
        metrics: Evaluation results; present once completed.
        hardware: The machine the run executed on.
        failure_reason: Why the run failed or was aborted.
    """

    id: ExperimentId
    mlflow_run_id: str
    dataset_version_id: DatasetVersionId
    model_family: str
    backbone: str
    hyperparameters: dict[str, Any]
    config_hash: str
    git_commit: str
    started_at: datetime
    status: ExperimentStatus = ExperimentStatus.RUNNING
    finished_at: datetime | None = None
    metrics: EvaluationMetrics | None = None
    hardware: HardwareInfo | None = None
    failure_reason: str | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate timestamps and terminal-state consistency.

        Raises:
            InvariantViolationError: If a timestamp is naive, the run finished before it
                started, or a completed run carries no metrics.
        """
        if self.started_at.tzinfo is None:
            raise InvariantViolationError(
                "started_at must be timezone-aware", code="experiment.naive_timestamp"
            )
        if self.finished_at is not None:
            if self.finished_at.tzinfo is None:
                raise InvariantViolationError(
                    "finished_at must be timezone-aware", code="experiment.naive_timestamp"
                )
            if self.finished_at < self.started_at:
                raise InvariantViolationError(
                    "finished_at must not precede started_at",
                    code="experiment.negative_duration",
                )
        if self.status is ExperimentStatus.COMPLETED and self.metrics is None:
            raise InvariantViolationError(
                "a completed experiment must carry evaluation metrics",
                code="experiment.missing_metrics",
            )

    def complete(self, metrics: EvaluationMetrics, finished_at: datetime) -> Self:
        """Return a completed copy carrying its evaluation results.

        Raises:
            IllegalStateTransitionError: If the run has already finished.
        """
        self._require_running(ExperimentStatus.COMPLETED)
        return dataclasses.replace(
            self,
            status=ExperimentStatus.COMPLETED,
            metrics=metrics,
            finished_at=finished_at,
        )

    def fail(self, reason: str, finished_at: datetime) -> Self:
        """Return a failed copy carrying the reason.

        Raises:
            IllegalStateTransitionError: If the run has already finished.
        """
        self._require_running(ExperimentStatus.FAILED)
        return dataclasses.replace(
            self,
            status=ExperimentStatus.FAILED,
            failure_reason=reason,
            finished_at=finished_at,
        )

    def abort(self, reason: str, finished_at: datetime) -> Self:
        """Return an aborted copy, for runs cancelled by a human or a timeout.

        Raises:
            IllegalStateTransitionError: If the run has already finished.
        """
        self._require_running(ExperimentStatus.ABORTED)
        return dataclasses.replace(
            self,
            status=ExperimentStatus.ABORTED,
            failure_reason=reason,
            finished_at=finished_at,
        )

    def _require_running(self, requested: ExperimentStatus) -> None:
        """Raise unless the run is still in progress."""
        if self.status.is_finished:
            raise IllegalStateTransitionError("Experiment", self.status, requested)

    @property
    def duration_seconds(self) -> float | None:
        """Return the wall-clock training time, or ``None`` while still running."""
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def is_promotable(self) -> bool:
        """Return whether this run produced a model eligible for the promotion gate."""
        return self.status is ExperimentStatus.COMPLETED and self.metrics is not None
