from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from app.api.routes.device_protocol import event_batch, heartbeat
from app.config import Settings
from app.data_reset.service import (
    NO_BACKUP_CONFIRMATION_PHRASE,
    create_reset_operation,
    create_reset_plan,
    load_quarantine_journal,
    perform_central_reset,
    restore_precommit_quarantine,
    sanitize_scoped_logs,
)
from app.db.models import (
    DailyDeviceRollup,
    DataResetOperation,
    DataResetParticipant,
    DataResetPlan,
    Device,
    DeviceCapability,
    DeviceCredential,
    DeviceDataState,
    DeviceEvent,
    DeviceHeartbeat,
    DeviceSiteAssignment,
    DeviceStatusSnapshot,
    EnrollmentToken,
    MonthlyDeviceRollup,
    NormalizedInterval,
    RawReading,
    Site,
    SiteDataState,
    SyncCursor,
    User,
    new_uuid,
)
from app.problem import ProblemError
from app.schemas import DeviceEventBatch, Heartbeat
from app.security.protocol import PROTOCOL, sign_headers

PASSWORD = "Long-Production-Password-42!"
ALL_RESET_CATEGORIES = [
    "measurement_history",
    "cost_history",
    "pricing_history",
    "generated_outputs",
]


def _raw_reading(
    *,
    device_id: str,
    site_id: str,
    sequence: int,
    interval_start: datetime,
) -> RawReading:
    return RawReading(
        id=new_uuid(),
        device_id=device_id,
        site_id=site_id,
        data_generation=0,
        sequence=sequence,
        boot_id=new_uuid(),
        interval_start=interval_start,
        interval_end=interval_start + timedelta(minutes=1),
        time_trusted=True,
        power_avg=Decimal("600"),
        device_interval_energy_wh=Decimal("10"),
        energy_method="power_integration",
        ct_rating_amps=Decimal("100"),
        quality_flags=[],
        firmware_version="1.0.18",
        record_hash=f"{sequence:064x}",
        original_payload={"power_avg": "600"},
        ingestion_source="push",
        ingested_at=interval_start + timedelta(minutes=1),
    )


def _artifact_roots(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    roots = tuple(tmp_path / name for name in ("reports", "logs", "bills", "rates", "backups"))
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
    return roots


async def _no_backup_operation(
    session: AsyncSession,
    *,
    site: Site,
    user: User,
    now: datetime,
    categories: list[str],
    idempotency_key: str,
) -> tuple[DataResetPlan, DataResetOperation]:
    plan = await create_reset_plan(
        session,
        site_id=site.id,
        requested_by=user.id,
        categories=categories,
        delete_imported_bill_documents=False,
        disconnected_sensor_policy="defer_until_reconnect",
        offline_after_seconds=30,
        now=now,
    )
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key=idempotency_key,
        reason="Focused data reset regression coverage",
        backup_mode="permanent_without_backup",
        confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=True,
        offline_after_seconds=30,
        now=now,
    )
    await session.commit()
    return plan, operation


