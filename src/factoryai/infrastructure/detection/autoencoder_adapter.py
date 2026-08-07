"""A lightweight, non-Anomalib autoencoder baseline (ADR-0002, option 2).

Registered alongside the Anomalib-backed families in the same plugin registry, and
deliberately owning every line itself with no Anomalib dependency at all — proof that the
`AnomalyDetector` port's pluggability is not only true for one library's models.
Reconstruction error is the anomaly score: a decoder trained to reproduce nominal images
reproduces a defect region poorly, which is exactly the "weak on subtle texture defects,
sensitive to threshold choice" trade-off ADR-0002 accepted for PatchCore over this option.
"""

from __future__ import annotations

import contextlib
import io
import math
import time
from pathlib import Path
from typing import Any, ClassVar, cast

import numpy as np
import torch
from PIL import Image
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
from torch import nn, optim

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

_DEFAULT_IMAGE_SIZE: tuple[int, int] = (256, 256)


class _ConvAutoencoder(nn.Module):
    """A small convolutional encoder-decoder; resolution-agnostic within reason."""

    def __init__(self, channels: int = 3, latent_channels: int = 32) -> None:
        """Build the network. Not part of the port — an implementation detail."""
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv2d(channels, 16, kernel_size=3, stride=2, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(16, latent_channels, kernel_size=3, stride=2, padding=1),
            nn.ReLU(inplace=True),
        )
        self.decoder = nn.Sequential(
            nn.ConvTranspose2d(
                latent_channels, 16, kernel_size=3, stride=2, padding=1, output_padding=1
            ),
            nn.ReLU(inplace=True),
            nn.ConvTranspose2d(16, channels, kernel_size=3, stride=2, padding=1, output_padding=1),
            nn.Sigmoid(),
        )

    def forward(self, batch: torch.Tensor) -> torch.Tensor:
        """Reconstruct ``batch``, resizing back to its exact input resolution if needed."""
        reconstructed: torch.Tensor = cast(torch.Tensor, self.decoder(self.encoder(batch)))
        if reconstructed.shape[-2:] != batch.shape[-2:]:
            reconstructed = nn.functional.interpolate(
                reconstructed, size=batch.shape[-2:], mode="bilinear", align_corners=False
            )
        return reconstructed


