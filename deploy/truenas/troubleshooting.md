# TrueNAS troubleshooting

Use **Apps > Installed > power-monitor > Workloads** for status, logs, and a
specific workload shell. Do not manage this production stack with direct Docker
commands in the TrueNAS host shell.

## App cannot be saved or images do not pull

- Run template validation, then deployment validation with the exact pool and
  gateway port. TrueNAS performs only basic YAML checks; the repository validator
  catches security and topology mistakes.
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
or inherited ACLs stripping read/traverse access from secret files. Correct ACLs
through **Datasets > Permissions**; never solve the problem with mode 0777.

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

## Devices push heartbeats but worker cannot backfill

Signed heartbeats are the primary address source. Confirm the newest accepted
heartbeat has the expected RFC1918 `current_ip`, and that the site's allowed CIDRs
include that LAN/VLAN. The worker joins the normal `public` bridge network so
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
