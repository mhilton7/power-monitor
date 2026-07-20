from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from sqlalchemy import func, select, text

from app import __version__
from app.api.deps import DbSession, Viewer
from app.config import get_settings
from app.db.models import AlertInstance, Device, DeviceHeartbeat, RawReading, WorkerState
from app.db.session import session_factory
from app.problem import ProblemError

router = APIRouter(tags=["system"])
_time_requests: dict[str, deque[float]] = defaultdict(deque)


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "live", "version": __version__}


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
async def system_info(_viewer: Viewer, session: DbSession) -> dict[str, Any]:
    settings = get_settings()
    worker = await session.get(WorkerState, "main")
    return {
        "product": settings.app_name,
        "version": __version__,
        "protocol": settings.protocol_version,
        "python_runtime": "3.13 production image",
        "worker": {
            "status": worker.status if worker else "not_started",
            "last_loop_at": worker.last_loop_at if worker else None,
            "last_success_at": worker.last_success_at if worker else None,
        },
        "defaults": {
            "site": settings.default_site_name,
            "timezone": settings.default_timezone,
            "currency": settings.default_currency,
            "heartbeat_seconds": settings.heartbeat_expectation_seconds,
        },
    }


@router.get("/api/v1/metrics", response_class=PlainTextResponse)
async def metrics(_viewer: Viewer, session: DbSession) -> PlainTextResponse:
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
    return PlainTextResponse(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


async def _sse_stream(site_id: str | None) -> Any:
    last_payload = ""
    while True:
        async with session_factory()() as session:
            query = select(Device).where(Device.lifecycle_status == "active")
            if site_id:
                query = query.where(Device.site_id == site_id)
            devices = list(await session.scalars(query))
            compact = [
                {
                    "id": device.id,
                    "status": device.status,
                    "last_seen_at": device.last_seen_at.isoformat()
                    if device.last_seen_at
                    else None,
                    "firmware_version": device.firmware_version,
                }
                for device in devices
            ]
        payload = json.dumps({"type": "fleet", "devices": compact}, separators=(",", ":"))
        if payload != last_payload:
            yield f"event: fleet\ndata: {payload}\n\n"
            last_payload = payload
        else:
            yield ": keepalive\n\n"
        await asyncio.sleep(5)


@router.get("/api/v1/events/stream", response_class=StreamingResponse)
async def live_events(_viewer: Viewer, site_id: str | None = None) -> StreamingResponse:
    return StreamingResponse(
        _sse_stream(site_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