@pytest.mark.asyncio
async def test_post_activation_gated_heartbeat_does_not_stale_exact_inventory_and_survives(
    session: AsyncSession,
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    reset_at = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="gated-heartbeat-reset@example.com",
        display_name="Gated Heartbeat Reset",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Gated Heartbeat Site",
        code="gated-heartbeat-site",
        timezone="America/Los_Angeles",
    )
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="gated-heartbeat-sensor",
        name="Gated Heartbeat Sensor",
        firmware_version="1.0.18",
        firmware_build_hash="a" * 64,
        last_seen_at=reset_at - timedelta(hours=1),
    )
    old_heartbeat = DeviceHeartbeat(
        id=new_uuid(),
        device_id=device.id,
        boot_id=new_uuid(),
        received_at=reset_at - timedelta(minutes=2),
        device_time=reset_at - timedelta(minutes=2),
        current_watts=Decimal("600"),
        pzem_ok=True,
        sd_ok=True,
        time_trusted=True,
        data_generation=0,
        newest_sequence=5,
        backlog_estimate=0,
        payload={
            "newest_stored_sequence": 5,
            "server_ack_sequence": 5,
            "sd": {"details": {"sequence_floor": 5, "next_sequence": 6}},
        },
    )
    old_status = DeviceStatusSnapshot(
        id=new_uuid(),
        device_id=device.id,
        captured_at=reset_at - timedelta(minutes=2),
        status="online_synchronized",
        evidence={"heartbeat": True, "power_w": "600"},
    )
    session.add_all(
        [
            user,
            site,
            device,
            DeviceCapability(
                device_id=device.id,
                hardware_target="esp32-s3",
                pzem_model="PZEM-004T V4.0",
                sd_required=True,
                features={"data_reset": "data-reset/1.0.0"},
                reported_at=reset_at,
            ),
            SyncCursor(
                device_id=device.id,
                highest_contiguous_sequence=5,
                maximum_seen_sequence=5,
                data_generation=0,
                reset_boundary=0,
                updated_at=reset_at,
            ),
            old_heartbeat,
            old_status,
        ]
    )
    await session.commit()

    plan, operation = await _no_backup_operation(
        session,
        site=site,
        user=user,
        now=reset_at,
        categories=ALL_RESET_CATEGORIES,
        idempotency_key="gated-heartbeat-reset-1",
    )
    state = await session.get(DeviceDataState, device.id)
    assert state is not None
    assert state.ingestion_gate == "pending_reconnect"
    assert plan.plan_snapshot["counts"]["device_heartbeats"] == 1
    assert plan.plan_snapshot["counts"]["device_status_snapshots"] == 1

    gated_payload = Heartbeat.model_validate(
        {
            "protocol_version": PROTOCOL,
            "schema_version": "heartbeat/1.0.0",
            "device_id": device.id,
            "boot_id": new_uuid(),
            "firmware_version": "1.0.18",
            "firmware_build_hash": "b" * 64,
            "data_generation": 0,
            "uptime_seconds": 60,
            "reboot_reason": "software_reset",
            "connection_mode": "push",
            "latest": {
                "measured_at": reset_at.isoformat(),
                "voltage_v": "120",
                "current_a": "5",
                "power_w": "600",
                "power_factor": "1",
                "frequency_hz": "60",
                "energy_wh": "10",
            },
            "pzem": {"ok": True, "status": "ok"},
            "sd": {"ok": True, "status": "ok"},
            "oldest_stored_sequence": 5,
            "newest_stored_sequence": 5,
            "server_ack_sequence": 5,
            "backlog_estimate": 0,
            "configuration_version": 1,
            "time": {"trusted": True, "source": "sntp"},
            "data_reset": {
                "protocol": "data-reset/1.0.0",
                "state": "preparing",
                "checkpoint": "prepare",
                "operation_id": operation.id,
                "target_generation": operation.reset_generation,
                "reset_boundary": 5,
                "reset_required": True,
            },
        }
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/device-heartbeats",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("127.0.0.1", 12345),
        }
    )
    with pytest.raises(ProblemError) as gated:
        await heartbeat(
            payload=gated_payload,
            request=request,
            verified=SimpleNamespace(device=device),
            session=session,
            settings=test_settings,
        )
    assert gated.value.code == "sensor_reset_required"

    safe_heartbeat = await session.scalar(
        select(DeviceHeartbeat)
        .where(DeviceHeartbeat.id != old_heartbeat.id)
        .order_by(DeviceHeartbeat.received_at.desc())
    )
    safe_status = await session.scalar(
        select(DeviceStatusSnapshot)
        .where(DeviceStatusSnapshot.id != old_status.id)
        .order_by(DeviceStatusSnapshot.captured_at.desc())
    )
    assert safe_heartbeat is not None
    assert safe_heartbeat.current_watts is None
    assert "latest" not in safe_heartbeat.payload
    assert safe_status is not None and safe_status.status == "reset_pending"

    operation.state = "backup_verified"
    await session.commit()
    report_root, log_root, bill_root, rate_root, backup_root = _artifact_roots(tmp_path)
    counts = await perform_central_reset(
        session,
        operation=operation,
        report_root=report_root,
        log_root=log_root,
        bill_artifact_root=bill_root,
        rate_artifact_root=rate_root,
        backup_root=backup_root,
    )

    assert counts["device_heartbeats"] == 1
    assert counts["device_status_snapshots"] == 1
    assert await session.get(DeviceHeartbeat, old_heartbeat.id) is None
    assert await session.get(DeviceStatusSnapshot, old_status.id) is None
    assert await session.get(DeviceHeartbeat, safe_heartbeat.id) is not None
    assert await session.get(DeviceStatusSnapshot, safe_status.id) is not None


