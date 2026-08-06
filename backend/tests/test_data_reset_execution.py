from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from worker.app.data_reset import (
    _commit_operation,
    _validate_completed_receipt,
    process_data_reset_operations,
)

from app.config import Settings
from app.data_reset.sensor_client import SensorResetCommunicationError
from app.data_reset.service import (
    NO_BACKUP_CONFIRMATION_PHRASE,
    VERIFIED_BACKUP_CONFIRMATION_PHRASE,
    create_reset_operation,
    create_reset_plan,
    load_quarantine_journal,
    mark_cancel_requested,
    operation_payload,
    perform_central_reset,
    purge_staged_files,
    queue_reset_backup,
    reset_backup_verification_is_conclusive,
    retry_reset_operation,
    sanitize_scoped_logs,
    stage_file_for_reset,
)
from app.db.models import (
    BackupRun,
    DataResetOperation,
    DataResetParticipant,
    DataResetPlan,
    Device,
    DeviceCapability,
    DeviceDataState,
    RawReading,
    Site,
    SiteDataState,
    SyncCursor,
    User,
    new_uuid,
)
from app.problem import ProblemError

ALL_RESET_CATEGORIES = [
    "measurement_history",
    "cost_history",
    "pricing_history",
    "generated_outputs",
]


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("migration_revision", "unknown"),
        ("table_count", 20),
        ("required_table_count", 4),
        ("required_table_count", 5.0),
        ("status_layout_revisions", -1),
        ("postgres_major", 13),
    ],
)
def test_reset_backup_requires_conclusive_isolated_restore_evidence(
    field: str,
    invalid_value: object,
) -> None:
    now = datetime.now(UTC)
    details: dict[str, object] = {
        "migration_revision": "20260806_0031",
        "table_count": 100,
        "required_table_count": 5,
        "status_layout_revisions": 1,
        "postgres_major": 17,
    }
    backup = BackupRun(
        status="verified",
        verified_at=now,
        path="verified-reset-backup",
        size_bytes=4096,
        manifest_hash="a" * 64,
        verification_details=details,
    )
    assert reset_backup_verification_is_conclusive(backup) is True

    details[field] = invalid_value
    assert reset_backup_verification_is_conclusive(backup) is False


@pytest.mark.parametrize(
    ("field", "expected_code"),
    [
        ("queues_cleared", "sensor_reset_queues_not_cleared"),
        ("exports_cleared", "sensor_reset_exports_not_cleared"),
        ("indexes_rebuilt", "sensor_reset_indexes_not_rebuilt"),
    ],
)
def test_completion_receipt_requires_conclusive_local_cleanup_evidence(
    field: str,
    expected_code: str,
) -> None:
    participant = DataResetParticipant(reset_boundary=91)
    receipt = {
        "configuration_preserved": True,
        "pzem_baseline_captured": True,
        "local_records_after": 0,
        "backlog_after": 0,
        "configuration_preservation_digest_before": "b" * 64,
        "configuration_preservation_digest_after": "b" * 64,
        "queues_cleared": True,
        "exports_cleared": True,
        "indexes_rebuilt": True,
        "sequence_floor": 91,
        "next_sequence": 92,
    }
    receipt[field] = False

    with pytest.raises(SensorResetCommunicationError) as rejected:
        _validate_completed_receipt(
            participant,
            {
                "_commit_receipt_parsed": receipt,
                "configuration_preservation_digest_before": "b" * 64,
                "configuration_preservation_digest_after": "b" * 64,
            },
        )

    assert rejected.value.code == expected_code


@pytest.mark.parametrize(
    ("commit_pzem_energy_wh", "verified_pzem_energy_wh"),
    [(99, 101), (100, 99)],
)
def test_completion_receipt_rejects_pzem_counter_decrease(
    commit_pzem_energy_wh: int,
    verified_pzem_energy_wh: int,
) -> None:
    operation = DataResetOperation(
        id="187da6e7-c0da-4f95-a2d2-740b874ed9a4",
        reset_generation=2,
        plan_revision=1,
    )
    plan = DataResetPlan(plan_fingerprint="d" * 64)
    participant = DataResetParticipant(
        device_id="b09baa6a-273f-4338-88e1-af0b47989036",
        reset_boundary=91,
        prepare_receipt_digest="a" * 64,
        firmware_version="1.0.18",
        firmware_build_hash="b" * 64,
        boot_id="1b79f263-bd3a-4a53-9251-4c4278f5536a",
        card_generation="77",
        prepare_receipt_safe={
            "local_records_before": 1,
            "backlog_before": 0,
            "measurement_pause_started_utc_ms": 1_800_000_000_000,
        },
    )
    receipt = {
        "protocol": "data-reset/1.0.0",
        "operation_id": operation.id,
        "device_id": participant.device_id,
        "target_generation": 2,
        "plan_revision": 1,
        "plan_digest": plan.plan_fingerprint,
        "state": "completed",
        "checkpoint": "completed",
        "reset_boundary": 91,
        "prepared_receipt_digest": "a" * 64,
        "firmware_version": "1.0.18",
        "firmware_build_hash": "b" * 64,
        "boot_id": participant.boot_id,
        "card_generation": "77",
        "local_records_before": 1,
        "local_records_after": 0,
        "backlog_before": 0,
        "backlog_after": 0,
        "records_deleted": 1,
        "sequence_floor": 91,
        "next_sequence": 92,
        "server_ack_sequence": 91,
        "server_maximum_seen": 91,
        "prepared_pzem_energy_wh": 100,
        "commit_pzem_energy_wh": commit_pzem_energy_wh,
        "verified_pzem_energy_wh": verified_pzem_energy_wh,
        "measurement_pause_started_utc_ms": 1_800_000_000_000,
        "measurement_pause_ended_utc_ms": 1_800_000_000_001,
        "measurement_pause_evidenced": True,
        "configuration_preserved": True,
        "pzem_baseline_captured": True,
        "configuration_preservation_digest_before": "c" * 64,
        "configuration_preservation_digest_after": "c" * 64,
        "queues_cleared": True,
        "exports_cleared": True,
        "indexes_rebuilt": True,
    }

    with pytest.raises(SensorResetCommunicationError) as rejected:
        _validate_completed_receipt(
            participant,
            {
                "_commit_receipt_parsed": receipt,
                "configuration_preservation_digest_before": "c" * 64,
                "configuration_preservation_digest_after": "c" * 64,
            },
            operation=operation,
            plan=plan,
        )

    assert rejected.value.code == "sensor_reset_commit_receipt_inconclusive"


