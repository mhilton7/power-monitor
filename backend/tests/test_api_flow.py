from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.security.protocol import PROTOCOL, sign_headers


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


@pytest.mark.asyncio
async def test_bootstrap_enrollment_heartbeat_and_push(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    initial = await client.get("/api/v1/auth/session")
    assert initial.json()["bootstrap_required"] is True
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "admin@example.com",
            "display_name": "Admin",
            "password": "Long-Production-Password-42!",
        },
    )
    assert bootstrap.status_code == 201, bootstrap.text
    sites = (await client.get("/api/v1/sites")).json()
    token = await client.post(
        "/api/v1/enrollment-tokens",
        headers=csrf(client),
        json={"site_id": sites[0]["id"], "name": "Garage HVAC"},
    )
    assert token.status_code == 201, token.text
    claim = await client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token.json()["token"],
            "protocol_version": PROTOCOL,
            "hardware_id": "esp32s3-flow-0001",
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
    again = await client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token.json()["token"],
            "protocol_version": PROTOCOL,
            "hardware_id": "esp32s3-flow-0002",
            "capabilities": {
                "hardware_target": "esp32-s3-pzem004t-v4",
                "pzem_model": "PZEM-004T V4.0",
                "sd_present": True,
            },
        },
    )
    assert again.status_code == 401
    device_id = claim.json()["device_id"]
    secret = claim.json()["enrollment_secret"].encode()
    heartbeat_payload = {
        "protocol_version": PROTOCOL,
        "schema_version": "heartbeat/1.0.0",
        "device_id": device_id,
        "boot_id": "123e4567-e89b-12d3-a456-426614174000",
        "firmware_version": "1.0.0",
        "firmware_build_hash": "abc123",
        "uptime_seconds": 30,
        "reboot_reason": "power_on",
        "current_ip": "192.168.1.50",
        "hostname": "sensor.local",
        "rssi_dbm": -50,
        "connection_mode": "push",
        "latest": {
            "measured_at": "2026-07-20T12:00:00Z",
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
    body = json.dumps(heartbeat_payload, separators=(",", ":")).encode()
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
    assert response.status_code == 200, response.text
    assert response.json()["immediate_sync_requested"] is True
    assert response.json()["sequence_cursor"] == {
        "highest_contiguous_accepted_sequence": 0,
        "maximum_seen_sequence": 0,
        "next_sequence_floor": 1,
        "data_generation": 0,
        "reset_boundary": 0,
    }
    sparse_batch = {
        "protocol_version": PROTOCOL,
        "schema_version": "reading-batch/1.0.0",
        "device_id": device_id,
        "readings": [
            {
                "sequence": 790,
                "boot_id": heartbeat_payload["boot_id"],
                "interval_start": "2026-07-20T11:59:00Z",
                "interval_end": "2026-07-20T12:00:00Z",
                "time_trusted": True,
                "voltage_avg": "120",
                "current_avg": "5",
                "power_avg": "600",
                "power_factor": "1",
                "frequency_hz": "60",
                "interval_energy_wh": "10",
                "energy_method": "power_integration",
                "ct_rating_amps": "100",
                "quality_flags": [],
                "firmware_version": "1.0.9",
            }
        ],
    }
    sparse_body = json.dumps(sparse_batch, separators=(",", ":")).encode()
    sparse = await client.post(
        "/api/v1/device-readings/batch",
        content=sparse_body,
        headers={
            **sign_headers(
                secret=secret,
                device_id=device_id,
                direction="device-to-server",
                method="POST",
                target="/api/v1/device-readings/batch",
                body=sparse_body,
            ),
            "Content-Type": "application/json",
        },
    )
    assert sparse.status_code == 200, sparse.text
    assert sparse.json()["highest_contiguous_accepted_sequence"] == 0
    reconciled = await client.post(
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
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["sequence_cursor"] == {
        "highest_contiguous_accepted_sequence": 0,
        "maximum_seen_sequence": 790,
        "next_sequence_floor": 791,
        "data_generation": 0,
        "reset_boundary": 0,
    }
    devices = (await client.get("/api/v1/devices")).json()
    assert devices[0]["name"] == "Garage HVAC"
    assert devices[0]["status"] == "online_with_backlog"

    rotation = await client.post(
        f"/api/v1/devices/{device_id}/credential-rotation",
        headers=csrf(client),
        json={"overlap_seconds": 300},
    )
    assert rotation.status_code == 200
    assert rotation.json()["secret_exposed"] is False
    delivery_path = "/api/v1/device-credentials/rotation"
    delivery = await client.get(
        delivery_path,
        headers=sign_headers(
            secret=secret,
            device_id=device_id,
            direction="device-to-server",
            method="GET",
            target=delivery_path,
        ),
    )
    assert delivery.status_code == 200, delivery.text
    new_secret = delivery.json()["enrollment_secret"].encode()
    credential_id = delivery.json()["credential_id"]
    confirm_path = "/api/v1/device-credentials/rotation/confirm"
    confirm_body = json.dumps({"credential_id": credential_id}, separators=(",", ":")).encode()
    confirmation = await client.post(
        confirm_path,
        content=confirm_body,
        headers={
            **sign_headers(
                secret=new_secret,
                device_id=device_id,
                direction="device-to-server",
                method="POST",
                target=confirm_path,
                body=confirm_body,
            ),
            "Content-Type": "application/json",
        },
    )
    assert confirmation.json() == {"confirmed": True}
    rejected_old = await client.post(
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
    assert rejected_old.status_code == 401
