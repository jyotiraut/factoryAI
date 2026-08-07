"""Anomalib-backed anomaly detectors (ADR-0002).

Anomalib's API has broken between major versions, so every family lives behind one base
class in this one file — the only place in the codebase that touches Anomalib directly.
Subclasses fix which `AnomalyModule` and default backbone they wrap; everything else
(staging data through `Folder`, running `Engine`, computing the metrics Anomalib does not
provide out of the box, reading the calibrated threshold, saving/loading a checkpoint, and
running inference on raw bytes) is identical across families and lives here once.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any, ClassVar, cast

import numpy as np
import torch
import torchvision.transforms.v2.functional as tvf
from anomalib import TaskType
from anomalib.data import Folder
from anomalib.data.utils import ValSplitMode
from anomalib.engine import Engine
from anomalib.models import Fastflow, Padim, Patchcore, ReverseDistillation
from anomalib.models.components import AnomalyModule
from PIL import Image
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score

from factoryai.domain.entities import EvaluationMetrics
from factoryai.domain.ports.detection import (
    AnomalyDetector,
    DetectorNotLoadedError,
    RawPrediction,
    TrainedModel,
    TrainingRequest,
    register_detector,
)
from factoryai.domain.value_objects import AnomalyScore
from factoryai.shared.errors import InfrastructureError

_ACCELERATOR_FOR_DEVICE: dict[str, str] = {"auto": "auto", "cpu": "cpu", "cuda": "gpu"}
"""Lightning's accelerator names differ from ``TrainingRequest.device``'s: it is "gpu",
never "cuda"."""


class AnomalibDetector(AnomalyDetector):
    """Base wrapper around one Anomalib model family."""

    _MODEL_CLASS: ClassVar[type[AnomalyModule]]
    _FAMILY: ClassVar[str]
    _DEFAULT_BACKBONE: ClassVar[str]

    def __init__(self, backbone: str | None = None) -> None:
        """Initialise with a backbone override, or the family's own default."""
        self._backbone = backbone or self._DEFAULT_BACKBONE
        self._model: AnomalyModule | None = None
        self._threshold: float | None = None

    @property
    def family(self) -> str:
        """Return the registered family name."""
        return self._FAMILY

    @property
    def backbone(self) -> str:
        """Return the feature extractor in use."""
        return self._backbone

    def _build_model(self, hyperparameters: dict[str, Any]) -> AnomalyModule:
        """Construct this family's Anomalib model from hyperparameters. Overridden per family."""
        raise NotImplementedError

    def extra_details(self, model: AnomalyModule) -> dict[str, Any]:
        """Return family-specific detail for :attr:`TrainedModel.extra`. Overridden where useful."""
        del model
        return {}

    def fit(self, request: TrainingRequest) -> TrainedModel:
        """Fit via Anomalib's ``Engine``, then compute the metrics Anomalib does not.

        Anomalib's own metrics cover AUROC (image- and, with masks, pixel-level); it does
        not compute precision, recall or a confusion matrix at the calibrated threshold
        (its ``torchmetrics`` fallback silently drops them for this torchmetrics version),
        so those are computed here directly from raw scores against ``request.test_dir``.

        Raises:
            InfrastructureError: If the Anomalib training loop itself fails.
        """
        started = time.monotonic()
        has_masks = request.ground_truth_dir is not None
        task = TaskType.SEGMENTATION if has_masks else TaskType.CLASSIFICATION
        datamodule = Folder(
            name="factoryai",
            root=request.train_dir.parent,
            normal_dir=request.train_dir,
            abnormal_dir=request.test_dir / "defect",
            normal_test_dir=request.test_dir / "good",
            mask_dir=request.ground_truth_dir,
            task=task,
            image_size=request.image_size,
            train_batch_size=32,
            eval_batch_size=32,
            num_workers=0,
            # The full test_dir is what "held-out evaluation" means here; Folder's own
            # default (FROM_TEST) would silently siphon half of it into a validation
            # split used only for threshold calibration.
            val_split_mode=ValSplitMode.SAME_AS_TEST,
            seed=request.seed,
        )
        model = self._build_model(request.hyperparameters)
        engine = Engine(
            task=task,
            accelerator=_ACCELERATOR_FOR_DEVICE[request.device],
            devices=1,
            default_root_dir=str(request.train_dir.parent / "engine"),
            image_metrics=["AUROC"],
            pixel_metrics=["AUROC"] if has_masks else [],
        )
        try:
            engine.fit(model=model, datamodule=datamodule)
            test_results = engine.test(model=model, datamodule=datamodule)
        except Exception as exc:  # Anomalib/Lightning raise all sorts for OOM, bad data, etc.
            raise InfrastructureError(f"training failed for {self._FAMILY}: {exc}") from exc
        training_time_seconds = time.monotonic() - started

        model.eval()
        self._model = model
        # anomalib's own attribute typing resolves `image_threshold` too loosely for mypy
        # to follow (a torchmetrics Metric subclass, not a plain Tensor).
        threshold = float(model.image_threshold.value.item())  # type: ignore[operator]
        self._threshold = threshold

        result_row = test_results[0] if test_results else {}
        image_auroc = float(result_row.get("image_AUROC", float("nan")))
        pixel_auroc = float(result_row["pixel_AUROC"]) if "pixel_AUROC" in result_row else None

        metrics = self._evaluate_at_threshold(
            request.test_dir, threshold, image_auroc=image_auroc, pixel_auroc=pixel_auroc
        )
        checkpoint_callback = engine.checkpoint_callback
        assert checkpoint_callback is not None  # Engine always attaches one by default
        artifact_path = Path(checkpoint_callback.best_model_path)
        return TrainedModel(
            artifact_path=artifact_path,
            threshold=threshold,
            metrics=metrics,
            training_time_seconds=training_time_seconds,
            extra=self.extra_details(model),
        )

    def _evaluate_at_threshold(
        self, test_dir: Path, threshold: float, *, image_auroc: float, pixel_auroc: float | None
    ) -> EvaluationMetrics:
        """Score every held-out image and derive precision/recall/F1/confusion matrix."""
        scores: list[float] = []
        true_labels: list[int] = []
        for directory, label in ((test_dir / "good", 0), (test_dir / "defect", 1)):
            payloads = [path.read_bytes() for path in sorted(directory.glob("*")) if path.is_file()]
            scores.extend(prediction.score.value for prediction in self.predict_batch(payloads))
            true_labels.extend([label] * len(payloads))

        predicted_labels = [1 if score >= threshold else 0 for score in scores]
        true_negative, false_positive, false_negative, true_positive = confusion_matrix(
            true_labels, predicted_labels, labels=[0, 1]
        ).ravel()
        return EvaluationMetrics(
            image_auroc=image_auroc,
            precision=float(precision_score(true_labels, predicted_labels, zero_division=0)),
            recall=float(recall_score(true_labels, predicted_labels, zero_division=0)),
            f1=float(f1_score(true_labels, predicted_labels, zero_division=0)),
            threshold=threshold,
            pixel_auroc=pixel_auroc,
            confusion_matrix=(
                int(true_negative),
                int(false_positive),
                int(false_negative),
                int(true_positive),
            ),
        )

    def load(self, artifact_path: Path, *, threshold: float) -> None:
        """Load a checkpoint saved by :meth:`fit` for inference only.

        ``weights_only=False`` is required because the checkpoint embeds its
        preprocessing transform (a ``torchvision.transforms.v2.Compose``), not just
        tensors — safe because this only ever loads a checkpoint this adapter itself
        produced (ADR-0002's Anomalib pin), never an untrusted file.
        """
        self._model = self._MODEL_CLASS.load_from_checkpoint(str(artifact_path), weights_only=False)
        self._model.eval()
        self._threshold = threshold

    def predict(self, image: bytes) -> RawPrediction:
        """Score a single image.

        Raises:
            DetectorNotLoadedError: If :meth:`load` or :meth:`fit` has not been called.
        """
        return self.predict_batch([image])[0]

    def predict_batch(self, images: list[bytes]) -> list[RawPrediction]:
        """Score several images in one forward pass.

        Raises:
            DetectorNotLoadedError: If :meth:`load` or :meth:`fit` has not been called.
        """
        if self._model is None or self._threshold is None:
            raise DetectorNotLoadedError("predict called before fit or load")
        if not images:
            return []
        started = time.monotonic()
        batch = self._decode_batch(images)
        with torch.no_grad():
            output = self._model.model(batch)
        elapsed_ms_per_image = (time.monotonic() - started) * 1000 / len(images)

        scores = output["pred_score"]
        anomaly_maps = output.get("anomaly_map")
        predictions = []
        for index in range(len(images)):
            heatmap = (
                self._encode_heatmap(anomaly_maps[index]) if anomaly_maps is not None else None
            )
            score = AnomalyScore(value=float(scores[index].item()), threshold=self._threshold)
            predictions.append(
                RawPrediction(
                    score=score, inference_time_ms=elapsed_ms_per_image, anomaly_map=heatmap
                )
            )
        return predictions

    def _decode_batch(self, images: list[bytes]) -> torch.Tensor:
        """Decode and preprocess raw image bytes into the tensor batch the model expects."""
        assert self._model is not None  # narrows the type; predict_batch already checked
        tensors = [
            tvf.pil_to_tensor(Image.open(io.BytesIO(payload)).convert("RGB")).float() / 255.0
            for payload in images
        ]
        return cast(torch.Tensor, self._model.transform(torch.stack(tensors)))

    def _encode_heatmap(self, anomaly_map: torch.Tensor) -> bytes:
        """PNG-encode one sample's anomaly heatmap, normalised to 8-bit grayscale."""
        array = anomaly_map.squeeze().detach().cpu().numpy()
        normalised = (array - array.min()) / (array.max() - array.min() + 1e-8)
        buffer = io.BytesIO()
        Image.fromarray((normalised * 255).astype(np.uint8)).save(buffer, format="PNG")
        return buffer.getvalue()


