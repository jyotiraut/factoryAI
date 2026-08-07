"""The composition root: builds concrete adapters from settings, once per process.

Every process shape — the API, a Celery worker, the CLI, an Airflow task — constructs one
:class:`Container` from the same :class:`~factoryai.shared.config.Settings` and gets back
the same wiring. This is what lets ``STORAGE_BACKEND=s3`` change every caller's behaviour
with a single environment variable instead of an edit at every call site.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from factoryai.application.use_cases.create_dataset_version import CreateDatasetVersion
from factoryai.application.use_cases.ingest_image import IngestImage
from factoryai.application.use_cases.train_model import TrainModel
from factoryai.domain.policies.validation import (
    AllowedColorModesRule,
    AllowedFormatRule,
    MaxFileSizeRule,
    ResolutionBoundsRule,
    ValidationChain,
)
from factoryai.domain.ports.detection import AnomalyDetector
from factoryai.domain.ports.imaging import ImageCodec
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import HardwareProbe, SystemClock, UuidGenerator
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.ports.tracking import ExperimentTracker, ModelRegistry
from factoryai.domain.ports.versioning import VersionControl
from factoryai.domain.value_objects import Resolution
from factoryai.infrastructure.imaging.pillow_codec import PillowImageCodec
from factoryai.infrastructure.persistence.engine import create_engine, create_session_factory
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from factoryai.infrastructure.storage.local import LocalObjectStore
from factoryai.infrastructure.storage.s3_compatible import S3CompatibleObjectStore
from factoryai.infrastructure.versioning.dvc_git import DvcGitVersionControl
from factoryai.shared.config import Settings
from factoryai.shared.errors import ConfigurationError

_REPO_ROOT = Path(__file__).resolve().parents[3]
"""The Git/DVC repository root: four levels up from this file
(``src/factoryai/bootstrap/container.py``)."""


@dataclass(frozen=True)
class Container:
    """Holds settings and lazily builds the adapters that depend on them.

    Adapters are built once and cached (:func:`functools.cached_property`): the database
    engine in particular is meant to be a single, long-lived connection pool per process,
    not something re-created on every use case call.

    Not ``slots=True``: :func:`functools.cached_property` caches by writing to the
    instance ``__dict__``, which slots removes entirely.
    """

    settings: Settings

    @cached_property
    def engine(self) -> AsyncEngine:
        """The application's async database engine."""
        return create_engine(self.settings.database)

    @cached_property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """The session factory every unit of work opens a transaction from."""
        return create_session_factory(self.engine)

    def unit_of_work(self) -> UnitOfWork:
        """Return a fresh unit of work.

        Deliberately not cached: a unit of work is one transaction, and hand out a new
        instance is what lets one process serve many independent requests concurrently.
        """
        return SqlAlchemyUnitOfWork(self.session_factory)

    @cached_property
    def object_store(self) -> ObjectStore:
        """The configured object store, selected by ``STORAGE_BACKEND``.

        Raises:
            ConfigurationError: If the backend is not yet implemented (``azure``, ``gcs``
                — ADR-0003 records these as adapters written when a phase needs them).
        """
        storage = self.settings.storage
        if storage.backend == "local":
            return LocalObjectStore(storage.local_root)
        if storage.backend in {"minio", "s3"}:
            return S3CompatibleObjectStore(
                endpoint_url=storage.endpoint,
                access_key=storage.access_key.get_secret_value(),
                secret_key=storage.secret_key.get_secret_value(),
                region=storage.region,
                use_ssl=storage.use_ssl,
            )
        raise ConfigurationError(
            f"no ObjectStore adapter is implemented yet for backend {storage.backend!r}",
            code="config.storage_backend_unimplemented",
            details={"backend": storage.backend},
        )

    @cached_property
    def image_codec(self) -> ImageCodec:
        """The configured image codec. Pillow today; the port is what makes that swappable."""
        return PillowImageCodec()

    @cached_property
    def validation_chain(self) -> ValidationChain:
        """The ingestion validation chain, composed from :class:`IngestionSettings`.

        This is the one place the rule set is assembled — adding a rule means adding an
        instance here, not touching :class:`IngestImage` (see ``docs/CONTRIBUTING.md``,
        "A new validation rule").
        """
        ingestion = self.settings.ingestion
        return ValidationChain(
            rules=(
                MaxFileSizeRule(ingestion.max_file_bytes),
                AllowedFormatRule(frozenset(ingestion.allowed_formats)),
                ResolutionBoundsRule(
                    minimum=Resolution(*ingestion.min_resolution),
                    maximum=Resolution(*ingestion.max_resolution),
                ),
                AllowedColorModesRule(frozenset(ingestion.allowed_color_modes)),
            )
        )

    def ingest_image_use_case(self) -> IngestImage:
        """Build the ``IngestImage`` use case, wired to this container's adapters."""
        return IngestImage(
            uow_factory=self.unit_of_work,
            object_store=self.object_store,
            image_codec=self.image_codec,
            validation_chain=self.validation_chain,
            clock=SystemClock(),
            id_generator=UuidGenerator(),
            raw_bucket=self.settings.storage.bucket_raw,
            duplicate_hamming_threshold=self.settings.ingestion.duplicate_hamming_threshold,
        )

    @cached_property
    def version_control(self) -> VersionControl:
        """The Git+DVC adapter, rooted at the repository ``dvc init`` was run against.

        ``datasets/`` (see ``.dvc/config``'s tracked paths) is the directory DVC-tracked
        manifests are materialised under — ADR-0006.
        """
        return DvcGitVersionControl(repo_root=_REPO_ROOT, dataset_root=_REPO_ROOT / "datasets")

    def create_dataset_version_use_case(self) -> CreateDatasetVersion:
        """Build the ``CreateDatasetVersion`` use case, wired to this container's adapters."""
        return CreateDatasetVersion(
            uow_factory=self.unit_of_work,
            version_control=self.version_control,
            clock=SystemClock(),
            id_generator=UuidGenerator(),
        )

    def _configure_mlflow_s3_env(self) -> None:
        """Set the environment variables MLflow's own boto3 client reads for artifact I/O.

        Unlike :attr:`object_store`, MLflow's artifact store is not something this
        container hands credentials to directly — the MLflow client constructs its own S3
        client internally, reading ``AWS_ACCESS_KEY_ID``/``AWS_SECRET_ACCESS_KEY``/
        ``MLFLOW_S3_ENDPOINT_URL`` from the process environment. ``setdefault`` so an
        operator's own explicit environment always wins.
        """
        storage = self.settings.storage
        os.environ.setdefault("MLFLOW_S3_ENDPOINT_URL", self.settings.mlflow.s3_endpoint_url)
        os.environ.setdefault("AWS_ACCESS_KEY_ID", storage.access_key.get_secret_value())
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", storage.secret_key.get_secret_value())

    @cached_property
    def experiment_tracker(self) -> ExperimentTracker:
        """The MLflow-backed experiment tracker (ADR-0004).

        Imported lazily inside this property, not at module level: a command that never
        trains a model should never need ``mlflow`` importable.
        """
        from factoryai.infrastructure.tracking.mlflow_tracker import MlflowExperimentTracker

        self._configure_mlflow_s3_env()
        return MlflowExperimentTracker(self.settings.mlflow.tracking_uri)

    @cached_property
    def model_registry(self) -> ModelRegistry:
        """The MLflow-backed model registry (ADR-0004).

        Imported lazily; see :attr:`experiment_tracker`.
        """
        from factoryai.infrastructure.tracking.mlflow_registry import MlflowModelRegistry

        self._configure_mlflow_s3_env()
        return MlflowModelRegistry(self.settings.mlflow.tracking_uri)

    @cached_property
    def hardware_probe(self) -> HardwareProbe:
        """The real hardware fingerprint probe. Imported lazily; see :attr:`experiment_tracker`."""
        from factoryai.infrastructure.monitoring.hardware import SystemHardwareProbe

        return SystemHardwareProbe()

    def detector_factory(self) -> Callable[[str, str | None], AnomalyDetector]:
        """Return a callable that builds a registered detector by name.

        Importing ``factoryai.infrastructure.detection`` is what registers every
        Anomalib-backed and custom detector (ADR-0002) — done here, lazily, rather than at
        module level, so a command that never trains a model never needs ``torch`` or
        ``anomalib`` importable.
        """
        import factoryai.infrastructure.detection  # noqa: F401 — side effect: registers detectors
        from factoryai.domain.ports.detection import get_detector_class

        def _build(name: str, backbone: str | None) -> AnomalyDetector:
            detector_cls = get_detector_class(name)
            # Every registered detector's constructor accepts an optional backbone
            # override by convention (see each adapter in infrastructure/detection/); the
            # port itself declares no `__init__`, so mypy cannot verify this statically.
            return detector_cls(backbone) if backbone else detector_cls()  # type: ignore[call-arg]

        return _build

    def train_model_use_case(self) -> TrainModel:
        """Build the ``TrainModel`` use case, wired to this container's adapters."""
        return TrainModel(
            uow_factory=self.unit_of_work,
            object_store=self.object_store,
            detector_factory=self.detector_factory(),
            experiment_tracker=self.experiment_tracker,
            model_registry=self.model_registry,
            version_control=self.version_control,
            hardware_probe=self.hardware_probe,
            clock=SystemClock(),
            id_generator=UuidGenerator(),
            workdir=_REPO_ROOT / "data" / "training",
            mlflow_experiment_name=self.settings.mlflow.experiment_name,
        )

    async def dispose(self) -> None:
        """Release the database connection pool.

        Call once, at process shutdown. Only meaningful if :attr:`engine` was ever
        accessed — disposing an engine that was never built would create one just to
        immediately tear it down.
        """
        if "engine" in self.__dict__:
            await self.engine.dispose()


def build_container(settings: Settings) -> Container:
    """Build the composition root for a process.

    Args:
        settings: The process's configuration, typically
            :func:`factoryai.shared.config.get_settings`.

    Returns:
        A container ready to hand out units of work and an object store.
    """
    return Container(settings=settings)
