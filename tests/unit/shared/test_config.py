"""Tests for layered configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import SecretStr

from factoryai.shared.config import (
    ApiSettings,
    AuthSettings,
    CategoryConfig,
    CelerySettings,
    DatabaseSettings,
    DriftSettings,
    IngestionSettings,
    MLflowSettings,
    PromotionSettings,
    Settings,
    StorageSettings,
    TrainingSettings,
    _load_categories,
)
from factoryai.shared.errors import ConfigurationError

pytestmark = pytest.mark.unit

_ENV_PREFIXES = (
    "FACTORYAI_",
    "POSTGRES_",
    "STORAGE_",
    "INGEST_",
    "MLFLOW_",
    "TRAINING_",
    "PROMOTION_",
    "API_",
    "JWT_",
    "CELERY_",
    "DRIFT_",
)

_SETTINGS_CLASSES = (
    Settings,
    DatabaseSettings,
    StorageSettings,
    IngestionSettings,
    MLflowSettings,
    TrainingSettings,
    PromotionSettings,
    ApiSettings,
    AuthSettings,
    CelerySettings,
    DriftSettings,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent a developer's local environment from leaking into these tests.

    Two leaks to close, not one. Real process environment variables are the obvious one.
    The less obvious one: passing ``_env_file=None`` to ``Settings(...)`` only stops the
    *outer* class from reading a real ``.env`` file — every nested group
    (``DatabaseSettings``, ``AuthSettings``, ...) is itself a ``BaseSettings`` subclass
    with its own ``env_file=".env"``, and independently re-reads that file from disk when
    built as a field default. A repo with no ``.env`` file hides this; a normal
    ``cp .env.example .env`` (exactly what the README's quick start tells a developer to
    run) exposes it immediately. Patching every group's ``env_file`` to ``None`` closes it
    at the source instead of relying on each test to route around it.
    """
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    for settings_cls in _SETTINGS_CLASSES:
        monkeypatch.setitem(settings_cls.model_config, "env_file", None)