@register_detector("patchcore")
class PatchcoreDetector(AnomalibDetector):
    """PatchCore: a memory bank of nominal patch embeddings (ADR-0002's default)."""

    _MODEL_CLASS = Patchcore
    _FAMILY = "patchcore"
    _DEFAULT_BACKBONE = "wide_resnet50_2"

    def _build_model(self, hyperparameters: dict[str, Any]) -> AnomalyModule:
        """Build a ``Patchcore`` model from this run's hyperparameters."""
        return Patchcore(
            backbone=self._backbone,
            layers=hyperparameters.get("layers", ["layer2", "layer3"]),
            coreset_sampling_ratio=hyperparameters.get("coreset_sampling_ratio", 0.1),
            num_neighbors=hyperparameters.get("num_neighbors", 9),
        )

    def extra_details(self, model: AnomalyModule) -> dict[str, Any]:
        """Return the memory bank's size — the parameter ADR-0002 flags as RAM/latency-driving."""
        # `model.model` is typed as a broad Module/Tensor/Size union in anomalib's own
        # annotations; `memory_bank` is a real registered buffer at runtime regardless.
        memory_bank = cast(torch.Tensor, model.model.memory_bank)
        return {
            "memory_bank_num_vectors": int(memory_bank.shape[0]),
            "memory_bank_feature_dim": int(memory_bank.shape[1]) if memory_bank.ndim > 1 else 0,
            "memory_bank_bytes": memory_bank.element_size() * memory_bank.nelement(),
        }