def test_log_sanitization_recovers_from_crash_after_quarantine_move(
    tmp_path: Path,
) -> None:
    operation_id = "187da6e7-c0da-4f95-a2d2-740b874ed9a4"
    site_id = "8ee76f54-5652-4a3f-9f87-8c481fdb5bc2"
    other_site_id = "45f06599-bf42-42e2-9995-896a487def0f"
    log_path = tmp_path / "events.jsonl"
    scoped = {"site_id": site_id, "power_w": "1200.5", "event": "sample"}
    preserved = {"site_id": other_site_id, "power_w": "800", "event": "sample"}
    log_path.write_text(
        "\n".join(json.dumps(item, separators=(",", ":")) for item in (scoped, preserved)) + "\n",
        encoding="utf-8",
    )
    journal: list[dict[str, str]] = []

    # Simulate a process crash after the original file was durably journaled
    # and moved, but before the redacted replacement was written.
    stage_file_for_reset(
        root=tmp_path,
        path=log_path,
        operation_id=operation_id,
        journal=journal,
    )
    assert not log_path.exists()

    recovered = load_quarantine_journal(root=tmp_path, operation_id=operation_id)
    assert (
        sanitize_scoped_logs(
            log_root=tmp_path,
            operation_id=operation_id,
            site_id=site_id,
            device_ids=set(),
            journal=recovered,
        )
        == 1
    )

    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["power_w"] == "[redacted-by-data-reset]"
    assert lines[1] == preserved
    purge_staged_files(recovered)
    assert log_path.is_file()
    assert not (tmp_path / ".data-reset-quarantine" / operation_id / "journal.json").exists()


@pytest.mark.asyncio
async def test_pinned_backup_manifest_is_reverified_before_central_commit(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="backup-pin-reset@example.com",
        display_name="Backup Pin Reset",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Backup Pin Reset Site",
        code="backup-pin-reset-site",
        timezone="America/Los_Angeles",
    )
    session.add_all([user, site])
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
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="backup-pin-reset-1",
        reason="Verify backup pin before deletion",
        backup_mode="verified_backup",
        confirmation_phrase=VERIFIED_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=False,
        offline_after_seconds=30,
        now=now,
    )
    backup = await queue_reset_backup(session, operation, now=now)
    backup_directory = test_settings.backup_path / "pinned-reset-backup"
    backup_directory.mkdir(parents=True, exist_ok=True)
    database_dump = backup_directory / "database.dump"
    database_dump.write_bytes(b"verified logical backup")
    checksums = backup_directory / "checksums.sha256"
    checksums.write_text(
        f"{hashlib.sha256(database_dump.read_bytes()).hexdigest()}  database.dump\n",
        encoding="utf-8",
    )
    manifest = backup_directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "power-monitor-backup/v2",
                "checksums_sha256": hashlib.sha256(checksums.read_bytes()).hexdigest(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    backup.path = "pinned-reset-backup"
    backup.status = "verified"
    backup.manifest_hash = manifest_hash
    backup.verified_at = now
    backup.size_bytes = manifest.stat().st_size
    backup.verification_details = {
        "migration_revision": "20260806_0031",
        "table_count": 100,
        "required_table_count": 5,
        "status_layout_revisions": 1,
        "postgres_major": 17,
    }
    operation.backup_checksum = manifest_hash
    operation.backup_verified_at = now
    operation.backup_reference = str(backup_directory.resolve())
    operation.state = "backup_verified"
    await session.commit()

    public_operation = await operation_payload(session, operation)
    assert public_operation["backup"]["reference"] == backup.id
    assert str(backup_directory.resolve()) not in json.dumps(public_operation, default=str)

    database_dump.write_bytes(b"tampered logical backup")
    test_settings.rate_sync_artifact_path = test_settings.report_path.parent / "rate-artifacts"
    for path in (
        test_settings.report_path,
        test_settings.log_path,
        test_settings.utility_bill_artifact_path,
        test_settings.rate_sync_artifact_path,
    ):
        path.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ProblemError) as rejected:
        await perform_central_reset(
            session,
            operation=operation,
            report_root=test_settings.report_path,
            log_root=test_settings.log_path,
            bill_artifact_root=test_settings.utility_bill_artifact_path,
            rate_artifact_root=test_settings.rate_sync_artifact_path,
            backup_root=test_settings.backup_path,
            now=now,
        )

    assert rejected.value.code == "data_reset_backup_artifact_invalid"
    assert operation.central_commit_at is None


@pytest.mark.asyncio
async def test_disconnected_sensor_reset_deletes_history_and_advances_generation(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="reset-executor@example.com",
        display_name="Reset Executor",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Reset Execution Site",
        code="reset-execution-site",
        timezone="America/Los_Angeles",
    )
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="reset-execution-sensor",
        name="Disconnected Reset Sensor",
        firmware_version="1.0.18",
        firmware_build_hash="a" * 64,
        last_seen_at=now - timedelta(hours=1),
    )
    capability = DeviceCapability(
        device_id=device.id,
        hardware_target="esp32-s3",
        pzem_model="PZEM-004T V4.0",
        sd_required=True,
        features={"supported_endpoints": ["data-reset/1.0.0"]},
        reported_at=now,
    )
    cursor = SyncCursor(
        device_id=device.id,
        highest_contiguous_sequence=8,
        maximum_seen_sequence=9,
        data_generation=0,
        reset_boundary=0,
        updated_at=now,
    )
    reading = RawReading(
        id=new_uuid(),
        device_id=device.id,
        site_id=site.id,
        data_generation=0,
        sequence=9,
        boot_id=new_uuid(),
        interval_start=now - timedelta(minutes=1),
        interval_end=now,
        time_trusted=True,
        power_avg=Decimal("1200.5"),
        device_interval_energy_wh=Decimal("20.0"),
        energy_method="power_integration",
        ct_rating_amps=Decimal("100"),
        quality_flags=[],
        firmware_version="1.0.18",
        record_hash="a" * 64,
        original_payload={"power_avg": "1200.5"},
        ingestion_source="push",
        ingested_at=now,
    )
    session.add_all([user, site, device, capability, cursor, reading])
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
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="execution-test-reset-1",
        reason="Commissioning history cleanup",
        backup_mode="permanent_without_backup",
        confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=True,
        offline_after_seconds=30,
        now=now,
    )
    await session.commit()

    test_settings.rate_sync_artifact_path = test_settings.report_path.parent / "rate-artifacts"
    for path in (
        test_settings.report_path,
        test_settings.log_path,
        test_settings.utility_bill_artifact_path,
        test_settings.rate_sync_artifact_path,
    ):
        path.mkdir(parents=True, exist_ok=True)

    states: list[str] = []
    for _ in range(5):
        result = await process_data_reset_operations(session, test_settings)
        states.append(str(result.get("state")))

    await session.refresh(operation)
    assert "sensors_prepared" in states
    assert "backup_verified" in states
    assert "database_reset_committed" in states
    assert operation.state == "completed_with_resets_pending_on_reconnect"
    assert operation.central_commit_at is not None
    assert operation.final_evidence["deleted_counts"]["raw_readings"] == 1
    assert await session.scalar(select(func.count()).select_from(RawReading)) == 0
    assert await session.get(Device, device.id) is not None
    assert await session.get(DeviceCapability, device.id) is not None

    device_state = await session.get(DeviceDataState, device.id)
    assert device_state is not None
    assert device_state.data_generation == 1
    assert device_state.reset_boundary == 9
    assert device_state.ingestion_gate == "pending_reconnect"
    assert device_state.reset_required_on_reconnect is True

    reset_cursor = await session.get(SyncCursor, device.id)
    assert reset_cursor is not None
    assert reset_cursor.data_generation == 1
    assert reset_cursor.reset_boundary == 9
    assert reset_cursor.highest_contiguous_sequence >= 9
    assert reset_cursor.maximum_seen_sequence >= 9

    site_state = await session.get(SiteDataState, site.id)
    assert site_state is not None
    assert site_state.data_generation == 1
    assert site_state.history_revision == 1
    assert site_state.last_reset_operation_id == operation.id

    active = await session.scalar(
        select(DataResetOperation).where(DataResetOperation.id == operation.id)
    )
    assert active is not None
    assert active.backup_verified_at is None
    assert active.backup_mode == "permanent_without_backup"


@pytest.mark.asyncio
async def test_missing_required_backup_fails_before_commit_and_reopens_scope(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="backup-reset-executor@example.com",
        display_name="Backup Reset Executor",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Missing Backup Reset Site",
        code="missing-backup-reset-site",
        timezone="America/Los_Angeles",
    )
    session.add_all([user, site])
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
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="missing-backup-reset-1",
        reason="Exercise missing backup safety",
        backup_mode="verified_backup",
        confirmation_phrase=VERIFIED_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=False,
        offline_after_seconds=30,
        now=now,
    )
    operation.state = "backup_running"
    operation.backup_run_id = new_uuid()
    await session.commit()

    first = await process_data_reset_operations(session, test_settings)
    assert first["state"] == "preparing_sensors"
    await session.refresh(operation)
    assert operation.central_commit_at is None
    assert operation.final_evidence["cancel_requested"] is True
    assert operation.failure_code == "data_reset_backup_missing"

    second = await process_data_reset_operations(session, test_settings)
    assert second["state"] == "failed_before_commit"
    await session.refresh(operation)
    assert operation.state == "failed_before_commit"
    assert operation.central_commit_at is None


@pytest.mark.asyncio
async def test_inconclusive_verified_backup_fails_before_commit(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="inconclusive-backup-reset@example.com",
        display_name="Inconclusive Backup Reset",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Inconclusive Backup Reset Site",
        code="inconclusive-backup-reset-site",
        timezone="America/Los_Angeles",
    )
    session.add_all([user, site])
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
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="inconclusive-backup-reset-1",
        reason="Reject incomplete isolated restore evidence",
        backup_mode="verified_backup",
        confirmation_phrase=VERIFIED_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=False,
        offline_after_seconds=30,
        now=now,
    )
    backup = await queue_reset_backup(session, operation, now=now)
    backup.status = "verified"
    backup.verified_at = now
    backup.path = "inconclusive-reset-backup"
    backup.manifest_hash = "e" * 64
    backup.size_bytes = 4096
    backup.verification_details = {"table_count": 100}
    await session.commit()

    first = await process_data_reset_operations(session, test_settings)
    assert first["state"] == "preparing_sensors"
    await session.refresh(operation)
    assert operation.central_commit_at is None
    assert operation.failure_code == "data_reset_backup_verification_inconclusive"

    second = await process_data_reset_operations(session, test_settings)
    assert second["state"] == "failed_before_commit"
    await session.refresh(operation)
    assert operation.state == "failed_before_commit"
    assert operation.central_commit_at is None


@pytest.mark.asyncio
async def test_completion_waits_for_new_generation_reading_proof(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="verification-reset-executor@example.com",
        display_name="Verification Reset Executor",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Reset Verification Site",
        code="reset-verification-site",
        timezone="America/Los_Angeles",
    )
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="reset-verification-sensor",
        name="Reset Verification Sensor",
        firmware_version="1.0.18",
        firmware_build_hash="b" * 64,
        last_seen_at=now - timedelta(hours=1),
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
                features={"supported_endpoints": ["data-reset/1.0.0"]},
                reported_at=now,
            ),
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
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="verification-reset-1",
        reason="Verify new generation readings",
        backup_mode="permanent_without_backup",
        confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=True,
        offline_after_seconds=30,
        now=now,
    )
    participant = await session.get(DataResetParticipant, (operation.id, device.id))
    assert participant is not None
    participant.state = "verified"
    participant.verified_at = now
    operation.state = "verification_running"
    operation.central_commit_at = now
    await session.commit()

    await _commit_operation(session, operation, plan, test_settings)
    await session.refresh(operation)
    assert operation.state == "verification_running"
    assert operation.final_evidence["new_readings_received"] is False

    session.add(
        RawReading(
            id=new_uuid(),
            device_id=device.id,
            site_id=site.id,
            data_generation=operation.reset_generation,
            sequence=1,
            boot_id=new_uuid(),
            interval_start=now,
            interval_end=now + timedelta(minutes=1),
            time_trusted=True,
            power_avg=Decimal("900"),
            device_interval_energy_wh=Decimal("15"),
            energy_method="power_integration",
            ct_rating_amps=Decimal("100"),
            quality_flags=[],
            firmware_version="1.0.18",
            record_hash="b" * 64,
            original_payload=None,
            ingestion_source="push",
            ingested_at=now + timedelta(seconds=1),
        )
    )
    await session.commit()

    await _commit_operation(session, operation, plan, test_settings)
    await session.refresh(operation)
    assert operation.state == "completed"
    assert operation.final_evidence["new_readings_received"] is True
    assert operation.final_evidence["new_cost_status"] == "not_requested_or_not_applicable"


