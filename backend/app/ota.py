from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.db.models import Device, DeviceCapability, FirmwareRelease

OTA_MANIFEST_SCHEMA = "pm-ota-manifest/2"
OTA_MANIFEST_PROTOCOL_VERSION = 2
OTA_MANIFEST_AUTHENTICATION_MODE = "existing_device_hmac"
OTA_MANIFEST_HMAC_ALGORITHM = "HMAC-SHA256"
OTA_MANIFEST_HKDF_INFO = b"pm-ota-manifest-v2/server-to-device"
OTA_TRUST_MODE = "existing_device_hmac"
LEGACY_OTA_TRUST_MODE = "ed25519_legacy"

TERMINAL_DEPLOYMENT_STATES = frozenset({"completed", "failed", "cancelled", "rolled_back"})
ACTIVE_DEPLOYMENT_STATES = frozenset(
    {
        "waiting_canary",
        "scheduled",
        "offered",
        "manifest_authenticated",
        "waiting_for_schedule",
        "download_started",
        "downloading",
        "binary_verified",
        "partition_written",
        "rebooting",
        "post_boot_validation",
        "validated",
        "awaiting_heartbeat",
        "rollback_detected",
    }
)
DEVICE_REPORT_STATES = frozenset(
    {
        "manifest_authenticated",
        "waiting_for_schedule",
        "download_started",
        "downloading",
        "binary_verified",
        "partition_written",
        "rebooting",
        "post_boot_validation",
        "validated",
        "failed",
        "rollback_detected",
        "rolled_back",
    }
)
DEPLOYMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "waiting_canary": frozenset({"scheduled", "cancelled"}),
    "scheduled": frozenset({"offered", "cancelled"}),
    "offered": frozenset({"manifest_authenticated", "failed", "cancelled"}),
    "manifest_authenticated": frozenset(
        {"waiting_for_schedule", "download_started", "failed", "cancelled"}
    ),
    "waiting_for_schedule": frozenset({"download_started", "failed", "cancelled"}),
    "download_started": frozenset({"downloading", "failed", "cancelled"}),
    "downloading": frozenset({"binary_verified", "failed", "cancelled"}),
    "binary_verified": frozenset({"partition_written", "failed", "cancelled"}),
    "partition_written": frozenset({"rebooting", "failed", "rollback_detected"}),
    "rebooting": frozenset({"post_boot_validation", "failed", "rollback_detected"}),
    "post_boot_validation": frozenset({"validated", "failed", "rollback_detected"}),
    "validated": frozenset({"awaiting_heartbeat", "failed", "rollback_detected"}),
    "awaiting_heartbeat": frozenset({"completed", "failed", "rollback_detected"}),
    "rollback_detected": frozenset({"rolled_back"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "rolled_back": frozenset(),
}

_PRERELEASE_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
_BUILD_IDENTIFIER = r"[0-9A-Za-z-]+"
_SEMVER = re.compile(
    rf"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    rf"(?:-({_PRERELEASE_IDENTIFIER}(?:\.{_PRERELEASE_IDENTIFIER})*))?"
    rf"(?:\+({_BUILD_IDENTIFIER}(?:\.{_BUILD_IDENTIFIER})*))?$"
)


def canonical_utc(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_hmac"}
    return json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def derive_ota_manifest_key(secret: bytes, device_id: str) -> bytes:
    canonical_device_id = str(UUID(device_id))
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=canonical_device_id.encode("utf-8"),
        info=OTA_MANIFEST_HKDF_INFO,
    ).derive(secret)


def sign_ota_manifest(secret: bytes, device_id: str, manifest: dict[str, Any]) -> str:
    key = bytearray(derive_ota_manifest_key(secret, device_id))
    try:
        digest = hmac.new(bytes(key), canonical_manifest_bytes(manifest), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    finally:
        for index in range(len(key)):
            key[index] = 0


def verify_ota_manifest_hmac(secret: bytes, device_id: str, manifest: dict[str, Any]) -> bool:
    supplied = manifest.get("manifest_hmac")
    if not isinstance(supplied, str):
        return False
    expected = sign_ota_manifest(secret, device_id, manifest)
    return hmac.compare_digest(expected, supplied)


def semver_key(value: str) -> tuple[int, int, int, tuple[tuple[int, int | str], ...]] | None:
    match = _SEMVER.fullmatch(value)
    if match is None:
        return None
    prerelease = match.group(4)
    if prerelease is None:
        prerelease_key: tuple[tuple[int, int | str], ...] = ((2, ""),)
    else:
        prerelease_key = tuple(
            (0, int(part)) if part.isdigit() else (1, part) for part in prerelease.split(".")
        )
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease_key)


def compare_semver(left: str, right: str) -> int | None:
    left_key = semver_key(left)
    right_key = semver_key(right)
    if left_key is None or right_key is None:
        return None
    return (left_key > right_key) - (left_key < right_key)


def hardware_compatible(device_target: str, release_target: str) -> bool:
    normalized_device = device_target.strip().lower()
    normalized_release = release_target.strip().lower()
    return normalized_device == normalized_release or (
        normalized_release == "esp32-s3"
        and (normalized_device == "esp32s3" or normalized_device.startswith("esp32-s3"))
    )


def ota_capability_payload(capability: DeviceCapability | None) -> dict[str, Any]:
    raw = capability.features.get("ota") if capability and capability.features else None
    ota = raw if isinstance(raw, dict) else {}
    supported = bool(ota.get("supported", False))
    protocol_version = ota.get("protocol_version")
    authentication_mode = ota.get("authentication_mode")
    rollback_supported = bool(ota.get("rollback_supported", False))
    partition_size = ota.get("partition_size_bytes")
    if not supported and raw is not None:
        state = "unsupported"
    elif not supported or not isinstance(protocol_version, int) or protocol_version < 2:
        state = "bootstrap_required"
    elif authentication_mode == "ed25519_legacy":
        state = "legacy_signed_ota_only"
    elif (
        authentication_mode != OTA_MANIFEST_AUTHENTICATION_MODE
        or not rollback_supported
        or not isinstance(partition_size, int)
        or partition_size <= 0
    ):
        state = "trust_missing"
    else:
        state = "ready"
    labels = {
        "ready": "Ready for server OTA",
        "legacy_signed_ota_only": "Legacy signed OTA only",
        "trust_missing": "OTA trust missing",
        "bootstrap_required": "One-time bootstrap required",
        "unsupported": "OTA unsupported",
    }
    return {
        "state": state,
        "label": labels[state],
        "supported": supported,
        "protocol_version": protocol_version if isinstance(protocol_version, int) else None,
        "authentication_mode": (
            authentication_mode if isinstance(authentication_mode, str) else None
        ),
        "rollback_supported": rollback_supported,
        "partition_size_bytes": partition_size if isinstance(partition_size, int) else None,
    }


def release_compatibility(
    device: Device, capability: DeviceCapability | None, release: FirmwareRelease
) -> dict[str, Any]:
    ota = ota_capability_payload(capability)
    reasons: list[str] = []
    if ota["state"] != "ready":
        reasons.append(str(ota["state"]))
    if capability is None or not hardware_compatible(
        capability.hardware_target, release.hardware_target
    ):
        reasons.append("hardware_incompatible")
    if not (
        device.protocol_version
        and release.protocol_min == device.protocol_version == release.protocol_max
    ):
        reasons.append("protocol_incompatible")
    partition_size = ota.get("partition_size_bytes")
    if isinstance(partition_size, int) and release.size_bytes > partition_size:
        reasons.append("partition_too_small")
    version_order = (
        compare_semver(release.version, device.firmware_version)
        if device.firmware_version
        else None
    )
    if version_order == 0:
        reasons.append("already_current")
    elif version_order is not None and version_order < 0:
        reasons.append("downgrade_requires_confirmation")
    return {
        "ready": not reasons,
        "reasons": reasons,
        "ota": ota,
        "current_version": device.firmware_version,
        "current_build_hash": device.firmware_build_hash,
        "target_version": release.version,
        "target_build_hash": release.build_hash,
    }


def firmware_readiness_payload(
    device: Device,
    capability: DeviceCapability | None,
    release: FirmwareRelease | None = None,
    *,
    bootstrap_offset: str = "0x20000",
) -> dict[str, Any]:
    ota = ota_capability_payload(capability)
    result: dict[str, Any] = {
        "device_id": device.id,
        "current_firmware_version": device.firmware_version,
        "current_firmware_build_hash": device.firmware_build_hash,
        "firmware_ota": ota,
    }
    if release is not None:
        result["release_id"] = release.id
        result["compatibility"] = release_compatibility(device, capability, release)
        result["bootstrap"] = {
            "required": ota["state"] == "bootstrap_required",
            "firmware_filename": f"power-monitor-sensor-{release.version}.bin",
            "sha256": release.sha256,
            "expected_version": release.version,
            "expected_build_hash": release.build_hash,
            "artifact_download_path": f"/api/v1/firmware-releases/{release.id}/artifact",
            "usb_command": (
                "python -m esptool --chip esp32s3 --port <PORT> --baud 460800 "
                f"write_flash {bootstrap_offset} "
                f"power-monitor-sensor-{release.version}.bin"
            ),
            "preserves": ["NVS", "Wi-Fi", "enrollment", "CA", "microSD", "sequence"],
        }
    return result
