"""In-memory fakes for the domain ports.

Fakes over mocks (``docs/CONTRIBUTING.md``): a fake actually behaves like the real thing —
storing what it's given, raising :class:`EntityNotFoundError` for what it isn't — so a test
against it exercises real use-case logic instead of asserting on which methods got called.

These implement every abstract method on their port, even the ones a given phase's use
cases do not yet exercise, so this module stays a genuine shared asset for Phase 4 onwards
rather than something each phase has to extend before it's usable.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from factoryai.domain.entities import (
    AuditEvent,
    Dataset,
    DatasetVersion,
    Deployment,
    DriftReport,
    EvaluationMetrics,
    Experiment,
    Feedback,
    HardwareInfo,
    InspectionImage,
    Job,
    ModelVersion,
    Prediction,
    User,
)
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.errors import (
    CorruptImageError,
    EntityNotFoundError,
    InvariantViolationError,
    JobIdempotencyKeyExistsError,
)
from factoryai.domain.ports.auth import TokenRevocationList
from factoryai.domain.ports.detection import (
    AnomalyDetector,
    DetectorNotLoadedError,
    RawPrediction,
    TrainedModel,
    TrainingRequest,
)
from factoryai.domain.ports.imaging import ImageCodec
from factoryai.domain.ports.repositories import (
    AuditRepository,
    DatasetRepository,
    DriftReportRepository,
    ExperimentRepository,
    ImageRepository,
    JobRepository,
    ModelRepository,
    PredictionRepository,
    UnitOfWork,
    UserRepository,
)
from factoryai.domain.ports.services import Clock, HardwareProbe, IdGenerator
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.ports.tracking import ExperimentTracker, ModelRegistry
from factoryai.domain.ports.versioning import VersionControl
from factoryai.domain.value_objects import (
    AnomalyScore,
    Category,
    Checksum,
    DatasetId,
    DatasetVersionId,
    DecodedImage,
    ExperimentId,
    ImageId,
    JobId,
    JobStatus,
    ModelStage,
    ModelVersionId,
    PredictionId,
    ProcessingStatus,
    StorageLocation,
    UserId,
)


class FakeClock(Clock):
    """Returns a fixed, injected time."""

    def __init__(self, now: datetime) -> None:
        """Initialise with the timestamp every call to :meth:`now` returns."""
        self._now = now

    def now(self) -> datetime:
        """Return the fixed timestamp this fake was constructed with."""
        return self._now


class FakeIdGenerator(IdGenerator):
    """Returns identifiers from a fixed, pre-seeded sequence, then random ones."""

    def __init__(self, *ids: uuid.UUID) -> None:
        """Initialise with the identifiers to hand out first, in order."""
        self._queue = list(ids)

    def new_id(self) -> uuid.UUID:
        """Return the next queued identifier, or a fresh random one once exhausted."""
        return self._queue.pop(0) if self._queue else uuid.uuid4()


class FakeHardwareProbe(HardwareProbe):
    """Returns a fixed, injected hardware snapshot."""

    def __init__(self, info: HardwareInfo | None = None) -> None:
        """Initialise with the snapshot every call to :meth:`capture` returns."""
        self._info = info or HardwareInfo(cpu_model="Fake CPU", cpu_count=4, memory_gb=16.0)

    def capture(self) -> HardwareInfo:
        """Return the scripted hardware snapshot."""
        return self._info


class FakeVersionControl(VersionControl):
    """An in-memory version control adapter — no real Git or DVC process is spawned.

    Attributes:
        commit: What :meth:`current_commit` returns; override to test a specific SHA.
    """

    def __init__(self, commit: str = "a" * 40) -> None:
        """Initialise with the commit SHA every call to :meth:`current_commit` returns."""
        self.commit = commit
        self._tracked: dict[str, bytes] = {}
        self.pushed_paths: list[str] = []
        """Every path ever passed to :meth:`track_and_push`, for assertions."""

    async def current_commit(self) -> str:
        """Return the scripted :attr:`commit`."""
        return self.commit

    async def track_and_push(self, relative_path: str, payload: bytes) -> str:
        """Record ``payload`` and return a deterministic hash derived from its content."""
        self._tracked[relative_path] = payload
        self.pushed_paths.append(relative_path)
        return hashlib.md5(payload).hexdigest()

    async def pull(self, relative_path: str) -> bytes:
        """Return the bytes previously recorded for ``relative_path``.

        Raises:
            EntityNotFoundError: If nothing was ever tracked at that path.
        """
        try:
            return self._tracked[relative_path]
        except KeyError as exc:
            raise EntityNotFoundError("DvcTrackedFile", relative_path) from exc


class FakeObjectStore(ObjectStore):
    """An in-memory object store, for tests that should not touch a filesystem or MinIO."""

    def __init__(self) -> None:
        """Initialise with an empty store."""
        self._objects: dict[tuple[str, str], bytes] = {}
        self.deleted: list[StorageLocation] = []
        """Every location ever passed to :meth:`delete`, for assertions."""

    async def put(
        self, location: StorageLocation, payload: bytes, *, content_type: str | None = None
    ) -> None:
        """Store ``payload`` under ``location``."""
        self._objects[(location.bucket, location.key)] = payload

    async def get(self, location: StorageLocation) -> bytes:
        """Return the stored bytes.

        Raises:
            EntityNotFoundError: If nothing is stored at ``location``.
        """
        try:
            return self._objects[(location.bucket, location.key)]
        except KeyError as exc:
            raise EntityNotFoundError("StorageObject", location.uri) from exc

    async def delete(self, location: StorageLocation) -> None:
        """Remove an object and record the call, for assertions in tests."""
        self._objects.pop((location.bucket, location.key), None)
        self.deleted.append(location)

    async def exists(self, location: StorageLocation) -> bool:
        """Return whether an object is present."""
        return (location.bucket, location.key) in self._objects

    async def presign(self, location: StorageLocation, *, ttl_seconds: int) -> str:
        """Return a fake URL — never a real, resolvable one."""
        return f"fake://{location.bucket}/{location.key}?ttl={ttl_seconds}"

    async def list_keys(self, bucket: str, *, prefix: str = "") -> AsyncIterator[str]:
        """Yield keys under a prefix within a bucket."""
        for stored_bucket, key in sorted(self._objects):
            if stored_bucket == bucket and key.startswith(prefix):
                yield key


class FakeImageCodec(ImageCodec):
    """A codec with a scripted, deterministic response — no real image bytes required.

    Attributes:
        decoded: What :meth:`decode` returns, unless :attr:`corrupt` is set.
        hash_value: What :meth:`perceptual_hash` returns, unless :attr:`corrupt` is set.
        corrupt: When ``True``, both methods raise :class:`CorruptImageError` instead.
    """

    def __init__(self, decoded: DecodedImage, hash_value: str = "0" * 16) -> None:
        """Initialise with the structural metadata and hash every call returns."""
        self.decoded = decoded
        self.hash_value = hash_value
        self.corrupt = False

    def decode(self, payload: bytes) -> DecodedImage:
        """Return the scripted :attr:`decoded` value, or raise if :attr:`corrupt`."""
        if self.corrupt:
            raise CorruptImageError("fake codec configured to reject this payload")
        return self.decoded

    def perceptual_hash(self, payload: bytes) -> str:
        """Return the scripted :attr:`hash_value`, or raise if :attr:`corrupt`."""
        if self.corrupt:
            raise CorruptImageError("fake codec configured to reject this payload")
        return self.hash_value


class FakeImageRepository(ImageRepository):
    """An in-memory image repository."""

    def __init__(self) -> None:
        """Initialise with an empty store."""
        self._by_id: dict[ImageId, InspectionImage] = {}

    async def add(self, image: InspectionImage) -> None:
        """Insert a new image."""
        self._by_id[image.id] = image

    async def update(self, image: InspectionImage) -> None:
        """Overwrite an existing image.

        Raises:
            EntityNotFoundError: If no such image exists.
        """
        if image.id not in self._by_id:
            raise EntityNotFoundError("InspectionImage", image.id)
        self._by_id[image.id] = image

    async def get(self, image_id: ImageId) -> InspectionImage:
        """Return an image by id.

        Raises:
            EntityNotFoundError: If no such image exists.
        """
        try:
            return self._by_id[image_id]
        except KeyError as exc:
            raise EntityNotFoundError("InspectionImage", image_id) from exc

    async def find_by_checksum(self, checksum: Checksum) -> InspectionImage | None:
        """Return the image with this exact checksum, if stored."""
        return next((img for img in self._by_id.values() if img.checksum == checksum), None)

    async def find_near_duplicates(
        self, perceptual_hash: str, *, max_distance: int
    ) -> list[InspectionImage]:
        """Return images whose perceptual hash is within ``max_distance``."""
        target = int(perceptual_hash, 16)
        matches = []
        for image in self._by_id.values():
            if image.perceptual_hash is None:
                continue
            distance = (target ^ int(image.perceptual_hash, 16)).bit_count()
            if distance <= max_distance:
                matches.append(image)
        return matches

    async def list_trainable(
        self, category: Category, *, limit: int | None = None
    ) -> list[InspectionImage]:
        """Return validated images for a category, oldest first."""
        matches = sorted(
            (img for img in self._by_id.values() if img.category == category and img.is_trainable),
            key=lambda img: img.uploaded_at,
        )
        return matches[:limit] if limit is not None else matches

    async def count_by_status(self, category: Category) -> dict[str, int]:
        """Return image counts grouped by processing status, zero-filled for every status."""
        counts = dict.fromkeys((status.value for status in ProcessingStatus), 0)
        for image in self._by_id.values():
            if image.category == category:
                counts[image.status.value] += 1
        return counts


class FakeDatasetRepository(DatasetRepository):
    """An in-memory dataset repository."""

    def __init__(self) -> None:
        """Initialise with empty stores."""
        self._datasets: dict[DatasetId, Dataset] = {}
        self._versions: dict[DatasetVersionId, DatasetVersion] = {}

    async def add_dataset(self, dataset: Dataset) -> None:
        """Insert a new dataset."""
        self._datasets[dataset.id] = dataset

    async def get_dataset(self, dataset_id: DatasetId) -> Dataset:
        """Return a dataset by id.

        Raises:
            EntityNotFoundError: If no such dataset exists.
        """
        try:
            return self._datasets[dataset_id]
        except KeyError as exc:
            raise EntityNotFoundError("Dataset", dataset_id) from exc

    async def find_dataset_by_name(self, name: str) -> Dataset | None:
        """Return a dataset by its unique name, if it exists."""
        return next((d for d in self._datasets.values() if d.name == name), None)

    async def add_version(self, version: DatasetVersion) -> None:
        """Insert a new dataset version."""
        self._versions[version.id] = version

    async def get_version(self, version_id: DatasetVersionId) -> DatasetVersion:
        """Return a dataset version by id.

        Raises:
            EntityNotFoundError: If no such version exists.
        """
        try:
            return self._versions[version_id]
        except KeyError as exc:
            raise EntityNotFoundError("DatasetVersion", version_id) from exc

    async def find_version_by_tag(self, dataset_id: DatasetId, tag: str) -> DatasetVersion | None:
        """Return a version by its tag, if it exists."""
        return next(
            (
                v
                for v in self._versions.values()
                if v.dataset_id == dataset_id and v.version_tag == tag
            ),
            None,
        )

    async def list_versions(self, dataset_id: DatasetId) -> list[DatasetVersion]:
        """Return every version of a dataset, newest first."""
        matches = [v for v in self._versions.values() if v.dataset_id == dataset_id]
        return sorted(matches, key=lambda v: v.created_at, reverse=True)

    async def list_all_versions(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[DatasetVersion], int]:
        """Return dataset versions across every dataset, newest first, with a total count."""
        ordered = sorted(self._versions.values(), key=lambda v: v.created_at, reverse=True)
        return ordered[offset : offset + limit], len(ordered)


class FakeExperimentRepository(ExperimentRepository):
    """An in-memory experiment repository."""

    def __init__(self) -> None:
        """Initialise with an empty store."""
        self._by_id: dict[ExperimentId, Experiment] = {}

    async def add(self, experiment: Experiment) -> None:
        """Insert a new experiment."""
        self._by_id[experiment.id] = experiment

    async def update(self, experiment: Experiment) -> None:
        """Overwrite an existing experiment.

        Raises:
            EntityNotFoundError: If no such experiment exists.
        """
        if experiment.id not in self._by_id:
            raise EntityNotFoundError("Experiment", experiment.id)
        self._by_id[experiment.id] = experiment

    async def get(self, experiment_id: ExperimentId) -> Experiment:
        """Return an experiment by id.

        Raises:
            EntityNotFoundError: If no such experiment exists.
        """
        try:
            return self._by_id[experiment_id]
        except KeyError as exc:
            raise EntityNotFoundError("Experiment", experiment_id) from exc

    async def list_for_dataset_version(self, version_id: DatasetVersionId) -> list[Experiment]:
        """Return every run trained on a given dataset version."""
        return [e for e in self._by_id.values() if e.dataset_version_id == version_id]

    async def list_recent(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Experiment], int]:
        """Return training runs across every dataset version, newest first.

        Includes a total count.
        """
        ordered = sorted(self._by_id.values(), key=lambda e: e.started_at, reverse=True)
        return ordered[offset : offset + limit], len(ordered)


class FakeModelRepository(ModelRepository):
    """An in-memory model repository."""

    def __init__(self) -> None:
        """Initialise with empty stores."""
        self._by_id: dict[ModelVersionId, ModelVersion] = {}
        self._deployments: list[Deployment] = []

    async def add(self, model: ModelVersion) -> None:
        """Insert a new model version."""
        self._by_id[model.id] = model

    async def update(self, model: ModelVersion) -> None:
        """Overwrite an existing model version.

        Raises:
            EntityNotFoundError: If no such version exists.
        """
        if model.id not in self._by_id:
            raise EntityNotFoundError("ModelVersion", model.id)
        self._by_id[model.id] = model

    async def get(self, model_version_id: ModelVersionId) -> ModelVersion:
        """Return a model version by id.

        Raises:
            EntityNotFoundError: If no such version exists.
        """
        try:
            return self._by_id[model_version_id]
        except KeyError as exc:
            raise EntityNotFoundError("ModelVersion", model_version_id) from exc

    async def find_by_stage(self, category: Category, stage: ModelStage) -> ModelVersion | None:
        """Return the model currently occupying a stage for a category, newest first."""
        matches = sorted(
            (m for m in self._by_id.values() if m.category == category and m.stage is stage),
            key=lambda m: m.created_at,
            reverse=True,
        )
        return matches[0] if matches else None

    async def list_versions(self, category: Category) -> list[ModelVersion]:
        """Return every registered version for a category, newest first."""
        matches = [m for m in self._by_id.values() if m.category == category]
        return sorted(matches, key=lambda m: m.created_at, reverse=True)

    async def add_deployment(self, deployment: Deployment) -> None:
        """Append a deployment record."""
        self._deployments.append(deployment)

    async def list_deployments(
        self, category: Category, *, environment: str, limit: int = 50
    ) -> list[Deployment]:
        """Return deployment history for an environment, newest first."""
        matches = [
            d
            for d in self._deployments
            if d.environment == environment
            and self._by_id.get(d.model_version_id) is not None
            and self._by_id[d.model_version_id].category == category
        ]
        matches.sort(key=lambda d: d.deployed_at, reverse=True)
        return matches[:limit]


class FakePredictionRepository(PredictionRepository):
    """An in-memory prediction and feedback repository."""

    def __init__(self) -> None:
        """Initialise with empty stores."""
        self._by_id: dict[PredictionId, Prediction] = {}
        self._feedback: list[Feedback] = []

    async def add(self, prediction: Prediction) -> None:
        """Append a prediction."""
        self._by_id[prediction.id] = prediction

    async def add_many(self, predictions: list[Prediction]) -> None:
        """Append a batch of predictions."""
        for prediction in predictions:
            self._by_id[prediction.id] = prediction

    async def get(self, prediction_id: PredictionId) -> Prediction:
        """Return a prediction by id.

        Raises:
            EntityNotFoundError: If no such prediction exists.
        """
        try:
            return self._by_id[prediction_id]
        except KeyError as exc:
            raise EntityNotFoundError("Prediction", prediction_id) from exc

    async def list_in_window(
        self,
        model_version_id: ModelVersionId,
        *,
        start: datetime,
        end: datetime,
        limit: int | None = None,
    ) -> list[Prediction]:
        """Return predictions served in a time window."""
        matches = sorted(
            (
                p
                for p in self._by_id.values()
                if p.model_version_id == model_version_id and start <= p.predicted_at < end
            ),
            key=lambda p: p.predicted_at,
        )
        return matches[:limit] if limit is not None else matches

    async def add_feedback(self, feedback: Feedback) -> None:
        """Append operator feedback."""
        self._feedback.append(feedback)

    async def list_corrections(self, category: Category, *, since: datetime) -> list[Feedback]:
        """Return feedback that overturned a prediction, since a given time.

        Does not filter by ``category`` — this fake has no cross-repository access to the
        image behind a prediction to check it. No current test needs that precision; a
        real category filter belongs in an integration test against the SQL repository.
        """
        del category
        return [
            feedback
            for feedback in self._feedback
            if feedback.is_correction and feedback.created_at >= since
        ]

    async def list_recent(
        self,
        *,
        model_version_id: ModelVersionId | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Prediction], int]:
        """Return served predictions, newest first, with a total count.

        Optionally narrowed to one model version.
        """
        matches = [
            p
            for p in self._by_id.values()
            if model_version_id is None or p.model_version_id == model_version_id
        ]
        ordered = sorted(matches, key=lambda p: p.predicted_at, reverse=True)
        return ordered[offset : offset + limit], len(ordered)

    async def list_needing_feedback(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Prediction], int]:
        """Return predictions with no feedback recorded yet, newest first.

        Includes a total count.
        """
        reviewed = {feedback.prediction_id for feedback in self._feedback}
        matches = [p for p in self._by_id.values() if p.id not in reviewed]
        ordered = sorted(matches, key=lambda p: p.predicted_at, reverse=True)
        return ordered[offset : offset + limit], len(ordered)


class FakeDriftReportRepository(DriftReportRepository):
    """An in-memory drift report repository."""

    def __init__(self) -> None:
        """Initialise with an empty store."""
        self._reports: list[DriftReport] = []

    async def add(self, report: DriftReport) -> None:
        """Append a drift report."""
        self._reports.append(report)

    async def latest(self, model_version_id: ModelVersionId) -> DriftReport | None:
        """Return the most recent report for a model, if any exists."""
        matches = sorted(
            (r for r in self._reports if r.model_version_id == model_version_id),
            key=lambda r: r.created_at,
            reverse=True,
        )
        return matches[0] if matches else None

    async def list_recent(
        self,
        *,
        model_version_id: ModelVersionId | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DriftReport], int]:
        """Return drift reports, newest first, with a total count.

        Optionally narrowed to one model version.
        """
        matches = [
            r
            for r in self._reports
            if model_version_id is None or r.model_version_id == model_version_id
        ]
        ordered = sorted(matches, key=lambda r: r.created_at, reverse=True)
        return ordered[offset : offset + limit], len(ordered)


class FakeAuditRepository(AuditRepository):
    """An in-memory audit repository, enforcing the same chain rule the real one does."""

    def __init__(self) -> None:
        """Initialise with an empty chain."""
        self._events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        """Append an event, verifying it correctly extends the chain.

        Raises:
            InvariantViolationError: If ``event`` does not extend the current chain head
                — mirrors the real repository's chain-conflict check, without needing an
                advisory lock in a single-threaded fake.
        """
        expected_sequence = self._events[-1].sequence + 1 if self._events else 1
        expected_prev_hash = self._events[-1].row_hash() if self._events else GENESIS_HASH
        if int(event.sequence) != expected_sequence or event.prev_hash != expected_prev_hash:
            raise InvariantViolationError(
                "audit chain head moved before this event could be appended",
                code="audit.chain_conflict",
            )
        self._events.append(event)

    async def latest(self) -> AuditEvent | None:
        """Return the most recent event, if any exists."""
        return self._events[-1] if self._events else None

    async def list_for_resource(
        self, resource_type: str, resource_id: str, *, limit: int = 100
    ) -> list[AuditEvent]:
        """Return the audit trail for one entity, newest first."""
        matches = [
            e
            for e in self._events
            if e.resource_type == resource_type and e.resource_id == resource_id
        ]
        return list(reversed(matches))[:limit]

    async def list_all(self) -> list[AuditEvent]:
        """Return every event, oldest first."""
        return list(self._events)


class FakeUserRepository(UserRepository):
    """An in-memory user repository."""

    def __init__(self) -> None:
        """Initialise with an empty store."""
        self._by_id: dict[UserId, User] = {}
        self._password_hashes: dict[UserId, str] = {}

    async def add(self, user: User) -> None:
        """Insert a new user."""
        self._by_id[user.id] = user

    async def update(self, user: User) -> None:
        """Overwrite an existing user.

        Raises:
            EntityNotFoundError: If no such user exists.
        """
        if user.id not in self._by_id:
            raise EntityNotFoundError("User", user.id)
        self._by_id[user.id] = user

    async def get(self, user_id: UserId) -> User:
        """Return a user by id.

        Raises:
            EntityNotFoundError: If no such user exists.
        """
        try:
            return self._by_id[user_id]
        except KeyError as exc:
            raise EntityNotFoundError("User", user_id) from exc

    async def find_by_email(self, email: str) -> User | None:
        """Return a user by their email address, if one exists."""
        return next((u for u in self._by_id.values() if u.email == email), None)

    async def set_password_hash(self, user_id: UserId, password_hash: str) -> None:
        """Store a user's password hash.

        Raises:
            EntityNotFoundError: If no such user exists.
        """
        if user_id not in self._by_id:
            raise EntityNotFoundError("User", user_id)
        self._password_hashes[user_id] = password_hash

    async def get_password_hash(self, user_id: UserId) -> str | None:
        """Return a user's password hash, or ``None`` if one was never set.

        Raises:
            EntityNotFoundError: If no such user exists.
        """
        if user_id not in self._by_id:
            raise EntityNotFoundError("User", user_id)
        return self._password_hashes.get(user_id)


class FakeJobRepository(JobRepository):
    """An in-memory job repository."""

    def __init__(self) -> None:
        """Initialise with an empty store."""
        self._by_id: dict[JobId, Job] = {}

    async def add(self, job: Job) -> None:
        """Insert a new job.

        Raises:
            JobIdempotencyKeyExistsError: If the idempotency key is already in use.
        """
        if any(
            existing.idempotency_key == job.idempotency_key for existing in self._by_id.values()
        ):
            raise JobIdempotencyKeyExistsError(
                f"a job with idempotency key {job.idempotency_key!r} already exists",
                details={"idempotency_key": job.idempotency_key},
            )
        self._by_id[job.id] = job

    async def update(self, job: Job) -> None:
        """Overwrite an existing job.

        Raises:
            EntityNotFoundError: If no such job exists.
        """
        if job.id not in self._by_id:
            raise EntityNotFoundError("Job", job.id)
        self._by_id[job.id] = job

    async def get(self, job_id: JobId) -> Job:
        """Return a job by id.

        Raises:
            EntityNotFoundError: If no such job exists.
        """
        try:
            return self._by_id[job_id]
        except KeyError as exc:
            raise EntityNotFoundError("Job", job_id) from exc

    async def find_by_idempotency_key(self, idempotency_key: str) -> Job | None:
        """Return the job submitted with this key, if one exists."""
        return next(
            (job for job in self._by_id.values() if job.idempotency_key == idempotency_key), None
        )

    async def list_by_status(self, status: JobStatus, *, limit: int = 100) -> list[Job]:
        """Return jobs in a given status, oldest first."""
        matching = sorted(
            (job for job in self._by_id.values() if job.status is status),
            key=lambda job: job.created_at,
        )
        return matching[:limit]

    async def count_by_status(self) -> dict[str, int]:
        """Return job counts grouped by status, zero-filled for every status."""
        counts = {status.value: 0 for status in JobStatus}
        for job in self._by_id.values():
            counts[job.status.value] += 1
        return counts


class FakeTokenRevocationList(TokenRevocationList):
    """An in-memory revocation list."""

    def __init__(self) -> None:
        """Initialise with an empty set of revoked identifiers."""
        self._revoked: set[str] = set()

    async def revoke(self, jti: str, *, expires_at: datetime) -> None:
        """Blacklist a refresh token identifier."""
        self._revoked.add(jti)

    async def is_revoked(self, jti: str) -> bool:
        """Return whether a refresh token identifier has been revoked."""
        return jti in self._revoked


class FakeAnomalyDetector(AnomalyDetector):
    """A detector with a scripted, deterministic response — no real model is ever fitted.

    Attributes:
        fitted_requests: Every :class:`TrainingRequest` passed to :meth:`fit`, for
            assertions.
        loaded: Set once :meth:`load` succeeds; :meth:`predict` raises before that.
        raise_on_fit: When set, :meth:`fit` raises this instead of succeeding.
    """

    def __init__(
        self,
        *,
        family: str = "fake-family",
        backbone: str = "fake-backbone",
        trained_model: TrainedModel | None = None,
        prediction: RawPrediction | None = None,
    ) -> None:
        """Initialise with the family/backbone identity and the scripted results."""
        self._family = family
        self._backbone = backbone
        self._trained_model = trained_model or TrainedModel(
            artifact_path=Path("fake-model.ckpt"),
            threshold=0.5,
            metrics=EvaluationMetrics(
                image_auroc=0.99, precision=0.95, recall=0.95, f1=0.95, threshold=0.5
            ),
            training_time_seconds=1.0,
        )
        self._prediction = prediction or RawPrediction(
            score=AnomalyScore(value=0.1, threshold=0.5), inference_time_ms=1.0
        )
        self.fitted_requests: list[TrainingRequest] = []
        self.loaded = False
        self.raise_on_fit: Exception | None = None

    @property
    def family(self) -> str:
        """Return the scripted family name."""
        return self._family

    @property
    def backbone(self) -> str:
        """Return the scripted backbone name."""
        return self._backbone

    def fit(self, request: TrainingRequest) -> TrainedModel:
        """Record the request and return the scripted result, or raise :attr:`raise_on_fit`."""
        self.fitted_requests.append(request)
        if self.raise_on_fit is not None:
            raise self.raise_on_fit
        return self._trained_model

    def load(self, artifact_path: Path, *, threshold: float) -> None:
        """Mark this fake as loaded — no file is actually read."""
        del artifact_path, threshold
        self.loaded = True

    def predict(self, image: bytes) -> RawPrediction:
        """Return the scripted prediction.

        Raises:
            DetectorNotLoadedError: If :meth:`load` has not been called.
        """
        del image
        if not self.loaded:
            raise DetectorNotLoadedError("predict called before load")
        return self._prediction

    def predict_batch(self, images: list[bytes]) -> list[RawPrediction]:
        """Return the scripted prediction once per image."""
        return [self.predict(image) for image in images]


class FakeExperimentTracker(ExperimentTracker):
    """An in-memory experiment tracker — no real MLflow server is contacted."""

    def __init__(self) -> None:
        """Initialise with empty run state."""
        self.runs: dict[str, dict[str, Any]] = {}
        self._next_id = 1

    def start_run(self, *, experiment_name: str, run_name: str) -> str:
        """Begin a run and return a fake, incrementing run id."""
        run_id = f"fake-run-{self._next_id}"
        self._next_id += 1
        self.runs[run_id] = {
            "experiment_name": experiment_name,
            "run_name": run_name,
            "params": {},
            "metrics": {},
            "artifacts": [],
            "status": "RUNNING",
        }
        return run_id

    def log_params(self, run_id: str, params: dict[str, Any]) -> None:
        """Record params against the run."""
        self.runs[run_id]["params"].update(params)

    def log_metrics(
        self, run_id: str, metrics: dict[str, float], *, step: int | None = None
    ) -> None:
        """Record metrics against the run."""
        del step
        self.runs[run_id]["metrics"].update(metrics)

    def log_evaluation(self, run_id: str, metrics: EvaluationMetrics) -> None:
        """Record an evaluation's scalar fields as metrics."""
        payload = metrics.to_dict()
        payload.pop("confusion_matrix", None)
        self.log_metrics(run_id, payload)

    def log_artifact(self, run_id: str, path: Path, *, artifact_path: str | None = None) -> None:
        """Record that an artifact was logged, without touching the filesystem."""
        self.runs[run_id]["artifacts"].append((path, artifact_path))

    def end_run(self, run_id: str, *, status: str = "FINISHED") -> None:
        """Mark the run's terminal status."""
        self.runs[run_id]["status"] = status


