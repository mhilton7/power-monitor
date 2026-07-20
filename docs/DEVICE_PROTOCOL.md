# Device protocol: `pm-protocol/1.0.0`

Every authenticated request includes `X-PM-Protocol`, `X-PM-Device-ID`, Unix `X-PM-Timestamp`, a random lowercase-hex `X-PM-Nonce` of at least 32 characters, `X-PM-Content-SHA256`, and `X-PM-Signature`.

```text
PM-HMAC-SHA256-V1
<UPPERCASE METHOD>
<PATH AND CANONICAL QUERY>
<TIMESTAMP>
<NONCE>
<LOWERCASE SHA-256 OF EXACT BODY BYTES>
```

Query names and values use RFC 3986 encoding, are sorted by encoded name then value, preserve duplicates, and omit `?` when empty. Derive 32-byte keys with HKDF-SHA256, empty salt, and info `pm-device-to-server-v1` or `pm-server-to-device-v1`. Compare signatures in constant time. The default acceptance window is 300 seconds; nonces are unique per device, credential, and direction. Revoked credentials fail before route logic.

`shared/auth-test-vectors/hmac-sha256-v1.json` is normative and checked by both tests and `scripts/contract_check.py`. OpenAPI contracts distinguish the server ingest and device-local surfaces.

## Synchronization

Durable records have monotonically increasing per-device sequence numbers. Push batches contain at most 500 records and a bounded body. Pull uses `GET /api/v1/readings?after_sequence=N&limit=500`. Both enter the same transaction. Identical retries are duplicates; changed content at an existing sequence is a security/data-integrity conflict. The response acknowledges only committed data and returns the highest contiguous sequence plus gaps. The server sends `/sync/ack` after commit. `410 Gone` records permanent loss and retained bounds.

Heartbeats carry live data, boot/firmware/network state, PZEM and SD evidence, retained bounds, acknowledged sequence, backlog, configuration version, trusted-time evidence, resources, and queues. Desired config and firmware availability are returned. Unknown additive fields are ignored; missing required fields fail with `application/problem+json`. Breaking changes require a new protocol version.

All timestamps are UTC ISO 8601 with `Z`. Device-local endpoints and event categories are enumerated in `shared/openapi/device-api.yaml`; server endpoints are in `device-ingest-api.yaml` and `server-api.yaml`.
