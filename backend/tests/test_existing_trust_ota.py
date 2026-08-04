from __future__ import annotations

import base64
import hashlib
import json
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes.firmware import _advisory_lock_key, _lock_firmware_scope
from app.db.models import Device, FirmwareDeployment, FirmwareRelease
from app.firmware_images import FirmwareImageError, parse_esp32s3_application_image
from app.ota import (
    OTA_MANIFEST_HKDF_INFO,
    canonical_manifest_bytes,
    compare_semver,
    derive_ota_manifest_key,
    verify_ota_manifest_hmac,
)
from app.schemas import FirmwareDeploymentReport
from app.security.protocol import PROTOCOL, sign_headers
from app.upload_limits import FIRMWARE_MULTIPART_OVERHEAD_BYTES

PASSWORD = "Long-Production-Password-42!"
PROJECT = "power-monitor-sensor"
BOOT_ID = "123e4567-e89b-12d3-a456-426614174099"


def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


def _field(value: str, size: int) -> bytes:
    payload = value.encode("ascii")
    assert len(payload) < size
    return payload + bytes(size - len(payload))


def esp32s3_image(
    *,
    version: str = "1.0.11",
    project: str = PROJECT,
    chip_id: int = 9,
    descriptor_magic: int = 0xABCD5432,
    variant: bytes = b"a",
) -> bytes:
    descriptor = bytearray(256)
    struct.pack_into("<I", descriptor, 0, descriptor_magic)
    descriptor[16:48] = _field(version, 32)
    descriptor[48:80] = _field(project, 32)
    descriptor[80:96] = _field("12:34:56", 16)
    descriptor[96:112] = _field("Aug 02 2026", 16)
    descriptor[112:144] = _field("esp-idf-v5.4", 32)
    descriptor[144:176] = hashlib.sha256(b"elf-" + variant).digest()
    segment = bytes(descriptor) + PROTOCOL.encode("ascii") + b"\x00" + variant * 127

    header = bytearray(24)
    header[0] = 0xE9
    header[1] = 1
    header[2] = 0
    header[8] = 0xEE
    struct.pack_into("<H", header, 12, chip_id)
    header[23] = 1
    image = bytearray(header)
    image.extend(struct.pack("<II", 0x3C000020, len(segment)))
    image.extend(segment)
    checksum = 0xEF
    for value in segment:
        checksum ^= value
    image.extend(bytes(15 - (len(image) % 16)))
    image.append(checksum)
    image.extend(hashlib.sha256(image).digest())
    return bytes(image)


async def _bootstrap(client: httpx.AsyncClient) -> str:
    await client.get("/api/v1/auth/session")
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "ota-admin@example.com",
            "display_name": "OTA Admin",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    sites = (await client.get("/api/v1/sites")).json()
    return sites[0]["id"]


async def _enroll(
    client: httpx.AsyncClient,
    site_id: str,
    suffix: str,
    *,
    ota: bool = True,
) -> tuple[str, bytes]:
    token = await client.post(
        "/api/v1/enrollment-tokens",
        headers=_csrf(client),
        json={"site_id": site_id, "name": f"OTA sensor {suffix}"},
    )
    assert token.status_code == 201, token.text
    capability: dict[str, Any] = {
        "hardware_target": "esp32-s3-pzem004t-v4",
        "pzem_model": "PZEM-004T V4.0",
        "sd_present": True,
        "sd_required": True,
        "supported_endpoints": ["health", "readings", "ota"],
    }
    if ota:
        capability["ota"] = {
            "supported": True,
            "protocol_version": 2,
            "authentication_mode": "existing_device_hmac",
            "rollback_supported": True,
            "partition_size_bytes": 6 * 1024 * 1024,
        }
    claim = await client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token.json()["token"],
            "protocol_version": PROTOCOL,
            "hardware_id": f"esp32s3-ota-{suffix}",
            "capabilities": capability,
        },
    )
    assert claim.status_code == 201, claim.text
    return claim.json()["device_id"], claim.json()["enrollment_secret"].encode()


async def _upload(
    client: httpx.AsyncClient,
    image: bytes,
    *,
    filename: str = "firmware.bin",
) -> httpx.Response:
    return await client.post(
        "/api/v1/firmware-releases",
        headers=_csrf(client),
        files={"binary": (filename, image, "application/octet-stream")},
    )


def _signed_headers(
    secret: bytes,
    device_id: str,
    method: str,
    target: str,
    body: bytes = b"",
) -> dict[str, str]:
    return sign_headers(
        secret=secret,
        device_id=device_id,
        direction="device-to-server",
        method=method,
        target=target,
        body=body,
    )


async def _signed_report(
    client: httpx.AsyncClient,
    secret: bytes,
    payload: dict[str, Any],
) -> httpx.Response:
    target = "/api/v1/device-firmware/report"
    body = json.dumps(payload, separators=(",", ":")).encode()
    return await client.post(
        target,
        content=body,
        headers={
            **_signed_headers(secret, payload["device_id"], "POST", target, body),
            "Content-Type": "application/json",
        },
    )


def _report(
    *,
    device_id: str,
    deployment_id: str,
    release: dict[str, Any],
    state: str,
    attempt: int = 1,
    progress: int = 0,
    bytes_received: int = 0,
    current_version: str = "1.0.10",
    current_build_hash: str = "old-build",
    boot_id: str = BOOT_ID,
    failure_code: str | None = None,
    failure_summary: str | None = None,
    evidence_sequence: int | None = None,
) -> dict[str, Any]:
    report = {
        "device_id": device_id,
        "deployment_id": deployment_id,
        "release_id": release["id"],
        "attempt": attempt,
        "state": state,
        "current_firmware_version": current_version,
        "current_build_hash": current_build_hash,
        "target_version": release["version"],
        "target_sha256": release["sha256"],
        "bytes_received": bytes_received,
        "image_size": release["size_bytes"],
        "progress": progress,
        "boot_id": boot_id,
        "failure_code": failure_code,
        "failure_summary": failure_summary,
    }
    if evidence_sequence is not None:
        report["evidence_sequence"] = evidence_sequence
    return report


