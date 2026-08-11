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


class CorruptImageError(DomainError):
    """The bytes offered as an image could not be decoded as one.

    Raised by an :class:`~factoryai.domain.ports.imaging.ImageCodec` adapter, never by the
    domain itself — decoding requires an imaging library the domain does not depend on.
    """

    default_code = "image.corrupt"


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


class DatasetVersionTagExistsError(DomainError):
    """A dataset version with this tag already exists within its dataset."""

    default_code = "dataset_version.tag_exists"


class EmptyDatasetVersionError(DomainError):
    """No trainable images exist for the requested category.

    A dataset version cannot be empty (:class:`~factoryai.domain.entities.dataset.
    DatasetVersion` enforces at least one member), so this is caught before that entity
    is even constructed.
    """

    default_code = "dataset_version.no_trainable_images"


class NoProductionModelError(DomainError):
    """A category has no model currently in the production stage.

    Raised by the inference path when a prediction is requested for a category nothing
    has ever been promoted for — a configuration gap, not a transient failure.
    """

    default_code = "inference.no_production_model"


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


class AuthenticationError(DomainError):
    """Login credentials were missing, unknown, or did not match.

    Deliberately not distinguished by "email unknown" vs "wrong password" — that
    distinction is what lets an attacker enumerate valid emails, so both collapse to one
    message and one code.
    """

    default_code = "auth.invalid_credentials"


class InactiveAccountError(DomainError):
    """The account exists but has been deactivated, so it may not authenticate."""

    default_code = "auth.account_inactive"


class TokenError(DomainError):
    """A bearer token was malformed, expired, or has been revoked."""

    default_code = "auth.invalid_token"


class AuthorizationError(DomainError):
    """An authenticated principal lacks the permission a requested action requires."""

    default_code = "auth.forbidden"

    def __init__(self, permission: str, *, details: dict[str, Any] | None = None) -> None:
        """Initialise with the permission that was required but not held."""
        super().__init__(
            f"permission {permission!r} is required",
            details={**(details or {}), "permission": permission},
        )
        self.permission = permission


class EmailAlreadyRegisteredError(DomainError):
    """A user registration named an email address already on file."""

    default_code = "auth.email_already_registered"
