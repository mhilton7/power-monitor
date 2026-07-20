from __future__ import annotations

import json
from typing import Any

import httpx

from app.security.protocol import PROTOCOL, sign_headers
from simulator.simulated_device.model import SimulatedDevice


def heartbeat_payload(device: SimulatedDevice) -> dict[str, Any]:
    latest = device.measurement()
    return {
        "protocol_version": PROTOCOL,
        "schema_version": "heartbeat/1.0.0",
        "device_id": device.device_id,
        "boot_id": device.boot_id,
        "firmware_version": device.firmware_version,
        "firmware_build_hash": "simulator-build",
        "uptime_seconds": 3600,
        "reboot_reason": "power_on",
        "current_ip": f"192.168.{50 + device.address_epoch}.{10 + device.index}",
        "hostname": f"power-sensor-{device.index}.local",
        "rssi_dbm": -48 - device.index % 25,
        "connection_mode": "hybrid",
        "latest": latest,
        "pzem": {
            "ok": not device.fault.pzem_failure,
            "status": "ok" if not device.fault.pzem_failure else "timeout",
            "error_count": int(device.fault.pzem_failure),
        },
        "sd": {
            "ok": device.fault.sd_state == "ok",
            "status": device.fault.sd_state,
            "error_count": int(device.fault.sd_state != "ok"),
        },
        "oldest_stored_sequence": min(
            (item.sequence for item in device.stored), default=0
        ),
        "newest_stored_sequence": device.sequence,
        "server_ack_sequence": device.ack_sequence,
        "backlog_estimate": max(0, device.sequence - device.ack_sequence),
        "configuration_version": device.config_version,
        "time": {
            "trusted": device.fault.clock_trusted,
            "source": "sntp" if device.fault.clock_trusted else "boot",
            "offset_ms": 4,
        },
        "resources": {"heap_free_bytes": 160000, "cpu_percent": 8},
        "queue": {"pending_records": max(0, device.sequence - device.ack_sequence)},
    }


async def signed_post(
    client: httpx.AsyncClient,
    device: SimulatedDevice,
    path: str,
    payload: dict[str, Any],
    *,
    replay: bool = False,
) -> httpx.Response:
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    if replay and device.replay_headers:
        headers = device.replay_headers
    else:
        secret = (
            "invalid-secret" if device.fault.authentication_failure else device.secret
        )
        headers = sign_headers(
            secret=secret.encode(),
            device_id=device.device_id,
            direction="device-to-server",
            method="POST",
            target=path,
            body=body,
        )
        device.replay_headers = headers
    return await client.post(
        path, content=body, headers={**headers, "Content-Type": "application/json"}
    )


async def push_once(
    client: httpx.AsyncClient, device: SimulatedDevice
) -> dict[str, int]:
    if device.fault.offline:
        return {"heartbeat": 0, "accepted": 0}
    heartbeat = await signed_post(
        client, device, "/api/v1/device-heartbeats", heartbeat_payload(device)
    )
    heartbeat.raise_for_status()
    readings = [item for item in device.stored if item.sequence > device.ack_sequence][
        :500
    ]
    accepted = 0
    if readings:
        response = await signed_post(
            client,
            device,
            "/api/v1/device-readings/batch",
            {
                "protocol_version": PROTOCOL,
                "schema_version": "reading-batch/1.0.0",
                "device_id": device.device_id,
                "readings": [item.model_dump(mode="json") for item in readings],
            },
        )
        response.raise_for_status()
        result = response.json()
        device.ack_sequence = result["highest_contiguous_accepted_sequence"]
        accepted = len(result["accepted"])
    return {"heartbeat": 1, "accepted": accepted}
