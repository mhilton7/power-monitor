from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.access import BUILTIN_ROLE_PERMISSIONS
from app.api.routes.device_protocol import _status_from_heartbeat
from app.schemas import Heartbeat
from app.security.protocol import PROTOCOL, sign_headers


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


def test_builtin_storage_permissions_are_read_only_for_viewers() -> None:
    assert "storage.view" in BUILTIN_ROLE_PERMISSIONS["viewer"]
    assert "storage.manage" not in BUILTIN_ROLE_PERMISSIONS["viewer"]
    assert {"storage.view", "storage.manage"}.issubset(BUILTIN_ROLE_PERMISSIONS["operator"])
    assert "storage.view" in BUILTIN_ROLE_PERMISSIONS["rate-manager"]
    assert "storage.manage" not in BUILTIN_ROLE_PERMISSIONS["rate-manager"]
    assert {"storage.view", "storage.manage"}.issubset(BUILTIN_ROLE_PERMISSIONS["admin"])


def test_storage_integrity_heartbeat_parser_is_backward_and_forward_compatible() -> None:
    payload = {
        "protocol_version": PROTOCOL,
        "device_id": "device-storage-parser",
        "boot_id": "boot-storage-parser",
        "firmware_version": "1.0.15",
        "firmware_build_hash": "storage-parser-build",
        "uptime_seconds": 60,
        "reboot_reason": "power_on",
        "connection_mode": "push",
        "pzem": {"ok": True, "status": "ok"},
        "sd": {
            "ok": True,
            "status": "ok",
            "details": {
                "history_integrity_verified": False,
                "future_storage_counter": 7,
            },
        },
        "oldest_stored_sequence": 1,
        "newest_stored_sequence": 1,
        "server_ack_sequence": 0,
        "backlog_estimate": 1,
        "configuration_version": 1,
        "time": {"trusted": True, "source": "sntp"},
    }
    legacy = Heartbeat.model_validate(payload)
    assert legacy.sd.details.effective_reading_index_integrity_verified is False
    assert legacy.model_dump(mode="json")["sd"]["details"]["future_storage_counter"] == 7

    current_payload = {
        **payload,
        "sd": {
            # The card remains mounted/writable; only the independent event
            # ledger is degraded in current firmware.
            "ok": True,
            "status": "event_log_integrity_degraded",
            "details": {
                "history_integrity_verified": True,
                "reading_index_integrity_verified": True,
                "event_log_integrity_verified": False,
                "event_log_integrity_status": "event_record_corruption_detected",
            },
        },
    }
    current = Heartbeat.model_validate(current_payload)
    assert current.sd.details.effective_reading_index_integrity_verified is True
    assert current.sd.details.event_log_integrity_status == ("event_record_corruption_detected")
    assert _status_from_heartbeat(current) == "online_storage_degraded"

    for expected_status in ("not_scanned", "unavailable"):
        additive_payload = {
            **current_payload,
            "sd": {
                **current_payload["sd"],
                "details": {
                    **current_payload["sd"]["details"],
                    "event_log_integrity_status": expected_status,
                },
            },
        }
        parsed = Heartbeat.model_validate(additive_payload)
        assert parsed.sd.details.event_log_integrity_status == expected_status

    reading_index_payload = {
        **payload,
        "sd": {
            "ok": True,
            "status": "reading_index_integrity_degraded",
            "details": {
                "history_integrity_verified": True,
                "reading_index_integrity_verified": False,
                "event_log_integrity_verified": True,
                "event_log_integrity_status": "verified",
            },
        },
    }
    reading_index = Heartbeat.model_validate(reading_index_payload)
    # The explicit new reading-index field takes precedence over its legacy
    # alias, including a meaningful False value.
    assert reading_index.sd.details.effective_reading_index_integrity_verified is False
    assert _status_from_heartbeat(reading_index) == "online_storage_degraded"

    invalid = {
        **current_payload,
        "sd": {
            **current_payload["sd"],
            "details": {
                **current_payload["sd"]["details"],
                "event_log_integrity_status": "unknown_corruption_state",
            },
        },
    }
    with pytest.raises(ValueError, match="event_log_integrity_status"):
        Heartbeat.model_validate(invalid)


async def enroll(client: httpx.AsyncClient) -> tuple[str, bytes]:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "storage-admin@example.com",
            "display_name": "Storage Admin",
            "password": "Long-Production-Password-42!",
        },
    )
    assert response.status_code == 201, response.text
    site_id = (await client.get("/api/v1/sites")).json()[0]["id"]
    token = await client.post(
        "/api/v1/enrollment-tokens",
        headers=csrf(client),
        json={"site_id": site_id, "name": "Outdoor-AC"},
    )
    claim = await client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token.json()["token"],
            "protocol_version": PROTOCOL,
            "hardware_id": "esp32s3-storage-0001",
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


