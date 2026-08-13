"""The feedback use case: record an operator's judgement of a served prediction.

This is the platform's only source of production ground truth (see
``domain/entities/prediction.py``'s ``Feedback`` docstring) — the mechanism by which a
mistaken prediction eventually reaches the next dataset version (Phase 12). Reaching it is
this use case's second half, not a later phase's: every reviewed prediction — confirmed or
corrected — relabels its underlying image with the now-known ground truth and moves it
straight to :attr:`~factoryai.domain.value_objects.ProcessingStatus.VALID` (see
``domain/entities/image.py``'s transition table), which is what makes it eligible for
:class:`~factoryai.application.use_cases.create_dataset_version.CreateDatasetVersion` the
next time it runs — no separate "promote reviewed images" step exists or is needed.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass

from factoryai.domain.entities import AuditEvent, Feedback, InspectionImage
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.errors import IllegalStateTransitionError
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import Clock, IdGenerator
from factoryai.domain.value_objects import (
    AuditSequence,
    FeedbackId,
    FeedbackVerdict,
    ImageLabel,
    PredictionId,
    UserId,
)

_FEEDBACK_REVIEWED_FLAG = "feedback_reviewed"
"""Metadata key marking an image as carrying an operator-established ground truth.

Read by :class:`~factoryai.application.use_cases.create_dataset_version.
CreateDatasetVersion` to route it into the growing regression suite (Phase 12, ADR-0015) —
a metadata flag rather than a new column, since nothing else needs to query on it and a
speculative dedicated column would be schema nobody has tested (the same reasoning
``docs/CONTRIBUTING.md`` already applies to ``validation_results``).
"""


@dataclass(frozen=True, slots=True)
class SubmitFeedbackCommand:
    """One operator's judgement of one prediction.

    Attributes:
        prediction_id: The prediction being judged.
        user_id: The operator submitting it.
        verdict: Whether the prediction was right.
        corrected_label: The true label; required when ``verdict`` is
            :attr:`~factoryai.domain.value_objects.FeedbackVerdict.INCORRECT`.
        notes: Optional free text.
        region: Optional ``(x, y, width, height)`` bounding box of the real defect.
    """

    prediction_id: PredictionId
    user_id: UserId
    verdict: FeedbackVerdict
    corrected_label: ImageLabel | None = None
    notes: str = ""
    region: tuple[int, int, int, int] | None = None


@dataclass(frozen=True, slots=True)
class SubmitFeedbackResult:
    """The outcome of recording feedback.

    Attributes:
        feedback_id: The persisted feedback record's identifier.
    """

    feedback_id: FeedbackId


class SubmitFeedback:
    """Records an operator's correction or confirmation of a served prediction."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        """Initialise with every collaborator this use case needs."""
        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator

    async def execute(self, command: SubmitFeedbackCommand) -> SubmitFeedbackResult:
        """Record feedback on a prediction and fold its ground truth into the image.

        Raises:
            EntityNotFoundError: If the prediction does not exist.
            InvariantViolationError: If ``command`` is internally inconsistent (an
                incorrect verdict with no correction, or a correct verdict with one).
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            prediction = await uow.predictions.get(command.prediction_id)
            feedback = Feedback(
                id=FeedbackId(self._id_generator.new_id()),
                prediction_id=command.prediction_id,
                user_id=command.user_id,
                verdict=command.verdict,
                created_at=now,
                corrected_label=command.corrected_label,
                notes=command.notes,
                region=command.region,
            )
            await uow.predictions.add_feedback(feedback)

            ground_truth = feedback.ground_truth or prediction.implied_label
            image = await uow.images.get(prediction.image_id)
            image = self._fold_ground_truth_into_image(image, ground_truth)
            await uow.images.update(image)

            latest = await uow.audit.latest()
            event = AuditEvent(
                sequence=AuditSequence((latest.sequence + 1) if latest else 1),
                action="feedback.submitted",
                resource_type="feedback",
                resource_id=str(feedback.id),
                occurred_at=now,
                prev_hash=latest.row_hash() if latest else GENESIS_HASH,
                actor_id=command.user_id,
                payload={
                    "prediction_id": str(prediction.id),
                    "verdict": command.verdict.value,
                    "is_correction": feedback.is_correction,
                    "ground_truth": ground_truth.value,
                },
            )
            await uow.audit.append(event)
            await uow.commit()

        return SubmitFeedbackResult(feedback_id=feedback.id)

    @staticmethod
    def _fold_ground_truth_into_image(
        image: InspectionImage, ground_truth: ImageLabel
    ) -> InspectionImage:
        """Relabel an image with its now-known ground truth and mark it trainable.

        The status move is best-effort: a :attr:`~factoryai.domain.value_objects.
        ProcessingStatus.REJECTED` or ``ARCHIVED`` image (terminal states) stays out of the
        trainable set regardless of feedback — those statuses mean something already
        decided this image should never train a model, and an operator's correction of its
        *prediction* does not reopen that decision. The relabel itself still applies, so the
        corrected ground truth is on record either way.
        """
        with contextlib.suppress(IllegalStateTransitionError):
            image = image.mark_valid()
        return image.relabel(ground_truth).with_metadata(**{_FEEDBACK_REVIEWED_FLAG: True})
