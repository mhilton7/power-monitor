# Operations

Use `/health/live` only for process restart decisions and `/health/ready` for database/migration readiness. Worker container health requires a successful scheduler loop within 45 seconds. Frontend and Caddy have independent checks. Protected `/api/v1/metrics` exposes fleet/readings/heartbeat/alert counters; JSON logs include request correlation and redact secret fields.

Update with `scripts/update.sh` or `.ps1`: build pulled bases, run Alembic, replace containers, and inspect `docker compose ps`. Take and verify a backup first. Never downgrade schema without a reviewed downgrade path.

Daily: inspect device backlog, active alerts, worker progress, last verified backup, disk, and PostgreSQL connections. Weekly: inspect gaps, auth failures, report/firmware storage, and failed notifications. Monthly: `VACUUM (ANALYZE)` through normal autovacuum review, check index/table growth, verify a restore, and check current rate sources. Retain raw readings according to policy; do not delete them merely because rollups exist.

Rate changes create new effective versions, preview tests, and explicit activation. Used versions remain immutable. Monitor named-volume free space and keep backup headroom. Container logs use the platform's rotation; configure Docker's `local` driver or journald limits on the host.

No ICMP capability is granted by default. Enable it only after a documented diagnostic need; TCP/API evidence remains primary.
