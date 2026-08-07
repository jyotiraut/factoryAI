"""The ingestion validation chain.

Each rule is a small, pure, independently testable check against an already-decoded
image; the chain is just "run every rule, collect what failed". Adding a rule means
implementing :class:`ValidationRule` and adding an instance where the chain is composed
(the composition root, ``bootstrap/container.py``) — the
:class:`~factoryai.application.use_cases.ingest_image.IngestImage` use case that calls
:meth:`ValidationChain.run` never changes (see ``docs/CONTRIBUTING.md``, "A new
validation rule").

What is deliberately *not* here: decodability (a codec either returns a
:class:`DecodedImage` or raises — Phase 3 treats that as its own failure, not a rule
needing a decoded image to run against), and duplicate detection (checksum and
perceptual-hash lookups need the image repository, which is I/O the domain does not
perform — see the use case instead). EXIF sanity and aspect-ratio bounds are noted as a
deferred scope cut in ``docs/ROADMAP.md`` Phase 3.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from factoryai.domain.value_objects import DecodedImage, Resolution


class ValidationRule(ABC):
    """One independent, composable check against a decoded image."""

    @property
    @abstractmethod
    def name(self) -> str:
        """A short, stable identifier for this rule, used in failure messages."""

    @abstractmethod
    def check(self, image: DecodedImage, *, size_bytes: int) -> str | None:
        """Evaluate this rule.

        Args:
            image: The already-decoded image structure.
            size_bytes: Size of the original file, in bytes.

        Returns:
            A human-readable failure reason, or ``None`` if the image passes.
        """


@dataclass(frozen=True, slots=True)
class MaxFileSizeRule(ValidationRule):
    """Rejects files above a configured size, regardless of what they decode to."""

    max_bytes: int

    @property
    def name(self) -> str:
        """Return this rule's identifier."""
        return "max_file_size"

    def check(self, image: DecodedImage, *, size_bytes: int) -> str | None:
        """Reject files larger than :attr:`max_bytes`."""
        if size_bytes > self.max_bytes:
            return f"{size_bytes} bytes exceeds the {self.max_bytes} byte limit"
        return None


@dataclass(frozen=True, slots=True)
class AllowedFormatRule(ValidationRule):
    """Rejects container formats outside a configured allow-list.

    Attributes:
        allowed_formats: Lower-case format names, e.g. ``{"png", "jpeg"}``.
    """

    allowed_formats: frozenset[str]

    @property
    def name(self) -> str:
        """Return this rule's identifier."""
        return "allowed_format"

    def check(self, image: DecodedImage, *, size_bytes: int) -> str | None:
        """Reject a format not present in :attr:`allowed_formats`."""
        if image.image_format.lower() not in self.allowed_formats:
            allowed = ", ".join(sorted(self.allowed_formats))
            return f"format {image.image_format!r} is not one of: {allowed}"
        return None


@dataclass(frozen=True, slots=True)
class ResolutionBoundsRule(ValidationRule):
    """Rejects images outside a configured resolution window."""

    minimum: Resolution
    maximum: Resolution

    @property
    def name(self) -> str:
        """Return this rule's identifier."""
        return "resolution_bounds"

    def check(self, image: DecodedImage, *, size_bytes: int) -> str | None:
        """Reject a resolution outside ``[minimum, maximum]`` on either dimension."""
        if not image.resolution.is_within(self.minimum, self.maximum):
            return (
                f"resolution {image.resolution} is outside the allowed window "
                f"[{self.minimum}, {self.maximum}]"
            )
        return None


@dataclass(frozen=True, slots=True)
class AllowedColorModesRule(ValidationRule):
    """Rejects colour modes outside a configured allow-list.

    Attributes:
        allowed_modes: Pillow-style mode names, e.g. ``{"RGB", "L", "RGBA"}``.
    """

    allowed_modes: frozenset[str]

    @property
    def name(self) -> str:
        """Return this rule's identifier."""
        return "allowed_color_mode"

    def check(self, image: DecodedImage, *, size_bytes: int) -> str | None:
        """Reject a colour mode not present in :attr:`allowed_modes`."""
        if image.color_mode not in self.allowed_modes:
            allowed = ", ".join(sorted(self.allowed_modes))
            return f"colour mode {image.color_mode!r} is not one of: {allowed}"
        return None


@dataclass(frozen=True, slots=True)
class ValidationChain:
    """An ordered, composable set of rules, evaluated against one decoded image.

    Every rule runs — this is a validation *report*, not a short-circuiting guard clause,
    so a rejected image tells the caller everything wrong with it in one pass rather than
    the first thing.
    """

    rules: tuple[ValidationRule, ...]

    def run(self, image: DecodedImage, *, size_bytes: int) -> tuple[str, ...]:
        """Evaluate every rule and collect the failures.

        Args:
            image: The already-decoded image structure.
            size_bytes: Size of the original file, in bytes.

        Returns:
            A failure message per failing rule, prefixed with the rule's name. Empty if
            the image passed every rule.
        """
        failures = []
        for rule in self.rules:
            reason = rule.check(image, size_bytes=size_bytes)
            if reason is not None:
                failures.append(f"{rule.name}: {reason}")
        return tuple(failures)