@pytest.mark.asyncio
async def test_planned_connected_prepare_failure_requires_explicit_retry(
    session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="connected-reset-block@example.com",
        display_name="Connected Reset Block",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Connected Reset Block Site",
        code="connected-reset-block-site",
        timezone="America/Los_Angeles",
    )
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="connected-reset-block-sensor",
        name="Connected Reset Block Sensor",
        firmware_version="1.0.18",
        firmware_build_hash="c" * 64,
        last_seen_at=now,
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
                reported_at=now,
            ),
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
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="connected-reset-block-1",
        reason="Prove connected prepare failures block",
        backup_mode="permanent_without_backup",
        confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=True,
        offline_after_seconds=30,
        now=now,
    )
    await session.commit()

    async def unavailable(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise SensorResetCommunicationError(
            "sensor_reset_unreachable",
            "Sensor stopped responding after confirmation",
            retryable=True,
        )

    monkeypatch.setattr("worker.app.data_reset.request_sensor_reset", unavailable)
    result = await process_data_reset_operations(session, test_settings)
    await session.refresh(operation)
    participant = await session.get(DataResetParticipant, (operation.id, device.id))
    state = await session.get(DeviceDataState, device.id)

    assert result["state"] == "attention_required"
    assert operation.state == "attention_required"
    assert operation.central_commit_at is None
    assert participant is not None
    assert participant.planned_classification == "connected"
    assert participant.state == "attention_required"
    assert state is not None
    assert state.ingestion_gate == "attention_required"
    assert (await process_data_reset_operations(session, test_settings))["processed"] == 0

    cancel_calls: list[str] = []

    async def cancel_after_lost_prepare(*_args: object, **kwargs: object) -> dict[str, object]:
        action = str(kwargs["action"])
        cancel_calls.append(action)
        assert action == "cancel"
        return {"state": "cancelled"}

    monkeypatch.setattr("worker.app.data_reset.request_sensor_reset", cancel_after_lost_prepare)
    await mark_cancel_requested(session, operation, now=datetime.now(UTC))
    await session.commit()
    cancelled = await process_data_reset_operations(session, test_settings)
    await session.refresh(operation)
    await session.refresh(state)
    assert cancelled["state"] == "cancelled"
    assert cancel_calls == ["cancel"]
    assert operation.central_commit_at is None
    assert state.ingestion_gate == "open"


@pytest.mark.asyncio
async def test_connected_prepare_snapshot_change_blocks_commit_and_remains_cancellable(
    session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="connected-reset-stale@example.com",
        display_name="Connected Reset Stale",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Connected Reset Stale Site",
        code="connected-reset-stale-site",
        timezone="America/Los_Angeles",
    )
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="connected-reset-stale-sensor",
        name="Connected Reset Stale Sensor",
        firmware_version="1.0.18",
        firmware_build_hash="d" * 64,
        last_seen_at=now,
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
                reported_at=now,
            ),
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
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="connected-reset-stale-1",
        reason="Prove a changed prepared snapshot cannot cross central commit",
        backup_mode="permanent_without_backup",
        confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=True,
        offline_after_seconds=30,
        now=now,
    )
    await session.commit()
    participant = await session.get(DataResetParticipant, (operation.id, device.id))
    assert participant is not None
    planned = next(
        item for item in plan.plan_snapshot["participants"] if item["device_id"] == device.id
    )
    changed_boundary = int(planned["boundary"]) + 1
    calls: list[str] = []

    async def reset_response(*_args: object, **kwargs: object) -> dict[str, object]:
        action = str(kwargs["action"])
        calls.append(action)
        if action == "cancel":
            return {"state": "cancelled"}
        assert action == "prepare"
        return {
            "state": "prepared",
            "_prepared_receipt_parsed": {
                "protocol": "data-reset/1.0.0",
                "operation_id": operation.id,
                "device_id": device.id,
                "target_generation": operation.reset_generation,
                "plan_revision": operation.plan_revision,
                "plan_digest": plan.plan_fingerprint,
                "state": "prepared",
                "checkpoint": "prepared",
                "firmware_version": device.firmware_version,
                "firmware_build_hash": device.firmware_build_hash,
                "boot_id": "16bd91c7-f3ba-4ad7-a882-09545acf23c7",
                "reset_boundary": changed_boundary,
                "sequence_floor": changed_boundary,
                "next_sequence": changed_boundary + 1,
                "server_ack_sequence": changed_boundary,
                "server_maximum_seen": changed_boundary,
                "newest_stored_sequence": changed_boundary,
                "newest_syncable_sequence": changed_boundary,
                "local_records_before": int(planned["local_record_count"]),
                "local_records_after": int(planned["local_record_count"]),
                "backlog_before": int(planned["backlog_estimate"]),
                "backlog_after": int(planned["backlog_estimate"]),
                "prepare_drain_records_added": 0,
                "prepare_drain_first_sequence": None,
                "prepare_drain_last_sequence": None,
                "prepare_drain_syncable_records_added": 0,
                "measurement_pause_started_utc_ms": 1_800_000_000_000,
                "card_generation": "stale-card",
                "prepared_pzem_energy_wh": 100_025,
                "software_energy_baseline_before_wh": 12_000,
                "pzem_baseline_captured": True,
                "configuration_preserved": True,
                "configuration_preservation_digest_before": "b" * 64,
                "sd_status": "verified",
            },
            "prepared_receipt_digest": "a" * 64,
            "configuration_preservation_digest_before": "b" * 64,
        }

    monkeypatch.setattr("worker.app.data_reset.request_sensor_reset", reset_response)
    result = await process_data_reset_operations(session, test_settings)
    await session.refresh(operation)
    await session.refresh(participant)
    await session.refresh(plan)
    device_state = await session.get(DeviceDataState, device.id)

    assert result["state"] == "attention_required"
    assert calls == ["prepare"]
    assert operation.central_commit_at is None
    assert operation.state == "attention_required"
    assert participant.state == "attention_required"
    assert participant.failure_code == "data_reset_plan_stale"
    assert participant.reset_boundary == planned["boundary"]
    assert participant.prepared_at is not None
    assert plan.invalidated_at is not None
    assert device_state is not None
    assert device_state.ingestion_gate == "attention_required"

    await mark_cancel_requested(session, operation, now=datetime.now(UTC))
    await session.commit()
    cancelled = await process_data_reset_operations(session, test_settings)
    await session.refresh(operation)
    await session.refresh(participant)
    await session.refresh(device_state)

    assert cancelled["state"] == "cancelled"
    assert calls == ["prepare", "cancel"]
    assert operation.state == "cancelled"
    assert operation.central_commit_at is None
    assert participant.state == "pending"
    assert device_state.ingestion_gate == "open"
    assert device_state.active_operation_id is None


