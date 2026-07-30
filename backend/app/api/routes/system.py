from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from sqlalchemy import func, select, text

from app.api.deps import Admin, AppSettings, DbSession, Viewer
from app.config import Settings, get_settings
from app.db.models import (
    AlertInstance,
    BackupRun,
    Device,
    DeviceHeartbeat,
    RateAssignment,
    RateChangeCandidate,
    RateSource,
    RateSyncConfiguration,
    RawReading,
    UtilityAccount,
    WorkerState,
)
from app.db.session import session_factory
from app.live_measurements import load_latest_measurements
from app.problem import ProblemError
from app.schemas import HealthComponent, HealthEvent, SystemHealthResponse

router = APIRouter(tags=["system"])
logger = structlog.get_logger(__name__)
_time_requests: dict[str, deque[float]] = defaultdict(deque)


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "live", "version": get_settings().power_monitor_version}


@router.get("/health/ready")
async def readiness(session: DbSession) -> JSONResponse:
    checks: dict[str, Any] = {"process": "ok"}
    status = 200
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
        migration = await session.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
        checks["migration"] = migration or "missing"
        if not migration:
            status = 503
    except Exception as exc:  # health boundary intentionally converts failure
        checks["database"] = "failed"
        checks["error"] = type(exc).__name__
        status = 503
    return JSONResponse(
        {"status": "ready" if status == 200 else "not_ready", "checks": checks}, status_code=status
    )


@router.get("/api/v1/time")
async def time_hint(request: Request) -> dict[str, Any]:
    source = request.client.host if request.client else "unknown"
    now_mono = time.monotonic()
    attempts = _time_requests[source]
    while attempts and attempts[0] < now_mono - 60:
        attempts.popleft()
    if len(attempts) >= 30:
        raise ProblemError(
            429,
            "Too many time requests",
            "Retry after 60 seconds",
            "time_rate_limited",
        )
    attempts.append(now_mono)
    now = datetime.now(UTC)
    return {
        "utc": now.isoformat().replace("+00:00", "Z"),
        "unix_seconds": int(now.timestamp()),
        "authoritative": False,
    }


@router.get("/api/v1/system/info")
async def system_info(principal: Viewer, session: DbSession) -> dict[str, Any]:
    if "settings.view" not in principal.permissions:
        raise ProblemError(403, "Permission denied", "Settings permission is required", "forbidden")
    settings = get_settings()
    worker = await session.get(WorkerState, "main")
    rate_config = await session.get(RateSyncConfiguration, "default")
    last_rate_success = await session.scalar(
        select(RateSource.last_success_at)
        .where(RateSource.enabled.is_(True), RateSource.last_success_at.is_not(None))
        .order_by(RateSource.last_success_at.desc())
        .limit(1)
    )
    pending_candidates = await session.scalar(
        select(func.count())
        .select_from(RateChangeCandidate)
        .where(RateChangeCandidate.status == "pending_review")
    )
    return {
        "product": settings.app_name,
        "version": settings.power_monitor_version,
        "release_commit": settings.release_commit,
        "api_schema_version": "1.0.0",
        "bill_import_context_schema_version": "utility-account-rate-context/1.0",
        "protocol": settings.protocol_version,
        "python_runtime": "3.13 production image",
        "worker": {
            "status": worker.status if worker else "not_started",
            "last_loop_at": worker.last_loop_at if worker else None,
            "last_success_at": worker.last_success_at if worker else None,
        },
        "rate_sync": {
            "enabled": rate_config.enabled if rate_config else settings.rate_sync_enabled,
            "schedule_cron": (
                rate_config.schedule_cron if rate_config else settings.rate_sync_cron
            ),
            "timezone": (rate_config.timezone if rate_config else settings.rate_sync_timezone),
            "approval_mode": (
                rate_config.approval_mode if rate_config else settings.rate_sync_policy
            ),
            "last_success_at": last_rate_success,
            "pending_candidates": pending_candidates or 0,
        },
        "defaults": {
            "site": settings.default_site_name,
            "timezone": settings.default_timezone,
            "currency": settings.default_currency,
            "heartbeat_seconds": settings.heartbeat_expectation_seconds,
        },
    }