@pytest.mark.asyncio
async def test_transferred_device_history_blocks_unsafe_old_site_reset(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="transferred-history-reset@example.com",
        display_name="Transferred History Reset",
        password_hash="not-used",
    )
    reset_site = Site(
        id=new_uuid(),
        name="Former Site",
        code="former-site",
        timezone="America/Los_Angeles",
    )
    current_site = Site(
        id=new_uuid(),
        name="Current Site",
        code="current-site",
        timezone="America/Los_Angeles",
    )
    device = Device(
        id=new_uuid(),
        site_id=current_site.id,
        hardware_id="transferred-history-sensor",
        name="Transferred History Sensor",
    )
    former_start = datetime(2026, 1, 15, 20, tzinfo=UTC)
    current_start = datetime(2026, 2, 15, 20, tzinfo=UTC)
    former_raw = _raw_reading(
        device_id=device.id,
        site_id=reset_site.id,
        sequence=1,
        interval_start=former_start,
    )
    current_raw = _raw_reading(
        device_id=device.id,
        site_id=current_site.id,
        sequence=2,
        interval_start=current_start,
    )
    former_normalized = NormalizedInterval(
        id=new_uuid(),
        raw_reading_id=former_raw.id,
        device_id=device.id,
        interval_start=former_raw.interval_start,
        interval_end=former_raw.interval_end,
        device_energy_wh=Decimal("10"),
        server_energy_wh=Decimal("10"),
        selected_energy_wh=Decimal("10"),
        selected_method="device_interval",
        validation_result="valid",
        validation_reason="",
        algorithm_version="energy-normalizer/1",
    )
    current_normalized = NormalizedInterval(
        id=new_uuid(),
        raw_reading_id=current_raw.id,
        device_id=device.id,
        interval_start=current_raw.interval_start,
        interval_end=current_raw.interval_end,
        device_energy_wh=Decimal("10"),
        server_energy_wh=Decimal("10"),
        selected_energy_wh=Decimal("10"),
        selected_method="device_interval",
        validation_result="valid",
        validation_reason="",
        algorithm_version="energy-normalizer/1",
    )
    former_daily = DailyDeviceRollup(
        device_id=device.id,
        local_date=date(2026, 1, 15),
        timezone="America/Los_Angeles",
        energy_wh=Decimal("10"),
        peak_watts=Decimal("600"),
        coverage_percent=Decimal("100"),
        calculated_at=now,
    )
    current_daily = DailyDeviceRollup(
        device_id=device.id,
        local_date=date(2026, 2, 15),
        timezone="America/Los_Angeles",
        energy_wh=Decimal("10"),
        peak_watts=Decimal("600"),
        coverage_percent=Decimal("100"),
        calculated_at=now,
    )
    former_monthly = MonthlyDeviceRollup(
        device_id=device.id,
        month_start=date(2026, 1, 1),
        energy_wh=Decimal("10"),
        peak_watts=Decimal("600"),
        coverage_percent=Decimal("100"),
        calculated_at=now,
    )
    current_monthly = MonthlyDeviceRollup(
        device_id=device.id,
        month_start=date(2026, 2, 1),
        energy_wh=Decimal("10"),
        peak_watts=Decimal("600"),
        coverage_percent=Decimal("100"),
        calculated_at=now,
    )
    session.add_all(
        [
            user,
            reset_site,
            current_site,
            device,
            DeviceSiteAssignment(
                id=new_uuid(),
                device_id=device.id,
                site_id=reset_site.id,
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                effective_to=datetime(2026, 2, 1, tzinfo=UTC),
                assigned_by=user.id,
                reason="Historical assignment",
                created_at=now,
            ),
            DeviceSiteAssignment(
                id=new_uuid(),
                device_id=device.id,
                site_id=current_site.id,
                effective_from=datetime(2026, 2, 1, tzinfo=UTC),
                effective_to=None,
                assigned_by=user.id,
                reason="Transferred",
                created_at=now,
            ),
            former_raw,
            current_raw,
            former_normalized,
            current_normalized,
            former_daily,
            current_daily,
            former_monthly,
            current_monthly,
        ]
    )
    await session.commit()

    assert not list(await session.scalars(select(Device.id).where(Device.site_id == reset_site.id)))
    with pytest.raises(ProblemError) as blocked:
        await create_reset_plan(
            session,
            site_id=reset_site.id,
            requested_by=user.id,
            categories=ALL_RESET_CATEGORIES,
            delete_imported_bill_documents=False,
            disconnected_sensor_policy="defer_until_reconnect",
            offline_after_seconds=30,
            now=now,
        )

    assert blocked.value.code == "data_reset_historical_device_scope_unsafe"
    assert blocked.value.extra == {"device_ids": [device.id]}
    assert await session.get(RawReading, former_raw.id) is not None
    assert await session.get(NormalizedInterval, former_normalized.id) is not None
    assert await session.get(DailyDeviceRollup, (device.id, former_daily.local_date)) is not None
    assert (
        await session.get(MonthlyDeviceRollup, (device.id, former_monthly.month_start)) is not None
    )
    assert await session.get(RawReading, current_raw.id) is not None
    assert await session.get(NormalizedInterval, current_normalized.id) is not None
    assert await session.get(DailyDeviceRollup, (device.id, current_daily.local_date)) is not None
    assert (
        await session.get(MonthlyDeviceRollup, (device.id, current_monthly.month_start)) is not None
    )


