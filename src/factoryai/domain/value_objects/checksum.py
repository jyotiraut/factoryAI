"""Content checksums used for integrity, addressing and duplicate detection."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Self

from factoryai.domain.errors import InvariantViolationError

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SHA256_LENGTH = 64


@dataclass(frozen=True, slots=True)
class Checksum:
    """A lowercase hexadecimal SHA-256 digest.

    Checksums serve three purposes at once: they detect corruption in object storage, they
    provide content-addressed storage keys, and they are the exact-duplicate check during
    ingestion. Because they are a unique key in the database, a malformed value must never
    reach persistence — hence validation in the constructor rather than at the boundary.

    Attributes:
        value: The 64-character lowercase hex digest.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the digest format.

        Raises:
            InvariantViolationError: If the value is not 64 lowercase hexadecimal characters.
        """
        if not _SHA256_PATTERN.match(self.value):
            raise InvariantViolationError(
                "checksum must be 64 lowercase hexadecimal characters",
                code="checksum.malformed",
                details={"length": len(self.value)},
            )

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Compute the checksum of an in-memory payload.

        Args:
            payload: The raw bytes to digest.

        Returns:
            The checksum of ``payload``.
        """
        return cls(hashlib.sha256(payload).hexdigest())

    @classmethod
    def of_checksums(cls, checksums: list[Checksum]) -> Self:
        """Compute a stable digest over a set of checksums.

        Used to give a dataset version a single content fingerprint. The inputs are sorted
        first, so the result depends on the *set* of members and not on the order in which
        they were collected.

        Args:
            checksums: The member checksums. May be empty.

        Returns:
            A checksum identifying the collection as a whole.
        """
        digest = hashlib.sha256()
        for item in sorted(checksum.value for checksum in checksums):
            digest.update(item.encode("ascii"))
        return cls(digest.hexdigest())

    @property
    def short(self) -> str:
        """Return the first 12 characters, for logs and UI labels."""
        return self.value[:12]

    @property
    def prefix(self) -> str:
        """Return the first two characters, used to shard object-storage key space."""
        return self.value[:2]

    def __str__(self) -> str:
        """Return the full digest."""
        return self.value
