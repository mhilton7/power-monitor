# Device enrollment

An administrator creates a cryptographically random token with a default 10-minute lifetime and optional site/circuit/name/role/CT/mode preassignment. The token can be claimed once. The device sends its hardware identity, capabilities, `pm-protocol/1.0.0`, and token to `POST /api/v1/device-enrollment/claim` over validated TLS.

Claim and token consumption occur in one transaction. The response contains a permanent UUID, a unique high-entropy enrollment secret shown only to the claiming device, effective non-secret configuration, server OTA public key metadata, and heartbeat/synchronization policy. Browser enrollment pages never receive the permanent secret.

The agent must commit identity and secret atomically to protected storage before sending the first signed heartbeat. If that write fails, erase the partial identity and use a new token. A factory reset requires reenrollment. Lost or compromised devices must be revoked immediately.

Credential rotation creates a new encrypted credential with an overlap deadline while the current key remains valid. The device obtains and confirms the new credential through its authenticated control flow; browser responses expose only credential ID/fingerprint. After confirmation or expiry the old key is revoked. Back up the application master key separately: database ciphertext is unrecoverable without it.
