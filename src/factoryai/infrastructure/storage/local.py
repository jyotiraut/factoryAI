"""Filesystem-backed object storage, for fast tests that should not need Docker."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.value_objects import StorageLocation


class LocalObjectStore(ObjectStore):
    """Stores objects under a root directory as ``<root>/<bucket>/<key>``.

    Blocking filesystem calls are pushed onto a worker thread via
    :func:`asyncio.to_thread` so this adapter honours the ``async`` port contract without
    ever actually blocking the event loop — important once this runs inside the same
    process as the FastAPI service (Phase 7).
    """

    def __init__(self, root: Path) -> None:
        """Initialise with the directory objects are stored under, created if absent."""
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, location: StorageLocation) -> Path:
        return self._root / location.bucket / location.key

    async def put(
        self,
        location: StorageLocation,
        payload: bytes,
        *,
        content_type: str | None = None,
    ) -> None:
        """Write ``payload`` to disk, creating any missing parent directories.

        ``content_type`` is accepted for interface compatibility with the S3-compatible
        adapter but not persisted — the local filesystem has no metadata slot for it, and
        nothing in this adapter's test usage reads it back.
        """
        path = self._path(location)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

        await asyncio.to_thread(_write)

    async def get(self, location: StorageLocation) -> bytes:
        """Read an object's bytes.

        Raises:
            EntityNotFoundError: If nothing is stored at ``location``.
        """
        path = self._path(location)
        if not await asyncio.to_thread(path.is_file):
            raise EntityNotFoundError("StorageObject", location.uri)
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, location: StorageLocation) -> None:
        """Remove an object. Deleting an absent object is a no-op, not an error."""
        path = self._path(location)
        await asyncio.to_thread(path.unlink, True)  # missing_ok=True

    async def exists(self, location: StorageLocation) -> bool:
        """Return whether an object is present."""
        return await asyncio.to_thread(self._path(location).is_file)

    async def presign(self, location: StorageLocation, *, ttl_seconds: int) -> str:
        """Return a ``file://`` URI.

        Not a real presigned URL — there is no server to honour a TTL against a bare
        filesystem — so this exists purely so tests exercising the port's contract have
        something to assert against. Production traffic uses the S3-compatible adapter.
        """
        return self._path(location).resolve().as_uri()

    async def list_keys(self, bucket: str, *, prefix: str = "") -> AsyncIterator[str]:
        """Yield object keys under a prefix within a bucket.

        ``prefix`` matches like S3's does: a plain string prefix on the key, not
        necessarily a whole path segment — ``"bottle/2026/08/ab"`` matches
        ``"bottle/2026/08/abc123.png"``.
        """
        bucket_root = self._root / bucket

        def _list() -> list[str]:
            if not bucket_root.is_dir():
                return []
            keys = (
                path.relative_to(bucket_root).as_posix()
                for path in bucket_root.rglob("*")
                if path.is_file()
            )
            return sorted(key for key in keys if key.startswith(prefix))

        for key in await asyncio.to_thread(_list):
            yield key

    async def clear(self) -> None:
        """Remove every object. Test-only convenience, not part of the domain port."""
        await asyncio.to_thread(shutil.rmtree, self._root, True)  # ignore_errors=True
        self._root.mkdir(parents=True, exist_ok=True)
