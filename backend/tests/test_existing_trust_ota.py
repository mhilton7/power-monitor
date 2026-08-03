from __future__ import annotations

import base64
import hashlib
import json
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    failure_code: str | None = None,
    failure_summary: str | None = None,
) -> dict[str, Any]:
    return {
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
        "boot_id": BOOT_ID,
        "failure_code": failure_code,
        "failure_summary": failure_summary,
    }


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
    with pytest.raises(ValueError, match="complete image"):
        FirmwareDeploymentReport.model_validate({**base, "bytes_received": 99})
    with pytest.raises(ValueError, match="requires failure_code"):
        FirmwareDeploymentReport.model_validate(
            {**base, "state": "failed", "bytes_received": 0, "progress": 0}
        )


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
        deployment.reading_confirmed_at = datetime.now(UTC)
        await session.commit()
    body = json.dumps(heartbeat, separators=(",", ":")).encode()
    final_heartbeat = await client.post(
        heartbeat_target,
        content=body,
        headers={
            **_signed_headers(secret, device_id, "POST", heartbeat_target, body),
            "Content-Type": "application/json",
        },
    )
    assert final_heartbeat.status_code == 200
    history = await client.get("/api/v1/firmware-deployments", params={"device_id": device_id})
    assert history.status_code == 200
    assert history.json()[0]["state"] == "completed"
    assert history.json()[0]["verification_heartbeats"] == 11


@pytest.mark.asyncio
async def test_bootstrap_canary_cancel_retry_stale_report_and_integrity_quarantine(
    api_client: Any,
    test_settings: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    tmp_path: Path,
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

    created = await client.post(
        "/api/v1/firmware-deployments",
        headers=_csrf(client),
        json={
            "firmware_release_id": release["id"],
            "device_ids": [canary_id, follower_id],
            "scheduled_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            "idempotency_key": "ota-canary-request-0001",
            "canary_first": True,
            "maximum_concurrency": 1,
        },
    )
    assert created.status_code == 201, created.text
    canary, follower = created.json()["deployments"]
    assert canary["state"] == "scheduled"
    assert follower["state"] == "waiting_canary"
    assert canary["rollout_group_id"] == follower["rollout_group_id"]

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