async def signed_post(
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, Any],
    device_id: str,
    secret: bytes,
) -> httpx.Response:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return await client.post(
        path,
        content=body,
        headers={
            **sign_headers(
                secret=secret,
                device_id=device_id,
                direction="device-to-server",
                method="POST",
                target=path,
                body=body,
            ),
            "Content-Type": "application/json",
        },
    )


@pytest.mark.asyncio
async def test_storage_reconciliation_does_not_mark_heartbeat_offline(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    device_id, secret = await enroll(client)
    heartbeat = {
        "protocol_version": PROTOCOL,
        "schema_version": "heartbeat/1.0.0",
        "device_id": device_id,
        "boot_id": "123e4567-e89b-12d3-a456-426614174101",
        "firmware_version": "1.0.9",
        "firmware_build_hash": "sequence-reconciliation-test",
        "uptime_seconds": 30,
        "reboot_reason": "power_on",
        "connection_mode": "push",
        "latest": None,
        "pzem": {"ok": True, "status": "healthy"},
        "sd": {
            "ok": False,
            "status": "sequence_reconciling",
            "details": {
                "sequence_reconciliation_in_progress": True,
                "sequence_floor": 0,
                "next_sequence": 1,
            },
        },
        "oldest_stored_sequence": 0,
        "newest_stored_sequence": 0,
        "server_ack_sequence": 785,
        "backlog_estimate": 0,
        "configuration_version": 1,
        "time": {"trusted": True, "source": "sntp"},
    }
    response = await signed_post(client, "/api/v1/device-heartbeats", heartbeat, device_id, secret)
    assert response.status_code == 200, response.text
    devices = (await client.get("/api/v1/devices")).json()
    assert devices[0]["status"] == "online_storage_reconciling"


@pytest.mark.asyncio
async def test_storage_status_policy_cleanup_and_safe_removal(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    device_id, secret = await enroll(client)
    heartbeat = {
        "protocol_version": PROTOCOL,
        "schema_version": "heartbeat/1.0.0",
        "device_id": device_id,
        "boot_id": "123e4567-e89b-12d3-a456-426614174100",
        "firmware_version": "1.1.0",
        "firmware_build_hash": "storage-test",
        "uptime_seconds": 120,
        "reboot_reason": "power_on",
        "current_ip": "192.168.1.51",
        "hostname": "outdoor-ac.local",
        "rssi_dbm": -52,
        "connection_mode": "push",
        "latest": None,
        "pzem": {"ok": True, "status": "ok"},
        "sd": {
            "ok": True,
            "status": "warning",
            "details": {
                "card_type": "SDHC",
                "capacity_bytes": 32 * 1024**3,
                "used_bytes": 29 * 1024**3,
                "free_bytes": 3 * 1024**3,
                "free_percent": 9,
                "pressure_state": "warning",
                "pressure_reason": "free_percent_threshold",
                "oldest_record_sequence": 10,
                "newest_record_sequence": 150,
                "server_ack_sequence": 120,
                "event_ack_sequence": 42,
                "unacknowledged_record_count": 30,
                "reclaimable_bytes": 512 * 1024**2,
                "protected_unacknowledged_bytes": 128 * 1024**2,
                "protected_untrusted_bytes": 64 * 1024**2,
                "segment_count": 12,
                "eligible_segment_count": 4,
                "protected_segment_count": 8,
                "open_segment_count": 1,
                "closed_segment_count": 11,
                "untrusted_segment_count": 2,
                "event_segment_count": 3,
                "export_count": 2,
                "repair_artifact_count": 1,
                "temporary_artifact_count": 4,
                "cleanup_in_progress": False,
                "cleanup_recovery_required": False,
                "last_cleanup_reclaimed_bytes": 256 * 1024**2,
                "growth_bytes_per_day": 2 * 1024**2,
                "estimated_days_remaining": 45,
                "retention_mode": "strict_age",
                "retention_days": 365,
                "minimum_local_history_days": 30,
                "storage_notice_percent": 20,
                "storage_warning_percent": 10,
                "storage_critical_percent": 5,
                "storage_emergency_percent": 2,
                "storage_emergency_reserve_bytes": 512 * 1024**2,
                "storage_cleanup_target_percent": 10,
                "storage_cleanup_target_bytes": 1024**3,
                "event_retention_days": 730,
                "reading_index_integrity_verified": True,
                "event_log_integrity_verified": False,
                "event_log_integrity_status": "event_record_corruption_detected",
            },
        },
        "oldest_stored_sequence": 10,
        "newest_stored_sequence": 150,
        "server_ack_sequence": 120,
        "backlog_estimate": 30,
        "configuration_version": 1,
        "time": {"trusted": True, "source": "sntp"},
        "resources": {"heap": 100000},
        "queue": {"pending": 1},
    }
    response = await signed_post(client, "/api/v1/device-heartbeats", heartbeat, device_id, secret)
    assert response.status_code == 200, response.text

    status = await client.get(f"/api/v1/devices/{device_id}/storage")
    assert status.status_code == 200, status.text
    details = status.json()["details"]
    assert details["oldest_stored_sequence"] == 10
    assert details["newest_stored_sequence"] == 150
    assert details["server_event_ack_sequence"] == 42
    assert details["unsynchronized_count"] == 30
    assert details["eligible_reclaimable_bytes"] == 512 * 1024**2
    assert details["protected_bytes"] == 192 * 1024**2
    assert details["estimated_bytes_per_day"] == 2 * 1024**2
    assert details["event_segment_count"] == 3
    assert details["temporary_artifact_count"] == 4
    assert details["reading_index_integrity_verified"] is True
    assert details["event_log_integrity_verified"] is False
    assert details["event_log_integrity_status"] == "event_record_corruption_detected"
    assert status.json()["effective_policy"]["retention_mode"] == "strict_age"
    assert status.json()["desired_policy"]["retention_days"] == 365
    assert status.json()["policy_pending"] is False

    policy = {
        "retention_mode": "continuous_protected",
        "retention_days": 730,
        "minimum_local_history_days": 30,
        "storage_notice_percent": 20,
        "storage_warning_percent": 10,
        "storage_critical_percent": 5,
        "storage_emergency_percent": 2,
        "storage_emergency_reserve_bytes": 512 * 1024**2,
        "storage_cleanup_target_percent": 10,
        "storage_cleanup_target_bytes": 1024**3,
        "event_retention_days": 730,
        "reason": "Administrator reviewed protected storage retention",
    }
    saved = await client.put(
        f"/api/v1/devices/{device_id}/storage/policy",
        headers=csrf(client),
        json=policy,
    )
    assert saved.status_code == 202, saved.text
    invalid = await client.put(
        f"/api/v1/devices/{device_id}/storage/policy",
        headers=csrf(client),
        json={**policy, "storage_notice_percent": 5},
    )
    assert invalid.status_code == 422
    cleanup = await client.post(
        f"/api/v1/devices/{device_id}/storage/cleanup",
        headers=csrf(client),
        json={"reason": "Administrator requested acknowledgement-aware safe cleanup"},
    )
    assert cleanup.status_code == 202, cleanup.text
    mismatch = await client.post(
        f"/api/v1/devices/{device_id}/storage/prepare-removal",
        headers=csrf(client),
        json={"reason": "Administrator is replacing the storage card", "confirmation": "wrong"},
    )
    assert mismatch.status_code == 409
    prepare = await client.post(
        f"/api/v1/devices/{device_id}/storage/prepare-removal",
        headers=csrf(client),
        json={
            "reason": "Administrator is replacing the storage card",
            "confirmation": "Outdoor-AC",
        },
    )
    assert prepare.status_code == 202, prepare.text


@pytest.mark.asyncio
async def test_device_event_acknowledgement_advances_only_contiguously(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    device_id, secret = await enroll(client)

    async def send(sequences: list[int]) -> httpx.Response:
        return await signed_post(
            client,
            "/api/v1/device-events/batch",
            {
                "protocol_version": PROTOCOL,
                "device_id": device_id,
                "first_stored_event_sequence": min(sequences),
                "events": [
                    {
                        "event_id": f"event-{sequence}",
                        "occurred_at": datetime.now(UTC).isoformat(),
                        "category": "sd",
                        "severity": "warning",
                        "evidence": {"event_sequence": sequence, "code": "storage_warning"},
                    }
                    for sequence in sequences
                ],
            },
            device_id,
            secret,
        )

    first = await send([1, 2])
    assert first.status_code == 200, first.text
    assert first.json()["highest_contiguous_event_sequence"] == 2
    gap = await send([4])
    assert gap.json()["highest_contiguous_event_sequence"] == 2
    filled = await send([3])
    assert filled.json()["highest_contiguous_event_sequence"] == 4
    duplicate = await send([1, 2])
    assert duplicate.json()["highest_contiguous_event_sequence"] == 4


@pytest.mark.asyncio
async def test_device_event_ack_does_not_infer_initial_boundary(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    device_id, secret = await enroll(client)
    response = await signed_post(
        client,
        "/api/v1/device-events/batch",
        {
            "protocol_version": PROTOCOL,
            "device_id": device_id,
            "events": [
                {
                    "event_id": "event-9",
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "category": "sd",
                    "severity": "warning",
                    "evidence": {"event_sequence": 9, "code": "storage_warning"},
                }
            ],
        },
        device_id,
        secret,
    )
    assert response.status_code == 200, response.text
    assert response.json()["highest_contiguous_event_sequence"] == 0
