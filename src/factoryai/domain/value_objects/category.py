"""Inspection category — the product class being examined."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from factoryai.domain.errors import InvariantViolationError

MVTEC_CATEGORIES: frozenset[str] = frozenset(
    {
        "bottle",
        "cable",
        "capsule",
        "carpet",
        "grid",
        "hazelnut",
        "leather",
        "metal_nut",
        "pill",
        "screw",
        "tile",
        "toothbrush",
        "transistor",
        "wood",
        "zipper",
    }
)
"""The fifteen MVTec AD classes.

The domain knows *which* categories exist; whether one is enabled, and with what image
size and backbone, is configuration (``configs/categories.yaml``). Keeping the two apart
means the domain does not read files, and enabling a category does not touch code.
"""


@dataclass(frozen=True, slots=True, order=True)
class Category:
    """A validated inspection category code.

    Attributes:
        code: A known MVTec AD class code, e.g. ``"bottle"``.
    """

    code: str

    def __post_init__(self) -> None:
        """Validate the category against the known set.

        Raises:
            InvariantViolationError: If the code is not a recognised MVTec AD class.
        """
        if self.code not in MVTEC_CATEGORIES:
            raise InvariantViolationError(
                f"unknown inspection category {self.code!r}",
                code="category.unknown",
                details={"category": self.code, "known": sorted(MVTEC_CATEGORIES)},
            )

    @classmethod
    def parse(cls, value: str) -> Self:
        """Parse a category code, tolerating surrounding whitespace and casing.

        Args:
            value: The raw category code, e.g. ``" Metal_Nut "``.

        Returns:
            The normalised category.

        Raises:
            InvariantViolationError: If the normalised code is not a known class.
        """
        return cls(value.strip().lower())

    @property
    def display_name(self) -> str:
        """Return a human-readable name, e.g. ``"Metal Nut"``."""
        return self.code.replace("_", " ").title()

    def __str__(self) -> str:
        """Return the category code."""
        return self.code
