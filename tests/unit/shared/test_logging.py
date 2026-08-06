"""Tests for structured logging configuration."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from factoryai.shared.correlation import correlation_scope
from factoryai.shared.logging import _NOISY_LIBRARIES, LogFormat, configure_logging, get_logger

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_structlog_after_each_test() -> Iterator[None]:
    """Prevent one test's logging configuration from leaking into the next."""
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers = []


def _emit(
    capsys: pytest.CaptureFixture[str],
    *,
    log_format: LogFormat = "json",
    level: str = "INFO",
    service: str = "factoryai",
    environment: str = "local",
) -> dict[str, Any]:
    """Configure logging, emit one record, and return it parsed as JSON."""
    configure_logging(level=level, log_format=log_format, service=service, environment=environment)
    logger = get_logger("factoryai.test")
    logger.info("something_happened", widget="gizmo")
    captured = capsys.readouterr().out.strip()
    result: dict[str, Any] = json.loads(captured.splitlines()[-1])
    return result


class TestJsonFormat:
    def test_emits_one_json_object_per_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        record = _emit(capsys, log_format="json")
        assert record["event"] == "something_happened"
        assert record["widget"] == "gizmo"

    def test_includes_the_log_level(self, capsys: pytest.CaptureFixture[str]) -> None:
        record = _emit(capsys, log_format="json")
        assert record["level"] == "info"

    def test_includes_service_and_environment(self, capsys: pytest.CaptureFixture[str]) -> None:
        record = _emit(capsys, log_format="json", service="api", environment="staging")
        assert record["service"] == "api"
        assert record["environment"] == "staging"

    def test_includes_a_timestamp(self, capsys: pytest.CaptureFixture[str]) -> None:
        record = _emit(capsys, log_format="json")
        assert "timestamp" in record

    def test_includes_the_logger_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        record = _emit(capsys, log_format="json")
        assert record["logger"] == "factoryai.test"


class TestCorrelationId:
    def test_defaults_to_the_unset_placeholder(self, capsys: pytest.CaptureFixture[str]) -> None:
        record = _emit(capsys, log_format="json")
        assert record["correlation_id"] == "-"

    def test_picks_up_the_bound_correlation_id(self, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging(log_format="json")
        logger = get_logger("factoryai.test")
        with correlation_scope("req-42"):
            logger.info("event_inside_scope")
        record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert record["correlation_id"] == "req-42"


class TestBoundFields:
    def test_get_logger_binds_initial_values(self, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging(log_format="json")
        logger = get_logger("factoryai.test", category="bottle")
        logger.info("image_ingested")
        record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert record["category"] == "bottle"

    def test_get_logger_without_initial_values_binds_nothing_extra(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(log_format="json")
        logger = get_logger("factoryai.test")
        logger.info("bare_event")
        record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert "category" not in record


class TestLevelFiltering:
    def test_records_below_the_configured_level_are_suppressed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(log_format="json", level="WARNING")
        logger = get_logger("factoryai.test")
        logger.info("should_not_appear")
        logger.warning("should_appear")
        output = capsys.readouterr().out.strip()
        lines = [json.loads(line) for line in output.splitlines()]
        events = [line["event"] for line in lines]
        assert "should_not_appear" not in events
        assert "should_appear" in events


class TestNoisyLibraries:
    def test_third_party_loggers_are_raised_to_warning(self) -> None:
        configure_logging(log_format="json", level="DEBUG")
        for name in _NOISY_LIBRARIES:
            assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING

    def test_root_logger_respects_the_configured_level(self) -> None:
        configure_logging(log_format="json", level="DEBUG")
        assert logging.getLogger().level == logging.DEBUG


class TestConsoleFormat:
    def test_console_format_is_not_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging(log_format="console")
        logger = get_logger("factoryai.test")
        logger.info("human_readable_event")
        output = capsys.readouterr().out
        assert "human_readable_event" in output
        with pytest.raises(json.JSONDecodeError):
            json.loads(output.strip().splitlines()[-1])
