"""Image decoding and perceptual hashing via Pillow."""

from __future__ import annotations

import io

import imagehash
from PIL import Image, UnidentifiedImageError

from factoryai.domain.errors import CorruptImageError
from factoryai.domain.ports.imaging import ImageCodec
from factoryai.domain.value_objects import DecodedImage, Resolution

_DECODE_ERRORS = (
    UnidentifiedImageError,
    Image.DecompressionBombError,
    OSError,
    ValueError,
)
"""Exceptions Pillow raises for input that is not, or is unsafe to treat as, an image.

``OSError`` covers truncated files — the header is well-formed enough for ``Image.open``
to accept it, but the pixel data itself is incomplete, which only surfaces once something
forces a full read (:meth:`PIL.Image.Image.load`).
"""


class PillowImageCodec(ImageCodec):
    """Decodes images and computes a perceptual-hash fingerprint via Pillow.

    Uses ``imagehash.phash`` (a DCT-based perceptual hash), not the simpler
    ``average_hash``. This is a correction, not a preference: ``average_hash`` compares
    coarse (8x8) pixel brightness against the image mean, and on industrial inspection
    photos — dominated by a large, near-uniform background with a small object in a fixed
    position — that comparison has essentially no discriminative power. Verified against
    the real MVTec AD ``bottle`` set: ``average_hash`` produced the *exact same* 64-bit
    fingerprint for every one of 209 distinct training photos (Hamming distance 0), which
    would have flagged the entire dataset as duplicates of the first image ingested.
    ``phash`` operates in the frequency domain instead, and on the same images gave
    genuinely different photos a distance of 8-26 bits while still recognising a
    recompressed or resized copy of the same photo as a near-duplicate (distance ~2).
    """

    def decode(self, payload: bytes) -> DecodedImage:
        """Decode ``payload`` into structural metadata.

        Raises:
            CorruptImageError: If Pillow cannot decode ``payload`` as an image, or refuses
                to (e.g. a decompression-bomb-sized image).
        """
        image = self._open(payload)
        return DecodedImage(
            resolution=Resolution(image.width, image.height),
            image_format=image.format or "UNKNOWN",
            color_mode=image.mode,
        )

    def perceptual_hash(self, payload: bytes) -> str:
        """Compute a perceptual hash of ``payload``, as a lowercase hex string.

        Raises:
            CorruptImageError: If Pillow cannot decode ``payload`` as an image.
        """
        image = self._open(payload)
        return str(imagehash.phash(image))

    def _open(self, payload: bytes) -> Image.Image:
        """Open and fully decode ``payload``, raising a domain error on failure."""
        try:
            image = Image.open(io.BytesIO(payload))
            image.load()
        except _DECODE_ERRORS as exc:
            raise CorruptImageError(
                f"could not decode image: {exc}", details={"reason": str(exc)}
            ) from exc
        return image
