from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from worker.app.data_reset import process_data_reset_operations

from app.config import Settings
from app.data_reset.sensor_client import SensorResetCommunicationError
from app.data_reset.service import (
    DATA_RESET_PROTOCOL,
    VERIFIED_BACKUP_CONFIRMATION_PHRASE,
    create_reset_operation,
    create_reset_plan,
)
from app.db.models import (
    BackupRun,
    DataResetParticipant,
    Device,
    DeviceCapability,
    DeviceDataState,
    DeviceHeartbeat,
    RawReading,
    Site,
    SyncCursor,
    User,
    new_uuid,
)
from app.ingestion.service import ingest_readings
from app.problem import ProblemError
from app.schemas import Reading

ALL_RESET_CATEGORIES = [
    "measurement_history",
    "cost_history",
    "pricing_history",
    "generated_outputs",
]


def _stored_reading(
    *,
    device: Device,
    sequence: int,
    now: datetime,
    record_hash: str,
) -> RawReading:
    return RawReading(
        id=new_uuid(),
        device_id=device.id,
        site_id=device.site_id,
        data_generation=0,
        sequence=sequence,
        boot_id=new_uuid(),
        interval_start=now - timedelta(minutes=2),
        interval_end=now - timedelta(minutes=1),
        time_trusted=True,
        power_avg=Decimal("700"),
        device_interval_energy_wh=Decimal("12"),
        energy_method="power_integration",
        ct_rating_amps=Decimal("100"),
        quality_flags=[],
        firmware_version="1.0.18",
        record_hash=record_hash,
        original_payload={"power_avg": "700"},
        ingestion_source="push",
        ingested_at=now - timedelta(minutes=1),
    )


def _new_reading(*, generation: int, sequence: int, now: datetime) -> Reading:
    return Reading(
        data_generation=generation,
        sequence=sequence,
        boot_id=new_uuid(),
        interval_start=now,
        interval_end=now + timedelta(minutes=1),
        time_trusted=True,
        power_avg=Decimal("800"),
        interval_energy_wh=Decimal("13"),
        energy_method="power_integration",
        ct_rating_amps=Decimal("100"),
        quality_flags=[],
        firmware_version="1.0.18",
    )