class FakeModelRegistry(ModelRegistry):
    """An in-memory model registry — no real MLflow server is contacted."""

    def __init__(self) -> None:
        """Initialise with an empty registry."""
        self._versions: dict[str, list[int]] = {}
        self._stages: dict[tuple[str, int], ModelStage] = {}
        self._locations: dict[tuple[str, int], StorageLocation] = {}

    def register(
        self,
        *,
        name: str,
        run_id: str,
        artifact_path: Path,
        tags: dict[str, str] | None = None,
    ) -> int:
        """Assign the next version number for ``name`` and record it as unassigned."""
        del run_id, tags
        version = len(self._versions.setdefault(name, [])) + 1
        self._versions[name].append(version)
        self._stages[(name, version)] = ModelStage.DEVELOPMENT
        self._locations[(name, version)] = StorageLocation(
            "factoryai-artifacts", f"{name}/{version}/{artifact_path.name}"
        )
        return version

    def transition_stage(self, *, name: str, version: int, stage: ModelStage) -> None:
        """Move a registered version to a lifecycle stage."""
        self._stages[(name, version)] = stage

    def download(self, *, name: str, version: int, destination: Path) -> Path:
        """Write a placeholder file inside ``destination`` and return its path.

        Mirrors the real ``MlflowModelRegistry.download``'s actual contract:
        ``destination`` is a directory the artifact is materialised *into*, not the
        target file path itself — the real adapter's ``mlflow.artifacts.
        download_artifacts(dst_path=...)`` call treats it the same way.
        """
        del name, version
        destination.mkdir(parents=True, exist_ok=True)
        artifact_path = destination / "fake-artifact.bin"
        artifact_path.write_bytes(b"fake-artifact")
        return artifact_path

    def get_stage_version(self, *, name: str, stage: ModelStage) -> int | None:
        """Return the version currently occupying a stage, if any."""
        matches = [
            version
            for (registered_name, version), current in self._stages.items()
            if registered_name == name and current is stage
        ]
        return max(matches) if matches else None

    def list_versions(self, *, name: str) -> list[int]:
        """Return every registered version number, ascending."""
        return sorted(self._versions.get(name, []))

    def registry_name_for(self, category: Category) -> str:
        """Return one registry name per category."""
        return f"factoryai-{category.code}"

    def resolve_artifact_location(self, *, name: str, version: int) -> StorageLocation:
        """Return the scripted location recorded at :meth:`register` time.

        Raises:
            EntityNotFoundError: If no such version was ever registered.
        """
        try:
            return self._locations[(name, version)]
        except KeyError as exc:
            raise EntityNotFoundError("ModelRegistryVersion", f"{name}/{version}") from exc