@pytest.mark.asyncio
async def test_closed_historical_assignment_without_rows_still_blocks_old_site_reset(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="transferred-empty-history-reset@example.com",
        display_name="Transferred Empty History Reset",
        password_hash="not-used",
    )
    former_site = Site(id=new_uuid(), name="Former Empty Site", code="former-empty-site")
    current_site = Site(id=new_uuid(), name="Current Empty Site", code="current-empty-site")
    device = Device(
        id=new_uuid(),
        site_id=current_site.id,
        hardware_id="transferred-empty-history-sensor",
        name="Transferred Empty History Sensor",
    )
    session.add_all(
        [
            user,
            former_site,
            current_site,
            device,
            DeviceSiteAssignment(
                id=new_uuid(),
                device_id=device.id,
                site_id=former_site.id,
                effective_from=now - timedelta(days=2),
                effective_to=now - timedelta(days=1),
                assigned_by=user.id,
                reason="Historical assignment with unknowable SD backlog",
                created_at=now,
            ),
            DeviceSiteAssignment(
                id=new_uuid(),
                device_id=device.id,
                site_id=current_site.id,
                effective_from=now - timedelta(days=1),
                effective_to=None,
                assigned_by=user.id,
                reason="Transferred",
                created_at=now,
            ),
        ]
    )
    await session.commit()

    with pytest.raises(ProblemError) as blocked:
        await create_reset_plan(
            session,
            site_id=former_site.id,
            requested_by=user.id,
            categories=ALL_RESET_CATEGORIES,
            delete_imported_bill_documents=False,
            disconnected_sensor_policy="defer_until_reconnect",
            offline_after_seconds=30,
            now=now,
        )

    assert blocked.value.code == "data_reset_historical_device_scope_unsafe"
    assert blocked.value.extra == {"device_ids": [device.id]}
    assert await session.scalar(select(func.count()).select_from(DataResetPlan)) == 0


def test_precommit_cancel_restores_sanitized_log_from_replacement_hash(
    tmp_path: Path,
) -> None:
    operation = DataResetOperation(id=new_uuid(), central_commit_at=None)
    site_id = new_uuid()
    log_path = tmp_path / "events.jsonl"
    original = (
        json.dumps({"site_id": site_id, "event": "sample", "power_w": "1200"}) + "\n"
    ).encode()
    log_path.write_bytes(original)
    journal: list[dict[str, str]] = []

    assert (
        sanitize_scoped_logs(
            log_root=tmp_path,
            operation_id=operation.id,
            site_id=site_id,
            device_ids=set(),
            journal=journal,
        )
        == 1
    )
    assert journal[0]["replacement_kind"] == "sanitized_log"
    assert "[redacted-by-data-reset]" in log_path.read_text(encoding="utf-8")

    assert restore_precommit_quarantine(operation=operation, roots=[tmp_path]) == 1
    assert log_path.read_bytes() == original
    assert load_quarantine_journal(root=tmp_path, operation_id=operation.id) == []


def test_precommit_cancel_rejects_unrecognized_log_replacement(tmp_path: Path) -> None:
    operation = DataResetOperation(id=new_uuid(), central_commit_at=None)
    site_id = new_uuid()
    log_path = tmp_path / "events.jsonl"
    original = (
        json.dumps({"site_id": site_id, "event": "sample", "power_w": "1200"}) + "\n"
    ).encode()
    log_path.write_bytes(original)
    journal: list[dict[str, str]] = []
    assert (
        sanitize_scoped_logs(
            log_root=tmp_path,
            operation_id=operation.id,
            site_id=site_id,
            device_ids=set(),
            journal=journal,
        )
        == 1
    )
    unrecognized = b'{"event":"written-after-crash","status":"new"}\n'
    log_path.write_bytes(unrecognized)

    with pytest.raises(ProblemError) as conflict:
        restore_precommit_quarantine(operation=operation, roots=[tmp_path])

    assert conflict.value.code == "data_reset_quarantine_conflict"
    assert log_path.read_bytes() == unrecognized
    assert Path(journal[0]["quarantine"]).read_bytes() == original
    assert load_quarantine_journal(root=tmp_path, operation_id=operation.id)


def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


