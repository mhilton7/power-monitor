# Operations

Use `/health/live` only for process restart decisions and `/health/ready` for database/migration readiness. Worker container health requires a successful scheduler loop within 45 seconds. Frontend and Caddy have independent checks. Protected `/api/v1/metrics` exposes fleet/readings/heartbeat/alert counters; daily JSON logs include request correlation and recursively redact passwords, authorization values, cookies, tokens, device secrets, HMAC material, private keys, and sensitive connection strings before persistence.

Update with `scripts/update.sh` or `.ps1`: build pulled bases, run Alembic, replace containers, and inspect `docker compose ps`. Take and verify a backup first. Never downgrade schema without a reviewed downgrade path.

Daily: inspect device backlog, active alerts, worker progress, last verified backup, disk, and PostgreSQL connections. Weekly: inspect gaps, auth failures, report/firmware storage, and failed notifications. Monthly: `VACUUM (ANALYZE)` through normal autovacuum review, check index/table growth, verify a restore, and check current rate sources. Retain raw readings according to policy; do not delete them merely because rollups exist.

Rate changes create new effective versions, preview tests, and explicit activation. Used versions remain immutable. Monitor named-volume free space and keep backup headroom.

## Application logs

The API, worker, enrollment/device synchronization paths, and backup tooling
write durable daily structured logs under `/data/logs`. Completed days may be
gzip-compressed, files older than 90 days are removed, and the current day's log
is never removed by rotation. The Docker volume and TrueNAS logs dataset keep
these files across container replacement or restart.

Administrators download logs from **Administration > Backups > Application
logs**. Choose an available range and service; the default is the most recent
seven days. The server creates a bounded temporary ZIP containing a README,
manifest, per-file SHA-256 hashes, and the selected redacted records. Export
creation and download are audited. The temporary archive is deleted after
download and the log directory is never served as static content.

Treat an exported archive as operationally sensitive even though credentials
are redacted. Store it only as long as a support or incident workflow requires.
TrueNAS administrators must grant the documented API, worker, and backup UIDs
write access to the dedicated logs dataset.

For the standard named-volume deployment, the API image initializes the logs
directory group-writable by GID `10001` and the backup container receives that
supplementary group only for this shared append-only logging path. Its primary
runtime identity remains UID/GID `10003`; other application-data mounts remain
read-only in the backup service.

No ICMP capability is granted by default. Enable it only after a documented diagnostic need; TCP/API evidence remains primary.

## Sensor lifecycle

Use **Sensor Enrollment > Claimed sensors > Remove sensor** to unclaim failed,
replaced, moved, duplicate, or test hardware. Removal revokes every device
credential, stops polling and synchronization, clears the active circuit
assignment, and hides the sensor from active views and fleet counts. It does
not delete raw readings, summaries, calculated costs, alerts, firmware history,
site context, lifecycle history, or audit records. Removed sensors remain under
the **Archived** filter with the actor, timestamp, reason, and retained-history
status.

To reuse the same physical sensor, create a new single-use enrollment token and
claim it again. The server retains the device UUID and history but advances its
lifecycle generation and returns a newly generated credential; a revoked secret
is never reused.

## SMTP notifications

Administrators configure email under **Administration > Notifications**. Use
STARTTLS or implicit TLS for authenticated relays, enter one or more recipients,
choose the alert types delivered to that channel, save, and send a test message.
The SMTP password is encrypted with `APP_MASTER_KEY`, is redacted from API
responses and logs, and may be left blank during an edit to retain the saved
credential. Disabling a channel stops new attempts without deleting delivery
history.

The sensor-disconnect trigger uses accepted signed heartbeat arrival time. Its
delay controls how long a sensor may be silent before the alert becomes active.
The power-surge trigger uses the latest signed watt measurement and becomes
active only after the configured threshold persists for the debounce period.
Maintenance windows continue to suppress delivery while retaining evidence.
These alerts are operational telemetry, not protective electrical controls; do
not use them in place of correctly rated breakers, surge protection, or qualified
electrical inspection.
