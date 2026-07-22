# Security

## Threat model and controls

The system assumes hostile browser traffic, replayed device traffic, compromised LAN peers, malicious pull targets, tampered firmware, and database disclosure. Caddy provides TLS; API security headers include HSTS, restrictive CSP, frame denial, MIME sniff protection, no-referrer, and no-store for sensitive responses.

Passwords use Argon2id. Browser sessions are random, server-stored, expiring, revocable, HttpOnly, Secure, and SameSite; mutations require a CSRF proof. Login throttling and audit events apply. Server-defined granular permissions and site scope are recalculated on each request; hiding frontend controls is never the security boundary. Material access/role changes revoke affected sessions. Last-administrator, self-lockout, protected-administrator, actor-permission, actor-site, archived-role, dependency, and optimistic-revision checks run server-side. Optional TOTP secrets are encrypted. No default credentials exist.

The semantic sign-in form permits standards-based browser password managers,
but autofill is never an authentication boundary. Native credential values are
read only at submit and sent in the existing POST body. Power Monitor does not
store passwords in browser application storage, interface settings, URLs, logs,
analytics, or telemetry. Production sign-in requires the configured HTTPS
origin; never disable certificate verification to make autofill appear.

Each device gets a unique high-entropy secret encrypted by Fernet under `APP_MASTER_KEY`. HKDF separates request directions. Exact body hashing, canonical HMAC, timestamp bounds, nonce persistence, constant-time comparison, per-device failure handling, revocation, and audit defend the device API. Logs redact secret-like fields and never print full signatures.

Daily application logs are persisted for 90 days. Redaction runs both before a
record is written and again while an administrator export is built. Log exports
are authorization- and CSRF-protected, bounded by a configured size limit,
assembled in a server-generated temporary path, audited, checksum-manifested,
and deleted after delivery. The persistent log directory is not exposed by the
gateway or a static file server.

SMTP credentials and webhook secrets are also encrypted under `APP_MASTER_KEY`.
The API returns only redacted delivery targets and configuration state; it never
returns a saved SMTP password. Authenticated SMTP is rejected unless STARTTLS or
implicit TLS is enabled.

Pull policy prevents arbitrary URLs and revalidates DNS results. Use VLANs/VPN; do not expose sensor ports. Firmware must have matching SHA-256 and a valid Ed25519 signature before activation; the server normally stores only public verification keys and already signed packages.

## Key rotation and incident response

Back up the master key offline and separately. To rotate it, stop API/worker, take and verify a backup, reencrypt every active credential/TOTP secret in one audited maintenance transaction, update the protected environment, and start/verify services. Never discard the old key until a restored copy is tested.

For a compromised device, revoke it, preserve audit/events, rotate related tokens, inspect nonce/auth failures, and reenroll only after factory reset. For a server compromise, isolate it, preserve logs, rotate TLS/session/master/device credentials as applicable, restore a verified backup, and let microSD history backfill from the restored cursor.

Report vulnerabilities privately to the deployment owner; this standalone source distribution does not configure a public disclosure inbox.

Emergency administrator recovery requires a trusted API workload console and
the audited `/srv/tools/recover-admin.py` utility described in
[Users & Access](USER_MANAGEMENT.md). It acts only on an existing account,
accepts replacement passwords through a private interactive prompt, revokes all
sessions, and has no web endpoint or static recovery credential.

Editable interface values are restricted to the code-backed catalog. The API
rejects markup/templates, control characters, unknown keys, invalid lengths,
credential-bearing URLs, and unsafe schemes. Public login delivery exposes only
public published values; drafts, editor identity, audit data, users, and private
settings are excluded. Compiled frontend defaults preserve a usable sign-in
form when delivery fails. Security-critical authentication messages are not
editable.

## Presentation and accessibility

Removing protocol or security-status wording from the dashboard shell is a
presentation change only. The `pm-protocol/1.0.0` compatibility checks, signed
heartbeats, credential isolation, authentication, authorization, CSRF, security
headers, and local storage behavior remain enforced by the server.

Pointer focus uses a compact border/shadow treatment without changing element
geometry. Keyboard focus uses a clearly visible two-pixel `:focus-visible`
outline with offset. Native controls, search inputs, dialogs, and menu controls
retain their labels, focus order, dialog semantics, and keyboard operation.
## Utility account and sensor network controls

Utility-account mutations use `utility_accounts.manage`; effective rate assignment additionally
uses `rates.assign`. Account-number suffixes are optional and the full account number is not
stored, returned, or logged. Rate assignments remain effective-dated and audit records contain
identifiers and configuration categories rather than bill credentials.

Sensor policies use `network.view` and `network.manage`. Canonical address parsing unwraps
IPv4-mapped IPv6 before policy evaluation. Forwarded client addresses are accepted only when the
direct peer is inside `TRUSTED_PROXY_CIDRS` (defaulting to the private Docker bridge range); the
right-most forwarded client is then evaluated. CIDR policy adds defense in depth and never
replaces TLS, device credentials, signed bodies, timestamps, nonces, or replay prevention.
