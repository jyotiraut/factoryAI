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

from factoryai.application.services.model_cache import ModelCache
from factoryai.application.use_cases.create_dataset_version import CreateDatasetVersion
from factoryai.application.use_cases.ingest_image import IngestImage
from factoryai.application.use_cases.list_production_models import ListProductionModels
from factoryai.application.use_cases.login import Login
from factoryai.application.use_cases.logout import Logout
from factoryai.application.use_cases.predict_image import PredictImage
from factoryai.application.use_cases.promote_model import PromoteModel, PromotionGate
from factoryai.application.use_cases.refresh_access_token import RefreshAccessToken
from factoryai.application.use_cases.register_user import RegisterUser
from factoryai.application.use_cases.rollback_deployment import RollbackDeployment
from factoryai.application.use_cases.submit_feedback import SubmitFeedback
from factoryai.application.use_cases.train_model import TrainModel
from factoryai.application.use_cases.verify_audit_chain import VerifyAuditChain
from factoryai.domain.policies.validation import (
    AllowedColorModesRule,
    AllowedFormatRule,
    MaxFileSizeRule,
    ResolutionBoundsRule,
    ValidationChain,
)
from factoryai.domain.ports.auth import PasswordHasher, TokenRevocationList, TokenService
from factoryai.domain.ports.detection import AnomalyDetector
from factoryai.domain.ports.imaging import ImageCodec
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import HardwareProbe, SystemClock, UuidGenerator
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.ports.tracking import ExperimentTracker, ModelRegistry
from factoryai.domain.ports.versioning import VersionControl
from factoryai.domain.value_objects import Resolution
from factoryai.infrastructure.auth.argon2_hasher import Argon2PasswordHasher
from factoryai.infrastructure.auth.jwt_tokens import JwtTokenService
from factoryai.infrastructure.imaging.pillow_codec import PillowImageCodec
from factoryai.infrastructure.persistence.engine import create_engine, create_session_factory
from factoryai.infrastructure.persistence.repositories import SqlAlchemyTokenRevocationList
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

    def promote_model_use_case(self) -> PromoteModel:
        """Build the ``PromoteModel`` use case, wired to this container's adapters."""
        promotion = self.settings.promotion
        return PromoteModel(
            uow_factory=self.unit_of_work,
            model_registry=self.model_registry,
            gate=PromotionGate(
                min_auroc=promotion.min_auroc,
                improvement_margin=promotion.improvement_margin,
                max_recall_regression=promotion.max_recall_regression,
            ),
            clock=SystemClock(),
            id_generator=UuidGenerator(),
        )

    def rollback_deployment_use_case(self) -> RollbackDeployment:
        """Build the ``RollbackDeployment`` use case, wired to this container's adapters."""
        return RollbackDeployment(
            uow_factory=self.unit_of_work,
            model_registry=self.model_registry,
            clock=SystemClock(),
            id_generator=UuidGenerator(),
        )

    @cached_property
    def model_cache(self) -> ModelCache:
        """The warmed detector cache the inference path shares across requests.

        Cached at the container level (unlike other use cases, built fresh per call) so
        every request in the process reuses the same loaded detectors instead of
        re-downloading and reloading a model artifact on every prediction.
        """
        return ModelCache(
            detector_factory=self.detector_factory(),
            model_registry=self.model_registry,
            workdir=_REPO_ROOT / "data" / "serving",
        )

    def predict_image_use_case(self) -> PredictImage:
        """Build the ``PredictImage`` use case, wired to this container's adapters."""
        return PredictImage(
            uow_factory=self.unit_of_work,
            object_store=self.object_store,
            image_codec=self.image_codec,
            model_cache=self.model_cache,
            clock=SystemClock(),
            id_generator=UuidGenerator(),
            raw_bucket=self.settings.storage.bucket_raw,
            heatmap_bucket=self.settings.storage.bucket_heatmaps,
        )

    def submit_feedback_use_case(self) -> SubmitFeedback:
        """Build the ``SubmitFeedback`` use case, wired to this container's adapters."""
        return SubmitFeedback(
            uow_factory=self.unit_of_work, clock=SystemClock(), id_generator=UuidGenerator()
        )

    def list_production_models_use_case(self) -> ListProductionModels:
        """Build the ``ListProductionModels`` use case, wired to this container's adapters."""
        return ListProductionModels(uow_factory=self.unit_of_work)

    @cached_property
    def password_hasher(self) -> PasswordHasher:
        """The argon2id password hasher (Phase 8, ADR-0011)."""
        return Argon2PasswordHasher()

    @cached_property
    def token_service(self) -> TokenService:
        """The JWT issuance/verification adapter, configured from ``JWT_*`` settings."""
        auth = self.settings.auth
        return JwtTokenService(
            secret_key=auth.secret_key.get_secret_value(),
            algorithm=auth.algorithm,
            access_token_minutes=auth.access_token_minutes,
            refresh_token_days=auth.refresh_token_days,
        )

    @cached_property
    def token_revocation_list(self) -> TokenRevocationList:
        """The refresh-token blacklist, backed by its own short-lived sessions."""
        return SqlAlchemyTokenRevocationList(self.session_factory)

    def register_user_use_case(self) -> RegisterUser:
        """Build the ``RegisterUser`` use case, wired to this container's adapters."""
        return RegisterUser(
            uow_factory=self.unit_of_work,
            password_hasher=self.password_hasher,
            clock=SystemClock(),
            id_generator=UuidGenerator(),
        )

    def login_use_case(self) -> Login:
        """Build the ``Login`` use case, wired to this container's adapters."""
        return Login(
            uow_factory=self.unit_of_work,
            password_hasher=self.password_hasher,
            token_service=self.token_service,
            clock=SystemClock(),
        )

    def refresh_access_token_use_case(self) -> RefreshAccessToken:
        """Build the ``RefreshAccessToken`` use case, wired to this container's adapters."""
        return RefreshAccessToken(
            uow_factory=self.unit_of_work,
            token_service=self.token_service,
            revocation_list=self.token_revocation_list,
        )

    def logout_use_case(self) -> Logout:
        """Build the ``Logout`` use case, wired to this container's adapters."""
        return Logout(
            uow_factory=self.unit_of_work,
            token_service=self.token_service,
            revocation_list=self.token_revocation_list,
            clock=SystemClock(),
        )

    def verify_audit_chain_use_case(self) -> VerifyAuditChain:
        """Build the ``VerifyAuditChain`` use case, wired to this container's adapters."""
        return VerifyAuditChain(uow_factory=self.unit_of_work)

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
