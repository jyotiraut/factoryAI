"""Unit tests for the composition root.

These stay in the unit suite deliberately: building an async engine or a unit-of-work
factory does not connect to anything (SQLAlchemy engines are lazy), so the container's
*wiring* logic is verifiable without a container test dependency of its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factoryai.bootstrap.container import Container, build_container
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.infrastructure.storage.local import LocalObjectStore
from factoryai.infrastructure.storage.s3_compatible import S3CompatibleObjectStore
from factoryai.shared.config import Settings
from factoryai.shared.errors import ConfigurationError

pytestmark = pytest.mark.unit


def _settings_with_backend(backend: str, tmp_path: Path) -> Settings:
    base = Settings(_env_file=None)
    return base.model_copy(
        update={
            "storage": base.storage.model_copy(update={"backend": backend, "local_root": tmp_path})
        }
    )


class TestBuildContainer:
    def test_returns_a_container_holding_the_given_settings(self) -> None:
        settings = Settings(_env_file=None)
        container = build_container(settings)
        assert container.settings is settings


class TestObjectStoreSelection:
    def test_local_backend_builds_a_local_object_store(self, tmp_path: Path) -> None:
        settings = _settings_with_backend("local", tmp_path)
        container = Container(settings=settings)
        assert isinstance(container.object_store, LocalObjectStore)

    @pytest.mark.parametrize("backend", ["minio", "s3"])
    def test_s3_compatible_backends_build_the_shared_adapter(
        self, backend: str, tmp_path: Path
    ) -> None:
        settings = _settings_with_backend(backend, tmp_path)
        container = Container(settings=settings)
        assert isinstance(container.object_store, S3CompatibleObjectStore)

    def test_unimplemented_backend_raises_a_clear_configuration_error(self, tmp_path: Path) -> None:
        settings = _settings_with_backend("azure", tmp_path)
        container = Container(settings=settings)
        with pytest.raises(ConfigurationError) as exc:
            _ = container.object_store
        assert exc.value.code == "config.storage_backend_unimplemented"

    def test_object_store_is_cached_across_accesses(self, tmp_path: Path) -> None:
        settings = _settings_with_backend("local", tmp_path)
        container = Container(settings=settings)
        assert container.object_store is container.object_store


class TestUnitOfWorkFactory:
    def test_unit_of_work_returns_the_expected_type(self, tmp_path: Path) -> None:
        settings = _settings_with_backend("local", tmp_path)
        container = Container(settings=settings)
        assert isinstance(container.unit_of_work(), UnitOfWork)

    def test_each_call_returns_a_fresh_instance(self, tmp_path: Path) -> None:
        """One unit of work per transaction — sharing one across requests would be a bug."""
        settings = _settings_with_backend("local", tmp_path)
        container = Container(settings=settings)
        assert container.unit_of_work() is not container.unit_of_work()

    def test_session_factory_is_bound_to_the_cached_engine(self, tmp_path: Path) -> None:
        settings = _settings_with_backend("local", tmp_path)
        container = Container(settings=settings)
        assert container.session_factory.kw["bind"] is container.engine


class TestDispose:
    async def test_dispose_is_a_no_op_when_the_engine_was_never_built(self, tmp_path: Path) -> None:
        settings = _settings_with_backend("local", tmp_path)
        container = Container(settings=settings)
        await container.dispose()  # must not raise, must not build an engine
        assert "engine" not in container.__dict__

    async def test_dispose_releases_a_built_engine(self, tmp_path: Path) -> None:
        settings = _settings_with_backend("local", tmp_path)
        container = Container(settings=settings)
        _ = container.engine
        await container.dispose()  # must not raise
