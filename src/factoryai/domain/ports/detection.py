"""The anomaly detection port and its plugin registry.

This is the seam that keeps PatchCore replaceable (ADR-0002). Adding FastFlow means adding
one adapter and one config file; the training pipeline, the API and the registry are
untouched.

These methods are synchronous. Detector work is CPU- and GPU-bound rather than I/O-bound,
so ``async`` would buy nothing and would mislead callers into thinking the event loop stays
free. Callers that must not block dispatch this work to a thread or to a Celery worker
(ADR-0008).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from factoryai.domain.entities import EvaluationMetrics
from factoryai.domain.errors import DomainError
from factoryai.domain.value_objects import AnomalyScore


@dataclass(frozen=True, slots=True)
class RawPrediction:
    """A detector's output for one image, before it becomes a domain ``Prediction``.

    Attributes:
        score: The anomaly score and the threshold it was judged against.
        inference_time_ms: Wall-clock duration of the forward pass.
        anomaly_map: Encoded PNG heatmap, when the model localises defects.
    """

    score: AnomalyScore
    inference_time_ms: float
    anomaly_map: bytes | None = None


@dataclass(frozen=True, slots=True)
class TrainedModel:
    """The artifact and calibration produced by a fitting run.

    Attributes:
        artifact_path: Local path to the serialised model.
        threshold: The calibrated decision boundary.
        metrics: Held-out evaluation results.
        training_time_seconds: Wall-clock duration of the fit.
        extra: Model-family-specific detail, such as the PatchCore memory-bank size.
    """

    artifact_path: Path
    threshold: float
    metrics: EvaluationMetrics
    training_time_seconds: float
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TrainingRequest:
    """Everything a detector needs to fit a model.

    Paths are passed rather than image bytes: training reads tens of thousands of files and
    materialising them in memory would not survive a real dataset. The training pipeline
    stages a dataset version to local disk first, which is also what makes the run
    reproducible.

    Attributes:
        train_dir: Directory of nominal training images.
        test_dir: Directory of held-out evaluation images.
        image_size: Input resolution ``(width, height)``.
        seed: Random seed pinned for reproducibility.
        device: ``"auto"``, ``"cpu"`` or ``"cuda"``.
        hyperparameters: Model-family-specific configuration.
        ground_truth_dir: Segmentation masks, when available, for pixel-level metrics.
    """

    train_dir: Path
    test_dir: Path
    image_size: tuple[int, int]
    seed: int
    device: str = "auto"
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    ground_truth_dir: Path | None = None


class AnomalyDetector(ABC):
    """A pluggable anomaly detection model family."""

    @property
    @abstractmethod
    def family(self) -> str:
        """Return the registered family name, e.g. ``"patchcore"``."""

    @property
    @abstractmethod
    def backbone(self) -> str:
        """Return the feature extractor in use, e.g. ``"wide_resnet50_2"``."""

    @abstractmethod
    def fit(self, request: TrainingRequest) -> TrainedModel:
        """Fit a model and calibrate its decision threshold.

        Args:
            request: Data locations and hyperparameters for this run.

        Returns:
            The trained artifact with its threshold and held-out metrics.

        Raises:
            InfrastructureError: If training fails for an environmental reason, such as
                exhausted GPU memory.
        """

    @abstractmethod
    def load(self, artifact_path: Path, *, threshold: float) -> None:
        """Load a previously fitted artifact for serving.

        Args:
            artifact_path: Local path to the serialised model.
            threshold: The threshold this artifact was calibrated with. Passed explicitly
                so that serving a model with another version's threshold is impossible.
        """

    @abstractmethod
    def predict(self, image: bytes) -> RawPrediction:
        """Score a single image.

        Args:
            image: Encoded image bytes.

        Returns:
            The score, latency and anomaly map.

        Raises:
            DetectorNotLoadedError: If :meth:`load` or :meth:`fit` has not been called.
        """

    @abstractmethod
    def predict_batch(self, images: list[bytes]) -> list[RawPrediction]:
        """Score several images together.

        Batching amortises the forward pass, so this is not merely a loop over
        :meth:`predict` and adapters should implement it as a genuine batch.
        """


class DetectorNotLoadedError(DomainError):
    """A detector was asked to predict before a model was loaded into it."""

    default_code = "detector.not_loaded"


DetectorT = TypeVar("DetectorT", bound=type[AnomalyDetector])

_REGISTRY: dict[str, type[AnomalyDetector]] = {}


def register_detector(name: str) -> Callable[[DetectorT], DetectorT]:
    """Register a detector implementation under a configuration name.

    Args:
        name: The value ``model.name`` takes in a training config, e.g. ``"patchcore"``.

    Returns:
        A class decorator that registers and returns the class unchanged.

    Raises:
        ValueError: If the name is already registered, which would otherwise let one
            plugin silently shadow another depending on import order.

    Example:
        >>> @register_detector("example")  # doctest: +SKIP
        ... class ExampleDetector(AnomalyDetector): ...
    """

    def decorator(cls: DetectorT) -> DetectorT:
        if name in _REGISTRY:
            raise ValueError(f"detector {name!r} is already registered by {_REGISTRY[name]!r}")
        _REGISTRY[name] = cls
        return cls

    return decorator


def get_detector_class(name: str) -> type[AnomalyDetector]:
    """Return the detector class registered under ``name``.

    Raises:
        KeyError: If no detector is registered under that name, listing what is available.
    """
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        available = ", ".join(sorted(_REGISTRY)) or "none"
        raise KeyError(f"unknown detector {name!r}; registered: {available}") from exc


def available_detectors() -> tuple[str, ...]:
    """Return the names of every registered detector, sorted."""
    return tuple(sorted(_REGISTRY))
