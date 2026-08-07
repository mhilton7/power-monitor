from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes.system import _sse_stream
from app.config import Settings
from app.db.models import (
    AccountUsageAuthority,
    AggregateMember,
    AggregateSet,
    AuditEvent,
    BillingCycle,
    Circuit,
    Device,
    DeviceCredential,
    DeviceHeartbeat,
    DeviceLifecycleEvent,
    DeviceSiteAssignment,
    LogExportJob,
    RawReading,
    Utility,
    UtilityAccount,
)
from app.logging import DailyJsonLogWriter, maintain_log_directory, retention_boundary
from app.security.protocol import PROTOCOL, sign_headers


def test_shared_log_volume_permissions_cover_api_worker_and_backup() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    for service_name in ("api", "worker", "backup"):
        assert "log_data:/data/logs" in services[service_name]["volumes"]
    assert services["backup"]["group_add"] == ["10001"]
    assert "chmod 2770 /data/logs" in (root / "deploy/docker/backend.Dockerfile").read_text(
        encoding="utf-8"
    )
    backend_dockerfile = (root / "deploy/docker/backend.Dockerfile").read_text(encoding="utf-8")
    assert "/app/data/rate-source-artifacts/utility-bills" in backend_dockerfile
    assert "chown -R power-monitor:power-monitor /app /data /srv" in backend_dockerfile
    assert "chmod 2770 /data/logs" in (root / "deploy/docker/backup.Dockerfile").read_text(
        encoding="utf-8"
    )
    assert 'group_add: ["10001"]' in (root / "deploy/truenas/compose.yaml").read_text(
        encoding="utf-8"
    )
    assert "umask 0007" in (root / "scripts/container-log.sh").read_text(encoding="utf-8")


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


async def bootstrap(client: httpx.AsyncClient, email: str = "admin@example.com") -> None:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": email,
            "display_name": "Dashboard Admin",
            "password": "Long-Production-Password-42!",
        },
    )
    assert response.status_code == 201, response.text


