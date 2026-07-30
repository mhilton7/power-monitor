#!/usr/bin/env python3
"""Inspect and safely reconcile backup database state with local artifacts."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db.models import BackgroundJob, BackupRun, new_uuid  # noqa: E402
from app.db.session import dispose_engine, session_factory  # noqa: E402

BACKUP_NAME = re.compile(r"^power-monitor-\d{8}T\d{6}Z(?:-[0-9a-f]{8})?$")
ACTIVE_JOB_TYPES = {
    "backup_create",
    "backup_verify",
    "backup_restore_preflight",
    "backup_delete",
}


def safe_directory(root: Path, stored: str | None) -> Path | None:
    if not stored:
        return None
    candidate = Path(stored)
    identifier = candidate.name if candidate.is_absolute() else stored
    if not BACKUP_NAME.fullmatch(identifier):
        return None
    expected = root / identifier
    try:
        resolved = expected.resolve(strict=True)
    except FileNotFoundError:
        return None
    except OSError as exc:
        print(
            f"ARTIFACT_ACCESS_DENIED identifier={identifier} "
            f"error={type(exc).__name__}",
            file=sys.stderr,
        )
        return None
    return (
        resolved
        if resolved == expected and resolved.is_dir() and not expected.is_symlink()
        else None
    )


def manifest_state(directory: Path) -> tuple[bool, str]:
    manifest_path = directory / "manifest.json"
    try:
        manifest_exists = manifest_path.is_file()
    except OSError:
        return False, "MANIFEST_INACCESSIBLE"
    if not manifest_exists:
        return False, "MANIFEST_MISSING"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "MANIFEST_INVALID"
    if manifest.get("format") != "power-monitor-backup/v2":
        return False, "MANIFEST_INVALID"
    try:
        checksums_exist = (directory / "checksums.sha256").is_file()
    except OSError:
        return False, "CHECKSUM_FILE_INACCESSIBLE"
    if not checksums_exist:
        return False, "CHECKSUM_FILE_MISSING"
    return True, "READY"


async def reconcile(root: Path, apply: bool, interrupted_minutes: int) -> int:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"backup root is not a directory: {root}")
    changed = 0
    async with session_factory()() as session:
        runs = list(
            await session.scalars(select(BackupRun).order_by(BackupRun.started_at))
        )
        jobs = list(
            await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type.in_(ACTIVE_JOB_TYPES)
                )
            )
        )
        active_jobs = [job for job in jobs if job.status in {"queued", "running"}]
        active_run_ids = {
            str((job.progress or {}).get("backup_run_id"))
            for job in active_jobs
            if (job.progress or {}).get("backup_run_id")
        }
        stored_names = [Path(run.path).name for run in runs if run.path]
        duplicates = {
            name for name, count in Counter(stored_names).items() if count > 1
        }
        for name in sorted(duplicates):
            print(f"DUPLICATE_ROW identifier={name}")

        queued_verification = bool(active_jobs)
        cutoff = datetime.now(UTC) - timedelta(minutes=interrupted_minutes)
        for run in runs:
            directory = safe_directory(root, run.path)
            valid = False
            artifact_state = "BACKUP_DIRECTORY_MISSING"
            if directory is not None:
                valid, artifact_state = manifest_state(directory)
            print(
                f"RUN id={run.id} status={run.status} "
                f"artifact={artifact_state} action=",
                end="",
            )
            if run.status in {"completed_unverified", "verification_queued"}:
                if not valid:
                    print("mark_verification_failed")
                    if apply:
                        run.status = "verification_failed"
                        run.verification_completed_at = datetime.now(UTC)
                        run.failed_stage = "reconciliation"
                        run.safe_error_code = artifact_state
                        run.safe_error_summary = "Backup artifacts are missing or invalid; review storage permissions"
                        run.updated_at = datetime.now(UTC)
                        changed += 1
                elif run.id in active_run_ids:
                    print("already_owned")
                elif queued_verification:
                    print("defer_until_active_operation_finishes")
                else:
                    print("queue_verification")
                    if apply:
                        run.status = "verification_queued"
                        run.updated_at = datetime.now(UTC)
                        job = BackgroundJob(
                            id=new_uuid(),
                            job_type="backup_verify",
                            status="queued",
                            requested_by=None,
                            requested_at=datetime.now(UTC),
                            scheduled_for=datetime.now(UTC),
                            correlation_id=f"backup-reconcile:{run.id}",
                            dedupe_key="backup:global",
                            idempotency_key=f"reconcile-verify:{run.id}",
                            trigger_type="reconciliation",
                            progress={"backup_run_id": run.id},
                            result={},
                        )
                        session.add(job)
                        queued_verification = True
                        changed += 1
            elif (
                run.status in {"queued", "creating", "verifying", "deleting"}
                and run.id not in active_run_ids
                and run.started_at.replace(tzinfo=run.started_at.tzinfo or UTC) < cutoff
            ):
                print("mark_interrupted")
                if apply:
                    run.status = (
                        "backup_failed"
                        if run.status in {"queued", "creating"}
                        else "verification_failed"
                        if run.status == "verifying"
                        else "deletion_failed"
                    )
                    run.failed_stage = "reconciliation"
                    run.safe_error_code = "INTERRUPTED_OPERATION"
                    run.safe_error_summary = (
                        "No active backup job owns this abandoned operation"
                    )
                    run.updated_at = datetime.now(UTC)
                    changed += 1
            else:
                print("none")

        known = set(stored_names)
        try:
            root_entries = sorted(root.iterdir())
        except OSError as exc:
            print(
                f"BACKUP_ROOT_ACCESS_DENIED error={type(exc).__name__}",
                file=sys.stderr,
            )
            root_entries = []
        for directory in root_entries:
            if directory.name == ".trash":
                for item in sorted(directory.iterdir()):
                    print(f"TRASH artifact={item.name} action=report_only")
            elif directory.name.startswith(".") and directory.name.endswith(
                ".incomplete"
            ):
                print(f"INCOMPLETE artifact={directory.name} action=report_only")
            elif BACKUP_NAME.fullmatch(directory.name) and directory.name not in known:
                print(f"ORPHAN artifact={directory.name} action=report_only")

        if apply:
            await session.commit()
    await dispose_engine()
    print(f"mode={'apply' if apply else 'dry-run'} changed={changed}")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=Path("/data/backups"),
        help="Container-visible backup root",
    )
    parser.add_argument("--interrupted-minutes", type=int, default=60)
    args = parser.parse_args()
    asyncio.run(reconcile(args.backup_root, args.apply, args.interrupted_minutes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