@pytest.mark.asyncio
async def test_cancel_authority_uses_durable_commit_boundaries(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="reset-cancel-boundary@example.com",
        display_name="Reset Cancel Boundary",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Reset Cancel Boundary Site",
        code="reset-cancel-boundary-site",
        timezone="America/Los_Angeles",
    )
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="reset-cancel-boundary-sensor",
        name="Reset Cancel Boundary Sensor",
        firmware_version="1.0.18",
        firmware_build_hash="e" * 64,
        last_seen_at=now,
    )
    session.add_all([user, site, device])
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
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="reset-cancel-boundary-1",
        reason="Exercise durable cancellation boundaries",
        backup_mode="permanent_without_backup",
        confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=True,
        offline_after_seconds=30,
        now=now,
    )
    participant = await session.get(DataResetParticipant, (operation.id, device.id))
    assert participant is not None

    operation.state = "attention_required"
    operation.central_commit_at = now
    await session.commit()
    with pytest.raises(ProblemError) as central_rejected:
        await mark_cancel_requested(session, operation, now=now)
    assert central_rejected.value.code == "data_reset_cancel_unsafe"

    operation.central_commit_at = None
    participant.commit_authorized_at = now
    await session.commit()
    with pytest.raises(ProblemError) as sensor_rejected:
        await mark_cancel_requested(session, operation, now=now)
    assert sensor_rejected.value.code == "data_reset_cancel_unsafe"

    participant.commit_authorized_at = None
    operation.state = "partial_failure"
    await session.commit()
    await mark_cancel_requested(session, operation, now=now)
    assert operation.final_evidence["cancel_requested"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("inventory_state", ["revoked", "removed"])
async def test_inactive_inventory_is_non_applicable_and_never_gated(
    session: AsyncSession,
    test_settings: Settings,
    inventory_state: str,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email=f"{inventory_state}-reset@example.com",
        display_name=f"{inventory_state.title()} Reset",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name=f"{inventory_state.title()} Reset Site",
        code=f"{inventory_state}-reset-site",
        timezone="America/Los_Angeles",
    )
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id=f"{inventory_state}-reset-sensor",
        name=f"{inventory_state.title()} Reset Sensor",
        lifecycle_status="active" if inventory_state == "revoked" else "decommissioned",
        revoked_at=now if inventory_state == "revoked" else None,
        last_seen_at=now,
    )
    session.add_all([user, site, device])
    await session.commit()
    plan = await create_reset_plan(
        session,
        site_id=site.id,
        requested_by=user.id,
        categories=ALL_RESET_CATEGORIES,
        delete_imported_bill_documents=False,
        disconnected_sensor_policy="block",
        offline_after_seconds=30,
        now=now,
    )
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key=f"{inventory_state}-reset-operation-1",
        reason="Verify inactive inventory is excluded",
        backup_mode="permanent_without_backup",
        confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=True,
        offline_after_seconds=30,
        now=now,
    )
    await session.commit()

    participant = await session.get(DataResetParticipant, (operation.id, device.id))
    device_state = await session.get(DeviceDataState, device.id)
    assert participant is not None
    assert participant.state == "not_applicable"
    assert participant.planned_classification == inventory_state
    assert device_state is not None
    assert device_state.ingestion_gate == "open"
    assert device_state.reset_required_on_reconnect is False
    assert device_state.active_operation_id is None

    test_settings.rate_sync_artifact_path = test_settings.report_path.parent / "rate-artifacts"
    for path in (
        test_settings.report_path,
        test_settings.log_path,
        test_settings.utility_bill_artifact_path,
        test_settings.rate_sync_artifact_path,
    ):
        path.mkdir(parents=True, exist_ok=True)
    for _ in range(5):
        await process_data_reset_operations(session, test_settings)

    await session.refresh(operation)
    await session.refresh(device_state)
    assert operation.state == "completed"
    assert device_state.ingestion_gate == "open"
    assert device_state.reset_required_on_reconnect is False
    assert device_state.active_operation_id is None


