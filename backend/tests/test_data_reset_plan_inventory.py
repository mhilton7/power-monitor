from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.data_reset.sensor_client import (
    SensorResetCommunicationError,
    probe_sensor_storage,
    validate_sensor_storage_snapshot,
)
from app.data_reset.service import (
    MAX_RESET_BOUNDARY,
    NO_BACKUP_CONFIRMATION_PHRASE,
    create_reset_operation,
    create_reset_plan,
    perform_central_reset,
    public_plan_payload,
)
from app.db.models import (
    AlertInstance,
    AlertRule,
    DataResetOperation,
    DataResetPlan,
    Device,
    DeviceAddress,
    DeviceCapability,
    DeviceCredential,
    DeviceDataState,
    DeviceHeartbeat,
    ExportJob,
    GeneratedReport,
    LogExportJob,
    NotificationAttempt,
    NotificationChannel,
    SensorNetworkPolicy,
    Site,
    User,
    new_uuid,
)
from app.problem import ProblemError
from app.security.protocol import SecretCipher, calculate_signature, sha256_hex

ALL_RESET_CATEGORIES = [
    "measurement_history",
    "cost_history",
    "pricing_history",
    "generated_outputs",
]


def _storage_snapshot(*, generation: int = 3) -> dict[str, Any]:
    return {
        "data_generation": generation,
        "sequence_floor": 40,
        "next_sequence": 51,
        "oldest_sequence": 40,
        "newest_sequence": 50,
        "newest_syncable_sequence": 45,
        "server_ack_sequence": 40,
        "backlog_estimate": 5,
        "local_record_count": 11,
        "durable_next_sequence": 51,
        "durable_newest_sequence": 50,
        "durable_newest_syncable_sequence": 45,
        "durable_backlog_estimate": 5,
        "durable_local_record_count": 11,
        "prepare_drain_records_projected": 0,
        "prepare_drain_first_sequence_projected": None,
        "prepare_drain_last_sequence_projected": None,
        "prepare_drain_syncable_records_projected": 0,
        "card_generation": "77",
        "card_identity_status": "verified",
        "sd_status": "writable",
    }


def _storage_wire_snapshot() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "present": True,
        "mounted": True,
        "writable": True,
        "data_generation": 3,
        "sequence_floor": 40,
        "next_sequence": 51,
        "oldest_sequence": 40,
        "newest_sequence": 50,
        "newest_syncable_sequence": 45,
        "server_ack_sequence": 40,
        "unsynchronized_estimate": 5,
        "local_record_count": 11,
        "prepare_projection_consistent": True,
        "prepare_projection_local_record_count": 11,
        "prepare_projection_next_sequence": 51,
        "prepare_projection_newest_sequence": 50,
        "prepare_projection_newest_syncable_sequence": 45,
        "prepare_drain_records_projected": 0,
        "prepare_drain_first_sequence_projected": None,
        "prepare_drain_last_sequence_projected": None,
        "prepare_drain_syncable_records_projected": 0,
        "card_generation": "77",
        "card_identity_status": "verified",
    }


@pytest.mark.parametrize(
    "field",
    [
        "data_generation",
        "sequence_floor",
        "next_sequence",
        "oldest_sequence",
        "newest_sequence",
        "newest_syncable_sequence",
        "server_ack_sequence",
        "unsynchronized_estimate",
        "local_record_count",
        "prepare_projection_local_record_count",
        "prepare_projection_next_sequence",
        "prepare_projection_newest_sequence",
        "prepare_projection_newest_syncable_sequence",
    ],
)
def test_storage_probe_rejects_values_above_signed_bigint(field: str) -> None:
    payload = _storage_wire_snapshot()
    payload[field] = 2**63

    with pytest.raises(SensorResetCommunicationError) as rejected:
        validate_sensor_storage_snapshot(payload)

    assert rejected.value.code == "sensor_probe_response_invalid"
    assert rejected.value.retryable is False


def test_storage_probe_defers_an_incoherent_prepare_projection() -> None:
    payload = _storage_wire_snapshot()
    payload["prepare_projection_consistent"] = False

    with pytest.raises(SensorResetCommunicationError) as rejected:
        validate_sensor_storage_snapshot(payload)

    assert rejected.value.code == "sensor_probe_projection_busy"
    assert rejected.value.retryable is True


def test_storage_probe_includes_exact_projected_prepare_drain() -> None:
    payload = _storage_wire_snapshot()
    payload.update(
        {
            "prepare_projection_local_record_count": 13,
            "prepare_projection_next_sequence": 53,
            "prepare_projection_newest_sequence": 52,
            "prepare_projection_newest_syncable_sequence": 52,
            "prepare_drain_records_projected": 2,
            "prepare_drain_first_sequence_projected": 51,
            "prepare_drain_last_sequence_projected": 52,
            "prepare_drain_syncable_records_projected": 2,
        }
    )

    snapshot = validate_sensor_storage_snapshot(payload)

    assert snapshot["durable_local_record_count"] == 11
    assert snapshot["local_record_count"] == 13
    assert snapshot["next_sequence"] == 53
    assert snapshot["newest_sequence"] == 52
    assert snapshot["newest_syncable_sequence"] == 52
    assert snapshot["backlog_estimate"] == 12
    assert snapshot["prepare_drain_records_projected"] == 2
    assert snapshot["prepare_drain_first_sequence_projected"] == 51
    assert snapshot["prepare_drain_last_sequence_projected"] == 52
    assert snapshot["prepare_drain_syncable_records_projected"] == 2


def test_storage_probe_rejects_newest_syncable_jump_without_syncable_drain() -> None:
    payload = _storage_wire_snapshot()
    payload.update(
        {
            "prepare_projection_local_record_count": 13,
            "prepare_projection_next_sequence": 53,
            "prepare_projection_newest_sequence": 52,
            "prepare_projection_newest_syncable_sequence": 52,
            "prepare_drain_records_projected": 2,
            "prepare_drain_first_sequence_projected": 51,
            "prepare_drain_last_sequence_projected": 52,
            "prepare_drain_syncable_records_projected": 0,
        }
    )

    with pytest.raises(SensorResetCommunicationError) as rejected:
        validate_sensor_storage_snapshot(payload)

    assert rejected.value.code == "sensor_probe_response_invalid"
    assert rejected.value.retryable is False


def _heartbeat(device: Device, received_at: datetime) -> DeviceHeartbeat:
    return DeviceHeartbeat(
        id=new_uuid(),
        device_id=device.id,
        boot_id=new_uuid(),
        received_at=received_at,
        pzem_ok=True,
        sd_ok=True,
        time_trusted=True,
        data_generation=2,
        newest_sequence=25,
        backlog_estimate=4,
        payload={
            "server_ack_sequence": 21,
            "newest_stored_sequence": 25,
            "newest_syncable_sequence": 25,
            "sd": {
                "details": {
                    "sequence_floor": 1,
                    "next_sequence": 26,
                    "local_record_count": 25,
                    "card_generation": "70",
                }
            },
        },
    )


@pytest.mark.asyncio
async def test_storage_probe_is_read_only_network_validated_and_hmac_signed(
    session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    secret = b"reset-plan-probe-secret"
    site = Site(
        id=new_uuid(),
        name="Probe Site",
        code="probe-site",
        timezone="America/Los_Angeles",
        allowed_cidrs=["192.168.40.0/24"],
    )
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="probe-sensor",
        name="Probe Sensor",
    )
    session.add_all(
        [
            site,
            device,
            DeviceAddress(
                id=new_uuid(),
                device_id=device.id,
                host="192.168.40.12",
                port=443,
                scheme="https",
                source="heartbeat",
                is_manual_override=False,
                first_seen_at=now,
                last_seen_at=now,
            ),
            DeviceCredential(
                id=new_uuid(),
                device_id=device.id,
                encrypted_secret=SecretCipher(test_settings.app_master_key).encrypt(secret),
                fingerprint="f" * 64,
                valid_from=now - timedelta(minutes=1),
                delivered_at=now,
                confirmed_at=now,
                created_at=now,
            ),
        ]
    )
    await session.commit()
    validated_target: dict[str, Any] = {}

    async def validate_target(**kwargs: Any) -> None:
        validated_target.update(kwargs)

    monkeypatch.setattr("app.data_reset.sensor_client.validate_poll_target", validate_target)

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/storage"
        assert request.content == b""
        timestamp = request.headers["X-PM-Timestamp"]
        nonce = request.headers["X-PM-Nonce"]
        digest = request.headers["X-PM-Content-SHA256"]
        assert digest == sha256_hex(b"")
        assert request.headers["X-PM-Signature"] == calculate_signature(
            secret,
            "server-to-device",
            "GET",
            "/api/v1/storage",
            timestamp,
            nonce,
            digest,
        )
        return httpx.Response(200, json=_storage_wire_snapshot())

    before_policies = await session.scalar(select(func.count()).select_from(SensorNetworkPolicy))
    result = await probe_sensor_storage(
        session,
        device=device,
        settings=test_settings,
        transport=httpx.MockTransport(handle),
    )
    after_policies = await session.scalar(select(func.count()).select_from(SensorNetworkPolicy))

    assert result == _storage_snapshot()
    assert validated_target["host"] == "192.168.40.12"
    assert validated_target["allowed_cidrs"] == ["192.168.40.0/24"]
    assert before_policies == after_policies == 0
    assert not session.new
    assert not session.dirty


@pytest.mark.asyncio
async def test_plan_classifies_only_fresh_authenticated_reset_candidates(
    session: AsyncSession,
    test_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    test_settings.report_path = tmp_path / "reports"
    test_settings.log_path = tmp_path / "logs"
    user = User(
        id=new_uuid(),
        email="inventory-reset@example.com",
        display_name="Inventory Reset",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Inventory Site",
        code="inventory-site",
        timezone="America/Los_Angeles",
    )
    devices: dict[str, Device] = {}
    for label in (
        "connected",
        "auth",
        "invalid",
        "identity",
        "stale",
        "unsupported",
        "revoked",
        "removed",
    ):
        device = Device(
            id=new_uuid(),
            site_id=site.id,
            hardware_id=f"inventory-{label}",
            name=label,
            firmware_version="1.0.18",
            firmware_build_hash="a" * 64,
            last_seen_at=now,
            revoked_at=now if label == "revoked" else None,
            lifecycle_status="decommissioned" if label == "removed" else "active",
        )
        devices[label] = device
    devices["identity"].firmware_build_hash = None
    session.add_all([user, site, *devices.values()])
    await session.flush()
    for label in ("connected", "auth", "invalid", "identity", "stale"):
        session.add(
            DeviceCapability(
                device_id=devices[label].id,
                hardware_target="esp32-s3",
                pzem_model="PZEM-004T V4.0",
                sd_required=True,
                features={"data_reset": "data-reset/1.0.0"},
                reported_at=now,
            )
        )
    session.add(
        DeviceCapability(
            device_id=devices["unsupported"].id,
            hardware_target="esp32-s3",
            pzem_model="PZEM-004T V4.0",
            sd_required=True,
            features={"supported_endpoints": ["health"]},
            reported_at=now,
        )
    )
    for label, device in devices.items():
        session.add(
            _heartbeat(
                device,
                now - timedelta(minutes=5) if label == "stale" else now,
            )
        )
    await session.commit()
    probed: list[str] = []

    async def fake_probe(
        _session: AsyncSession, *, device: Device, settings: Settings
    ) -> dict[str, Any]:
        del settings
        probed.append(device.name)
        if device.name == "auth":
            raise SensorResetCommunicationError(
                "sensor_probe_authentication_failed",
                "Rejected HMAC",
                retryable=False,
            )
        if device.name == "invalid":
            raise SensorResetCommunicationError(
                "sensor_probe_response_invalid",
                "Malformed signed storage evidence",
                retryable=False,
            )
        return _storage_snapshot()

    monkeypatch.setattr("app.data_reset.service.probe_sensor_storage", fake_probe)
    plan = await create_reset_plan(
        session,
        site_id=site.id,
        requested_by=user.id,
        categories=ALL_RESET_CATEGORIES,
        delete_imported_bill_documents=False,
        disconnected_sensor_policy="defer_until_reconnect",
        offline_after_seconds=30,
        settings=test_settings,
        now=now,
    )
    participants = {item["name"]: item for item in plan.plan_snapshot["participants"]}

    assert {key: value["classification"] for key, value in participants.items()} == {
        "connected": "connected",
        "auth": "authentication_failed",
        "invalid": "authentication_failed",
        "identity": "unsupported",
        "stale": "disconnected",
        "unsupported": "unsupported",
        "revoked": "revoked",
        "removed": "removed",
    }
    assert probed == ["auth", "connected", "invalid"]
    assert participants["connected"]["local_record_count"] == 11
    assert participants["connected"]["backlog_estimate"] == 5
    assert participants["connected"]["record_count_status"] == "exact_prepare_projection"
    assert plan.plan_snapshot["sensor_records_to_delete_now"] == 11
    assert plan.plan_snapshot["estimated_sensor_records"] == 11
    assert participants["connected"]["sensor_ack_sequence"] == 40
    assert participants["connected"]["sensor_newest_sequence"] == 50
    assert participants["connected"]["old_sequence_floor"] == 40
    assert participants["connected"]["old_next_sequence"] == 51
    assert participants["connected"]["card_generation"] == "77"
    assert participants["connected"]["probe_status"] == "authenticated"
    assert participants["invalid"]["probe_status"] == "sensor_probe_response_invalid"
    assert participants["identity"]["probe_status"] == "firmware_identity_incomplete"
    assert participants["stale"]["record_count_status"] == "last_reported"
    assert await session.scalar(select(func.count()).select_from(DeviceDataState)) == 0

    with pytest.raises(ProblemError) as rejected:
        await create_reset_operation(
            session,
            plan_id=plan.id,
            plan_revision=plan.revision,
            requested_by=user.id,
            idempotency_key="auth-failed-reset-plan-1",
            reason="Authentication failure must block reset",
            backup_mode="permanent_without_backup",
            confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
            permanent_without_backup_acknowledged=True,
            offline_after_seconds=30,
            settings=test_settings,
            now=now,
        )
    assert rejected.value.code == "data_reset_sensor_authentication_failed"
    assert await session.scalar(select(func.count()).select_from(DataResetOperation)) == 0
    assert await session.scalar(select(func.count()).select_from(DeviceDataState)) == 0


@pytest.mark.asyncio
async def test_execution_rejects_exhausted_active_boundary_before_mutation(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="sequence-space-reset@example.com",
        display_name="Sequence Space Reset",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Sequence Space Site",
        code="sequence-space-site",
        timezone="America/Los_Angeles",
    )
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="sequence-space-sensor",
        name="Sequence Space Sensor",
        firmware_version="1.0.18",
        firmware_build_hash="a" * 64,
        last_seen_at=now,
    )
    heartbeat = _heartbeat(device, now)
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
                reported_at=now,
            ),
            heartbeat,
        ]
    )
    await session.commit()

    plan = await create_reset_plan(
        session,
        site_id=site.id,
        requested_by=user.id,
        categories=ALL_RESET_CATEGORIES,
        delete_imported_bill_documents=False,
        disconnected_sensor_policy="defer_until_reconnect",
        offline_after_seconds=30,
        now=now,
    )
    await session.commit()

    heartbeat.newest_sequence = MAX_RESET_BOUNDARY + 1
    await session.commit()

    with pytest.raises(ProblemError) as rejected:
        await create_reset_operation(
            session,
            plan_id=plan.id,
            plan_revision=plan.revision,
            requested_by=user.id,
            idempotency_key="sequence-exhausted-reset-1",
            reason="Prove sequence exhaustion fails before reset mutation",
            backup_mode="permanent_without_backup",
            confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
            permanent_without_backup_acknowledged=True,
            offline_after_seconds=30,
            now=now,
        )

    assert rejected.value.code == "data_reset_sequence_space_exhausted"
    await session.refresh(plan)
    assert plan.invalidated_at is None
    assert await session.scalar(select(func.count()).select_from(DataResetPlan)) == 1
    assert await session.scalar(select(func.count()).select_from(DataResetOperation)) == 0
    assert await session.scalar(select(func.count()).select_from(DeviceDataState)) == 0
    assert not session.new
    assert not session.dirty


