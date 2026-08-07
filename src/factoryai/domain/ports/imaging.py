"""The image codec port.

Decoding and perceptual hashing both require an imaging library (Pillow today), so they
sit behind a port for the same reason storage and tracking do (ADR-0001). This is also
what keeps the domain's validation rules operating on plain data
(:class:`~factoryai.domain.value_objects.decoded_image.DecodedImage`) rather than on a
``PIL.Image`` instance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from factoryai.domain.value_objects import DecodedImage


class ImageCodec(ABC):
    """Decodes image bytes and computes a perceptual hash, independent of any library."""

    @abstractmethod
    def decode(self, payload: bytes) -> DecodedImage:
        """Decode raw bytes into structural metadata.

        Args:
            payload: The raw file bytes, exactly as uploaded.

        Returns:
            The decoded structure.

        Raises:
            CorruptImageError: If ``payload`` cannot be decoded as an image at all.
        """

    @abstractmethod
    def perceptual_hash(self, payload: bytes) -> str:
        """Compute a perceptual hash, as a lowercase hex string.

        Used for near-duplicate detection — a re-encoded or lightly recompressed copy of
        an already-stored image should hash close to the original even though its exact
        checksum differs.

        Raises:
            CorruptImageError: If ``payload`` cannot be decoded as an image at all.
        """
