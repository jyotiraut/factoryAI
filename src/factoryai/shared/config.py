"""Layered application configuration.

Precedence, lowest to highest: code defaults, ``configs/*.yaml``, environment variables
(and ``.env``), explicit constructor arguments. Nothing in the platform reads
``os.environ`` directly — every tunable value arrives through one of these objects.

Settings are grouped by concern, each group carrying its own environment prefix so that
``POSTGRES_HOST`` maps to ``settings.database.host``. Groups whose phase has not yet landed
are still defined here: it keeps :file:`.env.example` and the code in one place, and the
defaults are inert until an adapter reads them.

Secrets use :class:`~pydantic.SecretStr`, which keeps them out of ``repr`` output, log
records and the configuration hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from factoryai.shared.errors import ConfigurationError

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_DIR = REPO_ROOT / "configs"

Environment = Literal["local", "test", "staging", "production"]
LogFormat = Literal["json", "console"]
StorageBackend = Literal["minio", "s3", "azure", "gcs", "local"]
Device = Literal["auto", "cpu", "cuda"]

_BASE_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    frozen=True,
)


def _parse_resolution(value: Any) -> Any:
    """Convert a ``"WIDTHxHEIGHT"`` string into a ``(width, height)`` tuple."""
    if not isinstance(value, str):
        return value
    parts = value.lower().split("x")
    expected_parts = 2
    if len(parts) != expected_parts:
        raise ValueError(f"expected 'WIDTHxHEIGHT', got {value!r}")
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError as exc:
        raise ValueError(f"expected integer dimensions, got {value!r}") from exc


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection and pooling (Phase 2)."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="POSTGRES_")

    host: str = "localhost"
    port: int = Field(default=5432, ge=1, le=65535)
    db: str = "factoryai"
    user: str = "factoryai"
    password: SecretStr = SecretStr("")
    pool_size: int = Field(default=10, ge=1)
    max_overflow: int = Field(default=20, ge=0)
    echo_sql: bool = False

    def dsn(self, *, driver: str = "postgresql+psycopg") -> str:
        """Build a SQLAlchemy connection URL.

        Args:
            driver: SQLAlchemy dialect and driver, e.g. ``"postgresql+psycopg"``.

        Returns:
            A DSN containing the password in clear text. Never log the result.
        """
        secret = self.password.get_secret_value()
        return f"{driver}://{self.user}:{secret}@{self.host}:{self.port}/{self.db}"


class StorageSettings(BaseSettings):
    """Object storage, cloud-agnostic behind the ``ObjectStore`` port (ADR-0003)."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="STORAGE_")

    backend: StorageBackend = "minio"
    endpoint: str = "http://localhost:9000"
    access_key: SecretStr = SecretStr("")
    secret_key: SecretStr = SecretStr("")
    region: str = "eu-central-1"
    use_ssl: bool = False
    local_root: Path = Path("data/object-store")
    presign_ttl_seconds: int = Field(default=900, ge=60, le=604_800)

    bucket_raw: str = "factoryai-raw"
    bucket_datasets: str = "factoryai-datasets"
    bucket_artifacts: str = "factoryai-artifacts"
    bucket_heatmaps: str = "factoryai-heatmaps"

    @property
    def buckets(self) -> tuple[str, ...]:
        """Return every bucket the platform expects to exist."""
        return (
            self.bucket_raw,
            self.bucket_datasets,
            self.bucket_artifacts,
            self.bucket_heatmaps,
        )

    @model_validator(mode="before")
    @classmethod
    def _default_to_local_minio_credentials(cls, data: Any) -> Any:
        """Fill in MinIO's well-known local credentials when none were supplied.

        A fresh checkout must be able to run ``make up`` against the local MinIO container
        with zero configuration. This only fills a gap left by every other source (field
        default, config file, env var); it never overrides a credential the caller gave.
        """
        if not isinstance(data, dict):
            return data
        backend = data.get("backend", "minio")
        if backend in {"minio", "local"}:
            data.setdefault("access_key", "minioadmin")
            data.setdefault("secret_key", "minioadmin")
        return data

    @model_validator(mode="after")
    def _require_credentials_for_cloud_backends(self) -> Self:
        """Fail fast when a cloud backend is selected without explicit credentials.

        Unlike ``minio``/``local``, there is no safe default for a real cloud provider —
        falling back to the local MinIO credentials there would silently point production
        traffic at the wrong account.
        """
        cloud_backends = {"s3", "azure", "gcs"}
        if self.backend in cloud_backends and not self.access_key.get_secret_value():
            raise ValueError(f"STORAGE_ACCESS_KEY is required for backend {self.backend!r}")
        return self


