# Device enrollment

An administrator creates a cryptographically random token with a default 10-minute lifetime and optional site/circuit/name/role/CT/mode preassignment. The token can be claimed once. The device sends its hardware identity, capabilities, `pm-protocol/1.0.0`, and token to `POST /api/v1/device-enrollment/claim` over validated TLS.

Claim and token consumption occur in one transaction. The response contains a permanent UUID, a unique high-entropy enrollment secret shown only to the claiming device, effective non-secret configuration, server OTA public key metadata, and heartbeat/synchronization policy. Browser enrollment pages never receive the permanent secret.

The agent must commit identity and secret atomically to protected storage before sending the first signed heartbeat. If that write fails, erase the partial identity and use a new token. A factory reset requires reenrollment. Lost or compromised devices must be revoked immediately.

Credential rotation creates a new encrypted credential with an overlap deadline while the current key remains valid. The device obtains and confirms the new credential through its authenticated control flow; browser responses expose only credential ID/fingerprint. After confirmation or expiry the old key is revoked. Back up the application master key separately: database ciphertext is unrecoverable without it.

## Remove and re-enroll a sensor

Administrators remove a claimed sensor from **Enrollment > Claimed sensors**.
The confirmation dialog requires the exact friendly name or immutable UUID and
shows assignment, last heartbeat, and retained-reading range. Removal is a soft,
idempotent decommission: every credential is revoked, pending configuration is
cancelled, active circuit assignment is detached, and polling/synchronization
stop. Raw readings, rollups, cost calculations, alerts, firmware history, UUID,
and audit/lifecycle records are never deleted.

Removed sensors appear under **Archived sensors** with actor, timestamp, reason,
retention state, and re-enrollment eligibility. To use the same physical sensor
again, create a new single-use enrollment token and claim it normally. The server
retains the UUID and sequence cursor but creates a new credential and a new
configuration version; revoked secrets never become valid again. No automatic
factory-reset command is sent.