@register_detector("autoencoder")
class AutoencoderDetector(AnomalyDetector):
    """Reconstruction-error anomaly detection via a small conv autoencoder."""

    _FAMILY: ClassVar[str] = "autoencoder"
    _DEFAULT_BACKBONE: ClassVar[str] = "conv-autoencoder"

    def __init__(self, backbone: str | None = None) -> None:
        """Initialise with a backbone label override.

        This family has no real choice of feature extractor, so the label is descriptive
        only.
        """
        self._backbone = backbone or self._DEFAULT_BACKBONE
        self._model: _ConvAutoencoder | None = None
        self._threshold: float | None = None
        self._image_size: tuple[int, int] = _DEFAULT_IMAGE_SIZE

    @property
    def family(self) -> str:
        """Return the registered family name."""
        return self._FAMILY

    @property
    def backbone(self) -> str:
        """Return the backbone label this instance was constructed with."""
        return self._backbone

    def fit(self, request: TrainingRequest) -> TrainedModel:
        """Train the autoencoder on nominal images and calibrate a reconstruction-error threshold.

        Raises:
            InfrastructureError: If ``request.train_dir`` contains no images.
        """
        started = time.monotonic()
        self._image_size = request.image_size
        torch.manual_seed(request.seed)

        train_paths = sorted(path for path in request.train_dir.rglob("*") if path.is_file())
        if not train_paths:
            raise InfrastructureError(
                "autoencoder training requires at least one nominal training image",
                details={"train_dir": str(request.train_dir)},
            )
        train_batch = self._load_batch([path.read_bytes() for path in train_paths])

        model = _ConvAutoencoder()
        optimiser = optim.Adam(
            model.parameters(), lr=float(request.hyperparameters.get("learning_rate", 1e-3))
        )
        epochs = int(request.hyperparameters.get("epochs", 30))
        model.train()
        for _ in range(epochs):
            optimiser.zero_grad()
            loss = nn.functional.mse_loss(model(train_batch), train_batch)
            loss.backward()  # type: ignore[no-untyped-call]  # torch.Tensor.backward has no stub
            optimiser.step()
        training_time_seconds = time.monotonic() - started

        model.eval()
        self._model = model
        with torch.no_grad():
            train_errors = self._reconstruction_errors(model, train_batch).numpy()
        percentile = float(request.hyperparameters.get("threshold_percentile", 95))
        threshold = float(np.percentile(train_errors, percentile))
        self._threshold = threshold

        artifact_path = request.train_dir.parent / "autoencoder.pt"
        torch.save(
            {
                "state_dict": model.state_dict(),
                "threshold": threshold,
                "image_size": list(request.image_size),
            },
            artifact_path,
        )

        metrics = self._evaluate_at_threshold(request.test_dir, threshold)
        return TrainedModel(
            artifact_path=artifact_path,
            threshold=threshold,
            metrics=metrics,
            training_time_seconds=training_time_seconds,
            extra={"epochs": epochs, "threshold_percentile": percentile},
        )

    def _reconstruction_errors(self, model: _ConvAutoencoder, batch: torch.Tensor) -> torch.Tensor:
        """Return the per-sample mean squared reconstruction error."""
        reconstructed = model(batch)
        return torch.mean((reconstructed - batch) ** 2, dim=(1, 2, 3))

    def _evaluate_at_threshold(self, test_dir: Path, threshold: float) -> EvaluationMetrics:
        """Score every held-out image and derive AUROC, precision, recall, F1, confusion matrix."""
        scores: list[float] = []
        true_labels: list[int] = []
        for directory, label in ((test_dir / "good", 0), (test_dir / "defect", 1)):
            payloads = [path.read_bytes() for path in sorted(directory.glob("*")) if path.is_file()]
            scores.extend(prediction.score.value for prediction in self.predict_batch(payloads))
            true_labels.extend([label] * len(payloads))

        image_auroc = 0.5  # chance level: the honest answer when AUROC cannot be computed
        if len(set(true_labels)) > 1:
            with contextlib.suppress(ValueError):
                image_auroc = float(roc_auc_score(true_labels, scores))
        if not math.isfinite(image_auroc):
            image_auroc = 0.5

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
            confusion_matrix=(
                int(true_negative),
                int(false_positive),
                int(false_negative),
                int(true_positive),
            ),
        )

    def load(self, artifact_path: Path, *, threshold: float) -> None:
        """Load a checkpoint saved by :meth:`fit` for inference only."""
        checkpoint: dict[str, Any] = torch.load(
            artifact_path, map_location="cpu", weights_only=True
        )
        model = _ConvAutoencoder()
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        self._model = model
        self._threshold = threshold
        self._image_size = tuple(checkpoint.get("image_size", list(self._image_size)))

    def predict(self, image: bytes) -> RawPrediction:
        """Score a single image.

        Raises:
            DetectorNotLoadedError: If :meth:`load` or :meth:`fit` has not been called.
        """
        return self.predict_batch([image])[0]

    def predict_batch(self, images: list[bytes]) -> list[RawPrediction]:
        """Score several images in one forward pass.

        No anomaly map is produced — reconstruction error alone gives a single scalar per
        image, not a localisation signal, one of the trade-offs ADR-0002 weighed against
        PatchCore.

        Raises:
            DetectorNotLoadedError: If :meth:`load` or :meth:`fit` has not been called.
        """
        if self._model is None or self._threshold is None:
            raise DetectorNotLoadedError("predict called before fit or load")
        if not images:
            return []
        started = time.monotonic()
        batch = self._load_batch(images)
        with torch.no_grad():
            errors = self._reconstruction_errors(self._model, batch)
        elapsed_ms_per_image = (time.monotonic() - started) * 1000 / len(images)
        return [
            RawPrediction(
                score=AnomalyScore(value=float(errors[index].item()), threshold=self._threshold),
                inference_time_ms=elapsed_ms_per_image,
            )
            for index in range(len(images))
        ]

    def _load_batch(self, images: list[bytes]) -> torch.Tensor:
        """Decode raw image bytes into a normalised ``[0, 1]`` tensor batch."""
        tensors = []
        for payload in images:
            resized = Image.open(io.BytesIO(payload)).convert("RGB").resize(self._image_size)
            array = np.asarray(resized, dtype=np.float32) / 255.0
            tensors.append(torch.from_numpy(array).permute(2, 0, 1))
        return torch.stack(tensors)
