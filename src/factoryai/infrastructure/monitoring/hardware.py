"""Hardware fingerprinting for training-run provenance.

An inference-time or training-time metric is meaningless without knowing what it ran on;
this is the one place that introspects the actual machine, behind the
:class:`~factoryai.domain.ports.services.HardwareProbe` port.
"""

from __future__ import annotations

import platform

import psutil
import torch

from factoryai.domain.entities import HardwareInfo
from factoryai.domain.ports.services import HardwareProbe

_BYTES_PER_GIB = 1024**3


class SystemHardwareProbe(HardwareProbe):
    """Reads CPU, memory and GPU details from the running process's own machine."""

    def capture(self) -> HardwareInfo:
        """Return a snapshot of the current machine's hardware.

        GPU fields stay ``None`` on a CPU-only machine or when CUDA is unavailable to this
        process, which is itself a meaningful fact about how a run was produced.
        """
        gpu_model, gpu_memory_gb, driver_version = self._gpu_info()
        return HardwareInfo(
            cpu_model=platform.processor() or platform.machine() or "unknown",
            cpu_count=psutil.cpu_count(logical=True) or 1,
            memory_gb=psutil.virtual_memory().total / _BYTES_PER_GIB,
            gpu_model=gpu_model,
            gpu_memory_gb=gpu_memory_gb,
            driver_version=driver_version,
        )

    def _gpu_info(self) -> tuple[str | None, float | None, str | None]:
        """Return ``(model, memory_gb, cuda_version)``, or all ``None`` without a GPU."""
        if not torch.cuda.is_available():
            return None, None, None
        device = 0
        properties = torch.cuda.get_device_properties(device)
        return (
            torch.cuda.get_device_name(device),
            properties.total_memory / _BYTES_PER_GIB,
            torch.version.cuda,
        )