@register_detector("padim")
class PadimDetector(AnomalibDetector):
    """PaDiM: per-patch Gaussian modelling of nominal feature statistics."""

    _MODEL_CLASS = Padim
    _FAMILY = "padim"
    _DEFAULT_BACKBONE = "resnet18"

    def _build_model(self, hyperparameters: dict[str, Any]) -> AnomalyModule:
        """Build a ``Padim`` model from this run's hyperparameters."""
        return Padim(
            backbone=self._backbone,
            layers=hyperparameters.get("layers", ["layer1", "layer2", "layer3"]),
            n_features=hyperparameters.get("n_features"),
        )


@register_detector("fastflow")
class FastflowDetector(AnomalibDetector):
    """FastFlow: normalising flows over feature maps, trained by gradient descent.

    Unlike PatchCore/PaDiM, this trains for real over multiple epochs — Anomalib's own
    default epoch budget is used; wiring a configurable override is future work, tracked
    against real usage rather than guessed at (see ``docs/ROADMAP.md`` Phase 5).
    """

    _MODEL_CLASS = Fastflow
    _FAMILY = "fastflow"
    _DEFAULT_BACKBONE = "resnet18"

    def _build_model(self, hyperparameters: dict[str, Any]) -> AnomalyModule:
        """Build a ``Fastflow`` model from this run's hyperparameters."""
        return Fastflow(
            backbone=self._backbone,
            flow_steps=hyperparameters.get("flow_steps", 8),
            conv3x3_only=hyperparameters.get("conv3x3_only", False),
            hidden_ratio=hyperparameters.get("hidden_ratio", 1.0),
        )


@register_detector("reverse_distillation")
class ReverseDistillationDetector(AnomalibDetector):
    """Reverse Distillation: a teacher-student pair trained to disagree on anomalies."""

    _MODEL_CLASS = ReverseDistillation
    _FAMILY = "reverse_distillation"
    _DEFAULT_BACKBONE = "wide_resnet50_2"

    def _build_model(self, hyperparameters: dict[str, Any]) -> AnomalyModule:
        """Build a ``ReverseDistillation`` model from this run's hyperparameters."""
        return ReverseDistillation(
            backbone=self._backbone,
            layers=hyperparameters.get("layers", ["layer1", "layer2", "layer3"]),
        )