def _health_component(
    *,
    key: str,
    label: str,
    status: str,
    summary: str,
    checked_at: datetime,
    last_success_at: datetime | None = None,
    latency_ms: Decimal | None = None,
    details: dict[str, Any] | None = None,
    remediation: dict[str, str | None] | None = None,
) -> HealthComponent:
    return HealthComponent.model_validate(
        {
            "key": key,
            "label": label,
            "status": status,
            "summary": summary,
            "checked_at": checked_at,
            "last_success_at": last_success_at,
            "latency_ms": latency_ms,
            "details": details or {},
            "remediation": remediation,
        }
    )


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@router.get("/api/v1/system/health", response_model=SystemHealthResponse)
async def detailed_system_health(
    request: Request,
    _principal: Admin,
    session: DbSession,
) -> SystemHealthResponse:
    """Owner-only diagnostic health without credentials, file paths, or sensitive payloads."""
    checked_at = datetime.now(UTC)
    settings = get_settings()
    components: list[HealthComponent] = [
        _health_component(
            key="api",
            label="API",
            status="healthy",
            summary="The authenticated API process is responding.",
            checked_at=checked_at,
            details={"authenticated": True, "schema_version": "1.0.0"},
        )
    ]

    started = time.perf_counter()
    database_available = True
    try:
        await session.execute(text("SELECT 1"))
        migration = await session.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
        latency = Decimal(str(round((time.perf_counter() - started) * 1000, 2)))
        components.append(
            _health_component(
                key="database",
                label="Database",
                status="healthy" if migration else "degraded",
                summary=(
                    "PostgreSQL is reachable and migrations are applied."
                    if migration
                    else "PostgreSQL is reachable, but migration state is unavailable."
                ),
                checked_at=checked_at,
                last_success_at=checked_at,
                latency_ms=latency,
                details={"migration": migration or "missing"},
                remediation=(
                    None
                    if migration
                    else {
                        "label": "Review migration service",
                        "route": "/settings/advanced/system-health",
                        "action": "retry",
                    }
                ),
            )
        )
    except Exception as exc:
        database_available = False
        components.append(
            _health_component(
                key="database",
                label="Database",
                status="unhealthy",
                summary="The API cannot complete a PostgreSQL readiness query.",
                checked_at=checked_at,
                latency_ms=Decimal(str(round((time.perf_counter() - started) * 1000, 2))),
                details={"error_type": type(exc).__name__},
                remediation={
                    "label": "Check PostgreSQL and migrations",
                    "route": "/settings/advanced/system-health",
                    "action": "retry",
                },
            )
        )

    worker = await session.get(WorkerState, "main") if database_available else None
    worker_loop_at = _aware_utc(worker.last_loop_at) if worker else None
    worker_success_at = _aware_utc(worker.last_success_at) if worker else None
    worker_age = (
        (checked_at - worker_loop_at).total_seconds() if worker_loop_at is not None else None
    )
    worker_stale_after = max(60, settings.heartbeat_expectation_seconds * 4)
    if not database_available:
        worker_status = "unknown"
        worker_summary = "Worker state is unavailable while the database is unreachable."
    elif worker is None:
        worker_status = "unknown"
        worker_summary = "The worker has not recorded a loop in this database."
    elif worker.status not in {"healthy", "ok", "running"}:
        worker_status = "unhealthy"
        worker_summary = "The asynchronous worker reports an error state."
    elif worker_age is not None and worker_age > worker_stale_after:
        worker_status = "degraded"
        worker_summary = "The asynchronous worker loop is stale."
    else:
        worker_status = "healthy"
        worker_summary = "The asynchronous worker is processing normally."
    components.append(
        _health_component(
            key="worker",
            label="Worker",
            status=worker_status,
            summary=worker_summary,
            checked_at=checked_at,
            last_success_at=worker_success_at,
            details={
                "reported_status": worker.status if worker else "not_started",
                "last_loop_at": worker_loop_at.isoformat() if worker_loop_at else None,
                "stale_after_seconds": worker_stale_after,
            },
            remediation=(
                None
                if worker_status == "healthy"
                else {
                    "label": "Review worker container",
                    "route": "/settings/advanced/logs",
                    "action": "retry",
                }
            ),
        )
    )

    storage_paths = (
        settings.firmware_path,
        settings.report_path,
        settings.rate_sync_artifact_path,
        settings.utility_bill_artifact_path,
        settings.backup_path,
        settings.log_path,
    )
    storage_checks = [
        {
            "configured": True,
            "exists": path.exists(),
            "readable": path.exists() and os.access(path, os.R_OK),
            "writable": path.exists() and os.access(path, os.W_OK),
        }
        for path in storage_paths
    ]
    storage_ready = sum(
        1 for check in storage_checks if check["exists"] and check["readable"] and check["writable"]
    )
    storage_status = (
        "healthy"
        if storage_ready == len(storage_checks)
        else "degraded"
        if storage_ready
        else "unknown"
    )
    components.append(
        _health_component(
            key="storage",
            label="Storage",
            status=storage_status,
            summary=(
                "All configured local storage locations are accessible."
                if storage_status == "healthy"
                else "One or more configured local storage locations are not yet accessible."
            ),
            checked_at=checked_at,
            details={
                "configured_locations": len(storage_checks),
                "accessible_locations": storage_ready,
            },
            remediation=(
                None
                if storage_status == "healthy"
                else {
                    "label": "Review dataset permissions",
                    "route": "/settings/data",
                    "action": "retry",
                }
            ),
        )
    )

    backup = (
        await session.scalar(select(BackupRun).order_by(BackupRun.started_at.desc()).limit(1))
        if database_available
        else None
    )
    if not database_available:
        backup_status = "unknown"
        backup_summary = "Backup state is unavailable while the database is unreachable."
        backup_success = None
    elif backup is None:
        backup_status = "unknown"
        backup_summary = "No logical backup run has been recorded yet."
        backup_success = None
    elif backup.status in {"failed", "error"}:
        backup_status = "unhealthy"
        backup_summary = "The most recent logical backup failed."
        backup_success = None
    elif backup.verified_at is None:
        backup_status = "degraded"
        backup_summary = "The most recent logical backup has not been verified."
        backup_success = _aware_utc(backup.completed_at)
    else:
        backup_status = "healthy"
        backup_summary = "The most recent logical backup completed and was verified."
        backup_success = _aware_utc(backup.verified_at)
    components.append(
        _health_component(
            key="backups",
            label="Backups",
            status=backup_status,
            summary=backup_summary,
            checked_at=checked_at,
            last_success_at=backup_success,
            details={
                "latest_status": backup.status if backup else "not_run",
                "verified": bool(backup and backup.verified_at),
            },
            remediation=(
                None
                if backup_status == "healthy"
                else {
                    "label": "Open Data & Backups",
                    "route": "/settings/data",
                    "action": "navigate",
                }
            ),
        )
    )

    device_count = (
        (
            await session.scalar(
                select(func.count()).select_from(Device).where(Device.lifecycle_status == "active")
            )
            or 0
        )
        if database_available
        else 0
    )
    latest_heartbeat = (
        _aware_utc(await session.scalar(select(func.max(DeviceHeartbeat.received_at))))
        if database_available
        else None
    )
    latest_reading = (
        _aware_utc(await session.scalar(select(func.max(RawReading.ingested_at))))
        if database_available
        else None
    )
    live_reference = max(
        (value for value in (latest_heartbeat, latest_reading) if value is not None),
        default=None,
    )
    live_age = (checked_at - live_reference).total_seconds() if live_reference is not None else None
    if not database_available:
        live_status = "unknown"
        live_summary = "Live-data state is unavailable while the database is unreachable."
    elif not device_count:
        live_status = "unknown"
        live_summary = "No real sensors are enrolled; live data health is not applicable yet."
    elif live_reference is None:
        live_status = "degraded"
        live_summary = "Real sensors are enrolled, but no signed live data has been stored."
    elif live_age is not None and live_age > max(120, settings.heartbeat_expectation_seconds * 8):
        live_status = "degraded"
        live_summary = "The latest signed sensor data is stale."
    else:
        live_status = "healthy"
        live_summary = "Signed real-sensor data is arriving normally."
    components.append(
        _health_component(
            key="live_data",
            label="Live data",
            status=live_status,
            summary=live_summary,
            checked_at=checked_at,
            last_success_at=live_reference,
            details={
                "real_sensor_count": device_count,
                "latest_data_at": live_reference.isoformat() if live_reference else None,
                "synthetic_data_included": False,
            },
            remediation=(
                None
                if live_status == "healthy"
                else {
                    "label": "Open Sensors",
                    "route": "/settings/sensors",
                    "action": "navigate",
                }
            ),
        )
    )

    account_count = (
        (
            await session.scalar(
                select(func.count())
                .select_from(UtilityAccount)
                .where(UtilityAccount.status == "active", UtilityAccount.archived_at.is_(None))
            )
            or 0
        )
        if database_available
        else 0
    )
    current_assignments = (
        (
            await session.scalar(
                select(func.count())
                .select_from(RateAssignment)
                .where(
                    RateAssignment.cancelled_at.is_(None),
                    RateAssignment.effective_from <= checked_at,
                    (
                        RateAssignment.effective_to.is_(None)
                        | (RateAssignment.effective_to > checked_at)
                    ),
                )
            )
            or 0
        )
        if database_available
        else 0
    )
    failed_sources = (
        (
            await session.scalar(
                select(func.count())
                .select_from(RateSource)
                .where(RateSource.enabled.is_(True), RateSource.consecutive_failures > 0)
            )
            or 0
        )
        if database_available
        else 0
    )
    if not database_available:
        rate_status = "unknown"
        rate_summary = "Rate-engine state is unavailable while the database is unreachable."
    elif not account_count:
        rate_status = "unknown"
        rate_summary = "No electric service is configured; rate health is not applicable yet."
    elif not current_assignments:
        rate_status = "degraded"
        rate_summary = "An electric service exists without a current rate assignment."
    elif failed_sources:
        rate_status = "degraded"
        rate_summary = "Current rates remain usable, but a managed source needs attention."
    else:
        rate_status = "healthy"
        rate_summary = "A current rate assignment is available and managed sources are healthy."
    components.append(
        _health_component(
            key="rate_engine",
            label="Rate engine",
            status=rate_status,
            summary=rate_summary,
            checked_at=checked_at,
            details={
                "active_accounts": account_count,
                "current_assignments": current_assignments,
                "managed_sources_with_failures": failed_sources,
                "decimal_calculation": True,
            },
            remediation=(
                None
                if rate_status == "healthy"
                else {
                    "label": "Open Billing",
                    "route": "/billing",
                    "action": "navigate",
                }
            ),
        )
    )

    frontend_version = request.headers.get("X-Power-Monitor-Frontend-Version")
    if frontend_version:
        frontend_version = frontend_version.strip()[:64]
    version_mismatch = bool(
        frontend_version and frontend_version not in {"development", settings.power_monitor_version}
    )
    core_unhealthy = any(
        component.status == "unhealthy" and component.key in {"api", "database"}
        for component in components
    )
    overall: Literal["healthy", "degraded", "unhealthy", "unknown"]
    if core_unhealthy:
        overall = "unhealthy"
    elif version_mismatch or any(
        component.status in {"degraded", "unhealthy"} for component in components
    ):
        overall = "degraded"
    elif all(component.status == "unknown" for component in components):
        overall = "unknown"
    else:
        overall = "healthy"
    recent_events = [
        HealthEvent(
            occurred_at=checked_at,
            component=component.key,
            status=component.status,
            summary=component.summary,
        )
        for component in components
        if component.status in {"degraded", "unhealthy"}
    ][:10]
    return SystemHealthResponse(
        status=overall,
        checked_at=checked_at,
        components=components,
        versions={
            "backend": settings.power_monitor_version,
            "backend_commit": settings.release_commit,
            "frontend": frontend_version,
            "api_schema": "1.0.0",
            "protocol": settings.protocol_version,
            "compatibility": "mismatch" if version_mismatch else "compatible",
        },
        recent_events=recent_events,
    )


