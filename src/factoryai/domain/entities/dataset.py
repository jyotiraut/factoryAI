"""Datasets and their immutable versions."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from factoryai.domain.errors import InvariantViolationError
from factoryai.domain.value_objects import (
    Category,
    Checksum,
    DatasetId,
    DatasetSplit,
    DatasetVersionId,
    ImageId,
)

GIT_SHA_LENGTH = 40


@dataclass(frozen=True, slots=True)
class DatasetMember:
    """Membership of one image in one dataset version.

    Attributes:
        image_id: The image included.
        split: Which partition it belongs to.
    """

    image_id: ImageId
    split: DatasetSplit


@dataclass(frozen=True, slots=True)
class Dataset:
    """A named, category-scoped collection that accrues versions over time.

    Attributes:
        id: Unique identifier.
        name: Human-chosen name, unique across the platform.
        category: The product class this dataset covers.
        created_at: Timezone-aware creation timestamp.
        description: Optional free text.
    """

    id: DatasetId
    name: str
    category: Category
    created_at: datetime
    description: str = ""

    def __post_init__(self) -> None:
        """Validate the name and timestamp.

        Raises:
            InvariantViolationError: If the name is blank or the timestamp is naive.
        """
        if not self.name.strip():
            raise InvariantViolationError("dataset name must not be blank", code="dataset.no_name")
        if self.created_at.tzinfo is None:
            raise InvariantViolationError(
                "created_at must be timezone-aware", code="dataset.naive_timestamp"
            )


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    """An immutable, reproducible snapshot of a dataset.

    A version is a *set of references* plus a split assignment, not a copy of the images
    (``docs/DATA_MODEL.md`` §2). Creating version N+1 after adding fifty images costs fifty
    rows, not fifty files.

    Reproducibility rests on three recorded facts: the DVC hash (the exact bytes), the Git
    commit (the code and config that produced it), and the content checksum (a fingerprint
    over the members, independent of insertion order). All three must agree for an
    experiment to be replayable.

    Attributes:
        id: Unique identifier.
        dataset_id: The dataset this version belongs to.
        version_tag: Human-readable tag, e.g. ``"bottle-v1"``.
        dvc_hash: The DVC content hash for the materialised data.
        git_commit: The 40-character commit SHA at creation time.
        members: Image references with their split assignment.
        created_at: Timezone-aware creation timestamp.
        note: Optional description of what changed.
    """

    id: DatasetVersionId
    dataset_id: DatasetId
    version_tag: str
    dvc_hash: str
    git_commit: str
    members: tuple[DatasetMember, ...]
    created_at: datetime
    note: str = ""
    _content_checksum: Checksum | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Validate tag, commit, membership uniqueness and timestamp.

        Raises:
            InvariantViolationError: If the tag is blank, the commit is not a 40-character SHA,
                the version is empty, an image appears twice, or the timestamp is naive.
        """
        if not self.version_tag.strip():
            raise InvariantViolationError(
                "version tag must not be blank", code="dataset_version.no_tag"
            )
        if len(self.git_commit) != GIT_SHA_LENGTH:
            raise InvariantViolationError(
                "git_commit must be a full 40-character SHA",
                code="dataset_version.bad_commit",
                details={"length": len(self.git_commit)},
            )
        if not self.members:
            raise InvariantViolationError(
                "a dataset version must contain at least one image",
                code="dataset_version.empty",
            )
        image_ids = [member.image_id for member in self.members]
        if len(set(image_ids)) != len(image_ids):
            duplicates = [item for item, count in Counter(image_ids).items() if count > 1]
            raise InvariantViolationError(
                "an image may appear at most once in a dataset version",
                code="dataset_version.duplicate_member",
                details={"duplicates": [str(item) for item in duplicates]},
            )
        if self.created_at.tzinfo is None:
            raise InvariantViolationError(
                "created_at must be timezone-aware", code="dataset_version.naive_timestamp"
            )

    @property
    def image_count(self) -> int:
        """Return the total number of images in this version."""
        return len(self.members)

    def split_counts(self) -> dict[DatasetSplit, int]:
        """Return the number of images in each split, including empty ones."""
        counts = dict.fromkeys(DatasetSplit, 0)
        for member in self.members:
            counts[member.split] += 1
        return counts

    def image_ids(self, split: DatasetSplit | None = None) -> tuple[ImageId, ...]:
        """Return the image identifiers in this version.

        Args:
            split: Restrict to one partition. All images are returned when omitted.
        """
        return tuple(
            member.image_id for member in self.members if split is None or member.split is split
        )

    def content_checksum(self, member_checksums: dict[ImageId, Checksum]) -> Checksum:
        """Compute the fingerprint of this version's contents.

        Args:
            member_checksums: Checksum of every member image.

        Returns:
            A single checksum covering the whole version, order-independent.

        Raises:
            InvariantViolationError: If any member is missing from ``member_checksums``.
        """
        missing = [
            member.image_id for member in self.members if member.image_id not in member_checksums
        ]
        if missing:
            raise InvariantViolationError(
                f"{len(missing)} member checksum(s) missing",
                code="dataset_version.incomplete_checksums",
                details={"missing": [str(item) for item in missing[:10]]},
            )
        return Checksum.of_checksums([member_checksums[member.image_id] for member in self.members])
