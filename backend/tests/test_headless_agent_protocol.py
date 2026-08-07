from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.agent_protocol import _record_headless_ota_result
from app.api.routes.firmware import _queue_headless_ota_command
from app.config import Settings
from app.data_reset.sensor_client import request_sensor_reset
from app.db.models import (
    Device,
    DeviceCapability,
    DeviceCommand,
    DeviceCredential,
    FirmwareDeployment,
    FirmwareRelease,
    Site,
    User,
)
from app.ota import release_compatibility
from app.security.agent_protocol import (
    AGENT_PROTOCOL,
    ProtocolAuthError,
    calculate_agent_request_signature,
    calculate_agent_response_signature,
    derive_agent_key,
    verify_agent_request,
)
from app.security.protocol import SecretCipher, sha256_hex

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Long-Production-Password-42!"


def test_headless_release_accepts_enrolled_target_alias_and_agent_protocol() -> None:
    device = Device(
        site_id="4f13827a-6a94-46dc-aabc-89a5ed75fed1",
        hardware_id="headless-compatibility-device",
        name="Headless compatibility",
        protocol_version=AGENT_PROTOCOL,
        connection_mode="push",
        firmware_version="2.0.3",
        firmware_build_hash="a" * 64,
    )
    capability = DeviceCapability(
        device_id="5db961d7-4812-449c-bf85-9683d3a4babc",
        hardware_target="esp32s3",
        pzem_model="PZEM-004T V4.0",
        sd_required=False,
        features={
            "ota": {
                "supported": True,
                "protocol_version": 2,
                "authentication_mode": "existing_device_hmac",
                "rollback_supported": True,
                "partition_size_bytes": 6 * 1024 * 1024,
            }
        },
    )
    release = FirmwareRelease(
        version="2.0.4",
        channel="stable",
        trust_mode="existing_device_hmac",
        project_name="power_monitor_sensor_headless",
        hardware_target="esp32-s3",
        protocol_min=AGENT_PROTOCOL,
        protocol_max=AGENT_PROTOCOL,
        size_bytes=1_009_792,
        sha256="b" * 64,
        build_hash="c" * 64,
        verification_status="verified",
    )

    compatibility = release_compatibility(device, capability, release)

    assert compatibility["ready"] is True
    assert compatibility["reasons"] == []


def test_shared_pm_agent_hmac_vector() -> None:
    vector = json.loads(
        (ROOT / "shared" / "auth-test-vectors" / "pm-agent-hmac-v2.json").read_text()
    )
    secret = bytes.fromhex(vector["secret_hex"])
    assert derive_agent_key(secret, "device-to-server").hex() == vector["device_to_server_key_hex"]
    assert derive_agent_key(secret, "server-to-device").hex() == vector["server_to_device_key_hex"]
    request = vector["request"]
    assert (
        calculate_agent_request_signature(
            secret=secret,
            device_id=request["device_id"],
            boot_id=request["boot_id"],
            counter=request["counter"],
            nonce=request["nonce"],
            method=request["method"],
            target=request["target_input"],
            body_sha256=request["body_sha256"],
        )
        == request["signature_hex"]
    )
    response = vector["response"]
    digest, signature = calculate_agent_response_signature(
        secret=secret,
        request_nonce=response["request_nonce"],
        request_counter=response["request_counter"],
        status=response["status"],
        body=response["body_utf8"].encode(),
    )
    assert digest == response["body_sha256"]
    assert signature == response["signature_hex"]


