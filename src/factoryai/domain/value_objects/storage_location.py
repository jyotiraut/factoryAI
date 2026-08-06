"""A backend-agnostic pointer to an object in storage."""

from __future__ import annotations

from dataclasses import dataclass

from factoryai.domain.errors import InvariantViolationError


@dataclass(frozen=True, slots=True)
class StorageLocation:
    """Bucket and key identifying a stored object.

    Deliberately free of scheme, endpoint and credentials: the same location resolves
    against MinIO locally and S3 in production, and only the adapter knows the difference
    (ADR-0003). Persisting a full URL instead would bake the current backend into the
    database and make migration a data rewrite.

    Attributes:
        bucket: The container name.
        key: The object key within the bucket.
    """

    bucket: str
    key: str

    def __post_init__(self) -> None:
        """Validate the bucket and key.

        Raises:
            InvariantViolationError: If either component is empty, or the key is absolute or
                contains a parent-directory traversal.
        """
        if not self.bucket:
            raise InvariantViolationError(
                "storage bucket must not be empty", code="storage.no_bucket"
            )
        if not self.key:
            raise InvariantViolationError("storage key must not be empty", code="storage.no_key")
        if self.key.startswith("/") or ".." in self.key.split("/"):
            raise InvariantViolationError(
                f"storage key {self.key!r} must be relative and free of '..' segments",
                code="storage.unsafe_key",
                details={"key": self.key},
            )

    @property
    def uri(self) -> str:
        """Return an ``s3://bucket/key`` style URI, for logs and display only."""
        return f"s3://{self.bucket}/{self.key}"

    @property
    def extension(self) -> str:
        """Return the lowercase file extension without the dot, or an empty string."""
        _, _, suffix = self.key.rpartition(".")
        return suffix.lower() if "." in self.key else ""

    def __str__(self) -> str:
        """Return the URI representation."""
        return self.uri
