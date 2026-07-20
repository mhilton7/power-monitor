from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    BackgroundJob,
    RateChangeCandidate,
    RateExtractionResult,
    RateSource,
    RateSourceArtifact,
    RateSourceCheckRun,
    RateSyncConfiguration,
    RateVersion,
)
from app.rates.candidates import create_candidate_from_document, document_differences
from app.rates.notifications import emit_rate_alert
from app.rates.schedule import latest_scheduled_time, next_scheduled_time
from app.rates.service import activate_version
from app.rates.sources import ADAPTERS, PARSER_VERSION, SourceFetchError, fetch_source

__all__ = ["document_differences", "latest_scheduled_time"]


def _safe_artifact_path(root: Path, sha256: str, suffix: str) -> Path:
    root = root.resolve()
    candidate = (root / sha256[:2] / f"{sha256}{suffix}").resolve()
    if root not in candidate.parents:
        raise ValueError("artifact path escaped configured root")
    return candidate


async def enqueue_scheduled_sync(session: AsyncSession, settings: Settings) -> bool:
    config = await session.get(RateSyncConfiguration, "default")
    if config is None or not config.enabled or not settings.rate_sync_enabled:
        return False
    now = datetime.now(UTC)
    scheduled = latest_scheduled_time(now, config.schedule_cron, config.timezone)
    jitter_limit = min(config.jitter_minutes, settings.rate_sync_jitter_minutes)
    digest = hashlib.sha256(scheduled.isoformat().encode()).digest()
    jitter = (
        int.from_bytes(digest[:2], "big") % (jitter_limit + 1) if jitter_limit else 0
    )
    due_at = scheduled + timedelta(minutes=jitter)
    config.next_scheduled_run = next_scheduled_time(
        now, config.schedule_cron, config.timezone
    )
    if now < due_at or (
        config.last_scheduled_for and config.last_scheduled_for >= scheduled
    ):
        return False
    job = BackgroundJob(
        job_type="rate_source_sync",
        status="queued",
        requested_by=None,
        requested_at=now,
        scheduled_for=due_at,
        correlation_id=f"rate-sync-{secrets.token_hex(12)}",
        progress={"source_ids": [], "completed": 0},
        result={},
    )
    config.last_scheduled_for = scheduled
    config.updated_at = now
    session.add(job)
    await session.flush()
    return True


