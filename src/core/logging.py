"""
Structured JSON and Human-readable Logging Configuration.
"""
import logging
import sys
from typing import Any

import structlog

from src.config.settings import settings


def setup_logging() -> None:
    """Configure system logging handlers and structlog processors."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Any
    if settings.JSON_LOGS:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
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

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Silence overly chatty libraries
    for module_name in ["urllib3", "asyncio", "ccxt", "aiosqlite", "httpcore", "httpx"]:
        logging.getLogger(module_name).setLevel(logging.WARNING)


def get_logger(name: str = "quant_platform") -> structlog.stdlib.BoundLogger:
    """Obtain a structured logger instance bound to a module name."""
    return structlog.get_logger(name)


# Initialize logging immediately on import
setup_logging()
logger = get_logger("system")
