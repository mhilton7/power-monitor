from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.db.models import DataResetPlan, DeviceCapability, DeviceDataState, DeviceHeartbeat, Site
from app.security.protocol import PROTOCOL, sign_headers

PASSWORD = "Long-Production-Password-42!"
ALL_RESET_CATEGORIES = [
    "measurement_history",
    "cost_history",
    "pricing_history",
    "generated_outputs",
]


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


async def bootstrap_admin(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "reset-admin@example.com",
            "display_name": "Reset Admin",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    sites = await client.get("/api/v1/sites")
    assert sites.status_code == 200, sites.text
    return str(sites.json()[0]["id"])


@pytest.mark.asyncio
async def test_data_reset_plan_execute_and_action_idempotency(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    site_id = await bootstrap_admin(client)

    missing_csrf = await client.post(
        "/api/v1/system/data-reset/plan",
        json={"site_id": site_id, "categories": ALL_RESET_CATEGORIES},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["code"] == "csrf_failed"

    planned = await client.post(
        "/api/v1/system/data-reset/plan",
        headers=csrf(client),
        json={
            "site_id": site_id,
            "categories": ALL_RESET_CATEGORIES,
            "delete_imported_bill_documents": False,
            "disconnected_sensor_policy": "defer_until_reconnect",
        },
    )
    assert planned.status_code == 201, planned.text
    plan = planned.json()
    assert plan["site"]["id"] == site_id
    assert plan["confirmation_phrases"] == {
        "verified_backup": "RESET ALL READINGS AND PRICING HISTORY",
        "permanent_without_backup": (
            "PERMANENTLY RESET ALL READINGS AND PRICING HISTORY WITHOUT BACKUP"
        ),
    }
    assert "outputs" not in plan
    assert plan["participants"] == []

    stale_auth = await client.post(
        "/api/v1/system/data-reset/execute",
        headers=csrf(client),
        json={
            "plan_id": plan["plan_id"],
            "plan_revision": plan["revision"],
            "idempotency_key": "api-reset-execute-1",
            "reason": "Commissioning history cleanup",
            "backup_mode": "verified_backup",
            "confirmation_phrase": plan["confirmation_phrases"]["verified_backup"],
        },
    )
    assert stale_auth.status_code == 428
    assert stale_auth.json()["code"] == "reauthentication_required"

    reauthenticated = await client.post(
        "/api/v1/auth/reauthenticate",
        headers=csrf(client),
        json={"password": PASSWORD},
    )
    assert reauthenticated.status_code == 200, reauthenticated.text

    wrong_phrase = await client.post(
        "/api/v1/system/data-reset/execute",
        headers=csrf(client),
        json={
            "plan_id": plan["plan_id"],
            "plan_revision": plan["revision"],
            "idempotency_key": "api-reset-execute-wrong",
            "reason": "Commissioning history cleanup",
            "backup_mode": "verified_backup",
            "confirmation_phrase": "RESET SOMETHING ELSE",
        },
    )
    assert wrong_phrase.status_code == 422
    assert wrong_phrase.json()["code"] == "data_reset_confirmation_mismatch"

    executed = await client.post(
        "/api/v1/system/data-reset/execute",
        headers=csrf(client),
        json={
            "plan_id": plan["plan_id"],
            "plan_revision": plan["revision"],
            "idempotency_key": "api-reset-execute-1",
            "reason": "Commissioning history cleanup",
            "backup_mode": "verified_backup",
            "confirmation_phrase": plan["confirmation_phrases"]["verified_backup"],
        },
    )
    assert executed.status_code == 202, executed.text
    operation = executed.json()
    assert operation["state"] == "preparing_sensors"
    assert operation["backup"]["mode"] == "verified_backup"

    replay = await client.post(
        "/api/v1/system/data-reset/execute",
        headers=csrf(client),
        json={
            "plan_id": plan["plan_id"],
            "plan_revision": plan["revision"],
            "idempotency_key": "api-reset-execute-1",
            "reason": "Commissioning history cleanup",
            "backup_mode": "verified_backup",
            "confirmation_phrase": plan["confirmation_phrases"]["verified_backup"],
        },
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["operation_id"] == operation["operation_id"]

    concurrent = await client.post(
        "/api/v1/system/data-reset/execute",
        headers=csrf(client),
        json={
            "plan_id": plan["plan_id"],
            "plan_revision": plan["revision"],
            "idempotency_key": "api-reset-execute-concurrent",
            "reason": "Competing reset must be rejected",
            "backup_mode": "verified_backup",
            "confirmation_phrase": plan["confirmation_phrases"]["verified_backup"],
        },
    )
    assert concurrent.status_code == 409, concurrent.text
    assert concurrent.json()["code"] == "data_reset_active"
    assert concurrent.json()["operation_id"] == operation["operation_id"]

    cancelled = await client.post(
        f"/api/v1/system/data-reset/{operation['operation_id']}/cancel",
        headers=csrf(client),
        json={
            "idempotency_key": "api-reset-cancel-1",
            "reason": "Operator cancelled before commit",
        },
    )
    assert cancelled.status_code == 200, cancelled.text
    cancel_revision = cancelled.json()["revision"]

    cancel_replay = await client.post(
        f"/api/v1/system/data-reset/{operation['operation_id']}/cancel",
        headers=csrf(client),
        json={
            "idempotency_key": "api-reset-cancel-1",
            "reason": "Operator cancelled before commit",
        },
    )
    assert cancel_replay.status_code == 200, cancel_replay.text
    assert cancel_replay.json()["revision"] == cancel_revision

    conflict = await client.post(
        f"/api/v1/system/data-reset/{operation['operation_id']}/cancel",
        headers=csrf(client),
        json={
            "idempotency_key": "api-reset-cancel-1",
            "reason": "A different cancellation request",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"

    fetched = await client.get(f"/api/v1/system/data-reset/{operation['operation_id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["operation_id"] == operation["operation_id"]


@pytest.mark.asyncio
async def test_no_backup_requires_separate_acknowledgement(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    site_id = await bootstrap_admin(client)
    plan_response = await client.post(
        "/api/v1/system/data-reset/plan",
        headers=csrf(client),
        json={"site_id": site_id, "categories": ALL_RESET_CATEGORIES},
    )
    assert plan_response.status_code == 201, plan_response.text
    plan = plan_response.json()
    await client.post(
        "/api/v1/auth/reauthenticate",
        headers=csrf(client),
        json={"password": PASSWORD},
    )

    denied = await client.post(
        "/api/v1/system/data-reset/execute",
        headers=csrf(client),
        json={
            "plan_id": plan["plan_id"],
            "plan_revision": plan["revision"],
            "idempotency_key": "api-no-backup-1",
            "reason": "Permanent commissioning cleanup",
            "backup_mode": "permanent_without_backup",
            "confirmation_phrase": plan["confirmation_phrases"]["permanent_without_backup"],
            "permanent_without_backup_acknowledged": False,
        },
    )
    assert denied.status_code == 422
    assert denied.json()["code"] == "validation_error"

    accepted = await client.post(
        "/api/v1/system/data-reset/execute",
        headers=csrf(client),
        json={
            "plan_id": plan["plan_id"],
            "plan_revision": plan["revision"],
            "idempotency_key": "api-no-backup-1",
            "reason": "Permanent commissioning cleanup",
            "backup_mode": "permanent_without_backup",
            "confirmation_phrase": plan["confirmation_phrases"]["permanent_without_backup"],
            "permanent_without_backup_acknowledged": True,
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["recoverability"] == "irreversible_no_backup"


@pytest.mark.asyncio
async def test_expired_plan_is_denied_and_durably_invalidated(
    api_client: Any,
    session_factory_fixture: Any,
) -> None:
    client: httpx.AsyncClient = api_client
    site_id = await bootstrap_admin(client)
    planned = await client.post(
        "/api/v1/system/data-reset/plan",
        headers=csrf(client),
        json={"site_id": site_id, "categories": ALL_RESET_CATEGORIES},
    )
    assert planned.status_code == 201, planned.text
    plan_payload = planned.json()
    async with session_factory_fixture() as session:
        plan = await session.get(DataResetPlan, plan_payload["plan_id"])
        assert plan is not None
        expired_at = datetime.now(UTC) - timedelta(seconds=1)
        plan.created_at = expired_at - timedelta(minutes=1)
        plan.expires_at = expired_at
        await session.commit()
    reauthenticated = await client.post(
        "/api/v1/auth/reauthenticate",
        headers=csrf(client),
        json={"password": PASSWORD},
    )
    assert reauthenticated.status_code == 200, reauthenticated.text

    denied = await client.post(
        "/api/v1/system/data-reset/execute",
        headers=csrf(client),
        json={
            "plan_id": plan_payload["plan_id"],
            "plan_revision": plan_payload["revision"],
            "idempotency_key": "api-expired-reset-1",
            "reason": "Expired plans cannot authorize deletion",
            "backup_mode": "verified_backup",
            "confirmation_phrase": plan_payload["confirmation_phrases"]["verified_backup"],
        },
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "data_reset_plan_expired"
    async with session_factory_fixture() as session:
        plan = await session.get(DataResetPlan, plan_payload["plan_id"])
        assert plan is not None
        assert plan.invalidated_at is not None
        assert plan.invalidation_reason == "expired"


@pytest.mark.asyncio
async def test_read_only_viewer_cannot_plan_a_data_reset(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    site_id = await bootstrap_admin(client)
    created = await client.post(
        "/api/v1/users",
        headers=csrf(client),
        json={
            "email": "reset-viewer@example.com",
            "display_name": "Reset Read-only Viewer",
            "password": "Production-Reset-Viewer-Password-42!",
            "roles": ["viewer"],
        },
    )
    assert created.status_code == 201, created.text
    logged_out = await client.post("/api/v1/auth/logout", headers=csrf(client))
    assert logged_out.status_code == 204, logged_out.text
    logged_in = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "reset-viewer@example.com",
            "password": "Production-Reset-Viewer-Password-42!",
        },
    )
    assert logged_in.status_code == 200, logged_in.text

    denied = await client.post(
        "/api/v1/system/data-reset/plan",
        headers=csrf(client),
        json={"site_id": site_id, "categories": ALL_RESET_CATEGORIES},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "data_reset_administrator_required"


@pytest.mark.asyncio
async def test_material_site_change_invalidates_plan_before_execution(
    api_client: Any,
    session_factory_fixture: Any,
) -> None:
    client: httpx.AsyncClient = api_client
    site_id = await bootstrap_admin(client)
    planned = await client.post(
        "/api/v1/system/data-reset/plan",
        headers=csrf(client),
        json={"site_id": site_id, "categories": ALL_RESET_CATEGORIES},
    )
    assert planned.status_code == 201, planned.text
    plan = planned.json()
    async with session_factory_fixture() as session:
        site = await session.get(Site, site_id)
        assert site is not None
        site.revision += 1
        await session.commit()
    reauthenticated = await client.post(
        "/api/v1/auth/reauthenticate",
        headers=csrf(client),
        json={"password": PASSWORD},
    )
    assert reauthenticated.status_code == 200, reauthenticated.text

    denied = await client.post(
        "/api/v1/system/data-reset/execute",
        headers=csrf(client),
        json={
            "plan_id": plan["plan_id"],
            "plan_revision": plan["revision"],
            "idempotency_key": "api-stale-reset-1",
            "reason": "Changed sites require a new plan",
            "backup_mode": "verified_backup",
            "confirmation_phrase": plan["confirmation_phrases"]["verified_backup"],
        },
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "data_reset_plan_stale"


@pytest.mark.asyncio
async def test_gated_signed_heartbeat_refreshes_reset_capability_without_measurements(
    api_client: Any,
    session_factory_fixture: Any,
) -> None:
    client: httpx.AsyncClient = api_client
    site_id = await bootstrap_admin(client)
    token = await client.post(
        "/api/v1/enrollment-tokens",
        headers=csrf(client),
        json={"site_id": site_id, "name": "Upgradeable reset sensor"},
    )
    assert token.status_code == 201, token.text
    claim = await client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token.json()["token"],
            "protocol_version": PROTOCOL,
            "hardware_id": "reset-capability-refresh-sensor",
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
    device_id = str(claim.json()["device_id"])
    secret = str(claim.json()["enrollment_secret"]).encode()

    async with session_factory_fixture() as session:
        capability = await session.get(DeviceCapability, device_id)
        assert capability is not None
        assert capability.features.get("data_reset") is None
        data_state = await session.get(DeviceDataState, device_id)
        assert data_state is not None
        data_state.data_generation = 1
        data_state.reset_boundary = 17
        data_state.ingestion_gate = "pending_reconnect"
        data_state.reset_required_on_reconnect = True
        await session.commit()

    heartbeat = {
        "protocol_version": PROTOCOL,
        "schema_version": "heartbeat/1.0.0",
        "device_id": device_id,
        "boot_id": "123e4567-e89b-12d3-a456-426614174099",
        "firmware_version": "1.0.18",
        "firmware_build_hash": "reset-capable-build",
        "data_generation": 1,
        "uptime_seconds": 60,
        "reboot_reason": "ota_update",
        "current_ip": "192.168.1.80",
        "hostname": "reset-upgrade.local",
        "rssi_dbm": -48,
        "connection_mode": "push",
        "latest": {
            "measured_at": "2026-08-06T12:00:00Z",
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
        "newest_stored_sequence": 17,
        "server_ack_sequence": 17,
        "backlog_estimate": 1,
        "configuration_version": 1,
        "time": {"trusted": True, "source": "sntp"},
        "resources": {"heap": 100000},
        "queue": {"pending": 1},
        "data_reset": {
            "protocol": "data-reset/1.0.0",
            "state": "none",
            "checkpoint": "idle",
            "target_generation": 1,
            "reset_boundary": 17,
            "reset_required": True,
        },
    }
    body = json.dumps(heartbeat, separators=(",", ":")).encode()
    response = await client.post(
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
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "sensor_reset_required"

    async with session_factory_fixture() as session:
        capability = await session.get(DeviceCapability, device_id)
        assert capability is not None
        assert capability.features["data_reset"] == "data-reset/1.0.0"
        stored = await session.scalar(
            select(DeviceHeartbeat)
            .where(DeviceHeartbeat.device_id == device_id)
            .order_by(DeviceHeartbeat.received_at.desc())
            .limit(1)
        )
        assert stored is not None
        assert stored.current_watts is None
        assert "latest" not in stored.payload
        state = await session.get(DeviceDataState, device_id)
        assert state is not None
        assert state.ingestion_gate == "pending_reconnect"
        assert state.reset_required_on_reconnect is True
