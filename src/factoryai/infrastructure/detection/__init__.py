"""Adapters for the :class:`~factoryai.domain.ports.detection.AnomalyDetector` port.

Importing this package is what registers every Anomalib-backed detector (ADR-0002) — the
container imports it lazily, once, right before the first lookup by name, so that commands
which never train a model never pay Anomalib's import cost.
"""

from factoryai.infrastructure.detection.anomalib_adapter import (
    FastflowDetector,
    PadimDetector,
    PatchcoreDetector,
    ReverseDistillationDetector,
)
from factoryai.infrastructure.detection.autoencoder_adapter import AutoencoderDetector

__all__ = [
    "AutoencoderDetector",
    "FastflowDetector",
    "PadimDetector",
    "PatchcoreDetector",
    "ReverseDistillationDetector",
]
