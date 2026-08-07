"""Structural metadata produced by decoding an image, independent of any codec library."""

from __future__ import annotations

from dataclasses import dataclass

from factoryai.domain.errors import InvariantViolationError
from factoryai.domain.value_objects.resolution import Resolution


@dataclass(frozen=True, slots=True)
class DecodedImage:
    """What a codec learns by actually opening an image, as plain data.

    This is the seam between "bytes that might not even be an image" and the domain's
    validation rules: a codec (infrastructure, Pillow today) turns raw bytes into a
    :class:`DecodedImage` or raises, and every rule downstream operates on this — never on
    a ``PIL.Image`` — which is what keeps :mod:`factoryai.domain.policies.validation` free
    of an imaging library import.

    Attributes:
        resolution: Pixel dimensions.
        image_format: Container format, e.g. ``"PNG"``, ``"JPEG"``. Upper-case by
            convention, matching Pillow's own ``Image.format``.
        color_mode: Pixel layout, e.g. ``"RGB"``, ``"L"``, ``"RGBA"``.
    """

    resolution: Resolution
    image_format: str
    color_mode: str

    def __post_init__(self) -> None:
        """Validate that format and colour mode were actually determined.

        Raises:
            InvariantViolationError: If either string is blank.
        """
        if not self.image_format.strip():
            raise InvariantViolationError(
                "image_format must not be blank", code="decoded_image.no_format"
            )
        if not self.color_mode.strip():
            raise InvariantViolationError(
                "color_mode must not be blank", code="decoded_image.no_color_mode"
            )
