"""The object storage port.

Deliberately the smallest surface that satisfies the platform's needs (ADR-0003). Every
S3-ism beyond this — storage classes, multipart tuning, lifecycle rules — stays inside the
adapter, so an Azure or GCS implementation is not forced to emulate concepts it lacks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from factoryai.domain.value_objects import StorageLocation


class ObjectStore(ABC):
    """Binary blob storage, independent of any cloud provider."""

    @abstractmethod
    async def put(
        self,
        location: StorageLocation,
        payload: bytes,
        *,
        content_type: str | None = None,
    ) -> None:
        """Store an object, overwriting any existing object at the same location.

        Args:
            location: Where to store it.
            payload: The bytes to store.
            content_type: MIME type recorded alongside the object.

        Raises:
            InfrastructureError: If the write fails.
        """

    @abstractmethod
    async def get(self, location: StorageLocation) -> bytes:
        """Retrieve an object's bytes.

        Raises:
            EntityNotFoundError: If nothing is stored at ``location``.
            InfrastructureError: If the read fails for any other reason.
        """

    @abstractmethod
    async def delete(self, location: StorageLocation) -> None:
        """Remove an object.

        Deleting an absent object succeeds, so that compensating deletes after a failed
        ingestion are safe to retry.
        """

    @abstractmethod
    async def exists(self, location: StorageLocation) -> bool:
        """Return whether an object is present."""

    @abstractmethod
    async def presign(self, location: StorageLocation, *, ttl_seconds: int) -> str:
        """Return a time-limited URL granting direct read access.

        Used so that the dashboard can load images and heatmaps without proxying them
        through the API, and without any bucket being publicly readable.

        Args:
            location: The object to grant access to.
            ttl_seconds: How long the URL remains valid.
        """

    @abstractmethod
    def list_keys(self, bucket: str, *, prefix: str = "") -> AsyncIterator[str]:
        """Yield object keys under a prefix.

        Returns an iterator rather than a list because buckets can hold millions of keys
        and callers — the orphan sweeper, for instance — process them in a stream.
        """
