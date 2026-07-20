from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

SENSITIVE_KEY = re.compile(
    r"(password|secret|token|signature|authorization|cookie|master.?key)", re.IGNORECASE
)


def redact(_logger: Any, _method: str, event: dict[str, Any]) -> dict[str, Any]:
    for key in list(event):
        if SENSITIVE_KEY.search(key):
            event[key] = "[REDACTED]"
    return event


def configure_logging(level: str, *, json_logs: bool = True) -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )
