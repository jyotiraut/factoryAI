"""Image resolution as a validated value object."""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from typing import Self

from factoryai.domain.errors import InvariantViolationError


@dataclass(frozen=True, slots=True, order=True)
class Resolution:
    """Pixel dimensions of an image.

    Ordering compares total pixel count, so resolutions can be sorted and compared against
    configured bounds directly. Attributes are declared width-first to match the
    ``WIDTHxHEIGHT`` convention used throughout the configuration files.

    Attributes:
        width: Horizontal size in pixels. Must be positive.
        height: Vertical size in pixels. Must be positive.
    """

    width: int
    height: int

    def __post_init__(self) -> None:
        """Validate that both dimensions are positive.

        Raises:
            InvariantViolationError: If either dimension is zero or negative.
        """
        if self.width <= 0 or self.height <= 0:
            raise InvariantViolationError(
                "resolution dimensions must be positive",
                code="resolution.invalid",
                details={"width": self.width, "height": self.height},
            )

    @classmethod
    def parse(cls, value: str) -> Self:
        """Parse a ``"WIDTHxHEIGHT"`` string.

        Args:
            value: A string such as ``"1024x768"``. Case-insensitive.

        Returns:
            The parsed resolution.

        Raises:
            InvariantViolationError: If the string is not two positive integers separated by
                ``x``.
        """
        parts = value.lower().split("x")
        expected_parts = 2
        if len(parts) != expected_parts:
            raise InvariantViolationError(
                f"expected 'WIDTHxHEIGHT', got {value!r}",
                code="resolution.malformed",
            )
        try:
            return cls(int(parts[0]), int(parts[1]))
        except ValueError as exc:
            raise InvariantViolationError(
                f"expected integer dimensions, got {value!r}",
                code="resolution.malformed",
            ) from exc

    @property
    def pixel_count(self) -> int:
        """Return the total number of pixels."""
        return self.width * self.height

    @property
    def megapixels(self) -> float:
        """Return the pixel count in megapixels."""
        return self.pixel_count / 1_000_000

    @property
    def aspect_ratio(self) -> float:
        """Return width divided by height."""
        return self.width / self.height

    @property
    def is_square(self) -> bool:
        """Return whether the image is square."""
        return self.width == self.height

    @property
    def simplified_ratio(self) -> tuple[int, int]:
        """Return the aspect ratio reduced to lowest terms, e.g. ``(4, 3)``."""
        divisor = gcd(self.width, self.height)
        return (self.width // divisor, self.height // divisor)

    def fits_within(self, bounds: Resolution) -> bool:
        """Return whether both dimensions are less than or equal to ``bounds``.

        This is a stricter test than comparing pixel counts: a 4000x100 image has fewer
        pixels than 1024x1024 but does not fit within it.
        """
        return self.width <= bounds.width and self.height <= bounds.height

    def is_within(self, minimum: Resolution, maximum: Resolution) -> bool:
        """Return whether this resolution lies inside an inclusive bounding window."""
        return minimum.fits_within(self) and self.fits_within(maximum)

    def __str__(self) -> str:
        """Return the ``WIDTHxHEIGHT`` representation."""
        return f"{self.width}x{self.height}"
