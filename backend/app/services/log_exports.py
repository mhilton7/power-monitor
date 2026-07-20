from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import IO, TextIO

from app.logging import (
    LOG_CATEGORIES,
    LOG_FORMAT_VERSION,
    _dated_log_files,
    maintain_log_directory,
    redact_log_line,
)


class NoLogsAvailableError(ValueError):
    pass


class LogExportTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class LogSource:
    path: Path
    category: str
    file_date: date


@dataclass(frozen=True)
class BuiltLogExport:
    path: Path
    size_bytes: int
    files: list[dict[str, object]]


def discover_logs(log_path: Path) -> list[LogSource]:
    selected: dict[tuple[str, date], Path] = {}
    for path, category, file_date in _dated_log_files(log_path):
        key = (category, file_date)
        current = selected.get(key)
        if current is None or (current.suffix == ".gz" and path.suffix != ".gz"):
            selected[key] = path
    return [
        LogSource(path=path, category=category, file_date=file_date)
        for (category, file_date), path in sorted(selected.items(), key=lambda item: item[0])
    ]


def cleanup_export_directory(log_path: Path, *, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    export_path = log_path / ".exports"
    if not export_path.is_dir():
        return
    cutoff = now - timedelta(days=1)
    for candidate in export_path.iterdir():
        if not candidate.is_file() or candidate.suffix not in {".zip", ".tmp"}:
            continue
        modified = datetime.fromtimestamp(candidate.stat().st_mtime, UTC)
        if modified < cutoff:
            with suppress(FileNotFoundError):
                candidate.unlink()


def log_availability(
    log_path: Path, *, now: datetime | None = None, retention_days: int = 90
) -> dict[str, object]:
    now = now or datetime.now(UTC)
    maintain_log_directory(log_path, now=now, retention_days=retention_days)
    cleanup_export_directory(log_path, now=now)
    sources = discover_logs(log_path)
    dates = [source.file_date for source in sources]
    compressed = [source.path for source in sources if source.path.suffix == ".gz"]
    last_rotation = (
        max(datetime.fromtimestamp(path.stat().st_mtime, UTC) for path in compressed)
        if compressed
        else None
    )
    sizes_by_category = {
        category: sum(
            source.path.stat().st_size for source in sources if source.category == category
        )
        for category in LOG_CATEGORIES
    }
    return {
        "earliest_date": min(dates).isoformat() if dates else None,
        "latest_date": max(dates).isoformat() if dates else None,
        "retention_days": retention_days,
        "stored_size_bytes": sum(source.path.stat().st_size for source in sources),
        "last_rotation_at": last_rotation.isoformat() if last_rotation else None,
        "services": [
            {
                "id": category,
                "available": sizes_by_category[category] > 0,
                "stored_size_bytes": sizes_by_category[category],
            }
            for category in LOG_CATEGORIES
        ],
    }


def _open_source(source: LogSource) -> TextIO:
    if source.path.suffix == ".gz":
        return gzip.open(source.path, "rt", encoding="utf-8", errors="replace")
    return source.path.open("r", encoding="utf-8", errors="replace")


def _write_redacted_source(
    archive_file: IO[bytes], source: LogSource, *, remaining_bytes: int
) -> tuple[int, str]:
    written = 0
    digest = hashlib.sha256()
    with _open_source(source) as stream:
        for line in stream:
            payload = redact_log_line(line)
            written += len(payload)
            if written > remaining_bytes:
                raise LogExportTooLargeError(
                    "The selected logs exceed the configured export limit; "
                    "choose a smaller date range"
                )
            archive_file.write(payload)
            digest.update(payload)
    return written, digest.hexdigest()


def build_log_export(
    *,
    log_path: Path,
    job_id: str,
    start_date: date,
    end_date: date,
    services: list[str],
    requesting_user_id: str,
    application_version: str,
    max_export_bytes: int,
    created_at: datetime | None = None,
) -> BuiltLogExport:
    created_at = created_at or datetime.now(UTC)
    selected = [
        source
        for source in discover_logs(log_path)
        if start_date <= source.file_date <= end_date and source.category in services
    ]
    if not selected:
        raise NoLogsAvailableError("No application logs exist for the selected range and services")

    export_path = log_path / ".exports"
    export_path.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f"log-export-{job_id}-", suffix=".tmp", dir=export_path
    )
    os.close(descriptor)
    temporary = Path(raw_path)
    ready = temporary.with_suffix(".zip")
    included: list[dict[str, object]] = []
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True
        ) as archive:
            for source in selected:
                archive_name = f"logs/{source.category}-{source.file_date.isoformat()}.jsonl"
                with archive.open(archive_name, "w", force_zip64=True) as destination:
                    size_bytes, sha256 = _write_redacted_source(
                        destination,
                        source,
                        remaining_bytes=max_export_bytes - total_uncompressed,
                    )
                total_uncompressed += size_bytes
                included.append(
                    {
                        "filename": archive_name,
                        "service": source.category,
                        "date": source.file_date.isoformat(),
                        "size_bytes": size_bytes,
                        "sha256": sha256,
                    }
                )
            manifest = {
                "format": "power-monitor-log-export/1.0.0",
                "created_at": created_at.isoformat(),
                "requested_start_date": start_date.isoformat(),
                "requested_end_date": end_date.isoformat(),
                "included_services": services,
                "files": included,
                "application_version": application_version,
                "log_format_version": LOG_FORMAT_VERSION,
                "requesting_administrator_id": requesting_user_id,
            }
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True, separators=(",", ": ")) + "\n",
            )
            archive.writestr(
                "README.txt",
                "Power Monitor application-log export. Files are structured JSON Lines and "
                "were redacted again during export. Verify each file against manifest.json.\n",
            )
        archive_size = temporary.stat().st_size
        if archive_size > max_export_bytes:
            raise LogExportTooLargeError(
                "The selected logs exceed the configured export limit; choose a smaller date range"
            )
        temporary.replace(ready)
        return BuiltLogExport(path=ready, size_bytes=archive_size, files=included)
    except Exception:
        for path in (temporary, ready):
            with suppress(FileNotFoundError):
                path.unlink()
        raise