async def _bootstrap_site(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "reenrollment-boundary@example.com",
            "display_name": "Reenrollment Boundary",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    sites = await client.get("/api/v1/sites")
    assert sites.status_code == 200, sites.text
    return str(sites.json()[0]["id"])


async def _enroll_boundary_sensor(
    client: httpx.AsyncClient,
    *,
    site_id: str,
    hardware_id: str,
) -> httpx.Response:
    token = await client.post(
        "/api/v1/enrollment-tokens",
        headers=_csrf(client),
        json={"site_id": site_id, "name": "Boundary Sensor"},
    )
    assert token.status_code == 201, token.text
    return await client.post(
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
                "supported_endpoints": ["health", "readings", "data-reset/1.0.0"],
                "data_reset_protocol": "data-reset/1.0.0",
            },
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("site_state_mode", ["current", "stale", "missing"])
async def test_fresh_enrollment_into_reset_site_requires_and_returns_generation_policy(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    site_state_mode: str,
) -> None:
    client: httpx.AsyncClient = api_client
    site_id = await _bootstrap_site(client)
    now = datetime.now(UTC)
    async with session_factory_fixture() as session:
        site_state = await session.get(SiteDataState, site_id)
        if site_state_mode == "missing" and site_state is not None:
            await session.delete(site_state)
        elif site_state is None:
            site_state = SiteDataState(
                site_id=site_id,
                data_generation=3 if site_state_mode == "current" else 1,
                history_revision=0,
                updated_at=now,
            )
            session.add(site_state)
        else:
            site_state.data_generation = 3 if site_state_mode == "current" else 1
        plan_id = new_uuid()
        operation_id = new_uuid()
        session.add_all(
            [
                DataResetPlan(
                    id=plan_id,
                    site_id=site_id,
                    requested_categories=["measurement_history"],
                    delete_imported_bill_documents=False,
                    disconnected_sensor_policy="defer_until_reconnect",
                    plan_snapshot={},
                    plan_fingerprint="9" * 64,
                    revision=1,
                    created_at=now - timedelta(days=1),
                    expires_at=now + timedelta(days=1),
                ),
                DataResetOperation(
                    id=operation_id,
                    plan_id=plan_id,
                    site_id=site_id,
                    state="completed",
                    revision=1,
                    reset_generation=3,
                    reset_timestamp=now - timedelta(hours=1),
                    requested_categories=["measurement_history"],
                    delete_imported_bill_documents=False,
                    disconnected_sensor_policy="defer_until_reconnect",
                    backup_mode="permanent_without_backup",
                    reason="Fresh enrollment generation recovery fixture",
                    idempotency_key=f"fresh-enrollment-{site_state_mode}",
                    request_fingerprint="8" * 64,
                    plan_revision=1,
                    started_at=now - timedelta(hours=1),
                    central_commit_at=now - timedelta(hours=1),
                    completed_at=now - timedelta(minutes=30),
                    final_evidence={},
                    created_at=now - timedelta(hours=1),
                    updated_at=now - timedelta(minutes=30),
                ),
            ]
        )
        await session.commit()

    token_response = await client.post(
        "/api/v1/enrollment-tokens",
        headers=_csrf(client),
        json={"site_id": site_id, "name": "Legacy Generation Sensor"},
    )
    assert token_response.status_code == 201, token_response.text
    token_text = str(token_response.json()["token"])
    unsupported = await client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token_text,
            "protocol_version": PROTOCOL,
            "hardware_id": f"fresh-reset-site-legacy-sensor-{site_state_mode}",
            "capabilities": {
                "hardware_target": "esp32-s3-pzem004t-v4",
                "pzem_model": "PZEM-004T V4.0",
                "sd_present": True,
                "sd_required": True,
                "supported_endpoints": ["health", "readings"],
            },
        },
    )
    assert unsupported.status_code == 409, unsupported.text
    assert unsupported.json()["code"] == "enrollment_data_generation_unsupported"
    assert unsupported.json()["required_data_generation"] == 3
    async with session_factory_fixture() as session:
        token_row = await session.scalar(
            select(EnrollmentToken).where(
                EnrollmentToken.token_hash == hashlib.sha256(token_text.encode()).hexdigest()
            )
        )
        assert token_row is not None and token_row.consumed_at is None

    supported = await _enroll_boundary_sensor(
        client,
        site_id=site_id,
        hardware_id=f"fresh-reset-site-supported-sensor-{site_state_mode}",
    )
    assert supported.status_code == 201, supported.text
    assert supported.json()["sync_policy"]["data_generation"] == 3
    assert supported.json()["sync_policy"]["reset_boundary"] == 0
    async with session_factory_fixture() as session:
        healed = await session.get(SiteDataState, site_id)
        assert healed is not None and healed.data_generation == 3