@pytest.mark.asyncio
async def test_three_sensor_reset_requires_reconnect_reset_before_new_sync(
    session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    user = User(
        id=new_uuid(),
        email="multi-sensor-reset@example.com",
        display_name="Multi Sensor Reset",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Multi Sensor Reset Site",
        code="multi-sensor-reset-site",
        timezone="America/Los_Angeles",
    )
    devices = [
        Device(
            id=new_uuid(),
            site_id=site.id,
            hardware_id=f"multi-reset-sensor-{index}",
            name=f"Multi Reset Sensor {index}",
            firmware_version="1.0.18",
            firmware_build_hash=str(index) * 64,
            last_seen_at=now if index < 3 else now - timedelta(hours=1),
        )
        for index in range(1, 4)
    ]
    sequences = {device.id: index * 10 for index, device in enumerate(devices, start=1)}
    local_counts = {
        devices[0].id: 4,
        devices[1].id: 7,
        devices[2].id: 5,
    }
    session.add_all([user, site, *devices])
    for index, device in enumerate(devices, start=1):
        sequence = sequences[device.id]
        session.add_all(
            [
                DeviceCapability(
                    device_id=device.id,
                    hardware_target="esp32-s3",
                    pzem_model="PZEM-004T V4.0",
                    sd_required=True,
                    features={"data_reset": DATA_RESET_PROTOCOL},
                    reported_at=now,
                ),
                SyncCursor(
                    device_id=device.id,
                    highest_contiguous_sequence=sequence - 1,
                    maximum_seen_sequence=sequence,
                    data_generation=0,
                    reset_boundary=0,
                    updated_at=now,
                ),
                _stored_reading(
                    device=device,
                    sequence=sequence,
                    now=now,
                    record_hash=f"{index + 9:x}" * 64,
                ),
                DeviceHeartbeat(
                    id=new_uuid(),
                    device_id=device.id,
                    boot_id=new_uuid(),
                    received_at=now,
                    pzem_ok=True,
                    sd_ok=True,
                    time_trusted=True,
                    data_generation=0,
                    newest_sequence=sequence,
                    backlog_estimate=1,
                    payload={
                        "server_ack_sequence": sequence - 1,
                        "newest_stored_sequence": sequence,
                        "newest_syncable_sequence": sequence,
                        "sd": {
                            "details": {
                                "sequence_floor": sequence,
                                "next_sequence": sequence + 1,
                                "local_record_count": local_counts[device.id],
                                "card_generation": str(100 + index),
                            }
                        },
                    },
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
    assert plan.plan_snapshot["counts"]["raw_readings"] == 3
    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="multi-sensor-reset-operation-1",
        reason="Prove coordinated multi-sensor reset and reconnect gating",
        backup_mode="verified_backup",
        confirmation_phrase=VERIFIED_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=False,
        offline_after_seconds=30,
        now=now,
    )
    await session.commit()

    connected = devices[:2]
    disconnected = devices[2]
    participants = {
        item.device_id: item
        for item in await session.scalars(
            select(DataResetParticipant).where(DataResetParticipant.operation_id == operation.id)
        )
    }
    assert [participants[item.id].planned_classification for item in connected] == [
        "connected",
        "connected",
    ]
    assert participants[disconnected.id].planned_classification == "disconnected"

    test_settings.rate_sync_artifact_path = test_settings.report_path.parent / "rate-artifacts"
    for path in (
        test_settings.report_path,
        test_settings.log_path,
        test_settings.utility_bill_artifact_path,
        test_settings.rate_sync_artifact_path,
        test_settings.backup_path,
    ):
        path.mkdir(parents=True, exist_ok=True)

    boot_ids = {device.id: new_uuid() for device in devices}
    card_generations = {
        device.id: str(100 + index) for index, device in enumerate(devices, start=1)
    }
    configuration_digests = {
        device.id: hashlib.sha256(f"config:{device.id}".encode()).hexdigest() for device in devices
    }
    calls: list[tuple[str, str]] = []
    reconnect_available = False

    async def sensor_reset_response(*_args: object, **kwargs: Any) -> dict[str, object]:
        device = kwargs["device"]
        action = str(kwargs["action"])
        assert isinstance(device, Device)
        calls.append((device.id, action))
        if device.id == disconnected.id and not reconnect_available:
            raise SensorResetCommunicationError(
                "sensor_reset_unreachable",
                "Disconnected sensor is not reachable yet",
                retryable=True,
            )
        participant = await session.get(DataResetParticipant, (operation.id, device.id))
        assert participant is not None
        boundary = participant.reset_boundary
        local_records = local_counts[device.id]
        configuration_digest = configuration_digests[device.id]
        common = {
            "protocol": DATA_RESET_PROTOCOL,
            "operation_id": operation.id,
            "device_id": device.id,
            "target_generation": operation.reset_generation,
            "plan_revision": operation.plan_revision,
            "plan_digest": plan.plan_fingerprint,
            "firmware_version": device.firmware_version,
            "firmware_build_hash": device.firmware_build_hash,
            "boot_id": boot_ids[device.id],
            "card_generation": card_generations[device.id],
        }
        if action == "prepare":
            receipt = {
                **common,
                "state": "prepared",
                "checkpoint": "prepared",
                "reset_boundary": boundary,
                "sequence_floor": boundary,
                "next_sequence": boundary + 1,
                "server_ack_sequence": boundary,
                "server_maximum_seen": boundary,
                "newest_stored_sequence": boundary,
                "newest_syncable_sequence": boundary,
                "local_records_before": local_records,
                "local_records_after": local_records,
                "backlog_before": 1,
                "backlog_after": 1,
                "prepare_drain_records_added": 0,
                "prepare_drain_first_sequence": None,
                "prepare_drain_last_sequence": None,
                "prepare_drain_syncable_records_added": 0,
                "measurement_pause_started_utc_ms": 1_800_000_000_000 + boundary,
                "prepared_pzem_energy_wh": 100_000 + boundary,
                "software_energy_baseline_before_wh": 10_000 + boundary,
                "pzem_baseline_captured": True,
                "configuration_preserved": True,
                "configuration_preservation_digest_before": configuration_digest,
                "sd_status": "verified",
            }
            return {
                "state": "prepared",
                "_prepared_receipt_parsed": receipt,
                "prepared_receipt_digest": hashlib.sha256(
                    f"prepare:{device.id}".encode()
                ).hexdigest(),
                "configuration_preservation_digest_before": configuration_digest,
            }
        assert action == "commit"
        receipt = {
            **common,
            "state": "completed",
            "checkpoint": "completed",
            "configuration_preserved": True,
            "pzem_baseline_captured": True,
            "local_records_before": local_records,
            "local_records_after": 0,
            "backlog_before": 1,
            "backlog_after": 0,
            "records_deleted": local_records,
            "prepared_receipt_digest": hashlib.sha256(f"prepare:{device.id}".encode()).hexdigest(),
            "prepared_pzem_energy_wh": 100_000 + boundary,
            "commit_pzem_energy_wh": 100_001 + boundary,
            "verified_pzem_energy_wh": 100_002 + boundary,
            "measurement_pause_started_utc_ms": 1_800_000_000_000 + boundary,
            "measurement_pause_ended_utc_ms": 1_800_000_000_001 + boundary,
            "measurement_pause_evidenced": True,
            "configuration_preservation_digest_before": configuration_digest,
            "configuration_preservation_digest_after": configuration_digest,
            "queues_cleared": True,
            "exports_cleared": True,
            "indexes_rebuilt": True,
            "reset_boundary": boundary,
            "sequence_floor": boundary,
            "next_sequence": boundary + 1,
            "server_ack_sequence": boundary,
            "server_maximum_seen": boundary,
        }
        return {
            "state": "completed",
            "_commit_receipt_parsed": receipt,
            "commit_receipt_digest": hashlib.sha256(f"commit:{device.id}".encode()).hexdigest(),
            "configuration_preservation_digest_before": configuration_digest,
            "configuration_preservation_digest_after": configuration_digest,
        }

    monkeypatch.setattr("worker.app.data_reset.request_sensor_reset", sensor_reset_response)

    observed_states = [
        str((await process_data_reset_operations(session, test_settings))["state"]),
        str((await process_data_reset_operations(session, test_settings))["state"]),
    ]
    assert observed_states == ["sensors_prepared", "backup_running"]
    await session.refresh(operation)
    assert operation.backup_run_id is not None
    backup = await session.get(BackupRun, operation.backup_run_id)
    assert backup is not None
    backup_directory = test_settings.backup_path / "multi-sensor-reset-backup"
    backup_directory.mkdir(parents=True, exist_ok=True)
    database_dump = backup_directory / "database.dump"
    database_dump.write_bytes(b"multi-sensor verified logical backup")
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
    backup.status = "verified"
    backup.verified_at = now
    backup.path = backup_directory.name
    backup.manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    backup.size_bytes = manifest.stat().st_size
    backup.verification_details = {
        "migration_revision": "20260806_0031",
        "table_count": 100,
        "required_table_count": 5,
        "status_layout_revisions": 1,
        "postgres_major": 17,
    }
    await session.commit()
    for _ in range(4):
        observed_states.append(
            str((await process_data_reset_operations(session, test_settings))["state"])
        )
    expected_states = [
        "sensors_prepared",
        "backup_running",
        "backup_verified",
        "database_reset_committed",
        "sensor_commit_running",
        "verification_running",
    ]
    assert observed_states == expected_states
    await session.refresh(operation)
    assert operation.backup_checksum == backup.manifest_hash
    assert operation.backup_verified_at is not None
    assert operation.backup_verified_at.replace(tzinfo=UTC) == now
    assert operation.final_evidence["deleted_counts"]["raw_readings"] == 3
    assert await session.scalar(select(func.count()).select_from(RawReading)) == 0

    for device in connected:
        participant = await session.get(DataResetParticipant, (operation.id, device.id))
        state = await session.get(DeviceDataState, device.id)
        assert participant is not None
        assert participant.state == "verified"
        assert participant.prepare_receipt_digest is not None
        assert participant.commit_receipt_digest is not None
        assert participant.prepare_receipt_safe["checkpoint"] == "prepared"
        assert participant.commit_receipt_safe["checkpoint"] == "completed"
        assert state is not None
        assert state.ingestion_gate == "open"
        assert state.reset_required_on_reconnect is False
        assert [action for item, action in calls if item == device.id] == [
            "prepare",
            "commit",
        ]

    disconnected_participant = await session.get(
        DataResetParticipant, (operation.id, disconnected.id)
    )
    disconnected_state = await session.get(DeviceDataState, disconnected.id)
    assert disconnected_participant is not None
    assert disconnected_participant.state == "pending_reconnect"
    assert disconnected_state is not None
    assert disconnected_state.ingestion_gate == "pending_reconnect"
    assert disconnected_state.reset_required_on_reconnect is True

    for device in connected:
        participant = participants[device.id]
        with pytest.raises(ProblemError) as obsolete:
            await ingest_readings(
                session,
                device_id=device.id,
                readings=[
                    _new_reading(
                        generation=0,
                        sequence=participant.reset_boundary + 1,
                        now=datetime.now(UTC),
                    )
                ],
                source="push",
                data_generation=0,
            )
        assert obsolete.value.code == "data_generation_obsolete"

    with pytest.raises(ProblemError) as reset_first:
        await ingest_readings(
            session,
            device_id=disconnected.id,
            readings=[
                _new_reading(
                    generation=operation.reset_generation,
                    sequence=disconnected_participant.reset_boundary + 1,
                    now=datetime.now(UTC),
                )
            ],
            source="push",
            data_generation=operation.reset_generation,
        )
    assert reset_first.value.code == "sensor_reset_required"

    for device in connected:
        participant = await session.get(DataResetParticipant, (operation.id, device.id))
        assert participant is not None
        sequence = participant.reset_boundary + 1
        accepted = await ingest_readings(
            session,
            device_id=device.id,
            readings=[
                _new_reading(
                    generation=operation.reset_generation,
                    sequence=sequence,
                    now=datetime.now(UTC),
                )
            ],
            source="push",
            data_generation=operation.reset_generation,
        )
        assert accepted.accepted == [sequence]
        await session.commit()

    pending_result = await process_data_reset_operations(session, test_settings)
    assert pending_result["state"] == "completed_with_resets_pending_on_reconnect"
    await session.refresh(operation)
    assert set(operation.final_evidence["new_reading_device_ids"]) == {
        device.id for device in connected
    }
    pending_revision = operation.revision
    pending_completed_at = operation.completed_at
    repeated_pending = await process_data_reset_operations(session, test_settings)
    assert repeated_pending["state"] == "completed_with_resets_pending_on_reconnect"
    await session.refresh(operation)
    assert operation.revision == pending_revision
    assert operation.completed_at == pending_completed_at

    disconnected.last_seen_at = datetime.now(UTC)
    await session.commit()
    reconnect_available = True
    reconnect_result = await process_data_reset_operations(session, test_settings)
    assert reconnect_result["state"] == "verification_running"
    await session.refresh(disconnected_participant)
    await session.refresh(disconnected_state)
    assert disconnected_participant.state == "verified"
    assert disconnected_participant.prepare_receipt_digest is not None
    assert disconnected_participant.commit_receipt_digest is not None
    assert disconnected_state.ingestion_gate == "open"
    assert disconnected_state.reset_required_on_reconnect is False
    disconnected_actions = [action for device_id, action in calls if device_id == disconnected.id]
    assert disconnected_actions[-2:] == ["prepare", "commit"]

    disconnected_sequence = disconnected_participant.reset_boundary + 1
    with pytest.raises(ProblemError) as disconnected_obsolete:
        await ingest_readings(
            session,
            device_id=disconnected.id,
            readings=[
                _new_reading(
                    generation=0,
                    sequence=disconnected_sequence,
                    now=datetime.now(UTC),
                )
            ],
            source="push",
            data_generation=0,
        )
    assert disconnected_obsolete.value.code == "data_generation_obsolete"

    accepted = await ingest_readings(
        session,
        device_id=disconnected.id,
        readings=[
            _new_reading(
                generation=operation.reset_generation,
                sequence=disconnected_sequence,
                now=datetime.now(UTC),
            )
        ],
        source="push",
        data_generation=operation.reset_generation,
    )
    assert accepted.accepted == [disconnected_sequence]
    await session.commit()

    completed = await process_data_reset_operations(session, test_settings)
    assert completed["state"] == "completed"
    await session.refresh(operation)
    assert operation.final_evidence["new_readings_received"] is True
    assert set(operation.final_evidence["new_reading_device_ids"]) == {
        device.id for device in devices
    }
    assert operation.final_evidence["new_cost_status"] == ("not_requested_or_not_applicable")
    assert await session.scalar(select(func.count()).select_from(RawReading)) == 3
    generations = set(
        await session.scalars(
            select(RawReading.data_generation).where(RawReading.site_id == site.id)
        )
    )
    assert generations == {operation.reset_generation}
