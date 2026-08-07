"""Unit tests for the Pillow-backed image codec.

No Docker, no filesystem — every payload is built in memory with Pillow itself, so these
stay fast and belong in the unit suite despite exercising a real imaging library.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from factoryai.domain.errors import CorruptImageError
from factoryai.domain.value_objects import Resolution
from factoryai.infrastructure.imaging.pillow_codec import PillowImageCodec

pytestmark = pytest.mark.unit


def _png_bytes(
    width: int = 64, height: int = 64, color: tuple[int, int, int] = (120, 50, 200)
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg_bytes(width: int = 64, height: int = 64) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(10, 200, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _grayscale_png_bytes(width: int = 64, height: int = 64) -> bytes:
    buffer = io.BytesIO()
    Image.new("L", (width, height), color=128).save(buffer, format="PNG")
    return buffer.getvalue()


class TestDecode:
    def test_reports_resolution_format_and_mode_for_a_png(self) -> None:
        codec = PillowImageCodec()
        decoded = codec.decode(_png_bytes(200, 100))
        assert decoded.resolution == Resolution(200, 100)
        assert decoded.image_format == "PNG"
        assert decoded.color_mode == "RGB"

    def test_reports_jpeg_format(self) -> None:
        codec = PillowImageCodec()
        decoded = codec.decode(_jpeg_bytes())
        assert decoded.image_format == "JPEG"

    def test_reports_grayscale_mode(self) -> None:
        codec = PillowImageCodec()
        decoded = codec.decode(_grayscale_png_bytes())
        assert decoded.color_mode == "L"

    def test_raises_for_garbage_bytes(self) -> None:
        codec = PillowImageCodec()
        with pytest.raises(CorruptImageError):
            codec.decode(b"this is not an image, just text pretending to be one")

    def test_raises_for_empty_bytes(self) -> None:
        codec = PillowImageCodec()
        with pytest.raises(CorruptImageError):
            codec.decode(b"")

    def test_raises_for_a_truncated_file(self) -> None:
        """A well-formed header with the pixel data cut off must still be rejected."""
        codec = PillowImageCodec()
        payload = _png_bytes()
        with pytest.raises(CorruptImageError):
            codec.decode(payload[: len(payload) // 2])


class TestPerceptualHash:
    def test_returns_a_16_character_lowercase_hex_string(self) -> None:
        codec = PillowImageCodec()
        digest = codec.perceptual_hash(_png_bytes())
        assert len(digest) == 16
        assert digest == digest.lower()
        int(digest, 16)  # must parse as hex; raises ValueError otherwise

    def test_identical_images_hash_identically(self) -> None:
        codec = PillowImageCodec()
        payload = _png_bytes(color=(1, 2, 3))
        assert codec.perceptual_hash(payload) == codec.perceptual_hash(payload)

    def test_re_encoding_the_same_image_hashes_close_to_identically(self) -> None:
        """The whole point of a perceptual hash: format changes should barely move it."""
        codec = PillowImageCodec()
        as_png = _png_bytes(color=(80, 80, 80))
        buffer = io.BytesIO()
        Image.open(io.BytesIO(as_png)).convert("RGB").save(buffer, format="JPEG", quality=95)

        first = int(codec.perceptual_hash(as_png), 16)
        second = int(codec.perceptual_hash(buffer.getvalue()), 16)
        distance = (first ^ second).bit_count()
        assert distance <= 4

    def test_structurally_different_images_hash_far_apart(self) -> None:
        """Verify two structurally distinct images hash far apart.

        A solid colour has zero internal variance, so a naive pixel-average comparison
        would call it identical to *any* other flat colour — not what this test is about.
        A periodic checkerboard is not a safe "structurally different" fixture either: at
        certain periods it aliases into a near-flat image once ``phash`` downsamples to
        32x32, which is a real property of DCT-based hashing, not a bug (this is why a
        diagonal split — genuine low-frequency structure — is used instead).
        """
        codec = PillowImageCodec()

        diagonal_split = Image.new("RGB", (64, 64), color=(0, 0, 0))
        for x in range(64):
            for y in range(64):
                if x > y:
                    diagonal_split.putpixel((x, y), (255, 255, 255))
        diagonal_buffer = io.BytesIO()
        diagonal_split.save(diagonal_buffer, format="PNG")

        solid = int(codec.perceptual_hash(_png_bytes(color=(128, 128, 128))), 16)
        split = int(codec.perceptual_hash(diagonal_buffer.getvalue()), 16)
        distance = (solid ^ split).bit_count()
        assert distance >= 8

    def test_raises_for_garbage_bytes(self) -> None:
        codec = PillowImageCodec()
        with pytest.raises(CorruptImageError):
            codec.perceptual_hash(b"not an image")
