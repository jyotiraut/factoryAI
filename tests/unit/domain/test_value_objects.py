"""Invariant tests for the domain value objects."""

from __future__ import annotations

import hashlib
import math

import pytest

from factoryai.domain.errors import InvariantViolationError
from factoryai.domain.value_objects import (
    AnomalyScore,
    Category,
    Checksum,
    DecodedImage,
    DriftSeverity,
    ModelStage,
    ProcessingStatus,
    Resolution,
    StorageLocation,
    UserRole,
)

pytestmark = pytest.mark.unit

VALID_DIGEST = "a" * 64


class TestChecksum:
    def test_accepts_a_well_formed_digest(self) -> None:
        assert Checksum(VALID_DIGEST).value == VALID_DIGEST

    @pytest.mark.parametrize(
        "bad",
        ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "  " + "a" * 62],
        ids=["empty", "too-short", "too-long", "uppercase", "non-hex", "whitespace"],
    )
    def test_rejects_malformed_digests(self, bad: str) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            Checksum(bad)
        assert exc.value.code == "checksum.malformed"

    def test_from_bytes_matches_hashlib(self) -> None:
        payload = b"inspection image bytes"
        assert Checksum.from_bytes(payload).value == hashlib.sha256(payload).hexdigest()

    def test_of_checksums_is_order_independent(self) -> None:
        first = Checksum("1" * 64)
        second = Checksum("2" * 64)
        assert Checksum.of_checksums([first, second]) == Checksum.of_checksums([second, first])

    def test_of_checksums_changes_when_a_member_changes(self) -> None:
        base = [Checksum("1" * 64), Checksum("2" * 64)]
        altered = [Checksum("1" * 64), Checksum("3" * 64)]
        assert Checksum.of_checksums(base) != Checksum.of_checksums(altered)

    def test_of_checksums_handles_an_empty_collection(self) -> None:
        assert Checksum.of_checksums([]).value == hashlib.sha256(b"").hexdigest()

    def test_short_and_prefix_slice_the_digest(self) -> None:
        checksum = Checksum(VALID_DIGEST)
        assert checksum.short == "a" * 12
        assert checksum.prefix == "aa"
        assert str(checksum) == VALID_DIGEST

    def test_is_immutable(self) -> None:
        checksum = Checksum(VALID_DIGEST)
        with pytest.raises(AttributeError):
            checksum.value = "b" * 64  # type: ignore[misc]


class TestResolution:
    def test_derived_properties(self) -> None:
        resolution = Resolution(1920, 1080)
        assert resolution.pixel_count == 2_073_600
        assert resolution.megapixels == pytest.approx(2.0736)
        assert resolution.aspect_ratio == pytest.approx(16 / 9)
        assert resolution.simplified_ratio == (16, 9)
        assert not resolution.is_square
        assert str(resolution) == "1920x1080"

    @pytest.mark.parametrize(("width", "height"), [(0, 10), (10, 0), (-1, 10), (10, -1)])
    def test_rejects_non_positive_dimensions(self, width: int, height: int) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            Resolution(width, height)
        assert exc.value.code == "resolution.invalid"

    def test_parse_accepts_mixed_case(self) -> None:
        assert Resolution.parse("1024X768") == Resolution(1024, 768)

    @pytest.mark.parametrize("bad", ["1024", "1024x768x3", "widexhigh", "", "1024x"])
    def test_parse_rejects_malformed_strings(self, bad: str) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            Resolution.parse(bad)
        assert exc.value.code == "resolution.malformed"

    def test_fits_within_compares_both_dimensions_not_area(self) -> None:
        wide = Resolution(4000, 100)
        square = Resolution(1024, 1024)
        assert wide.pixel_count < square.pixel_count
        assert not wide.fits_within(square)

    def test_is_within_bounds(self) -> None:
        minimum, maximum = Resolution(256, 256), Resolution(4096, 4096)
        assert Resolution(1024, 768).is_within(minimum, maximum)
        assert not Resolution(128, 128).is_within(minimum, maximum)
        assert not Resolution(8192, 8192).is_within(minimum, maximum)

    def test_ordering_uses_pixel_count(self) -> None:
        assert Resolution(100, 100) < Resolution(200, 200)


class TestAnomalyScore:
    def test_score_at_the_threshold_is_flagged(self) -> None:
        """Ties resolve towards flagging: a missed defect costs more than a false alarm."""
        assert AnomalyScore(value=0.5, threshold=0.5).is_anomalous

    def test_verdict_and_margin(self) -> None:
        nominal = AnomalyScore(value=0.2, threshold=0.5)
        assert not nominal.is_anomalous
        assert nominal.margin == pytest.approx(-0.3)

    def test_confidence_is_zero_on_the_boundary(self) -> None:
        assert AnomalyScore(value=0.5, threshold=0.5).confidence == 0.0

    def test_confidence_grows_with_distance_and_stays_bounded(self) -> None:
        near = AnomalyScore(value=0.6, threshold=0.5)
        far = AnomalyScore(value=5.0, threshold=0.5)
        assert near.confidence < far.confidence < 1.0

    def test_confidence_is_symmetric_around_the_threshold(self) -> None:
        above = AnomalyScore(value=0.8, threshold=0.5)
        below = AnomalyScore(value=0.2, threshold=0.5)
        assert above.confidence == pytest.approx(below.confidence)

    def test_explicit_scale_slows_confidence_growth(self) -> None:
        default = AnomalyScore(value=1.0, threshold=0.5)
        wide = AnomalyScore(value=1.0, threshold=0.5, scale=10.0)
        assert wide.confidence < default.confidence

    def test_zero_threshold_falls_back_to_unit_scale(self) -> None:
        score = AnomalyScore(value=1.0, threshold=0.0)
        assert score.effective_scale == 1.0
        assert score.confidence == pytest.approx(0.5)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_rejects_non_finite_values(self, bad: float) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            AnomalyScore(value=bad, threshold=0.5)
        assert exc.value.code == "anomaly_score.not_finite"

    @pytest.mark.parametrize("bad", [0.0, -1.0, math.nan])
    def test_rejects_invalid_scale(self, bad: float) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            AnomalyScore(value=0.5, threshold=0.5, scale=bad)
        assert exc.value.code == "anomaly_score.invalid_scale"

    def test_rescaled_keeps_the_raw_value_and_flips_the_verdict(self) -> None:
        original = AnomalyScore(value=0.6, threshold=0.5)
        assert original.is_anomalous
        recalibrated = original.rescaled(0.9)
        assert recalibrated.value == 0.6
        assert not recalibrated.is_anomalous

    def test_str_reports_the_verdict(self) -> None:
        assert "anomalous" in str(AnomalyScore(value=0.9, threshold=0.5))
        assert "nominal" in str(AnomalyScore(value=0.1, threshold=0.5))


