# Architecture

The system is server-centric. Caddy terminates HTTPS and exposes only the React frontend and FastAPI. PostgreSQL is isolated on an internal Compose network. The worker owns polling, synchronization, rollups, alerts, and file jobs; PostgreSQL advisory locking prevents duplicate scheduler ownership. A separate network permits worker-to-device traffic.

## Trust boundaries

- Browser authentication uses opaque server-side sessions, HttpOnly cookies, CSRF tokens, RBAC, and same-origin requests. Browser code has no device route or credential.
- Devices authenticate each non-enrollment request with directional HKDF-derived HMAC keys. Timestamp, nonce, body digest, protocol, device identity, and canonical target are verified before route logic.
- The application encrypts the enrolled secret with the operator-supplied master key. The database stores ciphertext and fingerprints.
- Pull targets pass site CIDR/domain policy, scheme/port restrictions, address resolution checks, and rebinding defenses before HTTP is attempted.

## Data flow

Push and pull records enter the same transactional ingestion service. `raw_readings` is immutable evidence, `normalized_intervals` records validation and selected energy, and cost result tables reference rate and calculation versions. `(device_id, sequence)` is unique. Identical retry is accepted; conflicting content creates a critical event; a cursor advances only over contiguous committed sequences.

Status is evidence-based: signed heartbeat age, device API result, meter, storage, trusted time, and synchronization state. ICMP/TCP diagnostics never determine application health alone.

Dashboard presentation is also server-resolved. Status and summary definitions
carry stable metric identities; the resolver enforces one canonical instance
per page, role, and breakpoint. Normal pages do not carry infrastructure-health
cards. Those diagnostics are restricted to Administration > System Health while
readiness probes and alert evaluation remain part of the existing health and
worker paths.

## Scale and failure behavior

Polling has bounded global/site concurrency, short timeouts, jitter, exponential backoff, and per-device circuit breaking. Devices retain durable records on microSD and resume after the server cursor. Raw data is the source for recomputable rollups and pricing. PostgreSQL timestamps are UTC; rate evaluation converts interval boundaries in the utility-account timezone.

The initial release uses standard PostgreSQL 17 without Redis or a time-series extension. Monthly native partitioning is optional after measured volume justifies it; migrations must preserve the global device/sequence uniqueness and tests must cover attach/detach operations before enabling it.