async def process_rate_sync_jobs(
    session: AsyncSession, settings: Settings, *, limit: int = 1
) -> dict[str, int]:
    await enqueue_scheduled_sync(session, settings)
    jobs = list(
        await session.scalars(
            select(BackgroundJob)
            .where(
                BackgroundJob.job_type == "rate_source_sync",
                BackgroundJob.status == "queued",
                (
                    BackgroundJob.scheduled_for.is_(None)
                    | (BackgroundJob.scheduled_for <= datetime.now(UTC))
                ),
            )
            .order_by(BackgroundJob.requested_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    completed = 0
    failed = 0
    candidates = 0
    for job in jobs:
        config = await session.get(RateSyncConfiguration, "default")
        job.status = "running"
        job.started_at = datetime.now(UTC)
        if config:
            config.last_attempted_run = job.started_at
        await session.flush()
        source_query = (
            select(RateSource)
            .where(RateSource.enabled.is_(True))
            .order_by(RateSource.url)
        )
        requested_ids = job.progress.get("source_ids", [])
        if requested_ids:
            source_query = source_query.where(RateSource.id.in_(requested_ids))
        sources = list(await session.scalars(source_query))
        outcomes: list[dict[str, Any]] = []
        for source in sources:
            check = RateSourceCheckRun(
                job_id=job.id,
                rate_source_id=source.id,
                checked_at=datetime.now(UTC),
                outcome="running",
            )
            session.add(check)
            await session.flush()
            try:
                fetched = await fetch_source(
                    source.url,
                    etag=source.etag,
                    last_modified=source.last_modified,
                    max_bytes=settings.rate_sync_max_source_bytes,
                    connect_timeout=settings.rate_sync_connect_timeout_seconds,
                    read_timeout=settings.rate_sync_read_timeout_seconds,
                    total_timeout=settings.rate_sync_total_timeout_seconds,
                    max_redirects=settings.rate_sync_max_redirects,
                    max_retries=settings.rate_sync_max_retries,
                )
                check.http_status = fetched.status_code
                check.final_url = fetched.final_url
                check.etag = fetched.etag
                check.last_modified = fetched.last_modified
                check.duration_ms = fetched.duration_ms
                check.response_bytes = len(fetched.content)
                source.last_checked_at = datetime.now(UTC)
                source.etag = fetched.etag
                source.last_modified = fetched.last_modified
                if fetched.status_code == 304:
                    check.outcome = "not_modified"
                    source.last_success_at = datetime.now(UTC)
                    source.consecutive_failures = 0
                    outcomes.append({"source_id": source.id, "outcome": "not_modified"})
                    continue
                digest = hashlib.sha256(fetched.content).hexdigest()
                previous_sha = await session.scalar(
                    select(RateSourceArtifact.sha256)
                    .join(
                        RateSourceCheckRun,
                        RateSourceCheckRun.id == RateSourceArtifact.source_check_id,
                    )
                    .where(RateSourceCheckRun.rate_source_id == source.id)
                    .order_by(RateSourceArtifact.captured_at.desc())
                    .limit(1)
                )
                if previous_sha == digest:
                    check.outcome = "unchanged_content"
                    source.last_success_at = datetime.now(UTC)
                    source.consecutive_failures = 0
                    outcomes.append(
                        {"source_id": source.id, "outcome": "unchanged_content"}
                    )
                    continue
                if config:
                    config.last_source_change = datetime.now(UTC)
                extension = (
                    ".pdf" if fetched.content_type == "application/pdf" else ".html"
                )
                artifact_path = _safe_artifact_path(
                    settings.rate_sync_artifact_path, digest, extension
                )
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                if not artifact_path.exists():
                    artifact_path.write_bytes(fetched.content)
                metadata_path = _safe_artifact_path(
                    settings.rate_sync_artifact_path, digest, ".metadata.json"
                )
                metadata_path.write_text(
                    json.dumps(
                        {
                            "source_url": source.url,
                            "final_url": fetched.final_url,
                            "captured_at": datetime.now(UTC).isoformat(),
                            "sha256": digest,
                            "content_type": fetched.content_type,
                            "byte_size": len(fetched.content),
                            "etag": fetched.etag,
                            "last_modified": fetched.last_modified,
                        },
                        sort_keys=True,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                artifact = RateSourceArtifact(
                    source_check_id=check.id,
                    sha256=digest,
                    content_type=fetched.content_type,
                    byte_size=len(fetched.content),
                    storage_path=str(artifact_path),
                    original_filename=Path(fetched.final_url).name[:255],
                    captured_at=datetime.now(UTC),
                )
                session.add(artifact)
                await session.flush()
                adapter = ADAPTERS[source.parser_id]
                parsed = adapter.parse(
                    fetched.content,
                    fetched.final_url,
                    fetched.content_type,
                    effective_from=source.effective_from_hint,
                )
                extraction = RateExtractionResult(
                    artifact_id=artifact.id,
                    parser_id=source.parser_id,
                    parser_version=PARSER_VERSION,
                    status=parsed.status,
                    normalized_payload={
                        "documents": [
                            item.model_dump(mode="json") for item in parsed.documents
                        ],
                        "citations": parsed.citations,
                        "discovered_links": parsed.discovered_links,
                    },
                    warnings=parsed.warnings,
                    errors=parsed.errors,
                    extracted_at=datetime.now(UTC),
                )
                session.add(extraction)
                await session.flush()
                extraction_path = _safe_artifact_path(
                    settings.rate_sync_artifact_path, digest, ".extraction.json"
                )
                extraction_path.write_text(
                    json.dumps(
                        {
                            "parser_id": extraction.parser_id,
                            "parser_version": extraction.parser_version,
                            "status": extraction.status,
                            "payload": extraction.normalized_payload,
                            "warnings": extraction.warnings,
                            "errors": extraction.errors,
                        },
                        sort_keys=True,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                for document in parsed.documents:
                    candidate = await create_candidate_from_document(
                        session,
                        document,
                        extraction,
                        artifact,
                        approval_mode=(
                            config.approval_mode if config else "manual_review"
                        ),
                        auto_activate_verified=(
                            config.auto_activate_verified if config else False
                        ),
                        maximum_percent_change=Decimal(
                            settings.rate_sync_auto_max_percent_change
                        ),
                        retroactive_days=settings.rate_sync_retroactive_auto_days,
                    )
                    if candidate:
                        candidates += 1
                        if config:
                            config.last_candidate_created = candidate.created_at
                            if candidate.status in {
                                "automatically_activated",
                                "scheduled",
                            }:
                                config.last_approved_version = candidate.reviewed_at
                check.outcome = parsed.status
                if parsed.status == "failed":
                    await emit_rate_alert(
                        session,
                        "rate_parser_failed",
                        {
                            "source_id": source.id,
                            "check_id": check.id,
                            "parser_id": source.parser_id,
                        },
                        dedupe_key=source.id,
                    )
                source.last_success_at = datetime.now(UTC)
                source.consecutive_failures = 0
                outcomes.append(
                    {
                        "source_id": source.id,
                        "outcome": parsed.status,
                        "artifact_id": artifact.id,
                    }
                )
            except (SourceFetchError, ValueError, KeyError) as exc:
                check.outcome = "failed"
                check.error_code = getattr(exc, "code", "processing_error")
                check.error_detail = str(exc)[:1000]
                source.last_checked_at = datetime.now(UTC)
                source.consecutive_failures += 1
                outcomes.append(
                    {
                        "source_id": source.id,
                        "outcome": "failed",
                        "error_code": check.error_code,
                    }
                )
                failed += 1
                await emit_rate_alert(
                    session,
                    "rate_source_unavailable",
                    {
                        "source_id": source.id,
                        "check_id": check.id,
                        "error_code": check.error_code,
                    },
                    dedupe_key=source.id,
                )
        job.status = (
            "succeeded"
            if all(item["outcome"] != "failed" for item in outcomes)
            else "failed"
        )
        job.completed_at = datetime.now(UTC)
        job.progress = {
            "source_ids": [item.id for item in sources],
            "completed": len(outcomes),
        }
        job.result = {"sources": outcomes, "candidate_count": candidates}
        if job.status == "failed":
            job.error_code = "source_check_failed"
            job.error_detail = "One or more approved sources could not be checked"
            if config:
                config.last_error = job.error_detail
        else:
            completed += 1
            if config:
                config.last_successful_run = job.completed_at
                config.last_error = None
            await emit_rate_alert(
                session,
                "rate_check_succeeded",
                {
                    "job_id": job.id,
                    "source_count": len(outcomes),
                    "candidate_count": candidates,
                },
            )
    return {
        "jobs_completed": completed,
        "source_failures": failed,
        "candidates": candidates,
    }


async def activate_due_versions(session: AsyncSession) -> int:
    versions = list(
        await session.scalars(
            select(RateVersion).where(
                RateVersion.status == "approved",
                RateVersion.effective_from <= date.today(),
            )
        )
    )
    activated = 0
    for version in versions:
        status, _report = await activate_version(
            session, version, None, automatically=version.automatically_activated
        )
        if status != "active":
            continue
        await emit_rate_alert(
            session,
            "rate_version_activated",
            {"rate_version_id": version.id, "scheduled_activation": True},
        )
        candidate = await session.scalar(
            select(RateChangeCandidate).where(
                RateChangeCandidate.candidate_rate_version_id == version.id,
                RateChangeCandidate.status.in_(["approved", "scheduled"]),
            )
        )
        if candidate:
            candidate.status = "activated"
        activated += 1
    return activated


async def check_stale_sources(
    session: AsyncSession, *, maximum_age_days: int = 8
) -> int:
    threshold = datetime.now(UTC) - timedelta(days=maximum_age_days)
    sources = list(
        await session.scalars(
            select(RateSource).where(
                RateSource.enabled.is_(True),
                (
                    RateSource.last_success_at.is_(None)
                    | (RateSource.last_success_at < threshold)
                ),
            )
        )
    )
    for source in sources:
        await emit_rate_alert(
            session,
            "rate_source_stale",
            {
                "source_id": source.id,
                "last_success_at": (
                    source.last_success_at.isoformat()
                    if source.last_success_at
                    else None
                ),
                "maximum_age_days": maximum_age_days,
            },
            dedupe_key=source.id,
        )
    return len(sources)
