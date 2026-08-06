from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from worker.app import main as worker_main

from app.api.routes.device_protocol import (
    _confirm_firmware_reading,
    _reconcile_firmware_heartbeat,
)
from app.db.models import (
    AuditEvent,
    Device,
    DeviceHeartbeat,
    FirmwareDeployment,
    FirmwareRelease,
    WorkerState,
)
from app.firmware_lifecycle import (
    VerificationContext,
    build_firmware_verification,
    classify_stale_firmware_deployment,
    is_superseded_target_report,
    load_verification_contexts,
    reconcile_stale_firmware_deployments,
    transition_firmware_deployment,
)
from app.schemas import Heartbeat, Reading

NOW = datetime(2026, 8, 4, 2, 0, tzinfo=UTC)


def _device() -> Device:
    return Device(
        id="device-ota-lifecycle",
        site_id="site-ota-lifecycle",
        hardware_id="hw-ota-lifecycle",
        name="OTA lifecycle sensor",
        firmware_version="1.0.14",
        firmware_build_hash="source-build",
        last_seen_at=NOW - timedelta(minutes=20),
    )


def _deployment(
    state: str,
    *,
    changed_at: datetime | None = None,
    expires_at: datetime | None = None,
    source_identity: bool = True,
) -> FirmwareDeployment:
    return FirmwareDeployment(
        id=f"deployment-{state}",
        firmware_release_id="release-ota-lifecycle",
        device_id="device-ota-lifecycle",
        status=state,
        state=state,
        revision=1,
        attempt=1,
        progress=0,
        bytes_received=0,
        scheduled_at=NOW - timedelta(hours=2),
        expires_at=expires_at,
        last_report_payload={},
        source_version="1.0.14" if source_identity else None,
        source_build_hash="source-build" if source_identity else None,
        interruption_evidence={},
        verification_heartbeats=0,
        state_changed_at=changed_at or NOW - timedelta(hours=2),
        created_by="user-ota-lifecycle",
        created_at=NOW - timedelta(hours=2),
    )


@pytest.mark.parametrize(
    ("state", "age", "expires", "expected_code"),
    [
        ("waiting_canary", 60, -1, "ota_deployment_expired"),
        ("scheduled", 60, -1, "ota_manifest_expired"),
        ("offered", 301, None, "ota_sensor_did_not_return"),
        ("download_started", 601, None, "ota_update_timed_out"),
        ("partition_written", 301, None, "ota_sensor_did_not_return"),
        ("awaiting_heartbeat", 901, None, "ota_post_boot_timeout"),
    ],
)
def test_stale_classifier_covers_every_ota_timeout_family(
    test_settings: Any,
    state: str,
    age: int,
    expires: int | None,
    expected_code: str,
) -> None:
    deployment = _deployment(
        state,
        changed_at=NOW - timedelta(seconds=age),
        expires_at=NOW + timedelta(seconds=expires) if expires is not None else None,
    )
    decision = classify_stale_firmware_deployment(deployment, _device(), test_settings, NOW)
    assert decision is not None
    assert decision.code == expected_code
    assert decision.state == "failed"


def test_legacy_and_rollback_reconciliation_are_evidence_aware(test_settings: Any) -> None:
    legacy = _deployment(
        "downloading",
        changed_at=NOW
        - timedelta(seconds=test_settings.firmware_legacy_evidence_timeout_seconds + 1),
        source_identity=False,
    )
    legacy_decision = classify_stale_firmware_deployment(legacy, _device(), test_settings, NOW)
    assert legacy_decision is not None
    assert legacy_decision.code == "ota_legacy_evidence_incomplete"

    rollback = _deployment("rollback_detected", changed_at=NOW - timedelta(seconds=1))
    rollback.rollback_version = "1.0.14"
    rollback.rollback_build_hash = "source-build"
    rollback_decision = classify_stale_firmware_deployment(rollback, _device(), test_settings, NOW)
    assert rollback_decision is not None
    assert rollback_decision.state == "rolled_back"
    assert rollback_decision.code == "ota_rollback_detected"


