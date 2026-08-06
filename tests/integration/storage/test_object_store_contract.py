"""Shared contract tests run against every :class:`ObjectStore` adapter.

Both adapters must behave identically from the port's perspective — that is the entire
point of the port (ADR-0003) — so one parametrized suite exercises both rather than
duplicating the same assertions per adapter.
"""

from __future__ import annotations

import pytest

from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.value_objects import StorageLocation

pytestmark = pytest.mark.integration

_BUCKET = "factoryai-test"


@pytest.fixture(params=["local_object_store", "s3_object_store"])
def object_store(request: pytest.FixtureRequest) -> ObjectStore:
    """Yield each registered adapter in turn, so every test below runs against both."""
    return request.getfixturevalue(request.param)  # type: ignore[no-any-return]


async def test_put_then_get_round_trips_bytes(object_store: ObjectStore) -> None:
    location = StorageLocation(_BUCKET, "bottle/2026/08/one.png")
    await object_store.put(location, b"raw image bytes")
    assert await object_store.get(location) == b"raw image bytes"


async def test_get_raises_for_a_missing_object(object_store: ObjectStore) -> None:
    location = StorageLocation(_BUCKET, "bottle/2026/08/missing.png")
    with pytest.raises(EntityNotFoundError):
        await object_store.get(location)


async def test_exists_reflects_presence(object_store: ObjectStore) -> None:
    location = StorageLocation(_BUCKET, "bottle/2026/08/two.png")
    assert not await object_store.exists(location)
    await object_store.put(location, b"data")
    assert await object_store.exists(location)


async def test_put_overwrites_an_existing_object(object_store: ObjectStore) -> None:
    location = StorageLocation(_BUCKET, "bottle/2026/08/three.png")
    await object_store.put(location, b"first version")
    await object_store.put(location, b"second version")
    assert await object_store.get(location) == b"second version"


async def test_delete_removes_an_object(object_store: ObjectStore) -> None:
    location = StorageLocation(_BUCKET, "bottle/2026/08/four.png")
    await object_store.put(location, b"data")
    await object_store.delete(location)
    assert not await object_store.exists(location)


async def test_deleting_an_absent_object_does_not_raise(object_store: ObjectStore) -> None:
    location = StorageLocation(_BUCKET, "bottle/2026/08/never-existed.png")
    await object_store.delete(location)  # must not raise


async def test_presign_returns_a_url_like_string(object_store: ObjectStore) -> None:
    location = StorageLocation(_BUCKET, "bottle/2026/08/five.png")
    await object_store.put(location, b"data")
    url = await object_store.presign(location, ttl_seconds=300)
    assert isinstance(url, str)
    assert url


async def test_list_keys_returns_only_matching_prefixes(object_store: ObjectStore) -> None:
    await object_store.put(StorageLocation(_BUCKET, "bottle/2026/08/aaa.png"), b"a")
    await object_store.put(StorageLocation(_BUCKET, "bottle/2026/08/aab.png"), b"b")
    await object_store.put(StorageLocation(_BUCKET, "cable/2026/08/zzz.png"), b"c")

    keys = {key async for key in object_store.list_keys(_BUCKET, prefix="bottle/2026/08/aa")}

    assert keys == {"bottle/2026/08/aaa.png", "bottle/2026/08/aab.png"}
