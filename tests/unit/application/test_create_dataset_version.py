"""Unit tests for the dataset-versioning use case, against fakes."""

from __future__ import annotations

import uuid

import pytest

from factoryai.application.use_cases.create_dataset_version import (
    CreateDatasetVersionCommand,
    SplitRatios,
)
from factoryai.domain.entities import InspectionImage
from factoryai.domain.errors import DatasetVersionTagExistsError, EmptyDatasetVersionError
from factoryai.domain.value_objects import (
    Category,
    Checksum,
    DatasetSplit,
    ImageId,
    ImageLabel,
    ProcessingStatus,
)
from tests.builders import NOW, an_image
from tests.fakes import FakeClock, FakeIdGenerator, FakeUnitOfWork, FakeVersionControl
from tests.use_case_factory import make_create_dataset_version_use_case

pytestmark = pytest.mark.unit

_CATEGORY = Category("bottle")


def _trainable_images(count: int, *, label: ImageLabel = ImageLabel.GOOD) -> list[InspectionImage]:
    """Build ``count`` distinct, valid, trainable images."""
    return [
        an_image(
            id=ImageId(uuid.uuid4()),
            checksum=Checksum(f"{index:064x}"),
            status=ProcessingStatus.VALID,
            label=label,
            uploaded_at=NOW,
        )
        for index in range(1, count + 1)
    ]


async def _seed_images(uow: FakeUnitOfWork, images: list[InspectionImage]) -> None:
    for image in images:
        await uow.images.add(image)