@pytest.mark.asyncio
async def test_active_unsupported_sensor_can_upgrade_and_finish_local_reset(
    session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="unsupported-upgrade@example.com",
        display_name="Unsupported Upgrade",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Unsupported Upgrade Site",
        code="unsupported-upgrade-site",
        timezone="America/Los_Angeles",
    )
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="unsupported-upgrade-sensor",
        name="Unsupported Upgrade Sensor",
        firmware_version="1.0.17",
        firmware_build_hash="f" * 64,
        last_seen_at=now,
    )
    capability = DeviceCapability(
        device_id=device.id,
        hardware_target="esp32-s3",
        pzem_model="PZEM-004T V4.0",
        sd_required=True,
        features={"supported_endpoints": ["health", "readings"]},
        reported_at=now,
    )
    session.add_all([user, site, device, capability])
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
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="unsupported-upgrade-reset-1",
        reason="Verify reset capability upgrade path",
        backup_mode="permanent_without_backup",
        confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=True,
        offline_after_seconds=30,
        now=now,
    )
    await session.commit()
    test_settings.rate_sync_artifact_path = test_settings.report_path.parent / "rate-artifacts"
    for path in (
        test_settings.report_path,
        test_settings.log_path,
        test_settings.utility_bill_artifact_path,
        test_settings.rate_sync_artifact_path,
    ):
        path.mkdir(parents=True, exist_ok=True)

    for _ in range(4):
        await process_data_reset_operations(session, test_settings)
    await session.refresh(operation)
    participant = await session.get(DataResetParticipant, (operation.id, device.id))
    device_state = await session.get(DeviceDataState, device.id)
    assert operation.state == "sensor_commit_running"
    assert participant is not None
    assert participant.state == "unsupported"
    assert participant.planned_classification == "unsupported"
    assert device_state is not None
    assert device_state.ingestion_gate == "pending_reconnect"

    capability.features = {"data_reset": "data-reset/1.0.0"}
    device.firmware_version = "1.0.18"
    device.firmware_build_hash = "9" * 64
    await session.commit()
    calls: list[str] = []
    planned_boundary = participant.reset_boundary

    async def reset_response(*_args: object, **kwargs: object) -> dict[str, object]:
        action = str(kwargs["action"])
        calls.append(action)
        if action == "prepare":
            payload = kwargs["payload"]
            assert isinstance(payload, dict)
            assert payload["reset_timestamp"].endswith("Z")
            assert "+00:00" not in payload["reset_timestamp"]
            return {
                "state": "prepared",
                "_prepared_receipt_parsed": {
                    "protocol": "data-reset/1.0.0",
                    "operation_id": operation.id,
                    "device_id": device.id,
                    "target_generation": operation.reset_generation,
                    "plan_revision": operation.plan_revision,
                    "plan_digest": plan.plan_fingerprint,
                    "state": "prepared",
                    "checkpoint": "prepared",
                    "firmware_version": device.firmware_version,
                    "firmware_build_hash": device.firmware_build_hash,
                    "boot_id": "1b79f263-bd3a-4a53-9251-4c4278f5536a",
                    "reset_boundary": planned_boundary + 5,
                    "sequence_floor": planned_boundary,
                    "next_sequence": planned_boundary + 6,
                    "server_ack_sequence": planned_boundary + 2,
                    "server_maximum_seen": planned_boundary + 3,
                    "newest_stored_sequence": planned_boundary + 5,
                    "newest_syncable_sequence": planned_boundary + 4,
                    "local_records_before": 42,
                    "local_records_after": 42,
                    "backlog_before": 2,
                    "backlog_after": 2,
                    "prepare_drain_records_added": 0,
                    "prepare_drain_first_sequence": None,
                    "prepare_drain_last_sequence": None,
                    "prepare_drain_syncable_records_added": 0,
                    "measurement_pause_started_utc_ms": 1_800_000_000_000,
                    "card_generation": "77",
                    "prepared_pzem_energy_wh": 100_025,
                    "software_energy_baseline_before_wh": 12_000,
                    "pzem_baseline_captured": True,
                    "configuration_preserved": True,
                    "configuration_preservation_digest_before": "b" * 64,
                    "sd_status": "verified",
                },
                "prepared_receipt_digest": "a" * 64,
                "configuration_preservation_digest_before": "b" * 64,
            }
        assert action == "commit"
        if calls.count("commit") == 1:
            return {
                "state": "prepared",
                "failure_code": "data_reset_pzem_commit_capture_failed",
            }
        return {
            "state": "completed",
            "_commit_receipt_parsed": {
                "protocol": "data-reset/1.0.0",
                "operation_id": operation.id,
                "device_id": device.id,
                "target_generation": operation.reset_generation,
                "plan_revision": operation.plan_revision,
                "plan_digest": plan.plan_fingerprint,
                "state": "completed",
                "checkpoint": "completed",
                "firmware_version": device.firmware_version,
                "firmware_build_hash": device.firmware_build_hash,
                "boot_id": "1b79f263-bd3a-4a53-9251-4c4278f5536a",
                "card_generation": "77",
                "configuration_preserved": True,
                "pzem_baseline_captured": True,
                "local_records_before": 42,
                "local_records_after": 0,
                "backlog_before": 2,
                "backlog_after": 0,
                "records_deleted": 42,
                "prepared_receipt_digest": "a" * 64,
                "prepared_pzem_energy_wh": 100_025,
                "commit_pzem_energy_wh": 100_026,
                "verified_pzem_energy_wh": 100_027,
                "measurement_pause_started_utc_ms": 1_800_000_000_000,
                "measurement_pause_ended_utc_ms": 1_800_000_000_001,
                "measurement_pause_evidenced": True,
                "configuration_preservation_digest_before": "b" * 64,
                "configuration_preservation_digest_after": "b" * 64,
                "queues_cleared": True,
                "exports_cleared": True,
                "indexes_rebuilt": True,
                "reset_boundary": participant.reset_boundary,
                "sequence_floor": participant.reset_boundary,
                "next_sequence": participant.reset_boundary + 1,
                "server_ack_sequence": participant.reset_boundary,
                "server_maximum_seen": participant.reset_boundary,
            },
            "commit_receipt_digest": "c" * 64,
            "configuration_preservation_digest_before": "b" * 64,
            "configuration_preservation_digest_after": "b" * 64,
        }

    monkeypatch.setattr("worker.app.data_reset.request_sensor_reset", reset_response)
    await process_data_reset_operations(session, test_settings)
    await session.refresh(operation)
    await session.refresh(participant)
    assert calls == ["prepare", "commit"]
    assert participant.state == "prepared"
    assert operation.state == "sensor_commit_running"

    await process_data_reset_operations(session, test_settings)
    await session.refresh(operation)
    await session.refresh(participant)
    await session.refresh(device_state)
    reset_cursor = await session.get(SyncCursor, device.id)
    assert calls == ["prepare", "commit", "commit"]
    assert participant.state == "verified"
    assert participant.reset_boundary == planned_boundary + 5
    assert participant.old_sequence_floor == planned_boundary
    assert participant.old_next_sequence == planned_boundary + 6
    assert participant.sensor_ack_sequence == planned_boundary + 2
    assert participant.sensor_newest_sequence == planned_boundary + 5
    assert operation.state == "verification_running"
    assert device_state.data_generation == operation.reset_generation
    assert device_state.reset_boundary == planned_boundary + 5
    assert device_state.ingestion_gate == "open"
    assert device_state.reset_required_on_reconnect is False
    assert reset_cursor is not None
    assert reset_cursor.data_generation == operation.reset_generation
    assert reset_cursor.reset_boundary == planned_boundary + 5
    assert reset_cursor.highest_contiguous_sequence == planned_boundary + 5
    assert reset_cursor.maximum_seen_sequence == planned_boundary + 5