@pytest.mark.asyncio
async def test_reenrollment_preserves_maximum_reset_boundary_and_rejects_old_batch(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client: httpx.AsyncClient = api_client
    site_id = await _bootstrap_site(client)
    hardware_id = "reenrollment-boundary-sensor"
    first_claim = await _enroll_boundary_sensor(
        client,
        site_id=site_id,
        hardware_id=hardware_id,
    )
    assert first_claim.status_code == 201, first_claim.text
    device_id = str(first_claim.json()["device_id"])
    removed = await client.post(
        f"/api/v1/admin/devices/{device_id}/unclaim",
        headers=_csrf(client),
        json={"confirmation": "Boundary Sensor", "reason": "replaced"},
    )
    assert removed.status_code == 200, removed.text

    now = datetime.now(UTC)
    plan_id = new_uuid()
    operation_id = new_uuid()
    async with session_factory_fixture() as session:
        device = await session.get(Device, device_id)
        state = await session.get(DeviceDataState, device_id)
        cursor = await session.get(SyncCursor, device_id)
        site_state = await session.get(SiteDataState, site_id)
        assert device is not None and device.lifecycle_status == "decommissioned"
        assert state is not None and cursor is not None and site_state is not None
        state.data_generation = 2
        state.reset_boundary = 60
        state.ingestion_gate = "open"
        state.reset_required_on_reconnect = False
        state.active_operation_id = None
        state.last_reset_at = now - timedelta(days=1)
        cursor.highest_contiguous_sequence = 70
        cursor.maximum_seen_sequence = 80
        cursor.data_generation = 2
        cursor.reset_boundary = 75
        site_state.data_generation = 2
        session.add_all(
            [
                DataResetPlan(
                    id=plan_id,
                    site_id=site_id,
                    requested_by=None,
                    requested_categories=["measurement_history"],
                    delete_imported_bill_documents=False,
                    disconnected_sensor_policy="defer_until_reconnect",
                    plan_snapshot={},
                    plan_fingerprint="c" * 64,
                    revision=1,
                    created_at=now - timedelta(days=2),
                    expires_at=now + timedelta(days=1),
                ),
                DataResetOperation(
                    id=operation_id,
                    plan_id=plan_id,
                    site_id=site_id,
                    requested_by=None,
                    state="completed",
                    revision=1,
                    reset_generation=2,
                    reset_timestamp=now - timedelta(days=1),
                    requested_categories=["measurement_history"],
                    delete_imported_bill_documents=False,
                    disconnected_sensor_policy="defer_until_reconnect",
                    backup_mode="permanent_without_backup",
                    reason="Historical reset boundary evidence",
                    idempotency_key="historical-reenrollment-boundary",
                    request_fingerprint="d" * 64,
                    plan_revision=1,
                    started_at=now - timedelta(days=1),
                    central_commit_at=now - timedelta(days=1),
                    completed_at=now - timedelta(days=1),
                    final_evidence={},
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                ),
                DataResetParticipant(
                    operation_id=operation_id,
                    device_id=device_id,
                    state="verified",
                    planned_classification="connected",
                    reset_generation=2,
                    reset_boundary=90,
                    old_sequence_floor=0,
                    old_next_sequence=91,
                    new_sequence_floor=90,
                    new_next_sequence=91,
                    server_highest_contiguous=90,
                    server_maximum_seen=90,
                    sensor_ack_sequence=90,
                    sensor_newest_sequence=90,
                    updated_at=now - timedelta(days=1),
                ),
            ]
        )
        await session.commit()

    reenrolled = await _enroll_boundary_sensor(
        client,
        site_id=site_id,
        hardware_id=hardware_id,
    )
    assert reenrolled.status_code == 201, reenrolled.text
    assert reenrolled.json()["device_id"] == device_id
    assert reenrolled.json()["sync_policy"]["data_generation"] == 2
    assert reenrolled.json()["sync_policy"]["reset_boundary"] == 90
    new_secret = str(reenrolled.json()["enrollment_secret"]).encode()

    async with session_factory_fixture() as session:
        state = await session.get(DeviceDataState, device_id)
        cursor = await session.get(SyncCursor, device_id)
        assert state is not None and cursor is not None
        assert state.reset_boundary == 90
        assert state.data_generation == 2
        assert cursor.reset_boundary == 90
        assert cursor.highest_contiguous_sequence == 90
        assert cursor.maximum_seen_sequence == 90

    batch = {
        "protocol_version": PROTOCOL,
        "schema_version": "reading-batch/1.0.0",
        "device_id": device_id,
        "data_generation": 2,
        "readings": [
            {
                "data_generation": 2,
                "sequence": 80,
                "boot_id": new_uuid(),
                "interval_start": (now - timedelta(minutes=1)).isoformat(),
                "interval_end": now.isoformat(),
                "time_trusted": True,
                "power_avg": "600",
                "interval_energy_wh": "10",
                "energy_method": "power_integration",
                "ct_rating_amps": "100",
                "quality_flags": [],
                "firmware_version": "1.0.18",
            }
        ],
    }
    body = json.dumps(batch, separators=(",", ":")).encode()
    replay = await client.post(
        "/api/v1/device-readings/batch",
        content=body,
        headers={
            **sign_headers(
                secret=new_secret,
                device_id=device_id,
                direction="device-to-server",
                method="POST",
                target="/api/v1/device-readings/batch",
                body=body,
            ),
            "Content-Type": "application/json",
        },
    )
    assert replay.status_code == 409, replay.text
    assert replay.json()["code"] == "reading_precedes_reset_boundary"
    async with session_factory_fixture() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RawReading)
                .where(RawReading.device_id == device_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DeviceCredential)
                .where(DeviceCredential.device_id == device_id)
            )
            >= 1
        )


