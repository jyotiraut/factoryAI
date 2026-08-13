"""Invariant and lifecycle tests for the domain entities."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from factoryai.domain.entities import DatasetMember, verify_chain
from factoryai.domain.errors import IllegalStateTransitionError, InvariantViolationError
from factoryai.domain.value_objects import (
    AnomalyScore,
    AuditSequence,
    Checksum,
    DatasetSplit,
    DeploymentAction,
    DriftSeverity,
    ExperimentStatus,
    FeedbackVerdict,
    ImageId,
    ImageLabel,
    ModelStage,
    ModelVersionId,
    ProcessingStatus,
    UserRole,
)
from tests.builders import (
    GIT_COMMIT,
    NOW,
    a_dataset,
    a_dataset_version,
    a_deployment,
    a_drift_report,
    a_drift_signal,
    a_model_version,
    a_prediction,
    a_user,
    an_audit_event,
    an_experiment,
    an_image,
    some_feedback,
    some_metrics,
)

pytestmark = pytest.mark.unit

NAIVE = datetime(2026, 8, 5, 12, 0)  # noqa: DTZ001 — deliberately naive, used to assert rejection


class TestInspectionImage:
    def test_starts_pending(self) -> None:
        assert an_image().status is ProcessingStatus.PENDING

    def test_rejects_non_positive_size(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            an_image(size_bytes=0)
        assert exc.value.code == "image.invalid_size"

    def test_rejects_a_naive_timestamp(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            an_image(uploaded_at=NAIVE)
        assert exc.value.code == "image.naive_timestamp"

    def test_valid_lifecycle_path(self) -> None:
        image = an_image().transition_to(ProcessingStatus.VALIDATING).mark_valid()
        assert image.status is ProcessingStatus.VALID
        assert image.is_trainable

    def test_pending_can_jump_to_valid_via_operator_feedback(self) -> None:
        """Phase 12: an operator's review is a stronger signal than the automated chain.

        A served prediction reviewed by an operator may go straight from PENDING to VALID.
        """
        image = an_image().mark_valid()
        assert image.status is ProcessingStatus.VALID

    def test_rejected_is_terminal(self) -> None:
        rejected = an_image().transition_to(ProcessingStatus.VALIDATING).mark_rejected()
        with pytest.raises(IllegalStateTransitionError):
            rejected.mark_valid()

    def test_archived_is_terminal(self) -> None:
        archived = an_image().transition_to(ProcessingStatus.VALIDATING).mark_valid().archive()
        with pytest.raises(IllegalStateTransitionError):
            archived.mark_valid()

    def test_quarantine_is_reversible(self) -> None:
        image = an_image().transition_to(ProcessingStatus.VALIDATING).mark_valid()
        restored = image.quarantine().mark_valid()
        assert restored.status is ProcessingStatus.VALID

    def test_transition_to_the_current_status_is_a_no_op(self) -> None:
        image = an_image()
        assert image.transition_to(ProcessingStatus.PENDING) is image

    def test_transitions_do_not_mutate_the_original(self) -> None:
        original = an_image()
        original.transition_to(ProcessingStatus.VALIDATING)
        assert original.status is ProcessingStatus.PENDING

    def test_relabel_returns_a_copy(self) -> None:
        image = an_image()
        relabelled = image.relabel(ImageLabel.DEFECT)
        assert relabelled.label is ImageLabel.DEFECT
        assert image.label is ImageLabel.UNLABELED

    def test_is_nominal_tracks_the_label(self) -> None:
        assert an_image(label=ImageLabel.GOOD).is_nominal
        assert not an_image(label=ImageLabel.DEFECT).is_nominal
        assert not an_image().is_nominal

    def test_metadata_merge_preserves_existing_keys(self) -> None:
        image = an_image(metadata={"line": "A", "camera": "cam-1"})
        merged = image.with_metadata(camera="cam-2", batch="B7")
        assert merged.metadata == {"line": "A", "camera": "cam-2", "batch": "B7"}
        assert image.metadata == {"line": "A", "camera": "cam-1"}

    def test_with_perceptual_hash(self) -> None:
        assert an_image().with_perceptual_hash("ff00ff00").perceptual_hash == "ff00ff00"


class TestDataset:
    def test_rejects_a_blank_name(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            a_dataset(name="   ")
        assert exc.value.code == "dataset.no_name"

    def test_rejects_a_naive_timestamp(self) -> None:
        with pytest.raises(InvariantViolationError):
            a_dataset(created_at=NAIVE)


class TestDatasetVersion:
    def test_counts_and_splits(self) -> None:
        version = a_dataset_version()
        assert version.image_count == 3
        assert version.split_counts() == {
            DatasetSplit.TRAIN: 1,
            DatasetSplit.VAL: 1,
            DatasetSplit.TEST: 1,
        }

    def test_image_ids_can_be_filtered_by_split(self) -> None:
        version = a_dataset_version()
        assert len(version.image_ids(DatasetSplit.TRAIN)) == 1
        assert len(version.image_ids()) == 3

    def test_rejects_an_empty_version(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            a_dataset_version(members=())
        assert exc.value.code == "dataset_version.empty"

    def test_rejects_a_duplicate_member(self) -> None:
        image_id = ImageId(uuid.uuid4())
        with pytest.raises(InvariantViolationError) as exc:
            a_dataset_version(
                members=(
                    DatasetMember(image_id, DatasetSplit.TRAIN),
                    DatasetMember(image_id, DatasetSplit.TEST),
                )
            )
        assert exc.value.code == "dataset_version.duplicate_member"

    def test_rejects_a_short_git_commit(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            a_dataset_version(git_commit="abc1234")
        assert exc.value.code == "dataset_version.bad_commit"

    def test_rejects_a_blank_tag(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            a_dataset_version(version_tag=" ")
        assert exc.value.code == "dataset_version.no_tag"

    def test_content_checksum_is_membership_dependent(self) -> None:
        version = a_dataset_version()
        checksums = {
            member.image_id: Checksum(f"{index:064x}")
            for index, member in enumerate(version.members)
        }
        first = version.content_checksum(checksums)
        assert first == version.content_checksum(checksums)

        altered = {**checksums}
        altered[version.members[0].image_id] = Checksum("f" * 64)
        assert version.content_checksum(altered) != first

    def test_content_checksum_requires_every_member(self) -> None:
        version = a_dataset_version()
        with pytest.raises(InvariantViolationError) as exc:
            version.content_checksum({})
        assert exc.value.code == "dataset_version.incomplete_checksums"


class TestEvaluationMetrics:
    @pytest.mark.parametrize("field_name", ["image_auroc", "precision", "recall", "f1"])
    def test_rejects_rates_outside_the_unit_interval(self, field_name: str) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            some_metrics(**{field_name: 1.5})
        assert exc.value.code == "metrics.out_of_range"

    def test_optional_metrics_may_be_absent(self) -> None:
        metrics = some_metrics(pixel_auroc=None, pro_score=None)
        assert metrics.pixel_auroc is None
        assert "pixel_auroc" not in metrics.to_dict()

    def test_rejects_a_negative_confusion_matrix(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            some_metrics(confusion_matrix=(-1, 0, 0, 0))
        assert exc.value.code == "metrics.invalid_confusion_matrix"

    def test_false_positive_rate(self) -> None:
        metrics = some_metrics(confusion_matrix=(80, 20, 0, 0))
        assert metrics.false_positive_rate == pytest.approx(0.2)

    def test_false_positive_rate_is_none_without_a_matrix(self) -> None:
        assert some_metrics(confusion_matrix=None).false_positive_rate is None


class TestExperiment:
    def test_completing_records_metrics_and_duration(self) -> None:
        experiment = an_experiment()
        finished = experiment.complete(some_metrics(), NOW + timedelta(minutes=5))
        assert finished.status is ExperimentStatus.COMPLETED
        assert finished.duration_seconds == pytest.approx(300)
        assert finished.is_promotable

    def test_duration_is_none_while_running(self) -> None:
        assert an_experiment().duration_seconds is None

    def test_failing_records_the_reason(self) -> None:
        failed = an_experiment().fail("CUDA out of memory", NOW)
        assert failed.status is ExperimentStatus.FAILED
        assert failed.failure_reason == "CUDA out of memory"
        assert not failed.is_promotable

    def test_aborting_records_the_reason(self) -> None:
        aborted = an_experiment().abort("cancelled by operator", NOW)
        assert aborted.status is ExperimentStatus.ABORTED

    def test_a_finished_run_cannot_finish_twice(self) -> None:
        finished = an_experiment().complete(some_metrics(), NOW)
        with pytest.raises(IllegalStateTransitionError):
            finished.fail("too late", NOW)

    def test_rejects_finishing_before_starting(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            an_experiment(
                status=ExperimentStatus.FAILED,
                finished_at=NOW - timedelta(seconds=1),
            )
        assert exc.value.code == "experiment.negative_duration"

    def test_completed_runs_must_carry_metrics(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            an_experiment(status=ExperimentStatus.COMPLETED, finished_at=NOW)
        assert exc.value.code == "experiment.missing_metrics"


class TestModelVersion:
    def test_starts_in_development_and_is_not_servable(self) -> None:
        model = a_model_version()
        assert model.stage is ModelStage.DEVELOPMENT
        assert not model.is_servable

    def test_development_cannot_jump_to_production(self) -> None:
        """A candidate must pass through staging, where the promotion gate runs."""
        with pytest.raises(IllegalStateTransitionError):
            a_model_version().transition_to(ModelStage.PRODUCTION)

    def test_promotion_path(self) -> None:
        model = a_model_version().transition_to(ModelStage.STAGING)
        promoted = model.transition_to(ModelStage.PRODUCTION)
        assert promoted.is_servable

    def test_archived_can_return_to_staging_for_rollback(self) -> None:
        archived = a_model_version().transition_to(ModelStage.ARCHIVED)
        assert archived.transition_to(ModelStage.STAGING).stage is ModelStage.STAGING

    def test_transition_to_the_current_stage_is_a_no_op(self) -> None:
        model = a_model_version()
        assert model.transition_to(ModelStage.DEVELOPMENT) is model

    def test_rejects_a_non_positive_registry_version(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            a_model_version(registry_version=0)
        assert exc.value.code == "model.invalid_version"

    def test_recalibrate_replaces_the_threshold(self) -> None:
        assert a_model_version().recalibrate(0.8).threshold == 0.8

    def test_recalibrate_rejects_a_non_finite_threshold(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            a_model_version().recalibrate(float("inf"))
        assert exc.value.code == "model.invalid_threshold"

    def test_reference_is_the_registry_coordinate(self) -> None:
        model = a_model_version(registry_name="factoryai-bottle", registry_version=7)
        assert model.reference == "factoryai-bottle/7"


class TestDeployment:
    def test_a_rollback_must_name_the_version_it_replaced(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            a_deployment(action=DeploymentAction.ROLLBACK)
        assert exc.value.code == "deployment.incomplete_rollback"

    def test_a_valid_rollback(self) -> None:
        rollback = a_deployment(
            action=DeploymentAction.ROLLBACK,
            previous_model_version_id=ModelVersionId(uuid.uuid4()),
        )
        assert rollback.changed_production

    def test_a_rejection_does_not_change_production(self) -> None:
        assert not a_deployment(action=DeploymentAction.REJECT).changed_production

    def test_automated_when_no_actor(self) -> None:
        assert a_deployment().is_automated

    def test_rejects_a_blank_environment(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            a_deployment(environment="")
        assert exc.value.code == "deployment.no_environment"


class TestPrediction:
    def test_verdict_and_confidence_delegate_to_the_score(self) -> None:
        prediction = a_prediction(score=AnomalyScore(value=0.9, threshold=0.5))
        assert prediction.is_anomalous
        assert prediction.implied_label is ImageLabel.DEFECT
        assert prediction.confidence > 0

    def test_nominal_prediction_implies_a_good_label(self) -> None:
        assert a_prediction().implied_label is ImageLabel.GOOD

    def test_rejects_negative_latency(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            a_prediction(inference_time_ms=-1)
        assert exc.value.code == "prediction.invalid_latency"

    def test_rejects_a_naive_timestamp(self) -> None:
        with pytest.raises(InvariantViolationError):
            a_prediction(predicted_at=NAIVE)


class TestFeedback:
    def test_a_correction_must_supply_the_true_label(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            some_feedback(verdict=FeedbackVerdict.INCORRECT)
        assert exc.value.code == "feedback.missing_correction"

    def test_a_confirmation_must_not_also_correct(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            some_feedback(verdict=FeedbackVerdict.CORRECT, corrected_label=ImageLabel.DEFECT)
        assert exc.value.code == "feedback.contradictory"

    def test_a_valid_correction_carries_ground_truth(self) -> None:
        feedback = some_feedback(
            verdict=FeedbackVerdict.INCORRECT, corrected_label=ImageLabel.DEFECT
        )
        assert feedback.is_correction
        assert feedback.ground_truth is ImageLabel.DEFECT

    def test_a_confirmation_establishes_no_ground_truth_on_its_own(self) -> None:
        assert some_feedback().ground_truth is None

    @pytest.mark.parametrize("region", [(0, 0, 0, 10), (0, 0, 10, 0), (0, 0, -5, 5)])
    def test_rejects_a_degenerate_region(self, region: tuple[int, int, int, int]) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            some_feedback(region=region)
        assert exc.value.code == "feedback.invalid_region"

    def test_accepts_a_valid_region(self) -> None:
        assert some_feedback(region=(10, 20, 30, 40)).region == (10, 20, 30, 40)


class TestDriftReport:
    def test_no_breach_means_no_drift(self) -> None:
        report = a_drift_report()
        assert not report.drift_detected
        assert report.severity is DriftSeverity.NONE

    def test_an_underpowered_window_is_inconclusive_not_healthy(self) -> None:
        """Reporting 'no drift' on too few samples would silently suppress retraining."""
        report = a_drift_report(
            sample_count=10,
            signals=(a_drift_signal(statistic=0.9),),
        )
        assert not report.is_conclusive
        assert not report.drift_detected
        assert report.severity is DriftSeverity.NONE

    def test_a_small_breach_is_low_severity(self) -> None:
        report = a_drift_report(signals=(a_drift_signal(statistic=0.11),))
        assert report.drift_detected
        assert report.severity is DriftSeverity.LOW
        assert not report.should_trigger_retraining

    def test_a_large_exceedance_is_high_severity(self) -> None:
        report = a_drift_report(signals=(a_drift_signal(statistic=0.25),))
        assert report.severity is DriftSeverity.HIGH
        assert report.should_trigger_retraining

    def test_two_agreeing_signals_reach_medium(self) -> None:
        report = a_drift_report(
            signals=(
                a_drift_signal(name="score", statistic=0.101),
                a_drift_signal(name="brightness", statistic=0.102),
            )
        )
        assert report.severity is DriftSeverity.MEDIUM

    def test_three_agreeing_signals_reach_high(self) -> None:
        report = a_drift_report(
            signals=tuple(
                a_drift_signal(name=f"signal-{index}", statistic=0.101) for index in range(3)
            )
        )
        assert report.severity is DriftSeverity.HIGH

    def test_rejects_an_inverted_window(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            a_drift_report(window_end=NOW - timedelta(hours=1))
        assert exc.value.code == "drift.inverted_window"

    def test_rejects_a_negative_sample_count(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            a_drift_report(sample_count=-1)
        assert exc.value.code == "drift.invalid_sample_count"

    def test_signal_exceedance(self) -> None:
        assert a_drift_signal(statistic=0.05).exceedance == 0.0
        assert a_drift_signal(statistic=0.20).exceedance == pytest.approx(1.0)


class TestAuditEvent:
    def test_row_hash_is_deterministic(self) -> None:
        event = an_audit_event(payload={"b": 2, "a": 1})
        reordered = an_audit_event(payload={"a": 1, "b": 2})
        assert event.row_hash() == reordered.row_hash()

    def test_row_hash_changes_when_the_payload_changes(self) -> None:
        assert an_audit_event().row_hash() != an_audit_event(payload={"x": 1}).row_hash()

    def test_a_valid_chain_verifies(self) -> None:
        first = an_audit_event()
        second = an_audit_event(
            sequence=AuditSequence(2), action="model.promoted", prev_hash=first.row_hash()
        )
        assert second.follows(first)
        assert verify_chain([first, second]) is None

    def test_a_tampered_record_breaks_the_chain(self) -> None:
        first = an_audit_event()
        second = an_audit_event(
            sequence=AuditSequence(2), action="model.promoted", prev_hash=first.row_hash()
        )
        tampered = an_audit_event(payload={"forged": True})
        assert verify_chain([tampered, second]) == 2

    def test_a_renumbered_record_breaks_the_chain(self) -> None:
        first = an_audit_event()
        skipped = an_audit_event(
            sequence=AuditSequence(3), action="model.promoted", prev_hash=first.row_hash()
        )
        assert verify_chain([first, skipped]) == 3

    def test_an_empty_chain_verifies(self) -> None:
        assert verify_chain([]) is None

    def test_verification_requires_the_genesis_record(self) -> None:
        fragment = an_audit_event(sequence=AuditSequence(5), prev_hash="f" * 64)
        with pytest.raises(InvariantViolationError) as exc:
            verify_chain([fragment])
        assert exc.value.code == "audit.partial_chain"

    def test_rejects_a_zero_sequence(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            an_audit_event(sequence=AuditSequence(0))
        assert exc.value.code == "audit.invalid_sequence"

    @pytest.mark.parametrize(
        ("field_name", "code"),
        [("action", "audit.no_action"), ("resource_type", "audit.no_resource_type")],
    )
    def test_rejects_blank_required_fields(self, field_name: str, code: str) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            an_audit_event(**{field_name: "  "})
        assert exc.value.code == code

    def test_rejects_a_malformed_prev_hash(self) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            an_audit_event(prev_hash="short")
        assert exc.value.code == "audit.malformed_prev_hash"


class TestUser:
    def test_role_hierarchy_is_honoured(self) -> None:
        engineer = a_user(role=UserRole.ML_ENGINEER)
        assert engineer.can(UserRole.OPERATOR)
        assert not engineer.can(UserRole.ADMINISTRATOR)

    def test_a_deactivated_user_can_do_nothing(self) -> None:
        """Deactivation must be a real revocation, not a cosmetic flag."""
        admin = a_user(role=UserRole.ADMINISTRATOR).deactivate()
        assert not admin.can(UserRole.VIEWER)

    def test_reactivation_restores_access(self) -> None:
        user = a_user().deactivate().reactivate()
        assert user.can(UserRole.OPERATOR)

    def test_assign_role_returns_a_copy(self) -> None:
        user = a_user()
        promoted = user.assign_role(UserRole.ADMINISTRATOR)
        assert promoted.role is UserRole.ADMINISTRATOR
        assert user.role is UserRole.OPERATOR

    @pytest.mark.parametrize(
        ("email", "code"),
        [
            ("  ", "user.no_email"),
            ("not-an-address", "user.invalid_email"),
            ("Operator@Factory.example", "user.email_not_normalised"),
        ],
    )
    def test_rejects_invalid_emails(self, email: str, code: str) -> None:
        with pytest.raises(InvariantViolationError) as exc:
            a_user(email=email)
        assert exc.value.code == code


def test_builders_use_a_fixed_reference_time() -> None:
    """Guards against a builder drifting to wall-clock time and making tests flaky."""
    assert NOW.tzinfo is UTC
    assert GIT_COMMIT != ""