def _heartbeat(
    *,
    device_id: str,
    firmware_version: str,
    firmware_build_hash: str,
    boot_id: str = BOOT_ID,
    reboot_reason: str = "software_reset",
    resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL,
        "schema_version": "heartbeat/1.0.0",
        "device_id": device_id,
        "boot_id": boot_id,
        "firmware_version": firmware_version,
        "firmware_build_hash": firmware_build_hash,
        "uptime_seconds": 120,
        "reboot_reason": reboot_reason,
        "connection_mode": "push",
        "pzem": {"ok": True, "status": "ok"},
        "sd": {"ok": True, "status": "ok"},
        "oldest_stored_sequence": 0,
        "newest_stored_sequence": 0,
        "server_ack_sequence": 0,
        "backlog_estimate": 0,
        "configuration_version": 1,
        "time": {"trusted": True, "source": "sntp"},
        "resources": resources or {},
        "queue": {},
        "ota": {
            "supported": True,
            "protocol_version": 2,
            "authentication_mode": "existing_device_hmac",
            "rollback_supported": True,
            "partition_size_bytes": 6 * 1024 * 1024,
        },
    }


async def _signed_heartbeat(
    client: httpx.AsyncClient,
    secret: bytes,
    payload: dict[str, Any],
) -> httpx.Response:
    target = "/api/v1/device-heartbeats"
    body = json.dumps(payload, separators=(",", ":")).encode()
    return await client.post(
        target,
        content=body,
        headers={
            **_signed_headers(secret, payload["device_id"], "POST", target, body),
            "Content-Type": "application/json",
        },
    )


@pytest.mark.parametrize(
    ("image", "code"),
    [
        (esp32s3_image()[:-7], "firmware_image_invalid"),
        (esp32s3_image(chip_id=0), "firmware_wrong_target"),
        (esp32s3_image(descriptor_magic=0), "firmware_not_application_image"),
        (b"partition-table", "firmware_image_invalid"),
        (esp32s3_image() + b"merged-flash", "firmware_image_invalid"),
        (esp32s3_image(project="another-project"), "firmware_project_mismatch"),
        (esp32s3_image(version="01.0.11"), "firmware_version_invalid"),
    ],
)
def test_strict_esp32s3_parser_rejects_unsafe_images(
    tmp_path: Path, image: bytes, code: str
) -> None:
    path = tmp_path / "firmware.bin"
    path.write_bytes(image)
    with pytest.raises(FirmwareImageError) as caught:
        parse_esp32s3_application_image(
            path,
            maximum_bytes=8 * 1024 * 1024,
            ota_partition_size_bytes=6 * 1024 * 1024,
        )
    assert caught.value.code == code


def test_strict_esp32s3_parser_extracts_standard_descriptor(tmp_path: Path) -> None:
    image = esp32s3_image(version="1.2.3-rc.1+build.7", variant=b"z")
    path = tmp_path / "firmware.bin"
    path.write_bytes(image)
    parsed = parse_esp32s3_application_image(
        path,
        maximum_bytes=len(image),
        ota_partition_size_bytes=len(image),
    )
    assert parsed.version == "1.2.3-rc.1+build.7"
    assert parsed.project_name == PROJECT
    assert parsed.hardware_target == "esp32-s3"
    assert parsed.protocol_min == parsed.protocol_max == PROTOCOL
    assert parsed.size_bytes == len(image)
    assert parsed.build_hash == hashlib.sha256(b"elf-z").hexdigest()
    assert parsed.build_timestamp == datetime(2026, 8, 2, 12, 34, 56, tzinfo=UTC)
    with pytest.raises(FirmwareImageError, match="does not fit") as caught:
        parse_esp32s3_application_image(
            path,
            maximum_bytes=len(image) - 1,
            ota_partition_size_bytes=len(image),
        )
    assert caught.value.code == "firmware_too_large"


def test_manifest_hkdf_hmac_vector_and_tamper_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[2]
    vector = json.loads(
        (root / "shared" / "auth-test-vectors" / "ota-manifest-v2.json").read_text()
    )
    secret = bytes.fromhex(vector["secret_hex"])
    manifest = {
        **vector["manifest_without_hmac"],
        "manifest_hmac": vector["manifest_hmac_base64url"],
    }
    assert derive_ota_manifest_key(secret, vector["device_id"]).hex() == vector["derived_key_hex"]
    assert (
        derive_ota_manifest_key(secret, vector["device_id"].upper()).hex()
        == vector["derived_key_hex"]
    )
    assert canonical_manifest_bytes(manifest).decode() == vector["canonical_json_utf8"]
    assert verify_ota_manifest_hmac(secret, vector["device_id"], manifest)
    assert not verify_ota_manifest_hmac(b"wrong-secret", vector["device_id"], manifest)
    assert not verify_ota_manifest_hmac(secret, "123e4567-e89b-12d3-a456-426614174010", manifest)
    for field, value in {
        "deployment_id": "123e4567-e89b-12d3-a456-426614174011",
        "sha256": "ef" * 32,
        "size_bytes": manifest["size_bytes"] + 1,
        "version": "1.0.12",
        "expires_at": "2026-08-02T19:59:59Z",
        "not_before": "2026-08-03T20:00:00Z",
        "hmac_key_context": "pm-device-to-server-v1",
    }.items():
        changed = {**manifest, field: value}
        assert not verify_ota_manifest_hmac(secret, vector["device_id"], changed), field

    called = False
    real_compare = __import__("hmac").compare_digest

    def tracked_compare(left: str, right: str) -> bool:
        nonlocal called
        called = True
        return real_compare(left, right)

    monkeypatch.setattr("app.ota.hmac.compare_digest", tracked_compare)
    assert verify_ota_manifest_hmac(secret, vector["device_id"], manifest)
    assert called


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("1.0.0", "1.0.0", 0),
        ("1.0.0+one", "1.0.0+two", 0),
        ("1.0.0", "1.0.0-rc.1", 1),
        ("1.0.0-1", "1.0.0-alpha", -1),
        ("1.0.0-alpha.1", "1.0.0-alpha", 1),
        ("1.0.0-01", "1.0.0", None),
        ("1.0.0-alpha..1", "1.0.0", None),
        ("01.0.0", "1.0.0", None),
    ],
)
def test_semver_precedence_is_strict(left: str, right: str, expected: int | None) -> None:
    assert compare_semver(left, right) == expected