@pytest.mark.asyncio
async def test_exact_output_alert_counts_and_central_stale_check_are_non_mutating(
    session: AsyncSession,
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    report_root = tmp_path / "reports"
    log_root = tmp_path / "logs"
    bill_root = tmp_path / "bills"
    rate_root = tmp_path / "rates"
    backup_root = tmp_path / "backups"
    for root in (report_root, log_root, bill_root, rate_root, backup_root):
        root.mkdir(parents=True, exist_ok=True)
    test_settings.report_path = report_root
    test_settings.log_path = log_root
    user = User(
        id=new_uuid(),
        email="count-reset@example.com",
        display_name="Count Reset",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Count Site",
        code="count-site",
        timezone="America/Los_Angeles",
    )
    other_site = Site(
        id=new_uuid(),
        name="Other Count Site",
        code="other-count-site",
        timezone="America/Los_Angeles",
    )
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="count-sensor",
        name="Count Sensor",
        last_seen_at=now,
    )
    rule = AlertRule(
        id=new_uuid(),
        name="Measurement surge",
        rule_type="power_surge",
        severity="warning",
        site_id=site.id,
        device_id=device.id,
    )
    alert = AlertInstance(
        id=new_uuid(),
        rule_id=rule.id,
        device_id=device.id,
        site_id=site.id,
        status="active",
        severity="warning",
        opened_at=now,
        last_seen_at=now,
    )
    channel = NotificationChannel(
        id=new_uuid(),
        name="Reset count channel",
        channel_type="email",
        encrypted_config=b"test-only",
    )
    attempt = NotificationAttempt(
        id=new_uuid(),
        alert_instance_id=alert.id,
        channel_id=channel.id,
        attempted_at=now,
        queued_at=now,
        status="sent",
        attempt_number=1,
    )
    export_file = report_root / "target-export.csv"
    report_file = report_root / "target-report.json"
    export_file.write_text("reading\n", encoding="utf-8")
    report_file.write_text("{}\n", encoding="utf-8")
    export = ExportJob(
        id=new_uuid(),
        requested_by=user.id,
        format="csv",
        query={"site_id": site.id},
        status="queued",
        file_path=export_file.name,
        content_hash="b" * 64,
        created_at=now,
        expires_at=now + timedelta(days=1),
    )
    report = GeneratedReport(
        id=new_uuid(),
        requested_by=user.id,
        status="completed",
        file_path=report_file.name,
        data_coverage={"site_id": site.id},
        created_at=now,
        expires_at=now + timedelta(days=1),
    )
    unrelated_active = ExportJob(
        id=new_uuid(),
        requested_by=user.id,
        format="json",
        query={"site_id": other_site.id},
        status="queued",
        created_at=now,
        expires_at=now + timedelta(days=1),
    )
    log_export = LogExportJob(
        id=new_uuid(),
        requested_by=user.id,
        requested_at=now,
        start_date=date.today(),
        end_date=date.today(),
        services=["api"],
        status="completed",
        file_path=".exports/logs.zip",
        size_bytes=4,
        completed_at=now,
        expires_at=now + timedelta(minutes=15),
        correlation_id="count-reset-log-export",
    )
    log_export_path = log_root / ".exports" / "logs.zip"
    log_export_path.parent.mkdir(parents=True, exist_ok=True)
    log_export_path.write_bytes(b"logs")
    scoped_log = log_root / "api.jsonl"
    scoped_log.write_text(
        json.dumps({"site_id": site.id, "power_w": "1000"}) + "\n",
        encoding="utf-8",
    )
    session.add_all(
        [
            user,
            site,
            other_site,
            device,
            rule,
            alert,
            channel,
            attempt,
            export,
            report,
            unrelated_active,
            log_export,
        ]
    )
    await session.commit()

    with pytest.raises(ProblemError) as active_rejected:
        await create_reset_plan(
            session,
            site_id=site.id,
            requested_by=user.id,
            categories=ALL_RESET_CATEGORIES,
            delete_imported_bill_documents=False,
            disconnected_sensor_policy="defer_until_reconnect",
            offline_after_seconds=30,
            settings=test_settings,
            now=now,
        )
    assert active_rejected.value.code == "data_reset_output_jobs_active"
    assert await session.scalar(select(func.count()).select_from(DataResetPlan)) == 0
    assert export_file.is_file()
    export.status = "completed"
    await session.flush()

    plan = await create_reset_plan(
        session,
        site_id=site.id,
        requested_by=user.id,
        categories=ALL_RESET_CATEGORIES,
        delete_imported_bill_documents=False,
        disconnected_sensor_policy="defer_until_reconnect",
        offline_after_seconds=30,
        settings=test_settings,
        now=now,
    )
    counts = plan.plan_snapshot["counts"]
    assert counts["alert_instances"] == 1
    assert counts["notification_attempts"] == 1
    assert counts["exports"] == 1
    assert counts["reports"] == 1
    assert counts["export_files"] == 1
    assert counts["report_files"] == 1
    assert counts["log_exports"] == 1
    assert counts["log_export_files"] == 1
    assert counts["sanitized_log_files"] == 1
    assert counts["account_reconciliation_adjustments"] == 0
    assert counts["manual_bill_adjustments"] == 0
    assert counts["utility_bill_cycle_drafts"] == 0
    assert unrelated_active.id not in plan.plan_snapshot["outputs"]["export_job_ids"]
    assert "outputs" not in public_plan_payload(plan)

    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="exact-count-reset-1",
        reason="Verify exact deletion count rejection",
        backup_mode="permanent_without_backup",
        confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=True,
        offline_after_seconds=30,
        settings=test_settings,
        now=now,
    )
    operation.state = "backup_verified"
    added_after_plan = AlertInstance(
        id=new_uuid(),
        rule_id=rule.id,
        device_id=device.id,
        site_id=site.id,
        status="active",
        severity="warning",
        opened_at=now,
        last_seen_at=now,
    )
    session.add(added_after_plan)
    await session.commit()

    with pytest.raises(ProblemError) as rejected:
        await perform_central_reset(
            session,
            operation=operation,
            report_root=report_root,
            log_root=log_root,
            bill_artifact_root=bill_root,
            rate_artifact_root=rate_root,
            backup_root=backup_root,
            now=now,
        )
    assert rejected.value.code == "data_reset_plan_stale"
    assert await session.get(AlertInstance, alert.id) is not None
    assert await session.get(AlertInstance, added_after_plan.id) is not None
    assert await session.get(DataResetPlan, plan.id) is not None
    assert export_file.is_file()
    assert report_file.is_file()
    assert log_export_path.is_file()
    assert "1000" in scoped_log.read_text(encoding="utf-8")
    assert operation.central_commit_at is None