@router.get("/api/v1/system/compatibility")
async def system_compatibility(principal: Viewer) -> dict[str, Any]:
    settings = get_settings()
    return {
        "product": settings.app_name,
        "backend_version": settings.power_monitor_version,
        "backend_commit": settings.release_commit,
        "api_schema_version": "1.0.0",
        "bill_import_context_schema_version": "utility-account-rate-context/1.0",
        "protocol_version": settings.protocol_version,
    }


@router.get("/api/v1/metrics", response_class=PlainTextResponse)
async def metrics(principal: Viewer, session: DbSession) -> PlainTextResponse:
    if "settings.view" not in principal.permissions:
        raise ProblemError(403, "Permission denied", "Settings permission is required", "forbidden")
    registry = CollectorRegistry()
    device_count = Gauge(
        "power_monitor_devices", "Devices by status", ["status"], registry=registry
    )
    heartbeat_total = Gauge(
        "power_monitor_heartbeats_total", "Persisted heartbeats", registry=registry
    )
    reading_total = Gauge(
        "power_monitor_readings_total", "Persisted durable readings", registry=registry
    )
    active_alerts = Gauge("power_monitor_active_alerts", "Active alerts", registry=registry)
    pending_rate_candidates = Gauge(
        "power_monitor_rate_candidates_pending",
        "Rate candidates awaiting administrator review",
        registry=registry,
    )
    rows = (
        await session.execute(select(Device.status, func.count()).group_by(Device.status))
    ).all()
    for status, count in rows:
        device_count.labels(status=status).set(count)
    heartbeat_total.set(
        await session.scalar(select(func.count()).select_from(DeviceHeartbeat)) or 0
    )
    reading_total.set(await session.scalar(select(func.count()).select_from(RawReading)) or 0)
    active_alerts.set(
        await session.scalar(
            select(func.count()).select_from(AlertInstance).where(AlertInstance.status == "active")
        )
        or 0
    )
    pending_rate_candidates.set(
        await session.scalar(
            select(func.count())
            .select_from(RateChangeCandidate)
            .where(RateChangeCandidate.status == "pending_review")
        )
        or 0
    )
    return PlainTextResponse(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


async def _sse_stream(
    site_id: str | None,
    allowed_site_ids: frozenset[str] | None,
    settings: Settings,
) -> Any:
    last_heartbeat_ids: dict[str, str] = {}
    last_reading_sequences: dict[str, int] = {}
    last_statuses: dict[str, str] = {}
    while True:
        async with session_factory()() as session:
            query = select(Device).where(Device.lifecycle_status == "active")
            if site_id:
                query = query.where(Device.site_id == site_id)
            elif allowed_site_ids is not None:
                query = query.where(Device.site_id.in_(allowed_site_ids))
            devices = list(await session.scalars(query))
            measurements, heartbeats, readings = await load_latest_measurements(
                session, devices, settings
            )
            compact = [
                {
                    "id": device.id,
                    "status": device.status,
                    "last_seen_at": device.last_seen_at.isoformat()
                    if device.last_seen_at
                    else None,
                    "firmware_version": device.firmware_version,
                    "latest_measurement_at": _iso_or_none(measurements[device.id].measured_at),
                    "measurement_freshness": measurements[device.id].freshness_state,
                }
                for device in devices
            ]
            heartbeat_ids = {key: item.id for key, item in heartbeats.items()}
            reading_sequences = {key: item.sequence for key, item in readings.items()}
            statuses = {device.id: device.status for device in devices}
        emitted = False
        if heartbeat_ids != last_heartbeat_ids:
            payload = json.dumps(
                {"type": "heartbeat", "site_id": site_id, "devices": compact},
                separators=(",", ":"),
            )
            yield f"event: heartbeat\ndata: {payload}\n\n"
            last_heartbeat_ids = heartbeat_ids
            emitted = True
            logger.info("SSE_EVENT_PUBLISHED", event_name="heartbeat", site_id=site_id)
        if reading_sequences != last_reading_sequences:
            payload = json.dumps(
                {"type": "reading", "site_id": site_id, "devices": compact},
                separators=(",", ":"),
            )
            yield f"event: reading\ndata: {payload}\n\n"
            last_reading_sequences = reading_sequences
            emitted = True
            logger.info("SSE_EVENT_PUBLISHED", event_name="reading", site_id=site_id)
        if statuses != last_statuses:
            payload = json.dumps(
                {"type": "device_status", "site_id": site_id, "devices": compact},
                separators=(",", ":"),
            )
            yield f"event: device_status\ndata: {payload}\n\n"
            last_statuses = statuses
            emitted = True
            logger.info("SSE_EVENT_PUBLISHED", event_name="device_status", site_id=site_id)
        if not emitted:
            yield ": keepalive\n\n"
        await asyncio.sleep(5)


@router.get("/api/v1/events/stream", response_class=StreamingResponse)
async def live_events(
    principal: Viewer,
    settings: AppSettings,
    site_id: str | None = None,
) -> StreamingResponse:
    if "devices.view" not in principal.permissions:
        raise ProblemError(
            403, "Permission denied", "Device view permission is required", "forbidden"
        )
    if site_id and not principal.can_access_site(site_id):
        raise ProblemError(404, "Resource not found", "Resource does not exist", "resource_missing")
    return StreamingResponse(
        _sse_stream(
            site_id,
            None if principal.all_sites else principal.site_ids,
            settings,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
