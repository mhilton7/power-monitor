# Security

## Threat model and controls

The system assumes hostile browser traffic, replayed device traffic, compromised LAN peers, malicious pull targets, tampered firmware, and database disclosure. Caddy provides TLS; API security headers include HSTS, restrictive CSP, frame denial, MIME sniff protection, no-referrer, and no-store for sensitive responses.

Passwords use Argon2id. Browser sessions are random, server-stored, expiring, revocable, HttpOnly, Secure, and SameSite; mutations require a CSRF proof. Login throttling and audit events apply. Admin/operator/viewer authorization is enforced in route dependencies. Optional TOTP secrets are encrypted. No default credentials exist.

Each device gets a unique high-entropy secret encrypted by Fernet under `APP_MASTER_KEY`. HKDF separates request directions. Exact body hashing, canonical HMAC, timestamp bounds, nonce persistence, constant-time comparison, per-device failure handling, revocation, and audit defend the device API. Logs redact secret-like fields and never print full signatures.

Pull policy prevents arbitrary URLs and revalidates DNS results. Use VLANs/VPN; do not expose sensor ports. Firmware must have matching SHA-256 and a valid Ed25519 signature before activation; the server normally stores only public verification keys and already signed packages.

## Key rotation and incident response

Back up the master key offline and separately. To rotate it, stop API/worker, take and verify a backup, reencrypt every active credential/TOTP secret in one audited maintenance transaction, update the protected environment, and start/verify services. Never discard the old key until a restored copy is tested.

For a compromised device, revoke it, preserve audit/events, rotate related tokens, inspect nonce/auth failures, and reenroll only after factory reset. For a server compromise, isolate it, preserve logs, rotate TLS/session/master/device credentials as applicable, restore a verified backup, and let microSD history backfill from the restored cursor.

Report vulnerabilities privately to the deployment owner; this standalone source distribution does not configure a public disclosure inbox.