@pytest.mark.asyncio
async def test_cross_site_reenrollment_requires_authenticated_local_clear(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client: httpx.AsyncClient = api_client
    original_site_id = await _bootstrap_site(client)
    hardware_id = "cross-site-reenrollment-sensor"
    first_claim = await _enroll_boundary_sensor(
        client,
        site_id=original_site_id,
        hardware_id=hardware_id,
    )
    assert first_claim.status_code == 201, first_claim.text
    device_id = str(first_claim.json()["device_id"])
    removed = await client.post(
        f"/api/v1/admin/devices/{device_id}/unclaim",
        headers=_csrf(client),
        json={"confirmation": "Boundary Sensor", "reason": "moved"},
    )
    assert removed.status_code == 200, removed.text

    target_site_id = new_uuid()
    async with session_factory_fixture() as session:
        session.add(
            Site(
                id=target_site_id,
                name="Cross-site target",
                code="cross-site-target",
            )
        )
        await session.commit()

    token_response = await client.post(
        "/api/v1/enrollment-tokens",
        headers=_csrf(client),
        json={"site_id": target_site_id, "name": "Boundary Sensor"},
    )
    assert token_response.status_code == 201, token_response.text
    token_text = str(token_response.json()["token"])
    blocked = await client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token_text,
            "protocol_version": PROTOCOL,
            "hardware_id": hardware_id,
            "capabilities": {
                "hardware_target": "esp32-s3-pzem004t-v4",
                "pzem_model": "PZEM-004T V4.0",
                "sd_present": True,
                "sd_required": True,
                "supported_endpoints": ["health", "readings", "data-reset/1.0.0"],
                "data_reset_protocol": "data-reset/1.0.0",
            },
        },
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "reenrollment_requires_data_reset_recovery"
    assert blocked.json()["cross_site_reenrollment"] is True

    async with session_factory_fixture() as session:
        token_row = await session.scalar(
            select(EnrollmentToken).where(
                EnrollmentToken.token_hash == hashlib.sha256(token_text.encode()).hexdigest()
            )
        )
        device = await session.get(Device, device_id)
        assert token_row is not None and token_row.consumed_at is None
        assert device is not None
        assert device.lifecycle_status == "decommissioned"
        assert device.site_id == original_site_id


@pytest.mark.asyncio
@pytest.mark.parametrize("device_state_mode", ["stale", "missing"])
async def test_reenrollment_is_blocked_when_removed_sensor_was_not_reset(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    device_state_mode: str,
) -> None:
    client: httpx.AsyncClient = api_client
    site_id = await _bootstrap_site(client)
    hardware_id = f"removed-reset-handoff-sensor-{device_state_mode}"
    first_claim = await _enroll_boundary_sensor(
        client,
        site_id=site_id,
        hardware_id=hardware_id,
    )
    assert first_claim.status_code == 201, first_claim.text
    device_id = str(first_claim.json()["device_id"])
    removed = await client.post(
        f"/api/v1/admin/devices/{device_id}/unclaim",
        headers=_csrf(client),
        json={"confirmation": "Boundary Sensor", "reason": "other"},
    )
    assert removed.status_code == 200, removed.text

    now = datetime.now(UTC)
    plan_id = new_uuid()
    operation_id = new_uuid()
    async with session_factory_fixture() as session:
        state = await session.get(DeviceDataState, device_id)
        cursor = await session.get(SyncCursor, device_id)
        site_state = await session.get(SiteDataState, site_id)
        assert state is not None and cursor is not None and site_state is not None
        if device_state_mode == "missing":
            await session.delete(state)
        else:
            # A stale/open state must not hide stronger generation evidence in
            # the cursor, site state, or historical reset participant.
            state.data_generation = 0
            state.reset_boundary = 0
            state.ingestion_gate = "open"
            state.reset_required_on_reconnect = False
            state.active_operation_id = None
            state.last_reset_at = None
        cursor.highest_contiguous_sequence = 50
        cursor.maximum_seen_sequence = 50
        cursor.data_generation = 1
        cursor.reset_boundary = 50
        site_state.data_generation = 1
        session.add_all(
            [
                DataResetPlan(
                    id=plan_id,
                    site_id=site_id,
                    requested_by=None,
                    requested_categories=["measurement_history"],
                    delete_imported_bill_documents=False,
                    disconnected_sensor_policy="defer_until_reconnect",
                    plan_snapshot={},
                    plan_fingerprint="e" * 64,
                    revision=1,
                    created_at=now - timedelta(minutes=2),
                    expires_at=now + timedelta(days=1),
                ),
                DataResetOperation(
                    id=operation_id,
                    plan_id=plan_id,
                    site_id=site_id,
                    requested_by=None,
                    state="completed",
                    revision=1,
                    reset_generation=1,
                    reset_timestamp=now,
                    requested_categories=["measurement_history"],
                    delete_imported_bill_documents=False,
                    disconnected_sensor_policy="defer_until_reconnect",
                    backup_mode="permanent_without_backup",
                    reason="Removed sensor reset handoff evidence",
                    idempotency_key="removed-reset-handoff",
                    request_fingerprint="f" * 64,
                    plan_revision=1,
                    started_at=now,
                    central_commit_at=now,
                    completed_at=now,
                    final_evidence={},
                    created_at=now,
                    updated_at=now,
                ),
                DataResetParticipant(
                    operation_id=operation_id,
                    device_id=device_id,
                    state="not_applicable",
                    planned_classification="removed",
                    reset_generation=1,
                    reset_boundary=50,
                    old_sequence_floor=0,
                    old_next_sequence=51,
                    new_sequence_floor=50,
                    new_next_sequence=51,
                    server_highest_contiguous=50,
                    server_maximum_seen=50,
                    sensor_ack_sequence=50,
                    sensor_newest_sequence=50,
                    updated_at=now,
                ),
            ]
        )
        credential_count_before = int(
            await session.scalar(
                select(func.count())
                .select_from(DeviceCredential)
                .where(DeviceCredential.device_id == device_id)
            )
            or 0
        )
        await session.commit()

    token_response = await client.post(
        "/api/v1/enrollment-tokens",
        headers=_csrf(client),
        json={"site_id": site_id, "name": "Boundary Sensor"},
    )
    assert token_response.status_code == 201, token_response.text
    token_text = str(token_response.json()["token"])
    blocked = await client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token_text,
            "protocol_version": PROTOCOL,
            "hardware_id": hardware_id,
            "capabilities": {
                "hardware_target": "esp32-s3-pzem004t-v4",
                "pzem_model": "PZEM-004T V4.0",
                "sd_present": True,
                "sd_required": True,
                "supported_endpoints": ["health", "readings", "data-reset/1.0.0"],
                "data_reset_protocol": "data-reset/1.0.0",
            },
        },
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "reenrollment_requires_data_reset_recovery"
    assert blocked.json()["required_data_generation"] == 1
    assert blocked.json()["reset_boundary"] == 50

    async with session_factory_fixture() as session:
        token_row = await session.scalar(
            select(EnrollmentToken).where(
                EnrollmentToken.token_hash == hashlib.sha256(token_text.encode()).hexdigest()
            )
        )
        device = await session.get(Device, device_id)
        credential_count_after = int(
            await session.scalar(
                select(func.count())
                .select_from(DeviceCredential)
                .where(DeviceCredential.device_id == device_id)
            )
            or 0
        )
        assert token_row is not None and token_row.consumed_at is None
        assert device is not None and device.lifecycle_status == "decommissioned"
        assert credential_count_after == credential_count_before


