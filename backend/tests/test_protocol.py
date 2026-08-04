from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device, DeviceCredential, Site
from app.schemas import Heartbeat
from app.security.protocol import (
    ProtocolAuthError,
    SecretCipher,
    calculate_signature,
    canonical_path_and_query,
    canonical_string,
    derive_key,
    sha256_hex,
    sign_headers,
    verify_device_request,
)

ROOT = Path(__file__).resolve().parents[2]


def test_exact_sensor_heartbeat_fixture_matches_runtime_model() -> None:
    payload = json.loads(
        (ROOT / "shared" / "fixtures" / "valid-heartbeat.json").read_text(encoding="utf-8")
    )
    heartbeat = Heartbeat.model_validate(payload)
    assert heartbeat.protocol_version == "pm-protocol/1.0.0"
    assert heartbeat.schema_version == "heartbeat/1.0.0"
    assert heartbeat.latest is not None
    assert heartbeat.latest.power_w == 1
    assert heartbeat.resources["ota_recovery"]["evidence_sequence"] == 9


def test_shared_hmac_vectors() -> None:
    document = json.loads(
        (ROOT / "shared" / "auth-test-vectors" / "hmac-sha256-v1.json").read_text()
    )
    for vector in document["vectors"]:
        secret = bytes.fromhex(vector["secret"])
        digest = sha256_hex(vector["body_utf8"].encode())
        assert digest == vector["content_sha256"]
        assert canonical_path_and_query(vector["target_input"]) == vector["canonical_target"]
        canonical = canonical_string(
            vector["method"],
            vector["target_input"],
            vector["timestamp"],
            vector["nonce"],
            digest,
        )
        assert canonical == vector["canonical_string"]
        assert derive_key(secret, vector["direction"]).hex() == vector["derived_key_hex"]
        assert (
            calculate_signature(
                secret,
                vector["direction"],
                vector["method"],
                vector["target_input"],
                vector["timestamp"],
                vector["nonce"],
                digest,
            )
            == vector["signature"]
        )


@pytest.mark.asyncio
async def test_verifier_rejects_replay_and_timestamp(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    key = Fernet.generate_key().decode()
    cipher = SecretCipher(key)
    site = Site(name="Test", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    device = Device(site_id=site.id, hardware_id="hw-test-1234", name="Device")
    session.add(device)
    await session.flush()
    secret = b"correct horse battery staple for device"
    session.add(
        DeviceCredential(
            device_id=device.id,
            encrypted_secret=cipher.encrypt(secret),
            fingerprint=sha256_hex(secret),
            valid_from=now - timedelta(seconds=1),
            created_at=now,
        )
    )
    await session.commit()
    body = b"{}"
    headers = {
        key.lower(): value
        for key, value in sign_headers(
            secret=secret,
            device_id=device.id,
            direction="device-to-server",
            method="POST",
            target="/api/v1/device-heartbeats",
            body=body,
            timestamp=int(now.timestamp()),
            nonce="a" * 32,
        ).items()
    }
    verified = await verify_device_request(
        session=session,
        headers=headers,
        method="POST",
        target="/api/v1/device-heartbeats",
        body=body,
        cipher=cipher,
        now=now,
    )
    assert verified.device.id == device.id
    await session.commit()
    with pytest.raises(ProtocolAuthError, match="already been used"):
        await verify_device_request(
            session=session,
            headers=headers,
            method="POST",
            target="/api/v1/device-heartbeats",
            body=body,
            cipher=cipher,
            now=now,
        )
    stale = dict(headers)
    stale["x-pm-nonce"] = "b" * 32
    stale["x-pm-timestamp"] = str(int((now - timedelta(hours=1)).timestamp()))
    with pytest.raises(ProtocolAuthError, match="outside"):
        await verify_device_request(
            session=session,
            headers=stale,
            method="POST",
            target="/api/v1/device-heartbeats",
            body=body,
            cipher=cipher,
            now=now,
        )