async def enrolled_agent(
    session: AsyncSession, settings: Settings
) -> tuple[Device, bytes, SecretCipher]:
    now = datetime.now(UTC)
    site = Site(name="Headless Test", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    device = Device(
        site_id=site.id,
        hardware_id="headless-test-device",
        name="Headless Agent",
        protocol_version=AGENT_PROTOCOL,
        connection_mode="push",
    )
    session.add(device)
    await session.flush()
    secret = b"headless agent deterministic enrollment secret"
    cipher = SecretCipher(settings.app_master_key)
    session.add(
        DeviceCredential(
            device_id=device.id,
            encrypted_secret=cipher.encrypt(secret),
            fingerprint=hashlib.sha256(secret).hexdigest(),
            valid_from=now - timedelta(seconds=1),
            created_at=now,
        )
    )
    await session.commit()
    return device, secret, cipher


def headers(
    *,
    secret: bytes,
    device_id: str,
    boot_id: str,
    counter: int,
    nonce: str,
    body: bytes,
    target: str = "/api/v2/agent/heartbeat",
) -> dict[str, str]:
    digest = sha256_hex(body)
    return {
        "x-pm-agent-protocol": AGENT_PROTOCOL,
        "x-pm-device-id": device_id,
        "x-pm-boot-id": boot_id,
        "x-pm-counter": str(counter),
        "x-pm-nonce": nonce,
        "x-pm-content-sha256": digest,
        "x-pm-signature": calculate_agent_request_signature(
            secret=secret,
            device_id=device_id,
            boot_id=boot_id,
            counter=counter,
            nonce=nonce,
            method="POST",
            target=target,
            body_sha256=digest,
        ),
    }


@pytest.mark.asyncio
async def test_counter_nonce_and_boot_replay_are_fail_closed(
    session: AsyncSession, test_settings: Settings
) -> None:
    device, secret, cipher = await enrolled_agent(session, test_settings)
    device_id = device.id
    body = b"{}"
    boot_one = "1b79f263-bd3a-4a53-9251-4c4278f5536a"
    first = headers(
        secret=secret,
        device_id=device_id,
        boot_id=boot_one,
        counter=41,
        nonce="a" * 32,
        body=body,
    )
    verified = await verify_agent_request(
        session=session,
        headers=first,
        method="POST",
        target="/api/v2/agent/heartbeat",
        body=body,
        cipher=cipher,
    )
    assert verified.boot_id == boot_one
    await session.commit()
    with pytest.raises(ProtocolAuthError, match="counter"):
        await verify_agent_request(
            session=session,
            headers=first,
            method="POST",
            target="/api/v2/agent/heartbeat",
            body=body,
            cipher=cipher,
        )
    await session.rollback()
    second = headers(
        secret=secret,
        device_id=device_id,
        boot_id=boot_one,
        counter=42,
        nonce="b" * 32,
        body=body,
    )
    await verify_agent_request(
        session=session,
        headers=second,
        method="POST",
        target="/api/v2/agent/heartbeat",
        body=body,
        cipher=cipher,
    )
    await session.commit()
    boot_two = "2b79f263-bd3a-4a53-9251-4c4278f5536a"
    third = headers(
        secret=secret,
        device_id=device_id,
        boot_id=boot_two,
        counter=1,
        nonce="c" * 32,
        body=body,
    )
    await verify_agent_request(
        session=session,
        headers=third,
        method="POST",
        target="/api/v2/agent/heartbeat",
        body=body,
        cipher=cipher,
    )
    await session.commit()
    old_boot = headers(
        secret=secret,
        device_id=device_id,
        boot_id=boot_one,
        counter=43,
        nonce="d" * 32,
        body=body,
    )
    with pytest.raises(ProtocolAuthError, match="retired boot"):
        await verify_agent_request(
            session=session,
            headers=old_boot,
            method="POST",
            target="/api/v2/agent/heartbeat",
            body=body,
            cipher=cipher,
        )


@pytest.mark.asyncio
async def test_reset_transport_enqueues_one_idempotent_headless_command(
    session: AsyncSession, test_settings: Settings
) -> None:
    device, _secret, _cipher = await enrolled_agent(session, test_settings)
    operation_id = "187da6e7-c0da-4f95-a2d2-740b874ed9a4"
    payload = {
        "protocol": "data-reset/1.0.0",
        "operation_id": operation_id,
        "device_id": device.id,
        "target_generation": 2,
        "plan_revision": 1,
    }
    first = await request_sensor_reset(
        session,
        device=device,
        settings=test_settings,
        action="prepare",
        operation_id=operation_id,
        target_generation=2,
        payload=payload,
    )
    second = await request_sensor_reset(
        session,
        device=device,
        settings=test_settings,
        action="prepare",
        operation_id=operation_id,
        target_generation=2,
        payload=payload,
    )
    assert first == second == {"state": "preparing"}
    commands = list(await session.scalars(select(DeviceCommand)))
    assert len(commands) == 1
    assert commands[0].command_type == "data_reset_prepare"
    assert commands[0].payload == payload


@pytest.mark.asyncio
async def test_headless_ota_command_and_progress_are_durable(
    session: AsyncSession, test_settings: Settings
) -> None:
    device, _secret, _cipher = await enrolled_agent(session, test_settings)
    now = datetime.now(UTC)
    user = User(
        email="headless-ota@example.com",
        display_name="Headless OTA",
        password_hash="not-used-in-this-test",
    )
    release = FirmwareRelease(
        version="2.0.1",
        channel="stable",
        trust_mode="existing_device_hmac",
        project_name="power_monitor_sensor_headless",
        hardware_target="esp32-s3",
        protocol_min=AGENT_PROTOCOL,
        protocol_max=AGENT_PROTOCOL,
        size_bytes=1024,
        sha256="a" * 64,
        build_hash="b" * 64,
        verification_status="verified",
        verified_at=now,
    )
    session.add_all([user, release])
    await session.flush()
    deployment = FirmwareDeployment(
        firmware_release_id=release.id,
        device_id=device.id,
        state="scheduled",
        status="scheduled",
        scheduled_at=now,
        expires_at=now + timedelta(hours=1),
        source_version="2.0.0",
        source_build_hash="c" * 64,
        created_by=user.id,
        created_at=now,
        state_changed_at=now,
    )
    session.add(deployment)
    await session.flush()
    _queue_headless_ota_command(
        session,
        deployment=deployment,
        device=device,
        release=release,
        now=now,
    )
    await session.flush()
    command = await session.scalar(
        select(DeviceCommand).where(DeviceCommand.device_id == device.id)
    )
    assert command is not None
    assert command.payload["firmware_sha256"] == release.sha256
    assert command.payload["protocol_marker"] == AGENT_PROTOCOL

    for stage, state, received in (
        ("accepted", "accepted", 0),
        ("downloading", "running", 512),
        ("rebooting", "running", 1024),
        ("validated", "completed", 1024),
    ):
        await _record_headless_ota_result(
            session,
            command,
            {
                "deployment_id": deployment.id,
                "release_id": release.id,
                "state": stage,
                "bytes_received": received,
                "image_size": 1024,
                "progress": received * 100 // 1024,
                "target_version": release.version,
                "target_build_hash": release.build_hash,
            },
            state,
            now,
        )
    assert deployment.state == "validated"
    assert deployment.progress == 100
    assert deployment.bytes_received == 1024


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


@pytest.mark.asyncio
async def test_v2_enrollment_signed_heartbeat_and_admin_command(
    api_client: Any, session: AsyncSession
) -> None:
    client: httpx.AsyncClient = api_client
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "headless-admin@example.com",
            "display_name": "Headless Admin",
            "password": PASSWORD,
        },
    )
    assert bootstrap.status_code == 201, bootstrap.text
    site_id = (await client.get("/api/v1/sites")).json()[0]["id"]
    token = await client.post(
        "/api/v1/enrollment-tokens",
        headers=csrf(client),
        json={"site_id": site_id, "name": "Headless Integration"},
    )
    assert token.status_code == 201, token.text
    claim = await client.post(
        "/api/v2/agent/enroll",
        json={
            "token": token.json()["token"],
            "protocol_version": AGENT_PROTOCOL,
            "hardware_id": "esp32s3-headless-integration",
            "capabilities": {
                "hardware_target": "esp32s3",
                "pzem_model": "PZEM-004T V4.0",
                "sd_present": False,
                "sd_required": False,
                "supported_endpoints": ["data-reset/1.0.0"],
                "data_reset_protocol": "data-reset/1.0.0",
                "ota": {
                    "supported": True,
                    "protocol_version": 2,
                    "authentication_mode": "existing_device_hmac",
                    "rollback_supported": True,
                    "partition_size_bytes": 6 * 1024 * 1024,
                },
            },
        },
    )
    assert claim.status_code == 201, claim.text
    device_id = claim.json()["device_id"]
    secret = claim.json()["enrollment_secret"].encode()
    boot_id = "83869685-4032-4e2c-8d5f-7aad43f1637e"

    # Reproduce the record left by the old heartbeat overwrite bug. The
    # authenticated boolean claim from firmware 2.0.0-2.0.3 must make the
    # already-enrolled device UI-OTA-ready again.
    capability = await session.get(DeviceCapability, device_id)
    assert capability is not None
    capability.features = {key: value for key, value in capability.features.items() if key != "ota"}
    await session.commit()

    async def send_heartbeat(
        counter: int, nonce: str, ota_capability: bool | dict[str, Any] = True
    ) -> httpx.Response:
        payload = {
            "protocol": AGENT_PROTOCOL,
            "device_id": device_id,
            "boot_id": boot_id,
            "firmware_version": "2.0.0",
            "build_hash": "1" * 64,
            "uptime_ms": counter * 1000,
            "reset_reason": "power_on",
            "wifi": {"connected": True, "rssi_dbm": -48},
            "latest": None,
            "pzem": {"ok": True, "status": "healthy"},
            "sd": {"ok": True, "status": "healthy"},
            "sequences": {
                "sequence_floor": 0,
                "maximum_seen_sequence": 1,
                "server_acknowledgement": 0,
                "next_sequence": 2,
                "oldest_stored_sequence": 1,
                "newest_stored_sequence": 1,
                "newest_syncable_sequence": 1,
                "backlog": 1,
            },
            "reset_projection": {
                "schema_version": 1,
                "present": True,
                "mounted": True,
                "writable": True,
                "data_generation": 0,
                "sequence_floor": 0,
                "next_sequence": 2,
                "oldest_sequence": 1,
                "newest_sequence": 1,
                "newest_syncable_sequence": 1,
                "server_ack_sequence": 0,
                "unsynchronized_estimate": 1,
                "local_record_count": 1,
                "prepare_projection_consistent": True,
                "prepare_projection_local_record_count": 1,
                "prepare_projection_next_sequence": 2,
                "prepare_projection_newest_sequence": 1,
                "prepare_projection_newest_syncable_sequence": 1,
                "prepare_drain_records_projected": 0,
                "prepare_drain_first_sequence_projected": None,
                "prepare_drain_last_sequence_projected": None,
                "prepare_drain_syncable_records_projected": 0,
                "card_generation": None,
                "card_identity_status": "bound",
            },
            "capabilities": {
                "data_reset": {
                    "supported": True,
                    "protocol": "data-reset/1.0.0",
                    "receipt_schema": 1,
                },
                "ota": ota_capability,
            },
            "configuration_revision": 1,
            "reset_generation": 0,
            "reset_operation": {"state": "idle", "checkpoint": "idle"},
            "resources": {"free_heap_bytes": 120000},
            "task_stack_margins": {"network": 4096},
            "last_command_result": None,
            "ota": {"state": "idle"},
            "time_trusted": False,
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        signed = headers(
            secret=secret,
            device_id=device_id,
            boot_id=boot_id,
            counter=counter,
            nonce=nonce,
            body=body,
        )
        return await client.post(
            "/api/v2/agent/heartbeat",
            content=body,
            headers={**signed, "Content-Type": "application/json"},
        )

    first = await send_heartbeat(1, "1" * 32)
    assert first.status_code == 200, first.text
    readiness = await client.get(f"/api/v1/devices/{device_id}/firmware-readiness")
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["firmware_ota"]["state"] == "ready"
    digest, signature = calculate_agent_response_signature(
        secret=secret,
        request_nonce="1" * 32,
        request_counter=1,
        status=200,
        body=first.content,
    )
    assert first.headers["x-pm-content-sha256"] == digest
    assert first.headers["x-pm-signature"] == signature
    assert first.json()["command"] is None

    queued = await client.post(
        f"/api/v1/devices/{device_id}/commands",
        headers=csrf(client),
        json={
            "command_type": "sync_now",
            "idempotency_key": f"test:{device_id}:sync",
            "expires_in_seconds": 300,
        },
    )
    assert queued.status_code == 201, queued.text
    second = await send_heartbeat(
        2,
        "2" * 32,
        {
            "supported": True,
            "protocol_version": 2,
            "authentication_mode": "existing_device_hmac",
            "rollback_supported": True,
            "partition_size_bytes": 6 * 1024 * 1024,
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["command"]["command_id"] == queued.json()["command_id"]
    assert second.json()["command"]["type"] == "sync_now"
    readiness = await client.get(f"/api/v1/devices/{device_id}/firmware-readiness")
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["firmware_ota"]["state"] == "ready"

    async def send_command_result(
        *, counter: int, nonce: str, command_id: str, state: str, result: dict[str, Any]
    ) -> httpx.Response:
        payload = {
            "protocol": AGENT_PROTOCOL,
            "device_id": device_id,
            "command_id": command_id,
            "state": state,
            "result": result,
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        target = "/api/v2/agent/commands/results"
        signed = headers(
            secret=secret,
            device_id=device_id,
            boot_id=boot_id,
            counter=counter,
            nonce=nonce,
            body=body,
            target=target,
        )
        return await client.post(
            target,
            content=body,
            headers={**signed, "Content-Type": "application/json"},
        )

    accepted = await send_command_result(
        counter=3,
        nonce="3" * 32,
        command_id=queued.json()["command_id"],
        state="accepted",
        result={"accepted_by_firmware": True},
    )
    assert accepted.status_code == 200, accepted.text
    redelivered = await send_heartbeat(4, "4" * 32)
    assert redelivered.status_code == 200, redelivered.text
    assert redelivered.json()["command"]["command_id"] == queued.json()["command_id"]
    assert redelivered.json()["command"]["type"] == "sync_now"
    completed = await send_command_result(
        counter=5,
        nonce="5" * 32,
        command_id=queued.json()["command_id"],
        state="completed",
        result={"accepted_by_firmware": True},
    )
    assert completed.status_code == 200, completed.text

    configuration = await client.post(
        f"/api/v1/devices/{device_id}/config",
        headers=csrf(client),
        json={"settings": {"ct_rating_amps": 200}, "acknowledge_ct_rating_change": True},
    )
    assert configuration.status_code == 201, configuration.text
    offered_config = await send_heartbeat(6, "6" * 32)
    assert offered_config.status_code == 200, offered_config.text
    config_command = offered_config.json()["command"]
    assert config_command["type"] == "apply_configuration"
    revision = config_command["payload"]["configuration_revision"]
    config_result = await send_command_result(
        counter=7,
        nonce="7" * 32,
        command_id=config_command["command_id"],
        state="completed",
        result={"configuration_revision": revision, "status": "applied"},
    )
    assert config_result.status_code == 200, config_result.text
    status = await client.get(f"/api/v1/devices/{device_id}/agent-status")
    assert status.status_code == 200, status.text
    assert status.json()["heartbeat"]["protocol"] == AGENT_PROTOCOL
    assert status.json()["commands"][0]["state"] == "completed"
    detail = await client.get(f"/api/v1/devices/{device_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["device"]["effective_config_version"] == revision
