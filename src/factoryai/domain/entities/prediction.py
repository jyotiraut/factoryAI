"""Predictions served in production and the operator feedback that corrects them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from factoryai.domain.errors import InvariantViolationError
from factoryai.domain.value_objects import (
    AnomalyScore,
    DatasetVersionId,
    FeedbackId,
    FeedbackVerdict,
    ImageId,
    ImageLabel,
    ModelVersionId,
    PredictionId,
    StorageLocation,
    UserId,
)


@dataclass(frozen=True, slots=True)
class Prediction:
    """One inference result, retained permanently.

    Every prediction is persisted, not only the interesting ones: the distribution of
    *all* scores is the reference signal drift detection compares against (Phase 11), and
    sampling would bias it.

    Both the model version and the dataset version are recorded. Together they answer
    "what did the system believe, and on what evidence" months after the fact, which is
    the question an audit or a customer complaint actually asks.

    Attributes:
        id: Unique identifier.
        image_id: The image inspected.
        model_version_id: The model that served this prediction.
        dataset_version_id: The data that model was trained on.
        score: The anomaly score with the threshold it was judged against.
        inference_time_ms: Wall-clock inference duration in milliseconds.
        predicted_at: Timezone-aware timestamp.
        heatmap_location: Where the anomaly map is stored; absent for models without
            localisation, or after the heatmap retention window has elapsed.
        correlation_id: Ties this prediction to the request that produced it.
    """

    id: PredictionId
    image_id: ImageId
    model_version_id: ModelVersionId
    dataset_version_id: DatasetVersionId
    score: AnomalyScore
    inference_time_ms: float
    predicted_at: datetime
    heatmap_location: StorageLocation | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        """Validate the latency and timestamp.

        Raises:
            InvariantViolationError: If the inference time is negative or the timestamp is
                naive.
        """
        if self.inference_time_ms < 0:
            raise InvariantViolationError(
                "inference time must not be negative",
                code="prediction.invalid_latency",
                details={"inference_time_ms": self.inference_time_ms},
            )
        if self.predicted_at.tzinfo is None:
            raise InvariantViolationError(
                "predicted_at must be timezone-aware", code="prediction.naive_timestamp"
            )

    @property
    def is_anomalous(self) -> bool:
        """Return the verdict: whether the product was flagged as defective."""
        return self.score.is_anomalous

    @property
    def confidence(self) -> float:
        """Return decision certainty in ``[0, 1]``; see :class:`AnomalyScore`."""
        return self.score.confidence

    @property
    def implied_label(self) -> ImageLabel:
        """Return the label this prediction asserts, for comparison against ground truth."""
        return ImageLabel.DEFECT if self.is_anomalous else ImageLabel.GOOD


@dataclass(frozen=True, slots=True)
class Feedback:
    """An operator's judgement of a prediction.

    This is the platform's only source of production ground truth. When a prediction is
    marked incorrect, the corrected label is what the next dataset version records, so
    feedback is the mechanism by which the model learns from its own mistakes (Phase 12).

    Attributes:
        id: Unique identifier.
        prediction_id: The prediction being judged.
        user_id: The operator who submitted it.
        verdict: Whether the prediction was right.
        created_at: Timezone-aware timestamp.
        corrected_label: The true label, required when the verdict is incorrect.
        notes: Optional free text from the operator.
        region: Optional ``(x, y, width, height)`` bounding box of the real defect.
    """

    id: FeedbackId
    prediction_id: PredictionId
    user_id: UserId
    verdict: FeedbackVerdict
    created_at: datetime
    corrected_label: ImageLabel | None = None
    notes: str = ""
    region: tuple[int, int, int, int] | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate verdict consistency, region geometry and timestamp.

        Raises:
            InvariantViolationError: If an incorrect verdict carries no corrected label, a
                correct verdict contradicts itself with one, the region has non-positive
                dimensions, or the timestamp is naive.
        """
        if self.verdict is FeedbackVerdict.INCORRECT and self.corrected_label is None:
            raise InvariantViolationError(
                "feedback marking a prediction incorrect must supply the correct label",
                code="feedback.missing_correction",
            )
        if self.verdict is FeedbackVerdict.CORRECT and self.corrected_label is not None:
            raise InvariantViolationError(
                "feedback confirming a prediction must not also correct its label",
                code="feedback.contradictory",
            )
        if self.region is not None:
            _, _, width, height = self.region
            if width <= 0 or height <= 0:
                raise InvariantViolationError(
                    "feedback region must have positive width and height",
                    code="feedback.invalid_region",
                    details={"region": list(self.region)},
                )
        if self.created_at.tzinfo is None:
            raise InvariantViolationError(
                "created_at must be timezone-aware", code="feedback.naive_timestamp"
            )

    @property
    def is_correction(self) -> bool:
        """Return whether this feedback overturns the model's verdict."""
        return self.verdict is FeedbackVerdict.INCORRECT

    @property
    def ground_truth(self) -> ImageLabel | None:
        """Return the true label this feedback establishes, when it establishes one.

        A correction states the truth directly. A confirmation only states that the model
        was right, so the truth must be read from the prediction itself — which the caller
        has and this entity deliberately does not.
        """
        return self.corrected_label
