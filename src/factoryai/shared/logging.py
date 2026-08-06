"""Structured logging configuration.

Production emits one JSON object per line so that a log shipper can index fields without
regex parsing; local development emits colourised key-value output that a human can read.
Both formats carry the same fields, which is what makes a local reproduction of a
production incident meaningful.

Every record automatically includes the current correlation ID (see
:mod:`factoryai.shared.correlation`), the logger name, the level and a UTC timestamp.

Call :func:`configure_logging` exactly once per process, as early as possible.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Literal

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from factoryai.shared.correlation import get_correlation_id

LogFormat = Literal["json", "console"]

_NOISY_LIBRARIES = (
    "botocore",
    "boto3",
    "urllib3",
    "s3transfer",
    "asyncio",
    "multipart",
)


def _add_correlation_id(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Attach the active correlation ID to every log record."""
    event_dict["correlation_id"] = get_correlation_id()
    return event_dict


def _add_service_context(service: str, environment: str) -> Processor:
    """Build a processor that stamps records with the service and environment names."""

    def processor(
        _logger: WrappedLogger,
        _method_name: str,
        event_dict: EventDict,
    ) -> EventDict:
        event_dict["service"] = service
        event_dict["environment"] = environment
        return event_dict

    return processor


def configure_logging(
    *,
    level: str = "INFO",
    log_format: LogFormat = "json",
    service: str = "factoryai",
    environment: str = "local",
) -> None:
    """Configure ``structlog`` and the standard library logging module together.

    Third-party libraries log through :mod:`logging`; our own code logs through
    ``structlog``. Routing both through the same renderer means a single, consistent
    stream rather than two interleaved formats.

    Args:
        level: Minimum level to emit, e.g. ``"DEBUG"`` or ``"INFO"``.
        log_format: ``"json"`` for machine consumption, ``"console"`` for humans.
        service: Name of the running process, e.g. ``"api"`` or ``"worker"``.
        environment: Deployment environment, e.g. ``"local"`` or ``"production"``.
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_correlation_id,
        _add_service_context(service, environment),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    # Every structlog call is funnelled through the real `logging` module — via
    # `wrap_for_formatter` and `structlog.stdlib.LoggerFactory()` — rather than rendered
    # and written directly. That is what lets one handler and one `ProcessorFormatter`
    # produce identical output for both our own structlog calls and third-party libraries
    # that log through plain `logging`, and it is why level filtering below is done on the
    # stdlib loggers rather than on the structlog wrapper.
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for noisy in _NOISY_LIBRARIES:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str, **initial_values: Any) -> structlog.stdlib.BoundLogger:
    """Return a bound logger.

    Args:
        name: Logger name, conventionally ``__name__`` of the calling module.
        **initial_values: Fields bound to every record from this logger, useful for
            long-lived context such as ``model_version`` or ``category``.

    Returns:
        A logger whose ``info``/``warning``/``error`` methods accept keyword fields.

    Example:
        >>> log = get_logger(__name__, category="bottle")
        >>> log.info("image_ingested", image_id="abc", duration_ms=12)  # doctest: +SKIP
    """
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    if initial_values:
        logger = logger.bind(**initial_values)
    return logger
