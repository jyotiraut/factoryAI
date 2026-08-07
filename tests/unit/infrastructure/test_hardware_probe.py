"""Unit tests for the real hardware fingerprint probe.

No mocking of ``psutil``/``torch``: this genuinely reads the machine running the test, and
asserts only properties true of any machine (positive core count, positive memory, a GPU
present only together with its memory and driver) — nothing here is Docker/network-bound,
so it belongs in the unit suite despite touching real system introspection.
"""

from __future__ import annotations

import pytest

from factoryai.infrastructure.monitoring.hardware import SystemHardwareProbe

pytestmark = pytest.mark.unit


class TestCapture:
    def test_returns_a_positive_cpu_count_and_memory(self) -> None:
        info = SystemHardwareProbe().capture()

        assert info.cpu_count >= 1
        assert info.memory_gb > 0
        assert info.cpu_model

    def test_gpu_fields_are_consistent(self) -> None:
        info = SystemHardwareProbe().capture()

        assert info.has_gpu == (info.gpu_model is not None)
        if not info.has_gpu:
            assert info.gpu_memory_gb is None
            assert info.driver_version is None

    def test_two_captures_report_the_same_machine(self) -> None:
        probe = SystemHardwareProbe()
        first, second = probe.capture(), probe.capture()

        assert first.cpu_model == second.cpu_model
        assert first.cpu_count == second.cpu_count
        assert first.has_gpu == second.has_gpu
