"""The inference use case: score a live image against the current production model.

Deliberately not :class:`~factoryai.application.use_cases.ingest_image.IngestImage` reused
under another name: ingestion's validation chain and duplicate detection are training-data
concerns. An inference request must always be scored — a camera feed photographing the
same nominal product repeatedly is normal operation, not a duplicate to reject. The image
is still persisted (every prediction is retained for drift analysis, Phase 11), just with
none of ingestion's gatekeeping and left in ``PENDING`` status, which is what already keeps
it out of :meth:`~factoryai.domain.ports.repositories.ImageRepository.list_trainable`
without a new status value.

"Always scored" does not mean "always a new image row", though: ``images.checksum_sha256``
carries a real uniqueness constraint (Phase 2's exact-duplicate guarantee), and the same
physical product photographed twice — or, as this phase's live verification found, an
MVTec file already sitting in the training set submitted for inference — produces
identical bytes. A second ``Prediction`` for content already on file must reference the
existing ``InspectionImage`` row, not attempt a second insert that violates the constraint
ingestion relies on. Content-addressing, not request-addressing, is what makes both true
at once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from factoryai.application.services.model_cache import ModelCache
from factoryai.domain.entities import (
    AuditEvent,
    Experiment,
    InspectionImage,
    ModelVersion,
    Prediction,
)
from factoryai.domain.entities.audit import GENESIS_HASH
from factoryai.domain.errors import NoProductionModelError
from factoryai.domain.ports.imaging import ImageCodec
from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.domain.ports.services import Clock, IdGenerator
from factoryai.domain.ports.storage import ObjectStore
from factoryai.domain.value_objects import (
    AuditSequence,
    Category,
    Checksum,
    DatasetVersionId,
    ImageId,
    ModelStage,
    ModelVersionId,
    PredictionId,
    StorageLocation,
)

_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "BMP": "bmp", "TIFF": "tiff"}
_CONTENT_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}


@dataclass(frozen=True, slots=True)
class PredictImageCommand:
    """One image to score.

    Attributes:
        category: Which production model should judge this image.
        payload: Raw file bytes, exactly as uploaded.
        correlation_id: Ties this prediction to the request that produced it.
        metadata: Free-form provenance to attach to the stored image (camera id, line id).
    """

    category: Category
    payload: bytes
    correlation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PredictImageResult:
    """The outcome of scoring one image.

    Attributes:
        prediction_id: The persisted prediction's identifier.
        image_id: The persisted image's identifier.
        anomaly_score: The raw score, higher means more anomalous.
        threshold: The decision boundary the score was judged against.
        is_anomalous: The verdict.
        confidence: Decision certainty in ``[0, 1]``.
        inference_time_ms: Wall-clock duration of the forward pass.
        model_version_id: Which model served this prediction.
        dataset_version_id: The data that model was trained on.
        heatmap_location: Where the anomaly heatmap was stored, if the model localises.
        correlation_id: Echoes :attr:`PredictImageCommand.correlation_id`.
    """

    prediction_id: PredictionId
    image_id: ImageId
    anomaly_score: float
    threshold: float
    is_anomalous: bool
    confidence: float
    inference_time_ms: float
    model_version_id: ModelVersionId
    dataset_version_id: DatasetVersionId
    heatmap_location: StorageLocation | None
    correlation_id: str | None


class PredictImage:
    """Scores one or more images against the current production model for a category."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        object_store: ObjectStore,
        image_codec: ImageCodec,
        model_cache: ModelCache,
        clock: Clock,
        id_generator: IdGenerator,
        raw_bucket: str,
        heatmap_bucket: str,
    ) -> None:
        """Initialise with every collaborator this use case needs.

        Args:
            uow_factory: Builds a fresh unit of work per call.
            object_store: Where the uploaded image and any heatmap are written.
            image_codec: Decodes bytes to structural metadata (resolution, format).
            model_cache: Serves a warmed, ready-to-predict detector per category.
            clock: Source of "now", for the prediction and image timestamps.
            id_generator: Source of new image and prediction identifiers.
            raw_bucket: Bucket inference images are written to.
            heatmap_bucket: Bucket anomaly heatmaps are written to.
        """
        self._uow_factory = uow_factory
        self._object_store = object_store
        self._image_codec = image_codec
        self._model_cache = model_cache
        self._clock = clock
        self._id_generator = id_generator
        self._raw_bucket = raw_bucket
        self._heatmap_bucket = heatmap_bucket

    async def execute(self, command: PredictImageCommand) -> PredictImageResult:
        """Score one image.

        Raises:
            NoProductionModelError: If the category has no model in the production stage.
            CorruptImageError: If ``command.payload`` cannot be decoded as an image.
        """
        return (await self.execute_batch([command]))[0]

    async def execute_batch(self, commands: list[PredictImageCommand]) -> list[PredictImageResult]:
        """Score several images together, sharing one detector forward pass.

        Every command must share the same category — batching across categories would
        defeat the point, since each needs its own detector.

        Raises:
            NoProductionModelError: If the category has no model in the production stage.
            CorruptImageError: If any payload cannot be decoded as an image.
            ValueError: If ``commands`` is empty or spans more than one category.
        """
        if not commands:
            raise ValueError("at least one image is required")
        categories = {command.category for command in commands}
        if len(categories) > 1:
            raise ValueError(f"a batch must share one category, got {categories}")
        category = commands[0].category

        now = self._clock.now()
        checksums = [Checksum.from_bytes(command.payload) for command in commands]

        model_version, experiment, resolved = await self._resolve(
            category, commands, checksums, now
        )
        detector = await self._model_cache.get(
            category,
            model_version_id=model_version.id,
            registry_name=model_version.registry_name,
            registry_version=model_version.registry_version,
            threshold=model_version.threshold,
            model_family=experiment.model_family,
            backbone=experiment.backbone,
        )

        for command, (image, is_new) in zip(commands, resolved, strict=True):
            if is_new:
                await self._object_store.put(
                    image.location, command.payload, content_type=_content_type_for(image)
                )

        raw_predictions = await asyncio.to_thread(
            detector.predict_batch, [command.payload for command in commands]
        )

        results = []
        async with self._uow_factory() as uow:
            triples = zip(commands, resolved, raw_predictions, strict=True)
            for command, (image, is_new), raw in triples:
                if is_new:
                    await uow.images.add(image)
                heatmap_location = await self._store_heatmap(image.id, category, raw.anomaly_map)
                prediction = Prediction(
                    id=PredictionId(self._id_generator.new_id()),
                    image_id=image.id,
                    model_version_id=model_version.id,
                    dataset_version_id=experiment.dataset_version_id,
                    score=raw.score,
                    inference_time_ms=raw.inference_time_ms,
                    predicted_at=now,
                    heatmap_location=heatmap_location,
                    correlation_id=command.correlation_id,
                )
                await uow.predictions.add(prediction)
                await self._append_audit_event(uow, prediction, command, now)
                results.append(
                    PredictImageResult(
                        prediction_id=prediction.id,
                        image_id=image.id,
                        anomaly_score=prediction.score.value,
                        threshold=prediction.score.threshold,
                        is_anomalous=prediction.is_anomalous,
                        confidence=prediction.confidence,
                        inference_time_ms=prediction.inference_time_ms,
                        model_version_id=model_version.id,
                        dataset_version_id=prediction.dataset_version_id,
                        heatmap_location=heatmap_location,
                        correlation_id=command.correlation_id,
                    )
                )
            await uow.commit()
        return results

    async def _resolve(
        self,
        category: Category,
        commands: list[PredictImageCommand],
        checksums: list[Checksum],
        now: datetime,
    ) -> tuple[ModelVersion, Experiment, list[tuple[InspectionImage, bool]]]:
        """Resolve the production model and every command's image, in one transaction.

        Each image is either an existing row (found by content checksum — ``is_new`` is
        ``False``, nothing more is written for it) or a freshly built, not-yet-persisted
        entity (``is_new`` is ``True``, for the caller to store and insert).

        Raises:
            NoProductionModelError: If the category has no model in the production stage.
            CorruptImageError: If any payload cannot be decoded as an image.
        """
        async with self._uow_factory() as uow:
            model_version = await uow.models.find_by_stage(category, ModelStage.PRODUCTION)
            if model_version is None:
                raise NoProductionModelError(
                    f"category {category.code!r} has no production model",
                    details={"category": category.code},
                )
            experiment = await uow.experiments.get(model_version.experiment_id)

            resolved: list[tuple[InspectionImage, bool]] = []
            # Two commands in the same batch can carry identical bytes (a repeated shot
            # of the same product): the second must resolve to the *first's* new image,
            # not build a second one — that would insert two rows with the same checksum
            # in the same transaction, the exact constraint violation this method exists
            # to avoid.
            pending_by_checksum: dict[Checksum, InspectionImage] = {}
            for command, checksum in zip(commands, checksums, strict=True):
                existing = await uow.images.find_by_checksum(checksum)
                if existing is not None:
                    resolved.append((existing, False))
                elif checksum in pending_by_checksum:
                    resolved.append((pending_by_checksum[checksum], False))
                else:
                    new_image = self._build_image(command, checksum, now)
                    pending_by_checksum[checksum] = new_image
                    resolved.append((new_image, True))
        return model_version, experiment, resolved

    def _build_image(
        self, command: PredictImageCommand, checksum: Checksum, now: datetime
    ) -> InspectionImage:
        """Decode one payload and build the (not-yet-stored) image entity for it.

        Raises:
            CorruptImageError: If the payload cannot be decoded as an image.
        """
        decoded = self._image_codec.decode(command.payload)
        extension = _EXTENSIONS.get(decoded.image_format, decoded.image_format.lower())
        location = StorageLocation(
            self._raw_bucket,
            f"inference/{command.category.code}/{now:%Y}/{now:%m}/{checksum.value}.{extension}",
        )
        return InspectionImage(
            id=ImageId(self._id_generator.new_id()),
            category=command.category,
            checksum=checksum,
            resolution=decoded.resolution,
            size_bytes=len(command.payload),
            location=location,
            uploaded_at=now,
            metadata={
                **command.metadata,
                "source": "inference",
                "color_mode": decoded.color_mode,
                "format": decoded.image_format,
            },
        )

    async def _store_heatmap(
        self, image_id: ImageId, category: Category, anomaly_map: bytes | None
    ) -> StorageLocation | None:
        """Write an anomaly heatmap to object storage, if the detector produced one."""
        if anomaly_map is None:
            return None
        location = StorageLocation(self._heatmap_bucket, f"{category.code}/{image_id}.png")
        await self._object_store.put(location, anomaly_map, content_type="image/png")
        return location

    async def _append_audit_event(
        self,
        uow: UnitOfWork,
        prediction: Prediction,
        command: PredictImageCommand,
        now: datetime,
    ) -> None:
        """Append the audit record for one served prediction."""
        latest = await uow.audit.latest()
        event = AuditEvent(
            sequence=AuditSequence((latest.sequence + 1) if latest else 1),
            action="prediction.served",
            resource_type="prediction",
            resource_id=str(prediction.id),
            occurred_at=now,
            prev_hash=latest.row_hash() if latest else GENESIS_HASH,
            payload={
                "category": command.category.code,
                "model_version_id": str(prediction.model_version_id),
                "is_anomalous": prediction.is_anomalous,
            },
            correlation_id=command.correlation_id,
        )
        await uow.audit.append(event)


def _content_type_for(image: InspectionImage) -> str | None:
    """Return a MIME type for a decoded image's format, or ``None`` if unrecognised."""
    return _CONTENT_TYPES.get(str(image.metadata.get("format", "")).upper())
