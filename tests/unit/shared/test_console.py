"""Unit tests for console I/O fix-ups."""

from __future__ import annotations

import io

import pytest

from factoryai.shared.console import configure_stdio_encoding

pytestmark = pytest.mark.unit


class TestConfigureStdioEncoding:
    def test_reconfigures_streams_that_support_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.setattr("sys.stderr", stderr)

        configure_stdio_encoding()

        assert stdout.encoding.lower() == "utf-8"
        assert stderr.encoding.lower() == "utf-8"

    def test_is_a_no_op_for_a_stream_without_reconfigure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _NoReconfigure:
            """A stream-like object with no `reconfigure` method, e.g. some redirects."""

        monkeypatch.setattr("sys.stdout", _NoReconfigure())

        configure_stdio_encoding()  # must not raise