class TestCategory:
    def test_accepts_a_known_class(self) -> None:
        assert Category("bottle").code == "bottle"

    def test_parse_normalises_case_and_whitespace(self) -> None:
        assert Category.parse("  Metal_Nut  ") == Category("metal_nut")

    @pytest.mark.parametrize("bad", ["banana", "", "Bottle", "bottles"])
    def test_rejects_unknown_classes(self, bad: str) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            Category(bad)
        assert exc.value.code == "category.unknown"

    def test_display_name_is_humanised(self) -> None:
        assert Category("metal_nut").display_name == "Metal Nut"


class TestStorageLocation:
    def test_uri_and_extension(self) -> None:
        location = StorageLocation("factoryai-raw", "bottle/2026/08/abc.png")
        assert location.uri == "s3://factoryai-raw/bottle/2026/08/abc.png"
        assert location.extension == "png"

    def test_extension_is_empty_without_a_dot(self) -> None:
        assert StorageLocation("bucket", "key-without-extension").extension == ""

    @pytest.mark.parametrize(
        ("bucket", "key", "code"),
        [
            ("", "key", "storage.no_bucket"),
            ("bucket", "", "storage.no_key"),
            ("bucket", "/absolute/key", "storage.unsafe_key"),
            ("bucket", "a/../../etc/passwd", "storage.unsafe_key"),
        ],
    )
    def test_rejects_unsafe_locations(self, bucket: str, key: str, code: str) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            StorageLocation(bucket, key)
        assert exc.value.code == code

    def test_allows_dots_inside_a_segment(self) -> None:
        assert StorageLocation("bucket", "v1..2/file.png").key == "v1..2/file.png"


class TestDecodedImage:
    def test_accepts_valid_structural_metadata(self) -> None:
        image = DecodedImage(resolution=Resolution(64, 64), image_format="PNG", color_mode="RGB")
        assert image.resolution == Resolution(64, 64)
        assert image.image_format == "PNG"
        assert image.color_mode == "RGB"

    @pytest.mark.parametrize("bad_format", ["", "   "])
    def test_rejects_a_blank_format(self, bad_format: str) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            DecodedImage(resolution=Resolution(64, 64), image_format=bad_format, color_mode="RGB")
        assert exc.value.code == "decoded_image.no_format"

    @pytest.mark.parametrize("bad_mode", ["", "   "])
    def test_rejects_a_blank_color_mode(self, bad_mode: str) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            DecodedImage(resolution=Resolution(64, 64), image_format="PNG", color_mode=bad_mode)
        assert exc.value.code == "decoded_image.no_color_mode"


class TestEnums:
    def test_terminal_statuses(self) -> None:
        assert ProcessingStatus.REJECTED.is_terminal
        assert ProcessingStatus.ARCHIVED.is_terminal
        assert not ProcessingStatus.VALID.is_terminal

    def test_only_valid_images_are_trainable(self) -> None:
        trainable = [status for status in ProcessingStatus if status.is_usable_for_training]
        assert trainable == [ProcessingStatus.VALID]

    def test_servable_stages(self) -> None:
        assert ModelStage.PRODUCTION.is_servable
        assert ModelStage.STAGING.is_servable
        assert not ModelStage.DEVELOPMENT.is_servable
        assert not ModelStage.ARCHIVED.is_servable

    def test_role_hierarchy(self) -> None:
        assert UserRole.ADMINISTRATOR.can_act_as(UserRole.VIEWER)
        assert UserRole.ML_ENGINEER.can_act_as(UserRole.OPERATOR)
        assert not UserRole.OPERATOR.can_act_as(UserRole.ML_ENGINEER)
        assert UserRole.VIEWER.can_act_as(UserRole.VIEWER)

    def test_every_role_has_a_rank(self) -> None:
        ranks = [role.rank for role in UserRole]
        assert sorted(ranks) == list(range(len(UserRole)))

    def test_drift_severity_triggers_retraining(self) -> None:
        assert DriftSeverity.HIGH.requires_retraining
        assert DriftSeverity.MEDIUM.requires_retraining
        assert not DriftSeverity.LOW.requires_retraining
        assert not DriftSeverity.NONE.requires_retraining

    def test_enum_values_are_the_persisted_strings(self) -> None:
        assert ProcessingStatus.VALID.value == "valid"
        assert ModelStage.PRODUCTION.value == "production"
