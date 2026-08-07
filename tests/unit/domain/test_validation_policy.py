"""Unit tests for the ingestion validation chain."""

from __future__ import annotations

import pytest

from factoryai.domain.policies.validation import (
    AllowedColorModesRule,
    AllowedFormatRule,
    MaxFileSizeRule,
    ResolutionBoundsRule,
    ValidationChain,
)
from factoryai.domain.value_objects import DecodedImage, Resolution

pytestmark = pytest.mark.unit


def _image(
    width: int = 512, height: int = 512, image_format: str = "PNG", color_mode: str = "RGB"
) -> DecodedImage:
    return DecodedImage(
        resolution=Resolution(width, height), image_format=image_format, color_mode=color_mode
    )


class TestMaxFileSizeRule:
    def test_passes_within_the_limit(self) -> None:
        rule = MaxFileSizeRule(max_bytes=1000)
        assert rule.check(_image(), size_bytes=999) is None

    def test_passes_at_exactly_the_limit(self) -> None:
        rule = MaxFileSizeRule(max_bytes=1000)
        assert rule.check(_image(), size_bytes=1000) is None

    def test_fails_over_the_limit(self) -> None:
        rule = MaxFileSizeRule(max_bytes=1000)
        reason = rule.check(_image(), size_bytes=1001)
        assert reason is not None
        assert "1001" in reason
        assert "1000" in reason

    def test_name_is_stable(self) -> None:
        assert MaxFileSizeRule(1000).name == "max_file_size"


class TestAllowedFormatRule:
    def test_passes_an_allowed_format(self) -> None:
        rule = AllowedFormatRule(frozenset({"png", "jpeg"}))
        assert rule.check(_image(image_format="PNG"), size_bytes=1) is None

    def test_comparison_is_case_insensitive(self) -> None:
        rule = AllowedFormatRule(frozenset({"png"}))
        assert rule.check(_image(image_format="png"), size_bytes=1) is None

    def test_fails_a_disallowed_format(self) -> None:
        rule = AllowedFormatRule(frozenset({"png", "jpeg"}))
        reason = rule.check(_image(image_format="BMP"), size_bytes=1)
        assert reason is not None
        assert "BMP" in reason


class TestResolutionBoundsRule:
    def test_passes_within_bounds(self) -> None:
        rule = ResolutionBoundsRule(Resolution(256, 256), Resolution(4096, 4096))
        assert rule.check(_image(1024, 1024), size_bytes=1) is None

    def test_fails_below_the_minimum(self) -> None:
        rule = ResolutionBoundsRule(Resolution(256, 256), Resolution(4096, 4096))
        reason = rule.check(_image(128, 128), size_bytes=1)
        assert reason is not None
        assert "128x128" in reason

    def test_fails_above_the_maximum(self) -> None:
        rule = ResolutionBoundsRule(Resolution(256, 256), Resolution(4096, 4096))
        reason = rule.check(_image(8192, 8192), size_bytes=1)
        assert reason is not None

    def test_fails_when_only_one_dimension_is_out_of_bounds(self) -> None:
        """A 4000x100 image must not pass just because its pixel count is small."""
        rule = ResolutionBoundsRule(Resolution(256, 256), Resolution(4096, 4096))
        reason = rule.check(_image(4000, 100), size_bytes=1)
        assert reason is not None


class TestAllowedColorModesRule:
    def test_passes_an_allowed_mode(self) -> None:
        rule = AllowedColorModesRule(frozenset({"RGB", "L"}))
        assert rule.check(_image(color_mode="RGB"), size_bytes=1) is None

    def test_comparison_is_case_sensitive(self) -> None:
        """Pillow mode names are case-sensitive ('RGB' vs 'rgb' is not the same thing)."""
        rule = AllowedColorModesRule(frozenset({"RGB"}))
        reason = rule.check(_image(color_mode="rgb"), size_bytes=1)
        assert reason is not None

    def test_fails_a_disallowed_mode(self) -> None:
        rule = AllowedColorModesRule(frozenset({"RGB"}))
        reason = rule.check(_image(color_mode="CMYK"), size_bytes=1)
        assert reason is not None
        assert "CMYK" in reason


class TestValidationChain:
    def test_an_image_passing_every_rule_has_no_failures(self) -> None:
        chain = ValidationChain(
            rules=(
                MaxFileSizeRule(1_000_000),
                AllowedFormatRule(frozenset({"png"})),
            )
        )
        assert chain.run(_image(), size_bytes=100) == ()

    def test_every_rule_runs_even_after_one_fails(self) -> None:
        """This is a report, not a short-circuiting guard — every failure is collected."""
        chain = ValidationChain(
            rules=(
                AllowedFormatRule(frozenset({"jpeg"})),
                AllowedColorModesRule(frozenset({"L"})),
            )
        )
        failures = chain.run(_image(image_format="PNG", color_mode="RGB"), size_bytes=1)
        assert len(failures) == 2

    def test_failure_messages_are_prefixed_with_the_rule_name(self) -> None:
        chain = ValidationChain(rules=(AllowedFormatRule(frozenset({"jpeg"})),))
        (failure,) = chain.run(_image(image_format="PNG"), size_bytes=1)
        assert failure.startswith("allowed_format:")

    def test_an_empty_chain_always_passes(self) -> None:
        assert ValidationChain(rules=()).run(_image(), size_bytes=1) == ()