def _write_log(path: Path, category: str, day: datetime, payload: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    destination = path / f"{category}-{day.date().isoformat()}.jsonl"
    destination.write_text(json.dumps(payload) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_log_export_defaults_zip_manifest_hashes_redaction_and_cleanup(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.log_path = tmp_path / "logs"
    await bootstrap(client)
    now = datetime.now(UTC)
    _write_log(
        test_settings.log_path,
        "api",
        now,
        {
            "event": "http_request",
            "request_id": "correlation-safe",
            "password": "must-not-export",
            "headers": {"Authorization": "Bearer must-not-export-either"},
            "message": (
                "Authorization: Bearer must-not-export-from-message "
                "postgresql+asyncpg://power:must-not-export-db@postgres/power"
            ),
        },
    )
    _write_log(
        test_settings.log_path,
        "worker",
        now - timedelta(days=1),
        {"event": "worker_loop", "device_id": "device-safe", "status": "ok"},
    )

    availability = await client.get("/api/v1/admin/logs/availability")
    assert availability.status_code == 200, availability.text
    assert availability.json()["retention_days"] == 90
    assert availability.json()["earliest_date"] == (now - timedelta(days=1)).date().isoformat()

    created = await client.post("/api/v1/admin/logs/exports", headers=csrf(client), json={})
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["start_date"] == (now.date() - timedelta(days=6)).isoformat()
    assert job["end_date"] == now.date().isoformat()
    assert job["status"] == "ready"
    assert set(job["services"]) == {
        "api",
        "worker",
        "enrollment",
        "device_sync",
        "rate_sync",
        "backup",
    }
    status = await client.get(f"/api/v1/admin/logs/exports/{job['id']}")
    assert status.status_code == 200 and status.json()["download_url"]

    async with session_factory_fixture() as session:
        stored_job = await session.get(LogExportJob, job["id"])
        assert stored_job is not None and stored_job.file_path
        temporary_path = Path(stored_job.file_path)
        assert temporary_path.is_file()

    downloaded = await client.get(job["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/zip"
    assert not temporary_path.exists()
    assert b"must-not-export" not in downloaded.content
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["application_version"] == test_settings.power_monitor_version
        assert manifest["log_format_version"] == "pm-log/1.0.0"
        assert manifest["requesting_administrator_id"]
        assert len(manifest["files"]) == 2
        for item in manifest["files"]:
            content = archive.read(item["filename"])
            assert len(content) == item["size_bytes"]
            assert hashlib.sha256(content).hexdigest() == item["sha256"]
            assert b"[REDACTED]" in content or item["service"] == "worker"

    async with session_factory_fixture() as session:
        audit = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "logs.export_completed", AuditEvent.object_id == job["id"]
            )
        )
        assert audit is not None
        assert audit.actor_id == manifest["requesting_administrator_id"]
        assert audit.details["export_size_bytes"] == job["size_bytes"]
        assert audit.details["correlation_id"]


@pytest.mark.asyncio
async def test_log_export_range_service_validation_empty_and_size_limit(
    api_client: Any,
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.log_path = tmp_path / "logs"
    await bootstrap(client, "log-ranges@example.com")
    today = datetime.now(UTC)
    boundary = retention_boundary(today.date())
    boundary_time = datetime.combine(boundary, datetime.min.time(), UTC)
    _write_log(test_settings.log_path, "worker", boundary_time, {"event": "boundary"})

    valid = await client.post(
        "/api/v1/admin/logs/exports",
        headers=csrf(client),
        json={
            "start_date": boundary.isoformat(),
            "end_date": today.date().isoformat(),
            "services": ["worker"],
        },
    )
    assert valid.status_code == 201, valid.text
    assert valid.json()["services"] == ["worker"]
    assert (await client.get(valid.json()["download_url"])).status_code == 200

    too_old = await client.post(
        "/api/v1/admin/logs/exports",
        headers=csrf(client),
        json={
            "start_date": (boundary - timedelta(days=1)).isoformat(),
            "end_date": today.date().isoformat(),
        },
    )
    assert too_old.status_code == 422
    assert too_old.json()["code"] == "log_range_before_retention"
    reversed_range = await client.post(
        "/api/v1/admin/logs/exports",
        headers=csrf(client),
        json={
            "start_date": today.date().isoformat(),
            "end_date": boundary.isoformat(),
        },
    )
    assert reversed_range.status_code == 422
    assert reversed_range.json()["code"] == "reversed_log_range"
    traversal = await client.post(
        "/api/v1/admin/logs/exports",
        headers=csrf(client),
        json={"services": ["../../secrets"]},
    )
    assert traversal.status_code == 422
    assert traversal.json()["code"] == "invalid_log_service"
    empty = await client.post(
        "/api/v1/admin/logs/exports",
        headers=csrf(client),
        json={"services": ["api"]},
    )
    assert empty.status_code == 404
    assert empty.json()["code"] == "logs_not_available"

    oversized = "x" * (1024 * 1024 + 1024)
    _write_log(test_settings.log_path, "api", today, {"event": "large", "message": oversized})
    test_settings.max_log_export_bytes = 1024 * 1024
    too_large = await client.post(
        "/api/v1/admin/logs/exports",
        headers=csrf(client),
        json={"services": ["api"]},
    )
    assert too_large.status_code == 413
    assert too_large.json()["code"] == "log_export_too_large"
    export_dir = test_settings.log_path / ".exports"
    assert not export_dir.exists() or not list(export_dir.iterdir())


@pytest.mark.asyncio
async def test_log_exports_reject_non_administrator(
    api_client: Any, test_settings: Settings, tmp_path: Path
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.log_path = tmp_path / "logs"
    await bootstrap(client, "root@example.com")
    created = await client.post(
        "/api/v1/users",
        headers=csrf(client),
        json={
            "email": "viewer@example.com",
            "display_name": "Viewer",
            "password": "Viewer-Production-Password-42!",
            "roles": ["viewer"],
        },
    )
    assert created.status_code == 201
    assert (await client.post("/api/v1/auth/logout", headers=csrf(client))).status_code == 204
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "viewer@example.com", "password": "Viewer-Production-Password-42!"},
    )
    assert login.status_code == 200
    response = await client.get("/api/v1/admin/logs/availability")
    assert response.status_code == 403


def test_durable_log_redaction_and_retention_preserve_current_day(tmp_path: Path) -> None:
    log_path = tmp_path / "logs"
    now = datetime.now(UTC)
    writer = DailyJsonLogWriter(log_path, "api", 90)
    writer(
        None,
        "info",
        {
            "event": "credential_test",
            "device_id": "safe-device-id",
            "password": "hidden-password",
            "nested": {"api_key": "hidden-api-key", "status": "safe"},
            "message": "Authorization: Bearer hidden-message-token",
        },
    )
    current = log_path / f"api-{now.date().isoformat()}.jsonl"
    content = current.read_text(encoding="utf-8")
    assert "safe-device-id" in content and '"status":"safe"' in content
    assert "hidden-password" not in content and "hidden-api-key" not in content
    assert "hidden-message-token" not in content

    old_date = now - timedelta(days=91)
    completed_date = now - timedelta(days=1)
    _write_log(log_path, "worker", old_date, {"event": "expired"})
    _write_log(log_path, "worker", completed_date, {"event": "completed"})
    maintain_log_directory(log_path, now=now, retention_days=90)
    assert current.is_file()
    assert not (log_path / f"worker-{old_date.date().isoformat()}.jsonl").exists()
    assert (log_path / f"worker-{completed_date.date().isoformat()}.jsonl.gz").is_file()


async def _enroll_sensor(client: httpx.AsyncClient, hardware_id: str) -> tuple[str, bytes]:
    sites = (await client.get("/api/v1/sites")).json()
    token = await client.post(
        "/api/v1/enrollment-tokens",
        headers=csrf(client),
        json={"site_id": sites[0]["id"], "name": "Removal Test Sensor"},
    )
    assert token.status_code == 201, token.text
    claim = await client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token.json()["token"],
            "protocol_version": PROTOCOL,
            "hardware_id": hardware_id,
            "capabilities": {
                "hardware_target": "esp32-s3-pzem004t-v4",
                "pzem_model": "PZEM-004T V4.0",
                "sd_present": True,
                "sd_required": True,
                "supported_endpoints": ["health", "readings"],
            },
        },
    )
    assert claim.status_code == 201, claim.text
    return claim.json()["device_id"], claim.json()["enrollment_secret"].encode()


def _heartbeat(device_id: str) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL,
        "schema_version": "heartbeat/1.0.0",
        "device_id": device_id,
        "boot_id": "123e4567-e89b-12d3-a456-426614174000",
        "firmware_version": "1.0.0",
        "firmware_build_hash": "abc123",
        "uptime_seconds": 30,
        "reboot_reason": "power_on",
        "current_ip": "192.168.1.50",
        "rssi_dbm": -50,
        "connection_mode": "push",
        "latest": {
            "measured_at": datetime.now(UTC).isoformat(),
            "voltage_v": "120",
            "current_a": "5",
            "power_w": "600",
            "power_factor": "1",
            "frequency_hz": "60",
            "energy_wh": "10",
        },
        "pzem": {"ok": True, "status": "ok"},
        "sd": {"ok": True, "status": "ok"},
        "oldest_stored_sequence": 1,
        "newest_stored_sequence": 1,
        "server_ack_sequence": 0,
        "backlog_estimate": 1,
        "configuration_version": 1,
        "time": {"trusted": True, "source": "sntp"},
        "resources": {"heap": 100000},
        "queue": {"pending": 1},
    }