class FakeUnitOfWork(UnitOfWork):
    """An in-memory unit of work: no real transaction, writes land immediately.

    Attributes:
        fail_on_commit: When set to an exception instance, :meth:`commit` raises it
            instead of succeeding — for testing a use case's response to a failed write
            (e.g. the compensating delete in
            :class:`~factoryai.application.use_cases.ingest_image.IngestImage`).
    """

    def __init__(self) -> None:
        """Initialise with fresh, empty fake repositories."""
        self.images = FakeImageRepository()
        self.datasets = FakeDatasetRepository()
        self.experiments = FakeExperimentRepository()
        self.models = FakeModelRepository()
        self.predictions = FakePredictionRepository()
        self.drift_reports = FakeDriftReportRepository()
        self.audit = FakeAuditRepository()
        self.users = FakeUserRepository()
        self.jobs = FakeJobRepository()
        self.fail_on_commit: Exception | None = None
        self.committed = False

    async def __aenter__(self) -> Self:
        """Return self; a fake has nothing to open."""
        self.committed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """No-op: a fake has no connection to close."""

    async def commit(self) -> None:
        """Mark this transaction committed, or raise :attr:`fail_on_commit` if set."""
        if self.fail_on_commit is not None:
            raise self.fail_on_commit
        self.committed = True

    async def rollback(self) -> None:
        """Mark this transaction not committed.

        Real rollback semantics are out of scope for this fake — writes already landed in
        the fake repositories immediately, since there is no staging area to discard.
        """
        self.committed = False