@pytest.mark.parametrize(
    ("state", "expected_code"),
    [
        ("waiting_canary", "ota_deployment_expired"),
        ("scheduled", "ota_manifest_expired"),
    ],
)
def test_legacy_queue_without_expiry_cannot_remain_active_forever(
    test_settings: Any, state: str, expected_code: str
) -> None:
    deployment = _deployment(
        state,
        changed_at=NOW
        - timedelta(seconds=test_settings.firmware_legacy_evidence_timeout_seconds + 1),
        expires_at=None,
    )
    decision = classify_stale_firmware_deployment(deployment, _device(), test_settings, NOW)
    assert decision is not None
    assert decision.state == "failed"
    assert decision.code == expected_code


def test_waiting_for_schedule_uses_manifest_expiry_not_download_timeout(
    test_settings: Any,
) -> None:
    deployment = _deployment(
        "waiting_for_schedule",
        changed_at=NOW - timedelta(seconds=test_settings.firmware_download_stale_seconds + 60),
        expires_at=NOW + timedelta(hours=2),
    )
    assert classify_stale_firmware_deployment(deployment, _device(), test_settings, NOW) is None

    deployment.expires_at = NOW - timedelta(seconds=1)
    decision = classify_stale_firmware_deployment(deployment, _device(), test_settings, NOW)
    assert decision is not None
    assert decision.code == "ota_manifest_expired"
    assert decision.evidence["reason"] == "update_window_not_reached_before_expiry"


def test_transition_helper_is_monotonic_terminal_and_retry_safe() -> None:
    deployment = _deployment("scheduled")
    assert transition_firmware_deployment(deployment, "offered", NOW)
    assert deployment.state_changed_at == NOW
    assert deployment.terminal_at is None

    scheduled = _deployment("manifest_authenticated")
    assert transition_firmware_deployment(scheduled, "waiting_for_schedule", NOW)
    assert transition_firmware_deployment(scheduled, "download_started", NOW + timedelta(seconds=1))
    assert deployment.revision == 2
    with pytest.raises(ValueError, match="invalid firmware transition"):
        transition_firmware_deployment(deployment, "partition_written", NOW)

    assert transition_firmware_deployment(deployment, "failed", NOW, evidence_reconciliation=True)
    assert deployment.terminal_at == NOW
    assert transition_firmware_deployment(
        deployment, "scheduled", NOW + timedelta(seconds=1), retry=True
    )
    assert deployment.terminal_at is None


def test_delayed_same_attempt_target_report_is_safely_superseded() -> None:
    deployment = _deployment("awaiting_heartbeat")
    release = FirmwareRelease(
        id="release-ota-lifecycle",
        version="1.0.15",
        build_hash="target-build",
    )
    assert is_superseded_target_report(
        deployment,
        release,
        state="validated",
        current_firmware_version="1.0.15",
        current_build_hash="target-build",
    )
    assert not is_superseded_target_report(
        deployment,
        release,
        state="validated",
        current_firmware_version="1.0.14",
        current_build_hash="source-build",
    )


@pytest.mark.asyncio
async def test_reconciler_terminalizes_once_without_list_endpoint(
    session: AsyncSession, test_settings: Any
) -> None:
    test_settings.firmware_download_stale_seconds = 30
    device = _device()
    deployment = _deployment("download_started", changed_at=NOW - timedelta(minutes=5))
    deployment.last_report_at = NOW - timedelta(minutes=5)
    session.add_all([device, deployment])
    await session.commit()

    first = await reconcile_stale_firmware_deployments(session, test_settings, NOW)
    await session.commit()
    second = await reconcile_stale_firmware_deployments(session, test_settings, NOW)
    await session.commit()

    assert first["terminalized"] == 1
    assert first["ota_update_timed_out"] == 1
    assert second["terminalized"] == 0
    stored = await session.get(FirmwareDeployment, deployment.id)
    assert stored is not None
    assert stored.state == "failed"
    assert stored.terminal_at == NOW
    audit_count = await session.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "firmware.deployment_reconciled",
            AuditEvent.object_id == deployment.id,
        )
    )
    assert audit_count == 1


