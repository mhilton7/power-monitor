from __future__ import annotations

import gzip
import json
import logging
import re
import shutil
import sys
import threading
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import structlog

LOG_FORMAT_VERSION = "pm-log/1.0.0"
LOG_CATEGORIES = ("api", "worker", "enrollment", "device_sync", "rate_sync", "backup")
LOG_FILE_PATTERN = re.compile(
    r"^(api|worker|enrollment|device_sync|rate_sync|backup)-(\d{4}-\d{2}-\d{2})\.jsonl(?:\.gz)?$"
)
SENSITIVE_KEY = re.compile(
    r"(password|secret|access.?token|refresh.?token|session|csrf|signature|authorization|"
    r"cookie|master.?key|hmac.?key|api.?key|private.?key|database.?url|enrollment.?secret)",
    re.IGNORECASE,
)
SENSITIVE_TEXT = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer|basic)?\s*[^\s,;]+"),
    re.compile(
        r"(?i)((?:password|secret|token|signature|cookie|api[_-]?key|private[_-]?key)"
        r"\s*[:=]\s*)[^\s,;]+"
    ),
    re.compile(r"(?i)((?:postgres(?:ql)?|mysql|mariadb)\+?[a-z0-9]*://[^:\s/@]+:)[^@\s/]+(@)"),
)


def _sanitize_text(value: str) -> str:
    redacted = value
    for pattern in SENSITIVE_TEXT:
        replacement = r"\1[REDACTED]\2" if pattern.groups >= 2 else r"\1[REDACTED]"
        redacted = pattern.sub(replacement, redacted)
    return redacted


def sanitize_log_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else sanitize_log_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_log_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def redact(_logger: Any, _method: str, event: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], sanitize_log_value(event))


def redact_log_line(line: str) -> bytes:
    """Return a normalized, redacted JSONL-safe representation of one log line."""

    stripped = line.rstrip("\r\n")
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        redacted = _sanitize_text(stripped)
        return (json.dumps({"event": "legacy_log", "message": redacted}) + "\n").encode()
    sanitized = sanitize_log_value(parsed)
    return (json.dumps(sanitized, separators=(",", ":"), default=str) + "\n").encode()


def retention_boundary(today: date, retention_days: int = 90) -> date:
    return today - timedelta(days=retention_days - 1)


def _dated_log_files(log_path: Path) -> list[tuple[Path, str, date]]:
    discovered: list[tuple[Path, str, date]] = []
    if not log_path.is_dir():
        return discovered
    for path in log_path.iterdir():
        if not path.is_file():
            continue
        match = LOG_FILE_PATTERN.fullmatch(path.name)
        if not match:
            continue
        discovered.append((path, match.group(1), date.fromisoformat(match.group(2))))
    return discovered


def maintain_log_directory(
    log_path: Path, *, now: datetime | None = None, retention_days: int = 90
) -> None:
    """Compress completed days and remove only files outside the retention window."""

    now = now or datetime.now(UTC)
    today = now.date()
    boundary = retention_boundary(today, retention_days)
    log_path.mkdir(parents=True, exist_ok=True)
    for path, _category, file_date in _dated_log_files(log_path):
        if file_date < boundary:
            with suppress(FileNotFoundError):
                path.unlink()
            continue
        if file_date >= today or path.suffix == ".gz":
            continue
        compressed = path.with_suffix(path.suffix + ".gz")
        temporary = compressed.with_suffix(compressed.suffix + ".tmp")
        if compressed.exists():
            with suppress(FileNotFoundError):
                path.unlink()
            continue
        try:
            with path.open("rb") as source, gzip.open(temporary, "wb", compresslevel=6) as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            temporary.replace(compressed)
            path.unlink()
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()


def _category_for_event(service: str, event: dict[str, Any]) -> str:
    explicit = str(event.get("category", ""))
    if explicit in LOG_CATEGORIES:
        return explicit
    context = " ".join(
        str(event.get(key, "")) for key in ("event", "path", "job", "source", "operation")
    ).lower()
    if "enroll" in context or "claim" in context:
        return "enrollment"
    if "rate" in context or "tariff" in context:
        return "rate_sync"
    if "backup" in context or "restore" in context:
        return "backup"
    if any(term in context for term in ("device", "heartbeat", "reading", "poll", "sync")):
        return "device_sync"
    return service if service in {"api", "worker"} else "api"


class DailyJsonLogWriter:
    def __init__(self, log_path: Path, service: str, retention_days: int) -> None:
        self.log_path = log_path
        self.service = service
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self._maintained_on: date | None = None

    def __call__(self, _logger: Any, _method: str, event: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC)
        event["service"] = self.service
        event["category"] = _category_for_event(self.service, event)
        event["log_format_version"] = LOG_FORMAT_VERSION
        sanitized = cast(dict[str, Any], sanitize_log_value(event))
        try:
            with self._lock:
                if self._maintained_on != now.date():
                    maintain_log_directory(
                        self.log_path, now=now, retention_days=self.retention_days
                    )
                    self._maintained_on = now.date()
                destination = self.log_path / (
                    f"{sanitized['category']}-{now.date().isoformat()}.jsonl"
                )
                # API, worker, and backup use separate UIDs but intentionally share
                # the log dataset. Normalize an existing file before opening it so
                # the directory's shared group/ACL remains effective across writers.
                with suppress(OSError):
                    destination.chmod(0o660)
                with destination.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(sanitized, separators=(",", ":"), default=str))
                    stream.write("\n")
                with suppress(OSError):
                    destination.chmod(0o660)
        except OSError:
            # Durable logging must never take down the API or worker. Container stdout
            # remains available for diagnosing a mount or permissions failure.
            pass
        return sanitized


def configure_logging(
    level: str,
    *,
    json_logs: bool = True,
    log_path: Path | None = None,
    service: str = "api",
    retention_days: int = 90,
) -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact,
    ]
    if log_path is not None:
        processors.append(DailyJsonLogWriter(log_path, service, retention_days))
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