class TestVersionCreated:
    async def test_image_count_and_split_counts_cover_every_image(self) -> None:
        uow = FakeUnitOfWork()
        await _seed_images(uow, _trainable_images(10))
        use_case = make_create_dataset_version_use_case(
            uow=uow,
            version_control=FakeVersionControl(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        result = await use_case.execute(
            CreateDatasetVersionCommand(
                dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1"
            )
        )

        assert result.image_count == 10
        assert sum(result.split_counts.values()) == 10
        assert set(result.split_counts) == set(DatasetSplit)

    async def test_the_same_seed_assigns_every_image_to_the_same_split(self) -> None:
        """Same counts alone would pass by construction; per-image assignment is the claim."""
        images = _trainable_images(20)

        async def _run() -> dict[ImageId, DatasetSplit]:
            uow = FakeUnitOfWork()
            await _seed_images(uow, images)
            use_case = make_create_dataset_version_use_case(
                uow=uow,
                version_control=FakeVersionControl(),
                clock=FakeClock(NOW),
                id_generator=FakeIdGenerator(),
            )
            result = await use_case.execute(
                CreateDatasetVersionCommand(
                    dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1", seed=7
                )
            )
            version = await uow.datasets.get_version(result.version_id)
            return {member.image_id: member.split for member in version.members}

        first, second = await _run(), await _run()
        assert first == second

    async def test_a_dataset_is_created_on_first_use(self) -> None:
        uow = FakeUnitOfWork()
        await _seed_images(uow, _trainable_images(1))
        use_case = make_create_dataset_version_use_case(
            uow=uow,
            version_control=FakeVersionControl(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        result = await use_case.execute(
            CreateDatasetVersionCommand(
                dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1"
            )
        )

        dataset = await uow.datasets.get_dataset(result.dataset_id)
        assert dataset.name == "bottle"

    async def test_a_second_version_reuses_the_existing_dataset(self) -> None:
        uow = FakeUnitOfWork()
        await _seed_images(uow, _trainable_images(1))
        use_case = make_create_dataset_version_use_case(
            uow=uow,
            version_control=FakeVersionControl(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        first = await use_case.execute(
            CreateDatasetVersionCommand(
                dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1"
            )
        )
        second = await use_case.execute(
            CreateDatasetVersionCommand(
                dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v2"
            )
        )

        assert first.dataset_id == second.dataset_id

    async def test_class_balance_reflects_ground_truth_labels(self) -> None:
        uow = FakeUnitOfWork()
        await _seed_images(uow, _trainable_images(3, label=ImageLabel.GOOD))
        await _seed_images(uow, _trainable_images(2, label=ImageLabel.DEFECT))
        use_case = make_create_dataset_version_use_case(
            uow=uow,
            version_control=FakeVersionControl(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        result = await use_case.execute(
            CreateDatasetVersionCommand(
                dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1"
            )
        )

        assert result.class_balance[ImageLabel.GOOD] == 3
        assert result.class_balance[ImageLabel.DEFECT] == 2

    async def test_the_manifest_is_pushed_to_version_control(self) -> None:
        uow = FakeUnitOfWork()
        await _seed_images(uow, _trainable_images(1))
        version_control = FakeVersionControl()
        use_case = make_create_dataset_version_use_case(
            uow=uow,
            version_control=version_control,
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        await use_case.execute(
            CreateDatasetVersionCommand(
                dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1"
            )
        )

        assert version_control.pushed_paths == ["bottle/bottle-v1.json"]

    async def test_the_recorded_git_commit_comes_from_version_control(self) -> None:
        uow = FakeUnitOfWork()
        await _seed_images(uow, _trainable_images(1))
        version_control = FakeVersionControl(commit="f" * 40)
        use_case = make_create_dataset_version_use_case(
            uow=uow,
            version_control=version_control,
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        result = await use_case.execute(
            CreateDatasetVersionCommand(
                dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1"
            )
        )

        assert result.git_commit == "f" * 40

    async def test_an_audit_event_is_appended(self) -> None:
        uow = FakeUnitOfWork()
        await _seed_images(uow, _trainable_images(1))
        use_case = make_create_dataset_version_use_case(
            uow=uow,
            version_control=FakeVersionControl(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        await use_case.execute(
            CreateDatasetVersionCommand(
                dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1"
            )
        )

        latest = await uow.audit.latest()
        assert latest is not None
        assert latest.action == "dataset_version.created"


class TestTagCollision:
    async def test_a_duplicate_tag_within_the_same_dataset_is_rejected(self) -> None:
        uow = FakeUnitOfWork()
        await _seed_images(uow, _trainable_images(2))
        use_case = make_create_dataset_version_use_case(
            uow=uow,
            version_control=FakeVersionControl(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )
        await use_case.execute(
            CreateDatasetVersionCommand(
                dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1"
            )
        )

        with pytest.raises(DatasetVersionTagExistsError):
            await use_case.execute(
                CreateDatasetVersionCommand(
                    dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1"
                )
            )


class TestEmptyCategory:
    async def test_no_images_at_all_is_rejected(self) -> None:
        uow = FakeUnitOfWork()
        use_case = make_create_dataset_version_use_case(
            uow=uow,
            version_control=FakeVersionControl(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        with pytest.raises(EmptyDatasetVersionError):
            await use_case.execute(
                CreateDatasetVersionCommand(
                    dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1"
                )
            )

    async def test_images_not_yet_validated_are_excluded(self) -> None:
        uow = FakeUnitOfWork()
        await uow.images.add(an_image(status=ProcessingStatus.PENDING))
        use_case = make_create_dataset_version_use_case(
            uow=uow,
            version_control=FakeVersionControl(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        with pytest.raises(EmptyDatasetVersionError):
            await use_case.execute(
                CreateDatasetVersionCommand(
                    dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1"
                )
            )


class TestFeedbackReviewedRegressionSuite:
    """Phase 12, ADR-0015: operator-reviewed images always land in TEST."""

    async def test_a_reviewed_image_lands_in_test_even_under_a_train_only_ratio(self) -> None:
        uow = FakeUnitOfWork()
        reviewed = an_image(
            checksum=Checksum(f"{1:064x}"),
            status=ProcessingStatus.VALID,
            uploaded_at=NOW,
        ).with_metadata(feedback_reviewed=True)
        await _seed_images(uow, [reviewed, *_trainable_images(9)])
        use_case = make_create_dataset_version_use_case(
            uow=uow,
            version_control=FakeVersionControl(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        result = await use_case.execute(
            CreateDatasetVersionCommand(
                dataset_name="bottle",
                category=_CATEGORY,
                version_tag="bottle-v1",
                split_ratios=SplitRatios(train=1.0, val=0.0, test=0.0),
            )
        )

        version = await uow.datasets.get_version(result.version_id)
        splits = {member.image_id: member.split for member in version.members}
        assert splits[reviewed.id] is DatasetSplit.TEST

    async def test_the_regression_suite_only_grows_as_more_images_are_reviewed(self) -> None:
        uow = FakeUnitOfWork()
        reviewed = [
            an_image(
                checksum=Checksum(f"{index:064x}"),
                status=ProcessingStatus.VALID,
                uploaded_at=NOW,
            ).with_metadata(feedback_reviewed=True)
            for index in range(1, 4)
        ]
        await _seed_images(uow, [*reviewed, *_trainable_images(10)])
        use_case = make_create_dataset_version_use_case(
            uow=uow,
            version_control=FakeVersionControl(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

        result = await use_case.execute(
            CreateDatasetVersionCommand(
                dataset_name="bottle", category=_CATEGORY, version_tag="bottle-v1"
            )
        )

        version = await uow.datasets.get_version(result.version_id)
        splits = {member.image_id: member.split for member in version.members}
        assert all(splits[image.id] is DatasetSplit.TEST for image in reviewed)
        assert result.split_counts[DatasetSplit.TEST] >= len(reviewed)


class TestSplitRatios:
    def test_ratios_must_sum_to_one(self) -> None:
        with pytest.raises(ValueError, match="sum to 1\\.0"):
            SplitRatios(train=0.5, val=0.3, test=0.3)

    def test_a_negative_ratio_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not be negative"):
            SplitRatios(train=1.1, val=-0.1, test=0.0)