@pytest.mark.asyncio
async def test_reconciler_batch_limit_cannot_starve_an_overdue_state(
    session: AsyncSession, test_settings: Any
) -> None:
    test_settings.firmware_reconcile_batch_size = 1
    test_settings.firmware_download_stale_seconds = 30
    queued_device = _device()
    queued_device.id = "device-queued-not-expired"
    queued_device.hardware_id = "hw-queued-not-expired"
    queued = _deployment(
        "waiting_canary",
        changed_at=NOW - timedelta(minutes=20),
        expires_at=NOW + timedelta(hours=1),
    )
    queued.id = "deployment-queued-not-expired"
    queued.device_id = queued_device.id
    stalled_device = _device()
    stalled_device.id = "device-download-stalled"
    stalled_device.hardware_id = "hw-download-stalled"
    stalled = _deployment("downloading", changed_at=NOW - timedelta(minutes=10))
    stalled.id = "deployment-download-stalled"
    stalled.device_id = stalled_device.id
    stalled.last_report_at = NOW - timedelta(minutes=10)
    session.add_all([queued_device, queued, stalled_device, stalled])
    await session.commit()

    outcome = await reconcile_stale_firmware_deployments(session, test_settings, NOW)
    await session.commit()

    assert outcome["examined"] == 2
    assert outcome["terminalized"] == 1
    assert outcome["ota_update_timed_out"] == 1
    assert queued.state == "waiting_canary"
    assert stalled.state == "failed"