async def _send_heartbeat(
    client: httpx.AsyncClient, device_id: str, secret: bytes
) -> httpx.Response:
    payload = _heartbeat(device_id)
    body = json.dumps(payload, separators=(",", ":")).encode()
    return await client.post(
        "/api/v1/device-heartbeats",
        content=body,
        headers={
            **sign_headers(
                secret=secret,
                device_id=device_id,
                direction="device-to-server",
                method="POST",
                target="/api/v1/device-heartbeats",
                body=body,
            ),
            "Content-Type": "application/json",
        },
    )


@pytest.mark.asyncio
async def test_live_measurement_is_consistent_between_devices_and_fleet(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: httpx.AsyncClient = api_client
    await bootstrap(client, "live-consistency@example.com")
    device_id, secret = await _enroll_sensor(client, "esp32-live-consistency")
    payload = _heartbeat(device_id)
    payload["latest"] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "voltage_v": "120.4",
        "current_a": "0.01",
        "power_w": "0.8",
        "power_factor": "0.83",
        "frequency_hz": "60.0",
        "energy_wh": "10",
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    heartbeat = await client.post(
        "/api/v1/device-heartbeats",
        content=body,
        headers={
            **sign_headers(
                secret=secret,
                device_id=device_id,
                direction="device-to-server",
                method="POST",
                target="/api/v1/device-heartbeats",
                body=body,
            ),
            "Content-Type": "application/json",
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text

    devices_response = await client.get("/api/v1/devices")
    fleet_response = await client.get("/api/v1/fleet/summary")
    assert devices_response.status_code == 200, devices_response.text
    assert fleet_response.status_code == 200, fleet_response.text
    device = next(item for item in devices_response.json() if item["id"] == device_id)
    fleet = fleet_response.json()
    assert Decimal(device["current_watts"]) == Decimal("0.8")
    assert Decimal(device["voltage_volts"]) == Decimal("120.4")
    assert Decimal(device["current_amps"]) == Decimal("0.01")
    assert Decimal(device["frequency_hz"]) == Decimal("60.0")
    assert Decimal(device["power_factor"]) == Decimal("0.83")
    assert device["measurement_freshness"] == "live"
    assert device["latest_measurement_at"] is not None
    assert device["measurement_received_at"] is not None
    assert Decimal(fleet["current_load_w"]) == Decimal("0.8")
    assert fleet["reporting_devices"] == 1
    assert fleet["has_live_data"] is True
    assert fleet["latest_data_at"] is not None
    assert fleet["latest_measurement_at"] == fleet["latest_data_at"]
    assert fleet["latest_received_at"] == device["measurement_received_at"]
    assert fleet["server_now"] is not None

    interval_end = datetime.now(UTC)
    interval_start = interval_end - timedelta(minutes=1)
    batch_payload = {
        "protocol_version": PROTOCOL,
        "schema_version": "reading-batch/1.0.0",
        "device_id": device_id,
        "readings": [
            {
                "sequence": 1,
                "boot_id": "123e4567-e89b-12d3-a456-426614174000",
                "interval_start": interval_start.isoformat(),
                "interval_end": interval_end.isoformat(),
                "time_trusted": True,
                "voltage_avg": "120.4",
                "current_avg": "0.01",
                "power_avg": "1.0",
                "power_factor": "0.83",
                "frequency_hz": "60.0",
                "interval_energy_wh": "0.0166667",
                "energy_method": "power_integration",
                "ct_rating_amps": "100",
                "quality_flags": [],
                "firmware_version": "1.0.0",
            }
        ],
    }
    batch_body = json.dumps(batch_payload, separators=(",", ":")).encode()
    batch = await client.post(
        "/api/v1/device-readings/batch",
        content=batch_body,
        headers={
            **sign_headers(
                secret=secret,
                device_id=device_id,
                direction="device-to-server",
                method="POST",
                target="/api/v1/device-readings/batch",
                body=batch_body,
            ),
            "Content-Type": "application/json",
        },
    )
    assert batch.status_code == 200, batch.text
    assert batch.json() == {
        "accepted": [1],
        "duplicates": [],
        "rejected": [],
        "highest_contiguous_accepted_sequence": 1,
        "missing_ranges": [],
        "data_generation": 0,
        "reset_boundary": 0,
    }
    duplicate = await client.post(
        "/api/v1/device-readings/batch",
        content=batch_body,
        headers={
            **sign_headers(
                secret=secret,
                device_id=device_id,
                direction="device-to-server",
                method="POST",
                target="/api/v1/device-readings/batch",
                body=batch_body,
            ),
            "Content-Type": "application/json",
        },
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["accepted"] == []
    assert duplicate.json()["duplicates"] == [1]
    assert duplicate.json()["highest_contiguous_accepted_sequence"] == 1

    async with session_factory_fixture() as session:
        stored = await session.scalar(
            select(RawReading).where(
                RawReading.device_id == device_id,
                RawReading.sequence == 1,
            )
        )
        assert stored is not None
        assert stored.site_id == device["site_id"]
        stored_interval_end = (
            stored.interval_end.replace(tzinfo=UTC)
            if stored.interval_end.tzinfo is None
            else stored.interval_end.astimezone(UTC)
        )
        assert stored_interval_end == interval_end
        assert stored.power_avg == Decimal("1.0")
        assert stored.voltage_avg == Decimal("120.4")
        assert stored.current_avg == Decimal("0.01")

    history = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={
            "scope": {"type": "device", "device_id": device_id},
            "display_mode": "individual",
            "metrics": [
                "power_w",
                "energy_kwh",
                "voltage_v",
                "current_a",
                "power_factor",
                "frequency_hz",
            ],
            "start_utc": (interval_start - timedelta(seconds=1)).isoformat(),
            "end_utc": (interval_end + timedelta(seconds=1)).isoformat(),
            "bucket": "raw",
            "timezone": "America/Los_Angeles",
        },
    )
    assert history.status_code == 200, history.text
    series = history.json()["individual"]
    assert len(series) == 1
    assert series[0]["device_id"] == device_id
    points = series[0]["points"]
    assert points
    # Raw History deliberately preserves empty buckets as null-valued gaps. The
    # one-second guard around this reading can cross a bucket boundary depending
    # on the runner clock, so validate committed readings without converting an
    # intentional gap to Decimal (or silently treating it as zero).
    measured_points = [point for point in points if point["average_power_w"] is not None]
    assert measured_points
    assert all(Decimal(point["average_power_w"]) == Decimal("1.0") for point in measured_points)
    assert all(Decimal(point["voltage_avg_v"]) == Decimal("120.4") for point in measured_points)
    assert all(Decimal(point["current_a"]) == Decimal("0.01") for point in measured_points)

    monkeypatch.setattr(
        "app.api.routes.system.session_factory",
        lambda: session_factory_fixture,
    )
    event_stream = _sse_stream(device["site_id"], None, test_settings)
    try:
        heartbeat_event = await anext(event_stream)
        status_event = await anext(event_stream)
    finally:
        await event_stream.aclose()
    assert heartbeat_event.startswith("event: heartbeat\n")
    assert status_event.startswith("event: device_status\n")
    assert device_id in heartbeat_event
    assert '"measurement_freshness":"live"' in heartbeat_event


@pytest.mark.asyncio
async def test_server_receipt_freshness_drives_offline_and_immediate_recovery(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: httpx.AsyncClient = api_client
    await bootstrap(client, "heartbeat-freshness@example.com")
    device_id, secret = await _enroll_sensor(client, "esp32-heartbeat-freshness")

    async def send_evidenced_heartbeat() -> httpx.Response:
        payload = _heartbeat(device_id)
        payload["resources"] = {
            "synchronization": {
                "last_local_deferral_reason": "internal_heap_fragmented",
            }
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        return await client.post(
            "/api/v1/device-heartbeats",
            content=body,
            headers={
                **sign_headers(
                    secret=secret,
                    device_id=device_id,
                    direction="device-to-server",
                    method="POST",
                    target="/api/v1/device-heartbeats",
                    body=body,
                ),
                "Content-Type": "application/json",
            },
        )

    accepted = await send_evidenced_heartbeat()
    assert accepted.status_code == 200, accepted.text
    fresh = next(
        item for item in (await client.get("/api/v1/devices")).json() if item["id"] == device_id
    )
    assert fresh["heartbeat_freshness"] == "online"
    assert fresh["heartbeat_age_seconds"] <= 1
    assert fresh["offline_after_seconds"] == 30
    assert fresh["previous_outage_reason"] == (
        "Sensor previously deferred synchronization because internal heap was fragmented."
    )

    async with session_factory_fixture() as session:
        stored_heartbeat = await session.scalar(
            select(DeviceHeartbeat)
            .where(DeviceHeartbeat.device_id == device_id)
            .order_by(DeviceHeartbeat.received_at.desc())
            .limit(1)
        )
        stored_device = await session.get(Device, device_id)
        assert stored_heartbeat is not None and stored_device is not None
        stored_heartbeat.received_at = datetime.now(UTC) - timedelta(seconds=31)
        # Contradict the receipt row: the denormalized label must not be authority.
        stored_device.last_seen_at = datetime.now(UTC)
        stored_device.status = "online_synchronized"
        await session.commit()

    stale = next(
        item for item in (await client.get("/api/v1/devices")).json() if item["id"] == device_id
    )
    stale_fleet = (await client.get("/api/v1/fleet/summary")).json()
    assert stale["status"] == "offline"
    assert stale["heartbeat_freshness"] == "offline"
    assert stale["measurement_freshness"] == "offline"
    assert stale["previous_outage_reason"] is None
    assert stale_fleet["online_devices"] == 0
    assert stale_fleet["reporting_devices"] == 0
    assert stale_fleet["has_live_data"] is False
    offline_filter = (await client.get("/api/v1/devices?status=offline")).json()
    assert [item["id"] for item in offline_filter] == [device_id]

    monkeypatch.setattr(
        "app.api.routes.system.session_factory",
        lambda: session_factory_fixture,
    )

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.api.routes.system.asyncio.sleep", no_wait)
    event_stream = _sse_stream(fresh["site_id"], None, test_settings)
    stale_heartbeat_event = await anext(event_stream)
    stale_status_event = await anext(event_stream)
    assert stale_heartbeat_event.startswith("event: heartbeat\n")
    assert stale_status_event.startswith("event: device_status\n")
    assert '"heartbeat_freshness":"offline"' in stale_status_event

    recovered = await send_evidenced_heartbeat()
    assert recovered.status_code == 200, recovered.text
    recovered_heartbeat_event = await anext(event_stream)
    recovered_status_event = await anext(event_stream)
    await event_stream.aclose()
    assert recovered_heartbeat_event.startswith("event: heartbeat\n")
    assert recovered_status_event.startswith("event: device_status\n")
    assert '"heartbeat_freshness":"online"' in recovered_status_event
    live = next(
        item for item in (await client.get("/api/v1/devices")).json() if item["id"] == device_id
    )
    live_fleet = (await client.get("/api/v1/fleet/summary")).json()
    assert live["heartbeat_freshness"] == "online"
    assert live["measurement_freshness"] == "live"
    assert live_fleet["online_devices"] == 1
    assert live_fleet["reporting_devices"] == 1
    assert live_fleet["has_live_data"] is True
    assert (await client.get("/api/v1/devices?status=offline")).json() == []


@pytest.mark.asyncio
async def test_fleet_combines_complete_non_overlapping_service_sensors(
    api_client: Any,
) -> None:
    client: httpx.AsyncClient = api_client
    await bootstrap(client, "multi-sensor-live-total@example.com")
    site = (await client.get("/api/v1/sites")).json()[0]
    account_response = await client.post(
        "/api/v1/utility-accounts",
        headers=csrf(client),
        json={
            "site_id": site["id"],
            "name": "Single Home Electric Service",
            "timezone": site["timezone"],
            "currency": site["currency"],
            "billing_cycle_start_day": 1,
            "generation_provider": "sce",
        },
    )
    assert account_response.status_code == 201, account_response.text
    account = account_response.json()
    sensor_details = [
        ("esp32-indoor-live-total", "Indoor AC", "1.1", True),
        ("esp32-outdoor-live-total", "Outdoor AC", "1.0", False),
    ]
    enrolled: list[tuple[str, bytes, str]] = []
    for hardware_id, circuit_name, watts, included in sensor_details:
        device_id, secret = await _enroll_sensor(client, hardware_id)
        circuit_response = await client.post(
            "/api/v1/circuits",
            headers=csrf(client),
            json={
                "site_id": site["id"],
                "parent_id": None,
                "name": circuit_name,
                "measurement_role": "branch",
                "split_phase_group": None,
            },
        )
        assert circuit_response.status_code == 201, circuit_response.text
        assignment = await client.put(
            f"/api/v1/admin/devices/{device_id}/measurement-assignment",
            headers=csrf(client),
            json={
                "circuit_id": circuit_response.json()["id"],
                "utility_account_id": account["id"],
                "include_in_default_site_total": included,
                "reason": f"Verified {circuit_name} branch assignment",
            },
        )
        assert assignment.status_code == 200, assignment.text
        enrolled.append((device_id, secret, watts))

    for device_id, secret, watts in enrolled:
        payload = _heartbeat(device_id)
        payload["latest"] = {
            "measured_at": datetime.now(UTC).isoformat(),
            "voltage_v": "115.8",
            "current_a": "0.01",
            "power_w": watts,
            "power_factor": "1.00",
            "frequency_hz": "60.0",
            "energy_wh": "10",
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        heartbeat = await client.post(
            "/api/v1/device-heartbeats",
            content=body,
            headers={
                **sign_headers(
                    secret=secret,
                    device_id=device_id,
                    direction="device-to-server",
                    method="POST",
                    target="/api/v1/device-heartbeats",
                    body=body,
                ),
                "Content-Type": "application/json",
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text

    devices_response = await client.get(f"/api/v1/devices?site_id={site['id']}")
    fleet_response = await client.get(f"/api/v1/fleet/summary?site_id={site['id']}")
    assert devices_response.status_code == 200, devices_response.text
    assert fleet_response.status_code == 200, fleet_response.text
    devices = devices_response.json()
    assert sorted(Decimal(item["current_watts"]) for item in devices) == [
        Decimal("1.0"),
        Decimal("1.1"),
    ]
    assert sum(item["included_in_default"] for item in devices) == 1
    fleet = fleet_response.json()
    assert Decimal(fleet["current_load_w"]) == Decimal("2.1")
    assert fleet["reporting_devices"] == 2
    assert fleet["has_live_data"] is True
    assert fleet["latest_data_at"] is not None


@pytest.mark.asyncio
async def test_signed_unavailable_sequence_ranges_advance_device_cursor(
    api_client: Any,
) -> None:
    client: httpx.AsyncClient = api_client
    await bootstrap(client, "sync-range-admin@example.com")
    device_id, secret = await _enroll_sensor(client, "esp32-sync-ranges")
    interval_end = datetime.now(UTC)

    def reading_payload(sequence: int) -> dict[str, Any]:
        reading_end = interval_end + timedelta(seconds=sequence)
        return {
            "sequence": sequence,
            "boot_id": "123e4567-e89b-12d3-a456-426614174000",
            "interval_start": (reading_end - timedelta(seconds=15)).isoformat(),
            "interval_end": reading_end.isoformat(),
            "time_trusted": True,
            "voltage_avg": "120.4",
            "current_avg": "0.01",
            "power_avg": "1.0",
            "power_factor": "0.83",
            "frequency_hz": "60.0",
            "interval_energy_wh": "0.0041667",
            "energy_method": "power_integration",
            "ct_rating_amps": "100",
            "quality_flags": [],
            "firmware_version": "1.0.0",
        }

    payload = {
        "protocol_version": PROTOCOL,
        "schema_version": "reading-batch/1.0.0",
        "device_id": device_id,
        "readings": [reading_payload(9), reading_payload(11)],
        "unavailable_sequence_ranges": [
            {"start_sequence": 1, "end_sequence": 8},
            {"start_sequence": 10, "end_sequence": 10},
        ],
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    response = await client.post(
        "/api/v1/device-readings/batch",
        content=body,
        headers={
            **sign_headers(
                secret=secret,
                device_id=device_id,
                direction="device-to-server",
                method="POST",
                target="/api/v1/device-readings/batch",
                body=body,
            ),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "accepted": [9, 11],
        "duplicates": [],
        "rejected": [],
        "highest_contiguous_accepted_sequence": 11,
        "missing_ranges": [],
        "data_generation": 0,
        "reset_boundary": 0,
    }


@pytest.mark.asyncio
async def test_sensor_unclaim_retains_history_revokes_and_reenrolls_with_new_secret(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client: httpx.AsyncClient = api_client
    await bootstrap(client, "sensor-admin@example.com")
    device_id, old_secret = await _enroll_sensor(client, "esp32-removal-001")
    assert (await _send_heartbeat(client, device_id, old_secret)).status_code == 200
    async with session_factory_fixture() as session:
        device = await session.get(Device, device_id)
        assert device is not None
        utility = await session.scalar(select(Utility).order_by(Utility.name))
        assert utility is not None
        circuit = Circuit(
            site_id=device.site_id,
            name="Removal test circuit",
            measurement_role="main",
        )
        account = UtilityAccount(
            site_id=device.site_id,
            utility_id=utility.id,
            name="Removal test electric service",
        )
        session.add_all((circuit, account))
        await session.flush()
        aggregate = AggregateSet(
            site_id=device.site_id,
            utility_account_id=account.id,
            name="Removal test billing group",
            cost_scope="allocated_account",
        )
        session.add(aggregate)
        await session.flush()
        direct_member = AggregateMember(
            aggregate_set_id=aggregate.id,
            device_id=device.id,
            allocation_percent=Decimal("100"),
        )
        usage_authority = AccountUsageAuthority(
            utility_account_id=account.id,
            authority_type="whole_account_meter",
            calculation_role="sensor_measurements",
            device_ids=[device.id],
            confidence="high",
            complete_account=True,
            updated_at=datetime.now(UTC),
        )
        billing_cycle = BillingCycle(
            utility_account_id=account.id,
            starts_at=datetime.now(UTC) - timedelta(days=15),
            ends_at=datetime.now(UTC) + timedelta(days=15),
            status="confirmed",
        )
        session.add_all((direct_member, usage_authority, billing_cycle))
        device.circuit_id = circuit.id
        device.utility_account_id = account.id
        device.measurement_role = "main"
        device.cost_scope = "allocated_account"
        device.include_in_default_site_total = True
        reading = RawReading(
            device_id=device.id,
            site_id=device.site_id,
            sequence=1,
            boot_id="123e4567-e89b-12d3-a456-426614174000",
            interval_start=datetime.now(UTC) - timedelta(minutes=1),
            interval_end=datetime.now(UTC),
            time_trusted=True,
            energy_method="device_interval",
            ct_rating_amps=device.ct_rating_amps,
            quality_flags=[],
            firmware_version="1.0.0",
            record_hash="a" * 64,
            original_payload={},
            ingestion_source="push",
            ingested_at=datetime.now(UTC),
        )
        session.add(reading)
        await session.commit()
        aggregate_id = aggregate.id
        account_id = account.id
        usage_authority_id = usage_authority.id
        billing_cycle_id = billing_cycle.id

    fleet_before = (await client.get("/api/v1/fleet/summary")).json()
    removed = await client.post(
        f"/api/v1/admin/devices/{device_id}/unclaim",
        headers=csrf(client),
        json={"confirmation": "Removal Test Sensor", "reason": "replaced"},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["historical_data_retained"] is True
    active = await client.get("/api/v1/devices?lifecycle=active")
    archived = await client.get("/api/v1/devices?lifecycle=decommissioned")
    assert all(item["id"] != device_id for item in active.json())
    archived_device = next(item for item in archived.json() if item["id"] == device_id)
    assert archived_device["decommission_reason"] == "replaced"
    assert archived_device["retained_history"] is True
    assert archived_device["re_enrollment_allowed"] is True
    fleet_after = (await client.get("/api/v1/fleet/summary")).json()
    assert fleet_after["total_devices"] == fleet_before["total_devices"] - 1
    assert fleet_after["estimated_cost_today"] == fleet_before["estimated_cost_today"]

    rejected = await _send_heartbeat(client, device_id, old_secret)
    assert rejected.status_code == 403
    repeated = await client.post(
        f"/api/v1/admin/devices/{device_id}/unclaim",
        headers=csrf(client),
        json={"confirmation": device_id, "reason": "replaced"},
    )
    assert repeated.status_code == 200 and repeated.json()["already_decommissioned"] is True

    detail = await client.get(f"/api/v1/devices/{device_id}")
    assert detail.json()["history"]["reading_count"] == 1
    async with session_factory_fixture() as session:
        active_credentials = await session.scalar(
            select(func.count())
            .select_from(DeviceCredential)
            .where(
                DeviceCredential.device_id == device_id,
                DeviceCredential.revoked_at.is_(None),
            )
        )
        event = await session.scalar(
            select(DeviceLifecycleEvent).where(
                DeviceLifecycleEvent.device_id == device_id,
                DeviceLifecycleEvent.event_type == "decommissioned",
            )
        )
        audit = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "device.unclaimed", AuditEvent.object_id == device_id
            )
        )
        removed_device = await session.get(Device, device_id)
        direct_member_count = await session.scalar(
            select(func.count())
            .select_from(AggregateMember)
            .where(AggregateMember.aggregate_set_id == aggregate_id)
        )
        open_site_assignment_count = await session.scalar(
            select(func.count())
            .select_from(DeviceSiteAssignment)
            .where(
                DeviceSiteAssignment.device_id == device_id,
                DeviceSiteAssignment.effective_to.is_(None),
            )
        )
        cleared_authority = await session.get(AccountUsageAuthority, usage_authority_id)
        invalidated_cycle = await session.get(BillingCycle, billing_cycle_id)
        assert active_credentials == 0
        assert removed_device is not None
        assert removed_device.circuit_id is None
        assert removed_device.utility_account_id is None
        assert removed_device.cost_scope == "energy_only"
        assert removed_device.include_in_default_site_total is False
        assert direct_member_count == 0
        assert open_site_assignment_count == 0
        assert cleared_authority is not None
        assert cleared_authority.device_ids == []
        assert cleared_authority.complete_account is False
        assert cleared_authority.confidence == "unverified"
        assert cleared_authority.revision == 2
        assert invalidated_cycle is not None
        assert invalidated_cycle.recalculation_required is True
        assert invalidated_cycle.usage_source_type == "unavailable"
        assert invalidated_cycle.tier_progress_source_type == "unavailable"
        assert invalidated_cycle.projection_source_type == "unavailable"
        assert event is not None and event.reason == "replaced" and event.actor_id
        assert event.details["cleared_assignments"]["utility_account_id"] == account_id
        assert audit is not None and audit.details["reason"] == "replaced"
        assert audit.details["cleared_assignments"]["removed_direct_aggregate_member_ids"]
        assert (
            usage_authority_id
            in audit.details["cleared_assignments"]["cleared_usage_authority_ids"]
        )
        assert (
            billing_cycle_id
            in audit.details["cleared_assignments"]["invalidated_billing_cycle_ids"]
        )
        assert audit.occurred_at

    reenrolled_id, new_secret = await _enroll_sensor(client, "esp32-removal-001")
    assert reenrolled_id == device_id
    assert new_secret != old_secret
    assert (await _send_heartbeat(client, device_id, old_secret)).status_code == 401
    assert (await _send_heartbeat(client, device_id, new_secret)).status_code == 200
    final_detail = await client.get(f"/api/v1/devices/{device_id}")
    assert final_detail.json()["history"]["reading_count"] == 1


@pytest.mark.asyncio
async def test_sensor_unclaim_authorization_confirmation_and_concurrency(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    await bootstrap(client, "concurrency-admin@example.com")
    device_id, _secret = await _enroll_sensor(client, "esp32-removal-002")
    mismatch = await client.post(
        f"/api/v1/admin/devices/{device_id}/unclaim",
        headers=csrf(client),
        json={"confirmation": "not-the-device"},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "device_confirmation_mismatch"

    headers = csrf(client)
    first, second = await asyncio.gather(
        client.post(
            f"/api/v1/admin/devices/{device_id}/unclaim",
            headers=headers,
            json={"confirmation": device_id, "reason": "testing_device"},
        ),
        client.post(
            f"/api/v1/admin/devices/{device_id}/unclaim",
            headers=headers,
            json={"confirmation": device_id, "reason": "testing_device"},
        ),
    )
    assert {first.status_code, second.status_code} == {200}, (first.text, second.text)
    assert sorted(
        [first.json()["already_decommissioned"], second.json()["already_decommissioned"]]
    ) == [False, True]

    user = await client.post(
        "/api/v1/users",
        headers=csrf(client),
        json={
            "email": "operator@example.com",
            "display_name": "Operator",
            "password": "Operator-Production-Password-42!",
            "roles": ["operator"],
        },
    )
    assert user.status_code == 201
    assert (await client.post("/api/v1/auth/logout", headers=csrf(client))).status_code == 204
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "operator@example.com",
            "password": "Operator-Production-Password-42!",
        },
    )
    assert login.status_code == 200
    forbidden = await client.post(
        f"/api/v1/admin/devices/{device_id}/unclaim",
        headers=csrf(client),
        json={"confirmation": device_id},
    )
    assert forbidden.status_code == 403