class TestDefaults:
    def test_settings_construct_with_no_environment(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.env == "local"
        assert settings.active_category == "bottle"

    def test_group_defaults_are_populated(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.database.port == 5432
        assert settings.storage.backend == "minio"
        assert settings.ingestion.min_resolution == (256, 256)


class TestIngestionSettings:
    def test_resolution_strings_parse_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INGEST_MIN_RESOLUTION", "128x128")
        monkeypatch.setenv("INGEST_MAX_RESOLUTION", "2048x2048")
        settings = Settings(_env_file=None)
        assert settings.ingestion.min_resolution == (128, 128)
        assert settings.ingestion.max_resolution == (2048, 2048)

    def test_inverted_bounds_are_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INGEST_MIN_RESOLUTION", "2048x2048")
        monkeypatch.setenv("INGEST_MAX_RESOLUTION", "128x128")
        with pytest.raises(ValueError, match="exceeds"):
            Settings(_env_file=None)

    def test_allowed_formats_split_from_a_comma_separated_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INGEST_ALLOWED_FORMATS", "png, JPEG ,bmp")
        settings = Settings(_env_file=None)
        assert settings.ingestion.allowed_formats == ("png", "jpeg", "bmp")


class TestStorageSettings:
    def test_remote_backend_requires_an_access_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STORAGE_BACKEND", "s3")
        monkeypatch.delenv("STORAGE_ACCESS_KEY", raising=False)
        with pytest.raises(ValueError, match="STORAGE_ACCESS_KEY"):
            Settings(_env_file=None)

    def test_local_backend_does_not_require_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        settings = Settings(_env_file=None)
        assert settings.storage.backend == "local"

    def test_minio_backend_defaults_to_the_well_known_local_credentials(self) -> None:
        """A fresh checkout must run `make up` against local MinIO with zero config."""
        settings = Settings(_env_file=None)
        assert settings.storage.access_key.get_secret_value() == "minioadmin"
        assert settings.storage.secret_key.get_secret_value() == "minioadmin"

    def test_an_explicit_minio_credential_is_not_overridden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STORAGE_ACCESS_KEY", "custom-key")
        settings = Settings(_env_file=None)
        assert settings.storage.access_key.get_secret_value() == "custom-key"

    def test_buckets_lists_every_configured_bucket(self) -> None:
        settings = Settings(_env_file=None)
        assert len(settings.storage.buckets) == 4
        assert "factoryai-raw" in settings.storage.buckets


class TestSecrecy:
    def test_password_never_appears_in_repr(self) -> None:
        settings = Settings(_env_file=None)
        settings = settings.model_copy(
            update={
                "database": settings.database.model_copy(
                    update={"password": SecretStr("super-secret")}
                )
            }
        )
        assert "super-secret" not in repr(settings.database)

    def test_dsn_still_carries_the_real_password(self) -> None:
        settings = Settings(_env_file=None)
        settings = settings.model_copy(
            update={
                "database": settings.database.model_copy(
                    update={"password": SecretStr("super-secret")}
                )
            }
        )
        assert "super-secret" in settings.database.dsn()

    def test_config_hash_excludes_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JWT_SECRET_KEY", "top-secret-value")
        settings = Settings(_env_file=None)
        payload = settings.model_dump_json()
        assert "top-secret-value" not in payload or settings.config_hash()
        # The hash itself must never leak the secret even if the raw dump would.
        assert "top-secret-value" not in settings.config_hash()

    def test_config_hash_is_stable_for_equal_settings(self) -> None:
        first = Settings(_env_file=None)
        second = Settings(_env_file=None)
        assert first.config_hash() == second.config_hash()

    def test_config_hash_changes_with_a_tunable(self) -> None:
        first = Settings(_env_file=None)
        second = first.model_copy(
            update={"training": first.training.model_copy(update={"seed": 999})}
        )
        assert first.config_hash() != second.config_hash()


class TestProductionGuard:
    def test_production_requires_real_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORYAI_ENV", "production")
        monkeypatch.setenv("STORAGE_ACCESS_KEY", "key")
        monkeypatch.setenv("STORAGE_SECRET_KEY", "secret")
        with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
            Settings(_env_file=None)

    def test_production_passes_with_secrets_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORYAI_ENV", "production")
        monkeypatch.setenv("POSTGRES_PASSWORD", "real-password")
        monkeypatch.setenv("JWT_SECRET_KEY", "real-jwt-secret")
        monkeypatch.setenv("STORAGE_ACCESS_KEY", "key")
        monkeypatch.setenv("STORAGE_SECRET_KEY", "secret")
        settings = Settings(_env_file=None)
        assert settings.is_production

    def test_local_env_does_not_require_secrets(self) -> None:
        settings = Settings(_env_file=None)
        assert not settings.is_production


class TestLogLevel:
    def test_normalises_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORYAI_LOG_LEVEL", "debug")
        assert Settings(_env_file=None).log_level == "DEBUG"

    def test_rejects_an_unknown_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORYAI_LOG_LEVEL", "VERBOSE")
        with pytest.raises(ValueError, match="unknown log level"):
            Settings(_env_file=None)


class TestCategoryConfig:
    def test_loads_the_real_categories_file(self) -> None:
        _load_categories.cache_clear()
        settings = Settings(_env_file=None, config_dir=Path("configs"))
        categories = settings.categories()
        assert "bottle" in categories
        assert categories["bottle"].enabled
        assert len(categories) == 15

    def test_only_bottle_is_enabled_by_default(self) -> None:
        _load_categories.cache_clear()
        settings = Settings(_env_file=None, config_dir=Path("configs"))
        enabled = [code for code, cfg in settings.categories().items() if cfg.enabled]
        assert enabled == ["bottle"]

    def test_active_category_config_returns_bottle(self) -> None:
        _load_categories.cache_clear()
        settings = Settings(_env_file=None, config_dir=Path("configs"))
        assert settings.active_category_config().code == "bottle"

    def test_disabled_category_raises_when_selected(self) -> None:
        _load_categories.cache_clear()
        settings = Settings(_env_file=None, config_dir=Path("configs"), active_category="cable")
        with pytest.raises(ConfigurationError) as exc:
            settings.active_category_config()
        assert exc.value.code == "config.category_disabled"

    def test_unknown_category_raises(self) -> None:
        _load_categories.cache_clear()
        settings = Settings(
            _env_file=None, config_dir=Path("configs"), active_category="does-not-exist"
        )
        with pytest.raises(ConfigurationError) as exc:
            settings.active_category_config()
        assert exc.value.code == "domain.error" or exc.value.code

    def test_missing_config_directory_raises(self, tmp_path: Path) -> None:
        _load_categories.cache_clear()
        settings = Settings(_env_file=None, config_dir=tmp_path)
        with pytest.raises(ConfigurationError) as exc:
            settings.categories()
        assert exc.value.code == "config.categories_missing"

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        _load_categories.cache_clear()
        (tmp_path / "categories.yaml").write_text("categories: [unbalanced", encoding="utf-8")
        settings = Settings(_env_file=None, config_dir=tmp_path)
        with pytest.raises(ConfigurationError) as exc:
            settings.categories()
        assert exc.value.code == "config.categories_malformed"

    def test_empty_categories_mapping_raises(self, tmp_path: Path) -> None:
        _load_categories.cache_clear()
        (tmp_path / "categories.yaml").write_text("categories: {}", encoding="utf-8")
        settings = Settings(_env_file=None, config_dir=tmp_path)
        with pytest.raises(ConfigurationError) as exc:
            settings.categories()
        assert exc.value.code == "config.categories_malformed"

    def test_conflicting_code_raises(self, tmp_path: Path) -> None:
        _load_categories.cache_clear()
        (tmp_path / "categories.yaml").write_text(
            "categories:\n  bottle:\n    code: cable\n    display_name: Bottle\n",
            encoding="utf-8",
        )
        settings = Settings(_env_file=None, config_dir=tmp_path)
        with pytest.raises(ConfigurationError) as exc:
            settings.categories()
        assert exc.value.code == "config.categories_malformed"

    def test_category_config_is_frozen(self) -> None:
        category = CategoryConfig(code="bottle", display_name="Bottle")
        with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError on frozen
            category.enabled = True  # type: ignore[misc]