@pytest.mark.asyncio
async def test_worker_loop_runs_ota_reconciliation_without_api_polling(
    monkeypatch: pytest.MonkeyPatch, test_settings: Any
) -> None:
    calls: list[str] = []

    async def ota_reconciliation(*_args: Any, **_kwargs: Any) -> dict[str, int]:
        calls.append("firmware")
        return {"examined": 1, "terminalized": 1, "ota_post_boot_timeout": 1}

    async def empty_mapping(*_args: Any, **_kwargs: Any) -> dict[str, int]:
        return {}

    async def zero(*_args: Any, **_kwargs: Any) -> int:
        return 0

    async def empty_list(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(worker_main, "reconcile_stale_firmware_deployments", ota_reconciliation)
    for name in (
        "reconcile_missing_normalized_intervals",
        "process_data_reset_operations",
        "evaluate_alerts",
        "process_rate_sync_jobs",
        "check_stale_sources",
    ):
        monkeypatch.setattr(worker_main, name, empty_mapping)
    for name in (
        "process_notification_jobs",
        "process_export_jobs",
        "process_report_jobs",
        "process_tier_recalculations",
        "process_cost_jobs",
        "recompute_recent_rollups",
        "activate_due_versions",
        "due_retention_deletions",
    ):
        monkeypatch.setattr(worker_main, name, zero)
    monkeypatch.setattr(worker_main, "poll_due_devices", empty_list)

    state = WorkerState(
        worker_name="main",
        instance_id="test",
        last_loop_at=NOW,
        last_success_at=NOW,
        status="healthy",
        details={},
    )
    session = AsyncMock()
    session.get.return_value = state
    details = await worker_main._process_work(session, object(), test_settings)

    assert calls == ["firmware"]
    assert details["firmware_reconciliation"] == {
        "examined": 1,
        "terminalized": 1,
        "ota_post_boot_timeout": 1,
    }
    # Firmware outcomes, pre-retention work, retention barriers, and worker
    # state are committed independently so locks never span device polling.
    assert session.commit.await_count == 4


def _heartbeat(*, boot_id: str, pzem_ok: bool = True) -> Heartbeat:
    return Heartbeat(
        protocol_version="pm-protocol/1.0.0",
        device_id="device-ota-lifecycle",
        boot_id=boot_id,
        firmware_version="1.0.15",
        firmware_build_hash="target-build",
        uptime_seconds=10,
        reboot_reason="software_reset",
        connection_mode="push",
        pzem={"ok": pzem_ok, "status": "ok" if pzem_ok else "failed"},
        sd={"ok": True, "status": "ok"},
        oldest_stored_sequence=1,
        newest_stored_sequence=2,
        server_ack_sequence=0,
        backlog_estimate=2,
        configuration_version=1,
        time={"trusted": True, "source": "ntp"},
    )


@pytest.mark.asyncio
async def test_target_reboot_resets_continuous_boot_verification_evidence(
    test_settings: Any,
) -> None:
    deployment = _deployment("awaiting_heartbeat")
    deployment.validated_version = "1.0.15"
    deployment.validated_build_hash = "target-build"
    deployment.last_boot_id = "boot-target-1"
    deployment.verification_heartbeats = 5
    deployment.stabilization_started_at = NOW - timedelta(minutes=2)
    deployment.reading_confirmed_at = NOW - timedelta(minutes=1)
    release = FirmwareRelease(
        id="release-ota-lifecycle",
        version="1.0.15",
        build_hash="target-build",
    )
    session = MagicMock()
    session.scalar = AsyncMock(return_value=deployment)
    session.get = AsyncMock(return_value=release)

    await _reconcile_firmware_heartbeat(
        session,
        _heartbeat(boot_id="boot-target-2", pzem_ok=False),
        test_settings,
        NOW,
    )

    assert deployment.state == "awaiting_heartbeat"
    assert deployment.last_boot_id == "boot-target-2"
    assert deployment.verification_heartbeats == 0
    assert deployment.stabilization_started_at == NOW
    assert deployment.reading_confirmed_at is None
    assert deployment.revision == 2
    assert deployment.state_changed_at == NOW
    audit = next(
        item
        for call in session.add.call_args_list
        for item in call.args
        if isinstance(item, AuditEvent)
    )
    assert audit.action == "firmware.deployment_target_restarted"


@pytest.mark.asyncio
async def test_accepted_or_identical_duplicate_target_boot_reading_satisfies_ota_gate(
    test_settings: Any,
) -> None:
    deployment = _deployment("awaiting_heartbeat")
    deployment.validated_version = "1.0.15"
    deployment.last_boot_id = "boot-target"
    session = MagicMock()
    session.scalar = AsyncMock(return_value=deployment)

    def reading(sequence: int, boot_id: str, version: str) -> Reading:
        return Reading(
            sequence=sequence,
            boot_id=boot_id,
            interval_start=NOW - timedelta(minutes=1),
            interval_end=NOW,
            time_trusted=True,
            power_avg=Decimal("1.0"),
            energy_method="integrated_power",
            ct_rating_amps=Decimal("100"),
            firmware_version=version,
        )

    source_backlog = reading(1, "boot-source", "1.0.14")
    await _confirm_firmware_reading(
        session,
        deployment.device_id,
        [source_backlog],
        {source_backlog.sequence},
        test_settings,
        NOW,
    )
    assert deployment.reading_confirmed_at is None

    duplicate_target = reading(2, "boot-target", "1.0.15")
    await _confirm_firmware_reading(
        session,
        deployment.device_id,
        [duplicate_target],
        {duplicate_target.sequence},
        test_settings,
        NOW,
    )
    assert deployment.reading_confirmed_at == NOW


@pytest.mark.asyncio
async def test_verification_context_is_scoped_to_deployment_target_boot(
    session: AsyncSession,
) -> None:
    device = _device()
    first = _deployment("completed")
    first.last_boot_id = "boot-target-1"
    second = _deployment("failed")
    second.last_boot_id = "boot-target-2"
    heartbeats = [
        DeviceHeartbeat(
            id="heartbeat-target-1",
            device_id=device.id,
            boot_id="boot-target-1",
            received_at=NOW - timedelta(minutes=2),
            pzem_ok=True,
            sd_ok=True,
            time_trusted=True,
            newest_sequence=10,
            backlog_estimate=0,
            payload={},
        ),
        DeviceHeartbeat(
            id="heartbeat-target-2",
            device_id=device.id,
            boot_id="boot-target-2",
            received_at=NOW - timedelta(minutes=1),
            pzem_ok=False,
            sd_ok=True,
            time_trusted=True,
            newest_sequence=11,
            backlog_estimate=0,
            payload={},
        ),
    ]
    session.add_all([device, first, second, *heartbeats])
    await session.commit()

    contexts = await load_verification_contexts(session, [first, second])

    assert contexts[first.id].heartbeat is not None
    assert contexts[first.id].heartbeat.boot_id == "boot-target-1"
    assert contexts[first.id].heartbeat.pzem_ok is True
    assert contexts[second.id].heartbeat is not None
    assert contexts[second.id].heartbeat.boot_id == "boot-target-2"
    assert contexts[second.id].heartbeat.pzem_ok is False


def test_verification_payload_exposes_one_actionable_blocker(test_settings: Any) -> None:
    deployment = _deployment("awaiting_heartbeat", changed_at=NOW - timedelta(minutes=1))
    deployment.validated_version = "1.0.15"
    deployment.validated_build_hash = "target-build"
    deployment.validated_at = NOW - timedelta(minutes=1)
    deployment.last_boot_id = "boot-target"
    deployment.verification_heartbeats = test_settings.firmware_verification_heartbeat_count
    deployment.stabilization_started_at = NOW - timedelta(minutes=5)
    release = FirmwareRelease(
        id="release-ota-lifecycle",
        version="1.0.15",
        build_hash="target-build",
    )
    heartbeat = DeviceHeartbeat(
        id="heartbeat-ota-lifecycle",
        device_id=deployment.device_id,
        boot_id="boot-target",
        received_at=NOW,
        pzem_ok=True,
        sd_ok=True,
        time_trusted=True,
        newest_sequence=10,
        backlog_estimate=0,
        payload={
            "reboot_reason": "software_reset",
            "resources": {
                "ota_recovery": {
                    "previous_boot_stage": "partition_written",
                }
            },
        },
    )
    verification = build_firmware_verification(
        deployment,
        release,
        test_settings,
        VerificationContext(device=_device(), heartbeat=heartbeat),
        NOW,
    )
    assert verification["blocker"] == {
        "code": "ota_waiting_post_update_reading",
        "state": "awaiting_heartbeat",
        "title": "Post-update reading",
        "detail": "Waiting for the first durable reading.",
        "action": "wait",
    }
    assert verification["target_version_expected"] == "1.0.15"
    assert verification["target_version_observed"] == "1.0.15"
    assert verification["target_build_hash_expected"] == "target-build"
    assert verification["target_build_hash_observed"] == "target-build"
    assert verification["target_boot_id_observed"] == "boot-target"
    assert verification["previous_boot_stage"] == "partition_written"
    assert verification["previous_reset_reason"] == "software_reset"
    assert verification["rollback_state"] == "not_detected"
    assert verification["exact_failure_code"] is None
    assert verification["blocking_critical_alert_count"] == 0
    assert verification["verification_heartbeat_count"] == (
        test_settings.firmware_verification_heartbeat_count
    )
    assert verification["verification_heartbeat_required"] == (
        test_settings.firmware_verification_heartbeat_count
    )
    checks = {item["key"]: item["status"] for item in verification["checks"]}
    assert checks["target_identity"] == "passed"
    assert checks["target_boot"] == "passed"
    assert checks["pzem"] == "passed"
    assert checks["storage"] == "passed"
    assert checks["trusted_time"] == "passed"
    assert checks["post_update_reading"] == "pending"