class IngestionSettings(BaseSettings):
    """Limits and rules applied before an image enters the dataset (Phase 3)."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="INGEST_")

    max_file_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    # NoDecode: these arrive as plain strings ("png,jpeg" / "1024x768"), not JSON, so
    # pydantic-settings' default env-var JSON decoding for complex types must be skipped
    # and parsing left entirely to the `mode="before"` validators below.
    allowed_formats: Annotated[tuple[str, ...], NoDecode] = ("png", "jpeg", "bmp", "tiff")
    # Pillow mode names ("RGB", "L", "RGBA", ...) are case-sensitive, unlike file formats.
    allowed_color_modes: Annotated[tuple[str, ...], NoDecode] = ("RGB", "L", "RGBA")
    min_resolution: Annotated[tuple[int, int], NoDecode] = (256, 256)
    max_resolution: Annotated[tuple[int, int], NoDecode] = (4096, 4096)
    duplicate_hamming_threshold: int = Field(default=3, ge=0, le=64)

    _normalise_min = field_validator("min_resolution", mode="before")(_parse_resolution)
    _normalise_max = field_validator("max_resolution", mode="before")(_parse_resolution)

    @field_validator("allowed_formats", mode="before")
    @classmethod
    def _split_formats(cls, value: Any) -> Any:
        """Accept a comma-separated string as well as a sequence."""
        if isinstance(value, str):
            return tuple(item.strip().lower() for item in value.split(",") if item.strip())
        return value

    @field_validator("allowed_color_modes", mode="before")
    @classmethod
    def _split_color_modes(cls, value: Any) -> Any:
        """Accept a comma-separated string as well as a sequence."""
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @model_validator(mode="after")
    def _check_resolution_bounds(self) -> Self:
        """Reject an inverted resolution window, which would accept nothing."""
        if self.min_resolution > self.max_resolution:
            raise ValueError(
                f"min_resolution {self.min_resolution} exceeds max_resolution {self.max_resolution}"
            )
        return self


class MLflowSettings(BaseSettings):
    """Experiment tracking and model registry (Phase 5, ADR-0004)."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="MLFLOW_")

    tracking_uri: str = "http://localhost:5000"
    experiment_name: str = "factoryai-bottle"
    registry_name: str = "factoryai-patchcore-bottle"
    s3_endpoint_url: str = "http://localhost:9000"


class TrainingSettings(BaseSettings):
    """Training pipeline defaults (Phase 5)."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="TRAINING_")

    config: Path = Path("configs/bottle/patchcore.yaml")
    seed: int = 42
    device: Device = "auto"
    num_workers: int = Field(default=4, ge=0)


class PromotionSettings(BaseSettings):
    """Thresholds the automated promotion gate enforces (Phase 6)."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="PROMOTION_")

    min_auroc: float = Field(default=0.95, ge=0.0, le=1.0)
    improvement_margin: float = Field(default=0.005, ge=0.0, le=1.0)
    max_recall_regression: float = Field(default=0.01, ge=0.0, le=1.0)


class ApiSettings(BaseSettings):
    """HTTP surface (Phase 7)."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="API_")

    host: str = "0.0.0.0"  # binding all interfaces is intended: the process runs inside a container
    port: int = Field(default=8000, ge=1, le=65535)
    workers: int = Field(default=2, ge=1)
    request_timeout_seconds: int = Field(default=30, ge=1)
    max_batch_size: int = Field(default=64, ge=1)
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        """Accept a comma-separated string as well as a sequence."""
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value


class AuthSettings(BaseSettings):
    """JWT authentication (Phase 8)."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="JWT_")

    secret_key: SecretStr = SecretStr("")
    algorithm: str = "HS256"
    access_token_minutes: int = Field(default=30, ge=1)
    refresh_token_days: int = Field(default=7, ge=1)


class CelerySettings(BaseSettings):
    """Background task queue (Phase 9, ADR-0005)."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="CELERY_")

    broker_url: str = "redis://localhost:6379/1"
    result_backend: str = "redis://localhost:6379/2"
    task_time_limit_seconds: int = Field(default=3600, ge=1)


class DriftSettings(BaseSettings):
    """Model monitoring thresholds (Phase 11)."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="DRIFT_")

    window_hours: int = Field(default=24, ge=1)
    min_samples: int = Field(default=200, ge=1)
    data_threshold: float = Field(default=0.15, ge=0.0, le=1.0)
    prediction_threshold: float = Field(default=0.10, ge=0.0, le=1.0)


class CategoryConfig(BaseModel):
    """Per-category inspection configuration loaded from ``configs/categories.yaml``.

    Category is configuration, not code: enabling a new MVTec class requires an entry here
    and nothing else. Only ``bottle`` is enabled until later phases validate the rest.
    """

    model_config = {"frozen": True}

    code: str
    display_name: str
    enabled: bool = False
    image_size: tuple[int, int] = (256, 256)
    center_crop: tuple[int, int] | None = None
    normalisation: Literal["imagenet", "none"] = "imagenet"
    default_model: str = "patchcore"
    notes: str = ""

    _normalise_size = field_validator("image_size", "center_crop", mode="before")(_parse_resolution)


