from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ota import (  # noqa: E402
    OTA_MANIFEST_HKDF_INFO,
    canonical_manifest_bytes,
    derive_ota_manifest_key,
    sign_ota_manifest,
)


def main() -> None:
    secret = bytes(range(32))
    device_id = "123e4567-e89b-12d3-a456-426614174000"
    manifest = {
        "schema_version": "pm-ota-manifest/2",
        "protocol_version": "pm-protocol/1.0.0",
        "deployment_id": "123e4567-e89b-12d3-a456-426614174001",
        "release_id": "123e4567-e89b-12d3-a456-426614174002",
        "device_id": device_id,
        "version": "1.0.11",
        "project_name": "power-monitor-sensor",
        "hardware_target": "esp32-s3",
        "protocol_min": "pm-protocol/1.0.0",
        "protocol_max": "pm-protocol/1.0.0",
        "size_bytes": 1_456_789,
        "sha256": "ab" * 32,
        "build_hash": "cd" * 32,
        "not_before": "2026-08-02T20:00:00Z",
        "expires_at": "2026-08-03T20:00:00Z",
        "allow_downgrade": False,
        "attempt": 1,
        "hmac_algorithm": "HMAC-SHA256",
        "hmac_key_context": OTA_MANIFEST_HKDF_INFO.decode("ascii"),
        "download_path": (
            "/api/v1/device-firmware/123e4567-e89b-12d3-a456-426614174002/"
            "download?deployment_id=123e4567-e89b-12d3-a456-426614174001"
        ),
    }
    document = {
        "schema": "pm-ota-manifest-test-vector/1",
        "description": "Existing device credential OTA manifest HKDF and HMAC vector",
        "secret_hex": secret.hex(),
        "device_id": device_id,
        "hkdf_salt_utf8": device_id,
        "hkdf_info_utf8": OTA_MANIFEST_HKDF_INFO.decode("ascii"),
        "derived_key_hex": derive_ota_manifest_key(secret, device_id).hex(),
        "manifest_without_hmac": manifest,
        "canonical_json_utf8": canonical_manifest_bytes(manifest).decode("utf-8"),
        "manifest_hmac_base64url": sign_ota_manifest(secret, device_id, manifest),
    }
    target = ROOT / "shared" / "auth-test-vectors" / "ota-manifest-v2.json"
    target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
