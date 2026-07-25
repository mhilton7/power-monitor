# TrueNAS troubleshooting

## Utility-bill import or OCR fails

- If the interface reports an incompatible release, update API, frontend,
  worker/migration, and backup image references together from one rendered
  release. Do not bypass the compatibility check or reuse a frontend digest
  with an older API digest.
- In **Administration > Security > System Health**, compare frontend and
  backend release/commit values and confirm both report
  `utility-account-rate-context/1.0`. A stale browser asset should clear after
  the complete App redeploy; a persistent mismatch means the YAML still mixes
  image releases.
- An account without an assigned plan and no selected account are both valid
  import states. Use the visible Retry action for a transient context request.
  A correlation ID can be matched to redacted API logs without exposing the
  bill or account number.
- Confirm the API workload uses the current immutable image digest. The release
  image contains `pdftoppm`, Tesseract, and English language data.
- Confirm UID 10001 has Modify/traverse/inherit access to
  `/mnt/Apps/Power/power-monitor/rate-source-artifacts`.
- If upload returns an internal-server error before Review appears, re-open the
  dataset ACL in **Datasets > Permissions** and confirm UID 10001 can create a
  temporary file in the dataset through the API workload. A missing inherited
  Modify ACE prevents the original PDF from being retained and therefore
  prevents extraction from starting.
- Review the API workload log for a redacted error code. Bill text and account
  identity are intentionally omitted from ordinary logs.
- Encrypted/password-protected PDFs are rejected by design. Export a
  password-free copy locally; do not put a password into Compose or a secret.
- Do not increase OCR page, DPI, memory, or timeout limits without reviewing the
  TrueNAS API workload resource limit.

Use **Apps > Installed > power-monitor > Workloads** for status, logs, and a
specific workload shell. Do not manage this production stack with direct Docker
commands in the TrueNAS host shell.

## App cannot be saved or images do not pull

- Run template validation, then deployment validation with the exact pool and
  gateway port. TrueNAS performs only basic YAML checks; the repository validator
  catches security and topology mistakes.
- For this installation, render with `--pool Apps` and confirm every host path
  begins with `/mnt/Apps/Power/power-monitor/`.
- A valid release image contains a semver tag and `@sha256:` digest. Application
  images use the `ghcr.io/mhilton7/power-monitor-*` namespace. Resolve all-zero
  digests and `POOL` before installation.
- Confirm the registry package is public or that TrueNAS has approved registry
  credentials. Confirm the manifest includes `linux/amd64`.

## Migration blocks the release

This is intentional. Inspect `postgres`, then `migrate` logs. Correct database
permissions, a missing secret, or the migration error and redeploy. Never remove
`service_completed_successfully`, start API manually, or mark a failed migration
healthy. Use the pre-upgrade backup/snapshots if the migration partially changed
data.

## Permission denied

Compare the workload UID/GID with [permissions.md](permissions.md). Common causes
are UID 999 missing from `postgres`, UID 10002 missing from Caddy state datasets,
UID 10001 or 10003 missing Modify access on `logs`, or inherited ACLs stripping
read/traverse access from secret files. Correct ACLs
through **Datasets > Permissions**; never solve the problem with mode 0777.

## Application logs are empty or cannot be downloaded

- Confirm `/mnt/Apps/Power/power-monitor/logs` exists and is mounted at
  `/data/logs` for API, worker, and backup.
- Confirm numeric UIDs 10001 and 10003 both have inherited Modify access.
- Use **Administration > Backups** to choose a range within the displayed 90-day
  window. A `413` response means the configured export limit was reached; select
  a smaller range or one service.
- A completed download is deleted from temporary server storage. Prepare it again
  if the browser reports that the short-lived export has expired.

## Gateway unhealthy or browser TLS warning

- Confirm `config/Caddyfile` is a regular readable file and the selected mode
  matches the secret files and site address.
- For internal mode, export only
  `caddy-data/caddy/pki/authorities/local/root.crt` and install it in every client
  trust store. A hostname mismatch means DNS/site-address configuration is wrong;
  it is not a reason to disable verification.
- For user-certificate mode, confirm the PEM chain includes intermediates, the key
  matches, and SANs include the configured hostname.
- For public ACME, public DNS must resolve correctly and external TCP 443 must
  reach gateway TCP 443. The default external port 8443 is for LAN deployment.

## Chrome does not offer or fill a saved login

- Open the exact HTTPS origin configured in `PUBLIC_ORIGIN`, including its port.
  A raw IP, HTTP URL, alternate hostname, or alternate port is a different
  password-manager context.
- Confirm Chrome password saving/autofill is enabled and that this exact site is
  not in the never-save list. Enterprise policy may override local settings.
- Inspect or remove only the incorrect entry for this synthetic/test account in
  Google Password Manager, then sign in at the same origin and save it again.
- Complete the manual procedure in
  [Browser compatibility](../../docs/BROWSER_COMPATIBILITY.md). Do not disable
  TLS verification or copy a production password into logs or screenshots.

## Devices push heartbeats but worker cannot backfill

Signed heartbeats are the primary address source. Confirm the newest accepted
heartbeat has the expected RFC1918 `current_ip`. In **Administration > Server &
network**, select the site and inspect **Server pull access**. Explicit deny-all
blocks every pull; listed mode requires an enabled CIDR containing that LAN/VLAN;
all-private mode accepts RFC1918/ULA but still rejects unsafe classes. Use **Test
sensor IP** to evaluate policy without scanning. The worker joins the normal `public` bridge network so
outbound bridge/NAT egress to RFC1918 networks is expected; PostgreSQL remains on
the separate internal network.

Check TrueNAS host routing and VLAN/firewall policy for outbound TCP from the Apps
bridge to the sensor API port and return traffic. Do not rely on mDNS. ICMP is not
required; enable the NET_RAW overlay only for optional diagnostics. Never use host
networking or privileged mode to bypass a routing problem.

## Backups fail or cannot be restored

- Confirm UID 10003 can modify `backups` and read firmware/config/report data.
- An encrypted backup requires the original `backup_encryption_key`. Do not rotate
  that key until all retained backups using it expire or are re-encrypted.
- A checksum failure means the copy is corrupt or incomplete. Do not restore it.
- A verification restore must report a migration revision and more than 20 public
  tables. Preserve a failed backup for investigation; use the prior verified one.

## Unexpected host ports

The App UI must show only the configured gateway mapping, normally TCP 8443 to
container TCP 443. PostgreSQL 5432, API 8000, frontend 8080, worker, migration,
and backup must have no host publication. Re-render from the checked-in template
if any other host port appears.