class Settings(BaseSettings):
    """Root settings object, composed of one group per concern."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="FACTORYAI_")

    env: Environment = "local"
    log_level: str = "INFO"
    log_format: LogFormat = "json"
    service_name: str = "factoryai"
    active_category: str = "bottle"
    config_dir: Path = DEFAULT_CONFIG_DIR

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    mlflow: MLflowSettings = Field(default_factory=MLflowSettings)
    training: TrainingSettings = Field(default_factory=TrainingSettings)
    promotion: PromotionSettings = Field(default_factory=PromotionSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    celery: CelerySettings = Field(default_factory=CelerySettings)
    drift: DriftSettings = Field(default_factory=DriftSettings)

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """Normalise and check the log level against the standard library names."""
        normalised = value.upper()
        if normalised not in logging.getLevelNamesMapping():
            raise ValueError(f"unknown log level {value!r}")
        return normalised

    @model_validator(mode="after")
    def _production_requires_real_secrets(self) -> Self:
        """Refuse to start a production process with placeholder credentials."""
        if self.env != "production":
            return self
        missing = [
            name
            for name, secret in (
                ("POSTGRES_PASSWORD", self.database.password),
                ("JWT_SECRET_KEY", self.auth.secret_key),
            )
            if not secret.get_secret_value()
        ]
        if missing:
            raise ValueError(f"production requires {', '.join(missing)} to be set")
        return self

    @property
    def is_production(self) -> bool:
        """Return whether this process is running in the production environment."""
        return self.env == "production"

    def config_hash(self) -> str:
        """Return a stable SHA-256 hash of the non-secret configuration.

        Logged with every training run so that an experiment can be tied to the exact
        configuration that produced it. Secrets are excluded, so the hash is safe to
        publish alongside metrics.

        Returns:
            A 64-character lowercase hexadecimal digest.
        """
        payload = self.model_dump(mode="json", exclude={"config_dir"})
        canonical = json.dumps(_strip_secrets(payload), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def categories(self) -> dict[str, CategoryConfig]:
        """Load every category definition from ``<config_dir>/categories.yaml``.

        Returns:
            A mapping of category code to its configuration.

        Raises:
            ConfigurationError: If the file is missing, malformed, or defines a category
                whose key and ``code`` disagree.
        """
        return _load_categories(self.config_dir)

    def active_category_config(self) -> CategoryConfig:
        """Return the configuration for :attr:`active_category`.

        Raises:
            ConfigurationError: If the category is unknown or not enabled.
        """
        categories = self.categories()
        try:
            category = categories[self.active_category]
        except KeyError as exc:
            known = ", ".join(sorted(categories))
            raise ConfigurationError(
                f"unknown category {self.active_category!r}; known categories: {known}",
                details={"category": self.active_category},
            ) from exc
        if not category.enabled:
            raise ConfigurationError(
                f"category {category.code!r} is defined but not enabled",
                code="config.category_disabled",
                details={"category": category.code},
            )
        return category


def _strip_secrets(value: Any) -> Any:
    """Recursively replace serialised ``SecretStr`` markers with a constant placeholder."""
    if isinstance(value, dict):
        return {key: _strip_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_secrets(item) for item in value]
    if value == "**********":
        return "<secret>"
    return value


@lru_cache(maxsize=8)
def _load_categories(config_dir: Path) -> dict[str, CategoryConfig]:
    """Read and validate ``categories.yaml``, caching the result per directory."""
    path = config_dir / "categories.yaml"
    if not path.is_file():
        raise ConfigurationError(
            f"category configuration not found at {path}",
            code="config.categories_missing",
            details={"path": str(path)},
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"category configuration at {path} is not valid YAML: {exc}",
            code="config.categories_malformed",
            details={"path": str(path)},
        ) from exc

    entries = raw.get("categories")
    if not isinstance(entries, dict) or not entries:
        raise ConfigurationError(
            f"{path} must define a non-empty 'categories' mapping",
            code="config.categories_malformed",
            details={"path": str(path)},
        )

    categories: dict[str, CategoryConfig] = {}
    for key, body in entries.items():
        payload = {"code": key, **(body or {})}
        if payload["code"] != key:
            raise ConfigurationError(
                f"category {key!r} declares a conflicting code {payload['code']!r}",
                code="config.categories_malformed",
            )
        try:
            categories[key] = CategoryConfig.model_validate(payload)
        except ValueError as exc:
            raise ConfigurationError(
                f"category {key!r} is invalid: {exc}",
                code="config.categories_malformed",
                details={"category": key},
            ) from exc
    return categories


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so that every caller observes the same immutable configuration. Tests that need
    different values should construct :class:`Settings` directly or call
    ``get_settings.cache_clear()``.

    Raises:
        ConfigurationError: If the environment yields an invalid configuration.
    """
    try:
        return Settings()
    except ValueError as exc:
        raise ConfigurationError(f"invalid configuration: {exc}") from exc
