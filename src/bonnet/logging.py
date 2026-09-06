"""Structured logging via structlog."""
from __future__ import annotations

import logging
import sys

import structlog

from .settings import get_settings

_configured = False


def configure() -> None:
    """Configure structlog + stdlib logging. Idempotent."""
    global _configured
    if _configured:
        return

    settings = get_settings()
    level = getattr(logging, settings.log_level, logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _configured = True


def get_logger(name: str = "bonnet") -> structlog.stdlib.BoundLogger:
    """Return a configured logger. Calls configure() lazily."""
    if not _configured:
        configure()
    return structlog.get_logger(name)


__all__ = ["configure", "get_logger"]