@pytest.mark.asyncio
async def test_waiting_operations_do_not_starve_an_unrelated_site(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="reset-scheduler@example.com",
        display_name="Reset Scheduler",
        password_hash="not-used",
    )
    session.add(user)
    await session.commit()

    async def operation_for(index: int) -> DataResetOperation:
        site = Site(
            id=new_uuid(),
            name=f"Reset Scheduler Site {index}",
            code=f"reset-scheduler-site-{index}",
            timezone="America/Los_Angeles",
        )
        session.add(site)
        await session.commit()
        plan = await create_reset_plan(
            session,
            site_id=site.id,
            requested_by=user.id,
            categories=ALL_RESET_CATEGORIES,
            delete_imported_bill_documents=False,
            disconnected_sensor_policy="defer_until_reconnect",
            offline_after_seconds=30,
            now=now + timedelta(seconds=index),
        )
        operation = await create_reset_operation(
            session,
            plan_id=plan.id,
            plan_revision=plan.revision,
            requested_by=user.id,
            idempotency_key=f"scheduler-reset-{index}",
            reason=f"Exercise scheduler operation {index}",
            backup_mode="permanent_without_backup",
            confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
            permanent_without_backup_acknowledged=True,
            offline_after_seconds=30,
            now=now + timedelta(seconds=index),
        )
        await session.commit()
        return operation

    attention = await operation_for(1)
    partial = await operation_for(2)
    reconnect = await operation_for(3)
    actionable = await operation_for(4)
    attention.state = "attention_required"
    partial.state = "partial_failure"
    partial.central_commit_at = now
    reconnect.state = "completed_with_resets_pending_on_reconnect"
    reconnect.central_commit_at = now
    await session.commit()

    result = await process_data_reset_operations(session, test_settings)
    assert result["operation_id"] == actionable.id
    assert result["state"] == "sensors_prepared"
    await session.refresh(attention)
    await session.refresh(partial)
    await session.refresh(reconnect)
    assert attention.state == "attention_required"
    assert partial.state == "partial_failure"
    assert reconnect.state == "completed_with_resets_pending_on_reconnect"

    actionable.state = "completed"
    await session.commit()
    probed = await process_data_reset_operations(session, test_settings)
    assert probed["operation_id"] == reconnect.id
    assert probed["state"] == "completed"


@pytest.mark.asyncio
async def test_cancelled_and_failed_before_commit_operations_cannot_retry(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="terminal-reset@example.com",
        display_name="Terminal Reset",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Terminal Reset Site",
        code="terminal-reset-site",
        timezone="America/Los_Angeles",
    )
    session.add_all([user, site])
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
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="terminal-reset-operation-1",
        reason="Verify terminal retry rejection",
        backup_mode="permanent_without_backup",
        confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=True,
        offline_after_seconds=30,
        now=now,
    )

    for terminal_state in ("cancelled", "failed_before_commit"):
        operation.state = terminal_state
        with pytest.raises(ProblemError) as denied:
            await retry_reset_operation(session, operation, now=now)
        assert denied.value.code == "data_reset_retry_unavailable"
        assert operation.state == terminal_state