@pytest.mark.asyncio
async def test_post_reset_historical_events_redact_descriptor_json_and_text_values(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    site = Site(id=new_uuid(), name="Historical Event Site", code="historical-event-site")
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="historical-event-sensor",
        name="Historical Event Sensor",
    )
    session.add_all(
        [
            site,
            device,
            DeviceDataState(
                device_id=device.id,
                site_id=site.id,
                data_generation=3,
                reset_boundary=40,
                ingestion_gate="open",
                reset_required_on_reconnect=False,
                last_reset_at=now - timedelta(minutes=1),
                generation_updated_at=now,
                updated_at=now,
            ),
        ]
    )
    await session.commit()
    occurred_at = now - timedelta(hours=1)
    payload = DeviceEventBatch.model_validate(
        {
            "protocol_version": PROTOCOL,
            "device_id": device.id,
            "data_generation": 3,
            "first_stored_event_sequence": 1,
            "events": [
                {
                    "event_id": "historical-metric",
                    "occurred_at": occurred_at.isoformat(),
                    "category": "security",
                    "severity": "warning",
                    "evidence": {
                        "event_sequence": 1,
                        "metric": "power_w",
                        "value": "1234.5",
                        "code": "preserved-diagnostic",
                    },
                },
                {
                    "event_id": "historical-json",
                    "occurred_at": occurred_at.isoformat(),
                    "category": "security",
                    "severity": "warning",
                    "evidence": {
                        "event_sequence": 2,
                        "payload": '{"energy_wh":900,"status":"archived"}',
                    },
                },
                {
                    "event_id": "historical-text",
                    "occurred_at": occurred_at.isoformat(),
                    "category": "security",
                    "severity": "warning",
                    "evidence": {
                        "event_sequence": 3,
                        "message": "PZEM energy=900 Wh before reset",
                    },
                },
            ],
        }
    )

    result = await event_batch(payload, SimpleNamespace(device=device), session)

    assert result["accepted"] == [
        "historical-metric",
        "historical-json",
        "historical-text",
    ]
    events = {
        item.event_id: item
        for item in await session.scalars(
            select(DeviceEvent).where(DeviceEvent.device_id == device.id)
        )
    }
    assert events["historical-metric"].evidence["metric"] == "power_w"
    assert events["historical-metric"].evidence["value"] == ("[redacted-by-data-reset]")
    assert events["historical-metric"].evidence["code"] == "preserved-diagnostic"
    encoded = json.loads(events["historical-json"].evidence["payload"])
    assert encoded == {
        "energy_wh": "[redacted-by-data-reset]",
        "status": "archived",
    }
    assert events["historical-text"].evidence["message"] == ("[redacted-by-data-reset]")
