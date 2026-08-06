"""S3-protocol object storage: MinIO locally, AWS S3 in the cloud (ADR-0003).

The two are the same wire protocol — this adapter serves both, selected by which endpoint
URL and credentials :mod:`factoryai.shared.config` hands it. Nothing in this file knows or
cares which one it is actually talking to.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.value_objects import StorageLocation
from factoryai.shared.errors import InfrastructureError, TransientError

_TRANSIENT_ERROR_CODES = frozenset(
    {"RequestTimeout", "SlowDown", "InternalError", "ServiceUnavailable", "Throttling"}
)
_HTTP_NOT_FOUND = 404


class S3CompatibleObjectStore(ObjectStore):
    """Object storage against any S3-compatible endpoint, via boto3.

    All boto3 calls are synchronous; each is pushed onto a worker thread via
    :func:`asyncio.to_thread` rather than blocking the event loop. A dedicated async S3
    client (``aioboto3``) would avoid the thread hop, but boto3 is the dependency already
    pinned for MLflow's artifact store — one less library to pin, patch and audit.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str,
        use_ssl: bool,
    ) -> None:
        """Initialise a boto3 S3 client against ``endpoint_url``.

        ``signature_version="s3v4"`` and path-style addressing are required for MinIO;
        both are also valid against AWS S3, so there is no reason to branch on backend.
        """
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            use_ssl=use_ssl,
            config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def ensure_bucket(self, bucket: str) -> None:
        """Create ``bucket`` if it does not already exist.

        Not part of the :class:`ObjectStore` port — this is provisioning, called once at
        startup for every bucket the platform expects (see
        :meth:`factoryai.shared.config.StorageSettings.buckets`), not a per-request
        operation any use case performs.
        """
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=bucket)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status != _HTTP_NOT_FOUND:
                raise self._wrap(exc, f"checking bucket {bucket!r}") from exc
            await asyncio.to_thread(self._client.create_bucket, Bucket=bucket)

    async def put(
        self,
        location: StorageLocation,
        payload: bytes,
        *,
        content_type: str | None = None,
    ) -> None:
        """Upload an object, overwriting any existing object at the same location."""
        kwargs = {"Bucket": location.bucket, "Key": location.key, "Body": payload}
        if content_type:
            kwargs["ContentType"] = content_type
        try:
            await asyncio.to_thread(partial(self._client.put_object, **kwargs))
        except ClientError as exc:
            raise self._wrap(exc, f"writing {location}") from exc

    async def get(self, location: StorageLocation) -> bytes:
        """Download an object's bytes.

        Raises:
            EntityNotFoundError: If nothing is stored at ``location``.
        """
        try:
            response = await asyncio.to_thread(
                self._client.get_object, Bucket=location.bucket, Key=location.key
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                raise EntityNotFoundError("StorageObject", location.uri) from exc
            raise self._wrap(exc, f"reading {location}") from exc
        return await asyncio.to_thread(response["Body"].read)

    async def delete(self, location: StorageLocation) -> None:
        """Remove an object. Deleting an absent object succeeds, per the S3 API itself."""
        try:
            await asyncio.to_thread(
                self._client.delete_object, Bucket=location.bucket, Key=location.key
            )
        except ClientError as exc:
            raise self._wrap(exc, f"deleting {location}") from exc

    async def exists(self, location: StorageLocation) -> bool:
        """Return whether an object is present."""
        try:
            await asyncio.to_thread(
                self._client.head_object, Bucket=location.bucket, Key=location.key
            )
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == _HTTP_NOT_FOUND:
                return False
            raise self._wrap(exc, f"checking {location}") from exc
        return True

    async def presign(self, location: StorageLocation, *, ttl_seconds: int) -> str:
        """Return a time-limited GET URL, so callers never need bucket credentials."""
        try:
            return await asyncio.to_thread(
                self._client.generate_presigned_url,
                "get_object",
                Params={"Bucket": location.bucket, "Key": location.key},
                ExpiresIn=ttl_seconds,
            )
        except ClientError as exc:
            raise self._wrap(exc, f"presigning {location}") from exc

    async def list_keys(self, bucket: str, *, prefix: str = "") -> AsyncIterator[str]:
        """Yield object keys under a prefix, transparently paginating."""
        paginator = self._client.get_paginator("list_objects_v2")

        def _pages() -> list[dict[str, Any]]:
            return list(paginator.paginate(Bucket=bucket, Prefix=prefix))

        try:
            pages = await asyncio.to_thread(_pages)
        except ClientError as exc:
            raise self._wrap(exc, f"listing {bucket}/{prefix}") from exc
        for page in pages:
            for entry in page.get("Contents", []):
                yield entry["Key"]

    def _wrap(self, exc: ClientError, action: str) -> InfrastructureError:
        """Translate a boto3 error into the shared infrastructure error hierarchy."""
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        message = f"S3 operation failed while {action}: {code}"
        if code in _TRANSIENT_ERROR_CODES:
            return TransientError(message, details={"code": code})
        return InfrastructureError(message, details={"code": code})
