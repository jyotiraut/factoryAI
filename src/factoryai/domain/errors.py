"""Business-rule violations raised by the domain layer.

These describe conditions the domain understands and rejects — a malformed checksum, a
duplicate image, a model promotion that fails its gate. They carry no HTTP status codes and
no SQL state; mapping them onto a transport is the presentation layer's job, done in one
place so the mapping is consistent.
"""

from __future__ import annotations

from typing import Any

from factoryai.shared.errors import FactoryAIError


class DomainError(FactoryAIError):
    """Base class for every business-rule violation."""

    default_code = "domain.error"


class InvariantViolationError(DomainError):
    """A value object or entity was constructed in an impossible state.

    Raised by constructors and state transitions. Reaching one usually means a bug rather
    than bad user input, since inputs are validated at the boundary before they reach here.
    """

    default_code = "domain.invariant_violation"


class ValidationFailedError(DomainError):
    """An inspection image failed one or more ingestion validation rules."""

    default_code = "image.validation_failed"

    def __init__(self, failures: list[str], *, details: dict[str, Any] | None = None) -> None:
        """Initialise with the list of rule names that rejected the image.

        Args:
            failures: Names of the validation rules that failed, in evaluation order.
            details: Additional structured context, such as the offending dimensions.
        """
        super().__init__(
            f"image rejected by {len(failures)} rule(s): {', '.join(failures)}",
            details={**(details or {}), "failures": failures},
        )
        self.failures = failures


class DuplicateImageError(DomainError):
    """An image with an identical or near-identical hash is already stored."""

    default_code = "image.duplicate"


class EntityNotFoundError(DomainError):
    """A referenced entity does not exist."""

    default_code = "entity.not_found"

    def __init__(self, entity_type: str, identifier: object) -> None:
        """Initialise from the entity type and the identifier that was looked up."""
        super().__init__(
            f"{entity_type} {identifier!r} was not found",
            details={"entity_type": entity_type, "identifier": str(identifier)},
        )


class IllegalStateTransitionError(DomainError):
    """A state machine was asked to move between two states that are not connected."""

    default_code = "domain.illegal_transition"

    def __init__(self, entity_type: str, current: object, requested: object) -> None:
        """Initialise from the entity type and the attempted transition."""
        super().__init__(
            f"{entity_type} cannot move from {current} to {requested}",
            details={
                "entity_type": entity_type,
                "current": str(current),
                "requested": str(requested),
            },
        )


class PromotionRejectedError(DomainError):
    """A candidate model failed the automated promotion gate."""

    default_code = "model.promotion_rejected"

    def __init__(self, reasons: list[str], *, details: dict[str, Any] | None = None) -> None:
        """Initialise with the gate criteria the candidate failed."""
        super().__init__(
            f"promotion rejected: {'; '.join(reasons)}",
            details={**(details or {}), "reasons": reasons},
        )
        self.reasons = reasons
