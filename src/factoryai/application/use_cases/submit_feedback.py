"""The feedback use case: record an operator's judgement of a served prediction.

This is the platform's only source of production ground truth (see
``domain/entities/prediction.py``'s ``Feedback`` docstring) — the mechanism by which a
mistaken prediction eventually reaches the next dataset version (Phase 12).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factoryai.domain.entities import AuditEvent, Feedback
from factoryai.domain.entities.audit import GENESIS_HASH
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
        """Record feedback on a prediction.

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
                },
            )
            await uow.audit.append(event)
            await uow.commit()

        return SubmitFeedbackResult(feedback_id=feedback.id)