def test_report_schema_requires_complete_verified_image() -> None:
    base = _report(
        device_id="123e4567-e89b-12d3-a456-426614174000",
        deployment_id="123e4567-e89b-12d3-a456-426614174001",
        release={
            "id": "123e4567-e89b-12d3-a456-426614174002",
            "version": "1.0.11",
            "sha256": "ab" * 32,
            "size_bytes": 100,
        },
        state="binary_verified",
        progress=100,
        bytes_received=100,
    )
    assert FirmwareDeploymentReport.model_validate(base).state == "binary_verified"
    waiting = FirmwareDeploymentReport.model_validate(
        {
            **base,
            "state": "waiting_for_schedule",
            "progress": 0,
            "bytes_received": 0,
            "evidence_sequence": 4,
        }
    )
    assert waiting.state == "waiting_for_schedule"
    assert waiting.evidence_sequence == 4
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        FirmwareDeploymentReport.model_validate({**base, "evidence_sequence": -1})
    with pytest.raises(ValueError, match="less than or equal to"):
        FirmwareDeploymentReport.model_validate({**base, "evidence_sequence": 2**64})
    with pytest.raises(ValueError, match="complete image"):
        FirmwareDeploymentReport.model_validate({**base, "bytes_received": 99})
    with pytest.raises(ValueError, match="requires failure_code"):
        FirmwareDeploymentReport.model_validate(
            {**base, "state": "failed", "bytes_received": 0, "progress": 0}
        )


@pytest.mark.asyncio
async def test_firmware_scope_lock_uses_stable_postgres_advisory_transaction_lock() -> None:
    session = MagicMock()
    session.execute = AsyncMock()
    bind = MagicMock()
    bind.dialect.name = "postgresql"
    session.get_bind.return_value = bind

    await _lock_firmware_scope(session, "rollout:group-one")

    statement, parameters = session.execute.await_args.args
    assert str(statement) == "SELECT pg_advisory_xact_lock(:lock_key)"
    assert parameters == {
        "lock_key": _advisory_lock_key("power-monitor:firmware:rollout:group-one")
    }
    assert _advisory_lock_key("scope-a") == _advisory_lock_key("scope-a")
    assert _advisory_lock_key("scope-a") != _advisory_lock_key("scope-b")


