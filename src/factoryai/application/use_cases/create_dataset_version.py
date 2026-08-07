"""The dataset-versioning use case: freeze a reproducible snapshot of a category's images.

A version is a *set of references* plus a split assignment (``docs/DATA_MODEL.md`` §2), so
this use case never copies image bytes — it selects the currently trainable images for a
category, assigns each a train/val/test partition deterministically, and records the
result as a manifest that DVC version-controls and a ``DatasetVersion`` row that
PostgreSQL indexes. ADR-0006 explains why both: DVC answers "give me the exact bytes",
PostgreSQL answers "what is in it" in SQL.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from factoryai.domain.entities import AuditEvent, Dataset, DatasetMember, InspectionImage
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.entities.dataset import DatasetVersion
from factoryai.domain.errors import DatasetVersionTagExistsError, EmptyDatasetVersionError
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import Clock, IdGenerator
from factoryai.domain.ports.versioning import VersionControl
from factoryai.domain.value_objects import (
    AuditSequence,
    Category,
    DatasetId,
    DatasetSplit,
    DatasetVersionId,
    ImageLabel,
    UserId,
)

_SPLIT_RATIO_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class SplitRatios:
    """The fraction of a dataset version assigned to each partition.

    Attributes:
        train: Fraction assigned to :attr:`~factoryai.domain.value_objects.DatasetSplit.TRAIN`.
        val: Fraction assigned to :attr:`~factoryai.domain.value_objects.DatasetSplit.VAL`.
        test: Fraction assigned to :attr:`~factoryai.domain.value_objects.DatasetSplit.TEST`.
    """

    train: float = 0.7
    val: float = 0.15
    test: float = 0.15

    def __post_init__(self) -> None:
        """Validate the ratios are non-negative and sum to 1.0.

        Raises:
            ValueError: If any ratio is negative or the total is not 1.0 (within
                floating-point tolerance).
        """
        if any(ratio < 0 for ratio in (self.train, self.val, self.test)):
            raise ValueError(f"split ratios must not be negative: {self}")
        total = self.train + self.val + self.test
        if abs(total - 1.0) > _SPLIT_RATIO_TOLERANCE:
            raise ValueError(f"split ratios must sum to 1.0, got {total}")


@dataclass(frozen=True, slots=True)
class CreateDatasetVersionCommand:
    """Freeze the current trainable set of one category into a new, reproducible version.

    Attributes:
        dataset_name: The named collection this version belongs to; created if it does
            not exist yet.
        category: Which product class's images to include.
        version_tag: Human-readable tag, e.g. ``"bottle-v1"``. Must be unique within the
            dataset.
        seed: Drives the deterministic train/val/test assignment — the same seed over the
            same trainable set always produces the same split.
        split_ratios: How the trainable set divides across partitions.
        note: Optional free-text description of what changed since the last version.
        created_by: The user requesting the version; absent for an automated job.
    """

    dataset_name: str
    category: Category
    version_tag: str
    seed: int = 42
    split_ratios: SplitRatios = field(default_factory=SplitRatios)
    note: str = ""
    created_by: UserId | None = None


@dataclass(frozen=True, slots=True)
class CreateDatasetVersionResult:
    """The outcome of freezing a dataset version.

    Attributes:
        dataset_id: The dataset this version belongs to.
        version_id: The new version's identifier.
        version_tag: Echoes the command's tag.
        dvc_hash: The DVC content hash for the pushed manifest.
        git_commit: The Git commit recorded alongside this version.
        image_count: Total images included.
        split_counts: Images per partition.
        class_balance: Images per ground-truth label.
        content_checksum: Checksum-of-checksums over every member image.
    """

    dataset_id: DatasetId
    version_id: DatasetVersionId
    version_tag: str
    dvc_hash: str
    git_commit: str
    image_count: int
    split_counts: dict[DatasetSplit, int]
    class_balance: dict[ImageLabel, int]
    content_checksum: str


def _assign_splits(
    images: list[InspectionImage], ratios: SplitRatios, *, seed: int
) -> list[tuple[InspectionImage, DatasetSplit]]:
    """Deterministically partition images into train/val/test.

    Sorts by ``(uploaded_at, id)`` first so the input to the shuffle is itself stable
    across calls — a repository could otherwise return the same images in a different
    order between runs — then shuffles with a seeded RNG. The same seed over the same
    trainable set always reproduces the same split, which is the property Phase 4's exit
    criteria require.
    """
    ordered = sorted(images, key=lambda image: (image.uploaded_at, str(image.id)))
    random.Random(seed).shuffle(ordered)

    total = len(ordered)
    n_train = min(round(total * ratios.train), total)
    n_val = min(round(total * ratios.val), total - n_train)

    return [
        *((image, DatasetSplit.TRAIN) for image in ordered[:n_train]),
        *((image, DatasetSplit.VAL) for image in ordered[n_train : n_train + n_val]),
        *((image, DatasetSplit.TEST) for image in ordered[n_train + n_val :]),
    ]


def _build_manifest(assigned: list[tuple[InspectionImage, DatasetSplit]]) -> dict[str, Any]:
    """Build a deterministic, JSON-serialisable manifest for the DVC-tracked snapshot.

    Sorted by image id (not split-assignment order) so that two versions with the exact
    same membership serialise identically regardless of shuffle order, which is what makes
    the resulting DVC hash a genuine content fingerprint rather than an artefact of
    iteration order.
    """
    return {
        "images": [
            {
                "image_id": str(image.id),
                "checksum": image.checksum.value,
                "split": split.value,
                "label": image.label.value,
            }
            for image, split in sorted(assigned, key=lambda pair: str(pair[0].id))
        ]
    }


class CreateDatasetVersion:
    """Freezes a category's current trainable images into a new, versioned snapshot."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        version_control: VersionControl,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        """Initialise with every collaborator this use case needs.

        Args:
            uow_factory: Builds a fresh unit of work per call.
            version_control: Reports the Git commit and version-controls the manifest.
            clock: Source of "now", for the dataset and version creation timestamps.
            id_generator: Source of new dataset and version identifiers.
        """
        self._uow_factory = uow_factory
        self._version_control = version_control
        self._clock = clock
        self._id_generator = id_generator

    async def execute(self, command: CreateDatasetVersionCommand) -> CreateDatasetVersionResult:
        """Freeze the current trainable set into a new dataset version.

        Raises:
            DatasetVersionTagExistsError: If ``command.version_tag`` is already used
                within this dataset.
            EmptyDatasetVersionError: If the category has no trainable images.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            dataset = await self._get_or_create_dataset(uow, command, now)

            if await uow.datasets.find_version_by_tag(dataset.id, command.version_tag):
                raise DatasetVersionTagExistsError(
                    f"dataset {command.dataset_name!r} already has a version tagged "
                    f"{command.version_tag!r}",
                    details={"dataset": command.dataset_name, "tag": command.version_tag},
                )

            images = await uow.images.list_trainable(command.category)
            if not images:
                raise EmptyDatasetVersionError(
                    f"category {command.category.code!r} has no trainable images",
                    details={"category": command.category.code},
                )

            assigned = _assign_splits(images, command.split_ratios, seed=command.seed)
            manifest_bytes = json.dumps(_build_manifest(assigned), sort_keys=True, indent=2).encode(
                "utf-8"
            )

            git_commit = await self._version_control.current_commit()
            dvc_hash = await self._version_control.track_and_push(
                f"{dataset.name}/{command.version_tag}.json", manifest_bytes
            )

            version = DatasetVersion(
                id=DatasetVersionId(self._id_generator.new_id()),
                dataset_id=dataset.id,
                version_tag=command.version_tag,
                dvc_hash=dvc_hash,
                git_commit=git_commit,
                members=tuple(DatasetMember(image.id, split) for image, split in assigned),
                created_at=now,
                note=command.note,
            )
            await uow.datasets.add_version(version)
            content_checksum = version.content_checksum(
                {image.id: image.checksum for image in images}
            )
            await self._append_audit_event(uow, version, dataset, command, now)
            await uow.commit()

        return CreateDatasetVersionResult(
            dataset_id=dataset.id,
            version_id=version.id,
            version_tag=version.version_tag,
            dvc_hash=dvc_hash,
            git_commit=git_commit,
            image_count=version.image_count,
            split_counts=version.split_counts(),
            class_balance=dict(Counter(image.label for image in images)),
            content_checksum=content_checksum.value,
        )

    async def _get_or_create_dataset(
        self, uow: UnitOfWork, command: CreateDatasetVersionCommand, now: datetime
    ) -> Dataset:
        """Return the named dataset, creating it on first use."""
        dataset = await uow.datasets.find_dataset_by_name(command.dataset_name)
        if dataset is not None:
            return dataset
        dataset = Dataset(
            id=DatasetId(self._id_generator.new_id()),
            name=command.dataset_name,
            category=command.category,
            created_at=now,
        )
        await uow.datasets.add_dataset(dataset)
        return dataset

    async def _append_audit_event(
        self,
        uow: UnitOfWork,
        version: DatasetVersion,
        dataset: Dataset,
        command: CreateDatasetVersionCommand,
        now: datetime,
    ) -> None:
        """Append the audit record for this version, extending the hash chain."""
        latest = await uow.audit.latest()
        event = AuditEvent(
            sequence=AuditSequence((latest.sequence + 1) if latest else 1),
            action="dataset_version.created",
            resource_type="dataset_version",
            resource_id=str(version.id),
            occurred_at=now,
            prev_hash=latest.row_hash() if latest else GENESIS_HASH,
            actor_id=command.created_by,
            payload={
                "dataset": dataset.name,
                "version_tag": command.version_tag,
                "image_count": version.image_count,
                "dvc_hash": version.dvc_hash,
                "git_commit": version.git_commit,
            },
        )
        await uow.audit.append(event)
