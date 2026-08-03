# Firmware management

Power Monitor uses Device-authenticated HMAC OTA for new sensor releases. An
administrator selects one `firmware.bin`; the browser does not provide a
manifest, hash, version, target, signing key, certificate, or device secret.

## Server verification and storage

`POST /api/v1/firmware-releases` accepts exactly one multipart field named
`binary`. The API streams it in 64 KiB chunks to a temporary file while counting
bytes and calculating SHA-256, flushes and fsyncs the file, then parses the ESP
image with bounded reads. A release is accepted only when all of the following
are valid:

- ESP image header, segment count, checksum, and appended image SHA-256;
- ESP32-S3 chip ID and an application descriptor in the first segment;
- exact project name `power-monitor-sensor` and a strict semantic version;
- standard application ELF hash and build timestamp;
- exact embedded protocol marker `pm-protocol/1.0.0`;
- image size within the configured upload limit and 6 MiB OTA slot; and
- no truncation, unexpected padding, or trailing bytes.

Bootloaders, partition tables, merged full-flash images, other chip families,
malformed versions, corrupt images, and same-version/different-byte uploads are
rejected with specific problem codes. Verified files are atomically installed
at `firmware/<sha256>/firmware.bin`; the original filename is sanitized metadata
only. A legacy Ed25519 hash is never silently relabeled as an existing-trust
release.

## Trust model

The four protections have separate jobs:

- HTTPS protects transport and authenticates the configured server endpoint.
- The ordinary device HMAC authenticates each sensor request to the server.
- The OTA manifest HMAC authenticates release instructions to one device.
- SHA-256 proves the streamed binary is the artifact named by that manifest.

For each manifest, the server decrypts the existing enrolled device credential
only for the request and derives a 32-byte key with HKDF-SHA256:

```text
salt = lowercase hyphenated device UUID encoded as UTF-8
info = pm-ota-manifest-v2/server-to-device
```

The derived key is neither persisted nor returned. The `pm-ota-manifest/2`
object is compact canonical JSON with UTF-8, sorted keys, no insignificant
whitespace, and every field except `manifest_hmac` authenticated by
HMAC-SHA256. The signature is base64url without padding. The normative
cross-language vector is
`shared/auth-test-vectors/ota-manifest-v2.json`.

The API, worker, frontend, and backup services do not receive Caddy private-key
storage or the TLS private-key secret. The application never reads `cert.key`,
`root.key`, or `tls.key`, and none of those keys signs firmware. Only Caddy
terminates HTTPS. Existing-trust OTA needs no new certificate or private-key
file.

## Capability and bootstrap

Heartbeats and enrollment can report:

```json
{
  "ota": {
    "supported": true,
    "protocol_version": 2,
    "authentication_mode": "existing_device_hmac",
    "rollback_supported": true,
    "partition_size_bytes": 6291456
  }
}
```

The server presents one of `ready`, `legacy_signed_ota_only`, `trust_missing`,
`bootstrap_required`, or `unsupported`. Firmware that predates OTA v2 cannot
use a protocol it does not contain. For that sensor, the readiness endpoint
returns the verified bootstrap filename and SHA-256 plus a non-erasing esptool
command that writes the application at `0x20000`. This preserves NVS,
enrollment, Wi-Fi, CA trust, microSD history, and sequence state. Once the next
signed heartbeat reports OTA v2, future updates use the dashboard.

## Deployment lifecycle

Creating or changing an installation requires `firmware.deploy`; uploading an
artifact requires `firmware.manage`; viewing state requires `firmware.view`.
An intentional downgrade additionally requires `allow_downgrade=true`, explicit
UI confirmation, and recent administrator reauthentication.

The server enforces one active deployment per device and records a monotonic
revision and attempt. The device retrieves its manifest and binary with its
normal authenticated HTTPS client. Manifest and download routes check device
ownership, schedule, expiry, capability, target, protocol, trust mode, release
verification, and artifact integrity. Binary responses are streamed with exact
`Content-Length`, `Digest`, `Cache-Control: no-store`, and
`X-Content-Type-Options: nosniff` headers.

Device milestone reports use normal request HMAC and the states
`manifest_authenticated`, `download_started`, `downloading`, `binary_verified`,
`partition_written`, `rebooting`, `post_boot_validation`, `validated`,
`failed`, `rollback_detected`, and `rolled_back`. Reports are transition-checked,
attempt-aware, and idempotent. Download completion alone is never success.

After local validation, the server moves the deployment to
`awaiting_heartbeat`. It marks `completed` only after the target version and
build hash remain healthy on the same boot for at least ten authenticated
heartbeats, one reading batch succeeds, and no critical alert or rollback is
present. Multi-sensor deployments default to a sequential canary rollout with
maximum concurrency one; promotion applies the same evidence gates.

Back up content-addressed artifacts with the database. Preserve historical
legacy records and nullable signing evidence, but new dashboard deployments use
only `trust_mode=existing_device_hmac`.