@pytest.mark.asyncio
async def test_upload_is_single_file_streamed_verified_and_content_addressed(
    api_client: Any,
    test_settings: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.firmware_path = tmp_path / "firmware"
    await _bootstrap(client)
    image = esp32s3_image()
    uploaded = await _upload(client, image, filename="../../unsafe name.bin")
    assert uploaded.status_code == 201, uploaded.text
    release = uploaded.json()
    assert release["version"] == "1.0.11"
    assert release["project_name"] == PROJECT
    assert release["hardware_target"] == "esp32-s3"
    assert release["protocol_min"] == release["protocol_max"] == PROTOCOL
    assert release["size_bytes"] == len(image)
    assert release["sha256"] == hashlib.sha256(image).hexdigest()
    assert release["build_hash"] == hashlib.sha256(b"elf-a").hexdigest()
    assert release["trust_mode"] == "existing_device_hmac"
    assert release["verification_status"] == "verified"
    artifact = test_settings.firmware_path / release["sha256"] / "firmware.bin"
    assert artifact.read_bytes() == image
    async with session_factory_fixture() as session:
        stored = await session.get(FirmwareRelease, release["id"])
        assert stored is not None
        assert stored.original_filename == "unsafe-name.bin"
        assert stored.file_path is None
        assert stored.signature is None
        assert stored.signing_key_id is None

    duplicate = await _upload(client, image)
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == release["id"]
    assert duplicate.json()["duplicate"] is True
    conflict = await _upload(client, esp32s3_image(variant=b"b"))
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "firmware_version_conflict"
    extra = await client.post(
        "/api/v1/firmware-releases",
        headers=_csrf(client),
        files=[
            ("binary", ("firmware.bin", image, "application/octet-stream")),
            ("version", (None, "1.0.11")),
        ],
    )
    assert extra.status_code == 422
    assert extra.json()["code"] == "firmware_upload_fields_invalid"
    assert not list(test_settings.firmware_path.glob(".ota-upload-*"))


@pytest.mark.asyncio
async def test_upload_rejects_empty_oversize_and_bad_image_with_cleanup(
    api_client: Any, test_settings: Any, tmp_path: Path
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.firmware_path = tmp_path / "firmware"
    await _bootstrap(client)
    empty = await _upload(client, b"")
    assert empty.status_code == 422
    assert empty.json()["code"] == "firmware_image_invalid"
    test_settings.firmware_max_bytes = 64
    oversized = await _upload(client, esp32s3_image())
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "firmware_too_large"
    test_settings.firmware_max_bytes = 8 * 1024 * 1024
    bad = await _upload(client, esp32s3_image(project="not-power-monitor"))
    assert bad.status_code == 422
    assert bad.json()["code"] == "firmware_project_mismatch"
    assert not list(test_settings.firmware_path.glob(".ota-upload-*"))


@pytest.mark.asyncio
async def test_upload_size_limit_rejects_content_length_before_body_or_auth(
    api_client: Any, test_settings: Any, tmp_path: Path
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.firmware_path = tmp_path / "firmware"
    test_settings.firmware_max_bytes = 1024
    maximum_body = test_settings.firmware_max_bytes + FIRMWARE_MULTIPART_OVERHEAD_BYTES

    class UnreadBody(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.iterated = False

        async def __aiter__(self) -> Any:
            self.iterated = True
            yield b"must-not-be-read"

    stream = UnreadBody()
    request = client.build_request(
        "POST",
        "/api/v1/firmware-releases",
        headers={
            "Content-Type": "multipart/form-data; boundary=preflight",
            "Content-Length": str(maximum_body + 1),
        },
        content=stream,
    )
    response = await client.send(request)

    assert response.status_code == 413
    assert response.json()["code"] == "firmware_too_large"
    assert stream.iterated is False
    assert not test_settings.firmware_path.exists()


@pytest.mark.asyncio
async def test_upload_size_limit_bounds_chunked_body_before_route_tempfile(
    api_client: Any, test_settings: Any, tmp_path: Path
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.firmware_path = tmp_path / "firmware"
    test_settings.firmware_max_bytes = 1024
    maximum_body = test_settings.firmware_max_bytes + FIRMWARE_MULTIPART_OVERHEAD_BYTES
    boundary = "chunked-firmware"
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="binary"; filename="firmware.bin"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()

    async def chunks() -> Any:
        yield prefix
        remaining = maximum_body + 1
        while remaining:
            chunk = b"x" * min(4096, remaining)
            remaining -= len(chunk)
            yield chunk
        yield suffix

    response = await client.post(
        "/api/v1/firmware-releases",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        content=chunks(),
    )

    assert response.status_code == 413
    assert response.json()["code"] == "firmware_too_large"
    assert not test_settings.firmware_path.exists()


@pytest.mark.asyncio
async def test_upload_never_relabels_a_legacy_hash_as_existing_trust(
    api_client: Any,
    test_settings: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.firmware_path = tmp_path / "firmware"
    site_id = await _bootstrap(client)
    device_id, _secret = await _enroll(client, site_id, "legacy-release")
    image = esp32s3_image(version="1.9.0", variant=b"legacy")
    async with session_factory_fixture() as session:
        legacy_release = FirmwareRelease(
            version="1.9.0",
            channel="stable",
            trust_mode="ed25519_legacy",
            hardware_target="esp32-s3",
            protocol_min=PROTOCOL,
            protocol_max=PROTOCOL,
            file_path=str(tmp_path / "legacy.bin"),
            size_bytes=len(image),
            sha256=hashlib.sha256(image).hexdigest(),
            signature="historical-signature",
            signing_key_id="historical-key",
            release_notes="Historical release",
            verified_at=datetime.now(UTC),
            active=False,
        )
        session.add(legacy_release)
        await session.commit()
        legacy_release_id = legacy_release.id
    deployment = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json={
            "firmware_release_id": legacy_release_id,
            "device_ids": [device_id],
            "scheduled_at": datetime.now(UTC).isoformat(),
        },
    )
    assert deployment.status_code == 422
    assert deployment.json()["code"] == "firmware_trust"
    response = await _upload(client, image)
    assert response.status_code == 409
    assert response.json()["code"] == "firmware_legacy_hash_conflict"


@pytest.mark.asyncio
async def test_manifest_download_reports_retry_and_final_heartbeat_verification(
    api_client: Any,
    test_settings: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.firmware_path = tmp_path / "firmware"
    test_settings.heartbeat_expectation_seconds = 1
    site_id = await _bootstrap(client)
    device_id, secret = await _enroll(client, site_id, "primary")
    other_id, other_secret = await _enroll(client, site_id, "other")
    release_response = await _upload(client, esp32s3_image())
    assert release_response.status_code == 201, release_response.text
    release = release_response.json()
    readiness = await client.get(
        f"/api/v1/devices/{device_id}/firmware-readiness",
        params={"release_id": release["id"]},
    )
    assert readiness.status_code == 200
    assert readiness.json()["firmware_ota"]["state"] == "ready"
    assert readiness.json()["compatibility"]["ready"] is True

    scheduled_at = datetime.now(UTC) - timedelta(seconds=1)
    created = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json={
            "firmware_release_id": release["id"],
            "device_ids": [device_id],
            "scheduled_at": scheduled_at.isoformat(),
            "idempotency_key": "ota-primary-request-0001",
        },
    )
    assert created.status_code == 201, created.text
    deployment_id = created.json()["deployment_ids"][0]
    repeated = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json={
            "firmware_release_id": release["id"],
            "device_ids": [device_id],
            "scheduled_at": scheduled_at.isoformat(),
            "idempotency_key": "ota-primary-request-0001",
        },
    )
    assert repeated.status_code == 201
    assert repeated.json()["deployment_ids"] == [deployment_id]

    manifest_path = "/api/v1/device-firmware/manifest"
    manifest_response = await client.get(
        manifest_path,
        headers=_signed_headers(secret, device_id, "GET", manifest_path),
    )
    assert manifest_response.status_code == 200, manifest_response.text
    manifest = manifest_response.json()
    assert manifest["schema_version"] == "pm-ota-manifest/2"
    assert manifest["device_id"] == device_id
    assert manifest["deployment_id"] == deployment_id
    assert manifest["hmac_key_context"] == OTA_MANIFEST_HKDF_INFO.decode()
    assert verify_ota_manifest_hmac(secret, device_id, manifest)
    assert not verify_ota_manifest_hmac(other_secret, other_id, manifest)
    unavailable = await client.get(
        manifest_path,
        headers=_signed_headers(other_secret, other_id, "GET", manifest_path),
    )
    assert unavailable.json() == {"available": False, "protocol_version": PROTOCOL}

    download_target = manifest["download_path"]
    wrong_download = await client.get(
        download_target,
        headers=_signed_headers(other_secret, other_id, "GET", download_target),
    )
    assert wrong_download.status_code == 404
    download = await client.get(
        download_target,
        headers=_signed_headers(secret, device_id, "GET", download_target),
    )
    assert download.status_code == 200, download.text
    assert download.content == esp32s3_image()
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["content-length"] == str(release["size_bytes"])
    assert download.headers["digest"] == (
        "sha-256=" + base64.b64encode(bytes.fromhex(release["sha256"])).decode()
    )

    milestones = [
        ("manifest_authenticated", 0, 0, "1.0.10", "old-build"),
        ("waiting_for_schedule", 0, 0, "1.0.10", "old-build"),
        ("download_started", 0, 0, "1.0.10", "old-build"),
        ("downloading", 50, release["size_bytes"] // 2, "1.0.10", "old-build"),
        ("binary_verified", 100, release["size_bytes"], "1.0.10", "old-build"),
        ("partition_written", 100, release["size_bytes"], "1.0.10", "old-build"),
        ("rebooting", 100, release["size_bytes"], "1.0.10", "old-build"),
        (
            "post_boot_validation",
            100,
            release["size_bytes"],
            release["version"],
            release["build_hash"],
        ),
        (
            "validated",
            100,
            release["size_bytes"],
            release["version"],
            release["build_hash"],
        ),
    ]
    last_payload: dict[str, Any] | None = None
    for state, progress, received, current_version, current_hash in milestones:
        last_payload = _report(
            device_id=device_id,
            deployment_id=deployment_id,
            release=release,
            state=state,
            progress=progress,
            bytes_received=received,
            current_version=current_version,
            current_build_hash=current_hash,
        )
        report_response = await _signed_report(client, secret, last_payload)
        assert report_response.status_code == 200, report_response.text
    assert last_payload is not None
    duplicate = await _signed_report(client, secret, last_payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True

    async with session_factory_fixture() as session:
        deployment = await session.get(FirmwareDeployment, deployment_id)
        assert deployment is not None
        deployment.stabilization_started_at = datetime.now(UTC) - timedelta(seconds=20)
        await session.commit()

    # Commit the first target-build reading before the target heartbeat moves
    # the deployment into its post-boot gate. Re-sending these exact bytes
    # afterwards exercises the lost-response path: ingestion returns a
    # cryptographically identical duplicate, which is still durable proof.
    interval_end = datetime.now(UTC)
    target_batch = {
        "protocol_version": PROTOCOL,
        "schema_version": "reading-batch/1.0.0",
        "device_id": device_id,
        "readings": [
            {
                "sequence": 1,
                "boot_id": BOOT_ID,
                "interval_start": (interval_end - timedelta(minutes=1)).isoformat(),
                "interval_end": interval_end.isoformat(),
                "time_trusted": True,
                "voltage_avg": "120.0",
                "current_avg": "0.01",
                "power_avg": "1.0",
                "power_factor": "0.83",
                "frequency_hz": "60.0",
                "interval_energy_wh": "0.0166667",
                "energy_method": "power_integration",
                "ct_rating_amps": "100",
                "quality_flags": [],
                "firmware_version": release["version"],
            }
        ],
    }
    reading_target = "/api/v1/device-readings/batch"
    target_batch_body = json.dumps(target_batch, separators=(",", ":")).encode()
    first_target_batch = await client.post(
        reading_target,
        content=target_batch_body,
        headers={
            **_signed_headers(
                secret,
                device_id,
                "POST",
                reading_target,
                target_batch_body,
            ),
            "Content-Type": "application/json",
        },
    )
    assert first_target_batch.status_code == 200, first_target_batch.text
    assert first_target_batch.json()["accepted"] == [1]

    heartbeat_target = "/api/v1/device-heartbeats"
    heartbeat = {
        "protocol_version": PROTOCOL,
        "schema_version": "heartbeat/1.0.0",
        "device_id": device_id,
        "boot_id": BOOT_ID,
        "firmware_version": release["version"],
        "firmware_build_hash": release["build_hash"],
        "uptime_seconds": 120,
        "reboot_reason": "software_reset",
        "connection_mode": "push",
        "pzem": {"ok": True, "status": "ok"},
        "sd": {"ok": True, "status": "ok"},
        "oldest_stored_sequence": 0,
        "newest_stored_sequence": 0,
        "server_ack_sequence": 0,
        "backlog_estimate": 0,
        "configuration_version": 1,
        "time": {"trusted": True, "source": "sntp"},
        "resources": {},
        "queue": {},
        "ota": {
            "supported": True,
            "protocol_version": 2,
            "authentication_mode": "existing_device_hmac",
            "rollback_supported": True,
            "partition_size_bytes": 6 * 1024 * 1024,
        },
    }
    for _ in range(10):
        body = json.dumps(heartbeat, separators=(",", ":")).encode()
        response = await client.post(
            heartbeat_target,
            content=body,
            headers={
                **_signed_headers(secret, device_id, "POST", heartbeat_target, body),
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 200, response.text
    async with session_factory_fixture() as session:
        deployment = await session.get(FirmwareDeployment, deployment_id)
        assert deployment is not None
        assert deployment.state == "awaiting_heartbeat"
        assert deployment.reading_confirmed_at is None

    duplicate_target_batch = await client.post(
        reading_target,
        content=target_batch_body,
        headers={
            **_signed_headers(
                secret,
                device_id,
                "POST",
                reading_target,
                target_batch_body,
            ),
            "Content-Type": "application/json",
        },
    )
    assert duplicate_target_batch.status_code == 200, duplicate_target_batch.text
    assert duplicate_target_batch.json()["duplicates"] == [1]
    history = await client.get("/api/v1/firmware-deployments", params={"device_id": device_id})
    assert history.status_code == 200
    assert history.json()[0]["state"] == "completed"
    assert history.json()[0]["verification_heartbeats"] == 10
    assert history.json()[0]["reading_confirmed_at"] is not None


@pytest.mark.asyncio
async def test_report_evidence_sequence_allows_equal_missed_milestone_replay(
    api_client: Any,
    test_settings: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.firmware_path = tmp_path / "firmware"
    site_id = await _bootstrap(client)
    device_id, secret = await _enroll(client, site_id, "missed-milestones")
    release_response = await _upload(client, esp32s3_image(variant=b"m"))
    assert release_response.status_code == 201, release_response.text
    release = release_response.json()

    created = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json={
            "firmware_release_id": release["id"],
            "device_ids": [device_id],
            "scheduled_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            "idempotency_key": "ota-missed-milestone-replay-0001",
        },
    )
    assert created.status_code == 201, created.text
    deployment_id = created.json()["deployment_ids"][0]
    manifest_path = "/api/v1/device-firmware/manifest"
    manifest_response = await client.get(
        manifest_path,
        headers=_signed_headers(secret, device_id, "GET", manifest_path),
    )
    assert manifest_response.status_code == 200, manifest_response.text

    # A sensor that persisted several checkpoints while the server was
    # unavailable replays those missed milestones with its latest retained
    # evidence sequence. The sequence orders durable state; it is not a unique
    # HTTP-report counter.
    evidence_sequence = 17
    replay = [
        ("manifest_authenticated", 0, 0),
        ("download_started", 0, 0),
        ("downloading", 25, release["size_bytes"] // 4),
        ("downloading", 50, release["size_bytes"] // 2),
        ("binary_verified", 100, release["size_bytes"]),
    ]
    last_payload: dict[str, Any] | None = None
    for state, progress, bytes_received in replay:
        last_payload = _report(
            device_id=device_id,
            deployment_id=deployment_id,
            release=release,
            state=state,
            progress=progress,
            bytes_received=bytes_received,
            evidence_sequence=evidence_sequence,
        )
        response = await _signed_report(client, secret, last_payload)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == state

    assert last_payload is not None
    duplicate = await _signed_report(client, secret, last_payload)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["duplicate"] is True

    # A delayed failure from older durable evidence is stale even though Failed
    # would otherwise be a legal graph transition from binary_verified.
    delayed_failure = _report(
        device_id=device_id,
        deployment_id=deployment_id,
        release=release,
        state="failed",
        progress=50,
        bytes_received=release["size_bytes"] // 2,
        failure_code="download_transport_failed",
        failure_summary="Delayed report retained before later verification",
        evidence_sequence=evidence_sequence - 1,
    )
    stale = await _signed_report(client, secret, delayed_failure)
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "firmware_report_stale"

    async with session_factory_fixture() as session:
        deployment = await session.get(FirmwareDeployment, deployment_id)
        assert deployment is not None
        assert deployment.state == "binary_verified"
        assert deployment.last_report_payload["evidence_sequence"] == evidence_sequence

    next_milestone = _report(
        device_id=device_id,
        deployment_id=deployment_id,
        release=release,
        state="partition_written",
        progress=100,
        bytes_received=release["size_bytes"],
        evidence_sequence=evidence_sequence,
    )
    accepted = await _signed_report(client, secret, next_milestone)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["state"] == "partition_written"


@pytest.mark.asyncio
async def test_panic_during_zero_percent_download_reconciles_to_failed_with_evidence(
    api_client: Any,
    test_settings: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.firmware_path = tmp_path / "firmware"
    test_settings.firmware_interruption_grace_seconds = 0
    site_id = await _bootstrap(client)
    device_id, secret = await _enroll(client, site_id, "panic-at-zero")
    source_version = "1.0.10"
    source_hash = "source-build-10"
    initial = await _signed_heartbeat(
        client,
        secret,
        _heartbeat(
            device_id=device_id,
            firmware_version=source_version,
            firmware_build_hash=source_hash,
        ),
    )
    assert initial.status_code == 200, initial.text
    release_response = await _upload(client, esp32s3_image(version="1.0.11"))
    assert release_response.status_code == 201, release_response.text
    release = release_response.json()
    created = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json={
            "firmware_release_id": release["id"],
            "device_ids": [device_id],
            "scheduled_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    deployment_id = created.json()["deployment_ids"][0]
    async with session_factory_fixture() as session:
        deployment = await session.get(FirmwareDeployment, deployment_id)
        assert deployment is not None
        deployment.state = deployment.status = "downloading"
        deployment.progress = 0
        deployment.bytes_received = 0
        deployment.last_report_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    recovery = {
        "ota_recovery": {
            "previous_boot_stage": "streaming",
            "previous_boot_bytes_received": 32768,
            "previous_boot_update_open": True,
            "previous_boot_reboot_expected": False,
        }
    }
    rebooted = await _signed_heartbeat(
        client,
        secret,
        _heartbeat(
            device_id=device_id,
            firmware_version=source_version,
            firmware_build_hash=source_hash,
            boot_id="223e4567-e89b-12d3-a456-426614174099",
            reboot_reason="panic",
            resources=recovery,
        ),
    )
    assert rebooted.status_code == 200, rebooted.text
    history = await client.get("/api/v1/firmware-deployments", params={"device_id": device_id})
    assert history.status_code == 200, history.text
    deployment = history.json()[0]
    assert deployment["state"] == "failed"
    assert deployment["failure_code"] == "ota_interrupted_before_install"
    assert deployment["progress_mode"] == "indeterminate"
    assert deployment["display_state"] == "failed"
    assert deployment["source_version"] == source_version
    assert deployment["interruption_evidence"]["ota_recovery"] == recovery["ota_recovery"]


@pytest.mark.asyncio
async def test_target_heartbeat_reconciles_lost_final_reports_after_install(
    api_client: Any,
    test_settings: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.firmware_path = tmp_path / "firmware"
    site_id = await _bootstrap(client)
    device_id, secret = await _enroll(client, site_id, "target-reconcile")
    initial = await _signed_heartbeat(
        client,
        secret,
        _heartbeat(
            device_id=device_id,
            firmware_version="1.0.10",
            firmware_build_hash="source-build-10",
        ),
    )
    assert initial.status_code == 200, initial.text
    release_response = await _upload(client, esp32s3_image(version="1.0.11"))
    assert release_response.status_code == 201, release_response.text
    release = release_response.json()
    created = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json={
            "firmware_release_id": release["id"],
            "device_ids": [device_id],
            "scheduled_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    deployment_id = created.json()["deployment_ids"][0]
    async with session_factory_fixture() as session:
        deployment = await session.get(FirmwareDeployment, deployment_id)
        assert deployment is not None
        deployment.state = deployment.status = "downloading"
        deployment.progress = 0
        deployment.bytes_received = 0
        deployment.last_report_at = datetime.now(UTC)
        await session.commit()

    source_failure_before_target = _report(
        device_id=device_id,
        deployment_id=deployment_id,
        release=release,
        state="failed",
        current_version="1.0.10",
        current_build_hash="source-build-10",
        boot_id=BOOT_ID,
        failure_code="ota_transport_failed",
        failure_summary="Queued source-boot report arrived before the target heartbeat",
        evidence_sequence=9,
    )
    recorded_source_failure = await _signed_report(client, secret, source_failure_before_target)
    assert recorded_source_failure.status_code == 200, recorded_source_failure.text
    assert recorded_source_failure.json()["state"] == "failed"

    installed = await _signed_heartbeat(
        client,
        secret,
        _heartbeat(
            device_id=device_id,
            firmware_version=release["version"],
            firmware_build_hash=release["build_hash"],
            boot_id="323e4567-e89b-12d3-a456-426614174099",
            resources={
                "ota_recovery": {
                    "previous_boot_stage": "reboot_scheduled",
                    "previous_boot_update_open": False,
                    "previous_boot_reboot_expected": True,
                    "deployment_id": deployment_id,
                    "attempt": 1,
                    "evidence_sequence": 10,
                }
            },
        ),
    )
    assert installed.status_code == 200, installed.text
    history = await client.get("/api/v1/firmware-deployments", params={"device_id": device_id})
    assert history.status_code == 200, history.text
    deployment = history.json()[0]
    assert deployment["state"] == "awaiting_heartbeat"
    assert deployment["progress"] == 100
    assert deployment["bytes_received"] == release["size_bytes"]
    assert deployment["progress_mode"] == "determinate"
    assert deployment["validated_version"] == release["version"]
    assert deployment["failure_code"] is None

    delayed_source_failure = _report(
        device_id=device_id,
        deployment_id=deployment_id,
        release=release,
        state="failed",
        current_version="1.0.10",
        current_build_hash="source-build-10",
        boot_id="323e4567-e89b-12d3-a456-426614174099",
        failure_code="ota_transport_failed",
        failure_summary="Queued source-boot report arrived late",
    )
    mismatched_attempt_failure = {
        **delayed_source_failure,
        "attempt": 999,
        "current_firmware_version": release["version"],
        "current_build_hash": release["build_hash"],
        "evidence_sequence": 999,
    }
    mismatched = await _signed_report(client, secret, mismatched_attempt_failure)
    assert mismatched.status_code == 409, mismatched.text
    assert mismatched.json()["code"] == "firmware_report_mismatch"

    ordered_heartbeat = await _signed_heartbeat(
        client,
        secret,
        _heartbeat(
            device_id=device_id,
            firmware_version=release["version"],
            firmware_build_hash=release["build_hash"],
            boot_id="323e4567-e89b-12d3-a456-426614174099",
            resources={
                "ota_recovery": {
                    "deployment_id": deployment_id,
                    "attempt": 1,
                    "evidence_sequence": 10,
                }
            },
        ),
    )
    assert ordered_heartbeat.status_code == 200, ordered_heartbeat.text

    delayed_source_heartbeat = await _signed_heartbeat(
        client,
        secret,
        _heartbeat(
            device_id=device_id,
            firmware_version="1.0.10",
            firmware_build_hash="source-build-10",
            boot_id=BOOT_ID,
            resources={
                "ota_recovery": {
                    "deployment_id": deployment_id,
                    "attempt": 1,
                    "evidence_sequence": 10,
                }
            },
        ),
    )
    assert delayed_source_heartbeat.status_code == 200, delayed_source_heartbeat.text
    after_delayed_heartbeat = await client.get(
        "/api/v1/firmware-deployments", params={"device_id": device_id}
    )
    assert after_delayed_heartbeat.status_code == 200, after_delayed_heartbeat.text
    assert after_delayed_heartbeat.json()[0]["state"] == "awaiting_heartbeat"
    assert after_delayed_heartbeat.json()[0]["rollback_at"] is None

    stale = await _signed_report(client, secret, delayed_source_failure)
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "firmware_report_stale"
    unchanged = await client.get("/api/v1/firmware-deployments", params={"device_id": device_id})
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()[0]["state"] == "awaiting_heartbeat"

    explicitly_older_failure = {
        **delayed_source_failure,
        "current_firmware_version": release["version"],
        "current_build_hash": release["build_hash"],
        "evidence_sequence": 9,
    }
    older = await _signed_report(client, secret, explicitly_older_failure)
    assert older.status_code == 409, older.text
    assert older.json()["code"] == "firmware_report_stale"

    equal_evidence_failure = {
        **explicitly_older_failure,
        "evidence_sequence": 10,
    }
    equal = await _signed_report(client, secret, equal_evidence_failure)
    assert equal.status_code == 409, equal.text
    assert equal.json()["code"] == "firmware_report_stale"

    later_target_failure = {
        **explicitly_older_failure,
        "evidence_sequence": 11,
        "failure_code": "ota_post_boot_health_failed",
        "failure_summary": "A newer retained target-boot health failure",
    }
    later = await _signed_report(client, secret, later_target_failure)
    assert later.status_code == 200, later.text
    assert later.json()["state"] == "failed"


@pytest.mark.asyncio
async def test_stale_zero_percent_download_is_failed_instead_of_stuck(
    api_client: Any,
    test_settings: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.firmware_path = tmp_path / "firmware"
    test_settings.firmware_download_stale_seconds = 30
    site_id = await _bootstrap(client)
    device_id, secret = await _enroll(client, site_id, "stale-download")
    initial = await _signed_heartbeat(
        client,
        secret,
        _heartbeat(
            device_id=device_id,
            firmware_version="1.0.10",
            firmware_build_hash="source-build-10",
        ),
    )
    assert initial.status_code == 200, initial.text
    release_response = await _upload(client, esp32s3_image(version="1.0.11"))
    release = release_response.json()
    created = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json={
            "firmware_release_id": release["id"],
            "device_ids": [device_id],
            "scheduled_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    deployment_id = created.json()["deployment_ids"][0]
    async with session_factory_fixture() as session:
        deployment = await session.get(FirmwareDeployment, deployment_id)
        assert deployment is not None
        deployment.state = deployment.status = "download_started"
        deployment.progress = 0
        deployment.bytes_received = 0
        deployment.last_report_at = datetime.now(UTC) - timedelta(minutes=5)
        await session.commit()
    history = await client.get("/api/v1/firmware-deployments", params={"device_id": device_id})
    assert history.status_code == 200, history.text
    deployment = history.json()[0]
    assert deployment["state"] == "failed"
    assert deployment["failure_code"] == "ota_update_timed_out"
    assert deployment["interruption_evidence"]["last_state"] == "download_started"


@pytest.mark.asyncio
async def test_bootstrap_canary_cancel_retry_stale_report_and_integrity_quarantine(
    api_client: Any,
    test_settings: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: httpx.AsyncClient = api_client
    test_settings.firmware_path = tmp_path / "firmware"
    site_id = await _bootstrap(client)
    canary_id, canary_secret = await _enroll(client, site_id, "canary")
    follower_id, _follower_secret = await _enroll(client, site_id, "follower")
    legacy_id, _legacy_secret = await _enroll(client, site_id, "legacy", ota=False)
    release_response = await _upload(client, esp32s3_image(version="2.0.0"))
    assert release_response.status_code == 201
    release = release_response.json()
    legacy_readiness = await client.get(
        f"/api/v1/devices/{legacy_id}/firmware-readiness",
        params={"release_id": release["id"]},
    )
    assert legacy_readiness.json()["firmware_ota"]["state"] == "bootstrap_required"
    assert legacy_readiness.json()["bootstrap"]["required"] is True
    blocked = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json={
            "firmware_release_id": release["id"],
            "device_ids": [legacy_id],
            "scheduled_at": datetime.now(UTC).isoformat(),
        },
    )
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "firmware_bootstrap_required"

    rollout_payload = {
        "firmware_release_id": release["id"],
        "device_ids": [canary_id, follower_id],
        "scheduled_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        "idempotency_key": "ota-canary-request-0001",
        "canary_first": True,
        "maximum_concurrency": 1,
    }
    created = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json=rollout_payload,
    )
    assert created.status_code == 201, created.text
    canary, follower = created.json()["deployments"]
    assert canary["state"] == "scheduled"
    assert follower["state"] == "waiting_canary"
    assert canary["rollout_group_id"] == follower["rollout_group_id"]

    replayed = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json=rollout_payload,
    )
    assert replayed.status_code == 201, replayed.text
    assert replayed.json()["deployment_ids"] == [canary["id"], follower["id"]]
    assert {item["rollout_group_id"] for item in replayed.json()["deployments"]} == {
        canary["rollout_group_id"]
    }

    conflicting_reuse = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json={**rollout_payload, "device_ids": [canary_id]},
    )
    assert conflicting_reuse.status_code == 409
    assert conflicting_reuse.json()["code"] == "idempotency_conflict"

    locked_groups: list[str] = []

    async def record_rollout_lock(_session: Any, rollout_group_id: str) -> None:
        locked_groups.append(rollout_group_id)

    monkeypatch.setattr(
        "app.api.routes.firmware._lock_rollout_group",
        record_rollout_lock,
    )

    cancelled = await client.post(
        f"/api/v1/firmware-deployments/{canary['id']}/cancel",
        headers=_csrf(client),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    retried = await client.post(
        f"/api/v1/firmware-deployments/{canary['id']}/retry",
        headers=_csrf(client),
    )
    assert retried.status_code == 200
    assert retried.json()["state"] == "scheduled"
    assert retried.json()["attempt"] == 2
    stale = _report(
        device_id=canary_id,
        deployment_id=canary["id"],
        release=release,
        state="manifest_authenticated",
        attempt=1,
    )
    stale_response = await _signed_report(client, canary_secret, stale)
    assert stale_response.status_code == 409
    assert stale_response.json()["code"] == "firmware_report_stale"
    illegal = _report(
        device_id=canary_id,
        deployment_id=canary["id"],
        release=release,
        state="partition_written",
        attempt=2,
        progress=100,
        bytes_received=release["size_bytes"],
    )
    illegal_response = await _signed_report(client, canary_secret, illegal)
    assert illegal_response.status_code == 409
    assert illegal_response.json()["code"] == "firmware_transition_invalid"

    async with session_factory_fixture() as session:
        stored_canary = await session.get(FirmwareDeployment, canary["id"])
        assert stored_canary is not None
        stored_canary.state = stored_canary.status = "completed"
        stored_canary.verification_heartbeats = 10
        stored_canary.reading_confirmed_at = datetime.now(UTC)
        stored_canary.validated_at = datetime.now(UTC)
        await session.commit()
    promoted = await client.post(
        f"/api/v1/firmware-deployments/{canary['id']}/promote",
        headers=_csrf(client),
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["deployment"]["id"] == follower["id"]
    assert promoted.json()["deployment"]["state"] == "scheduled"
    assert locked_groups == [canary["rollout_group_id"]] * 3

    manifest_path = "/api/v1/device-firmware/manifest"
    manifest = await client.get(
        manifest_path,
        headers=_signed_headers(canary_secret, canary_id, "GET", manifest_path),
    )
    assert manifest.json() == {"available": False, "protocol_version": PROTOCOL}

    async with session_factory_fixture() as session:
        device = await session.get(Device, canary_id)
        assert device is not None
        device.firmware_version = release["version"]
        device.firmware_build_hash = release["build_hash"]
        await session.commit()
    already_current = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json={
            "firmware_release_id": release["id"],
            "device_ids": [canary_id],
            "scheduled_at": datetime.now(UTC).isoformat(),
        },
    )
    assert already_current.status_code == 409
    assert already_current.json()["code"] == "firmware_already_current"

    artifact = test_settings.firmware_path / release["sha256"] / "firmware.bin"
    artifact.write_bytes(b"corrupt")
    admin_artifact = await client.get(f"/api/v1/firmware-releases/{release['id']}/artifact")
    assert admin_artifact.status_code == 503
    assert admin_artifact.json()["code"] == "firmware_integrity_failure"
    async with session_factory_fixture() as session:
        stored_release = await session.get(FirmwareRelease, release["id"])
        assert stored_release is not None
        assert stored_release.verification_status == "quarantined"
