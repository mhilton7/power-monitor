# Backup and restore

The production `backup` workload is the only backup scheduler. The browser and
nightly schedule submit database-backed jobs; opening or refreshing Data &
Backups performs GET requests only. A partial unique index permits only one
global backup operation in `queued` or `running` job state, while a second
partial unique index makes manual and nightly idempotency keys durable across
container restarts.

The state flow is:

```text
Queued -> Creating backup -> Verification pending -> Verification queued
       -> Verifying -> Verified
```

Creation uses a mode-077 atomic incomplete directory, a PostgreSQL 17 custom
zstd dump, firmware/config/report/rate-source-evidence archives, SHA-256
checksums, and `manifest.json`. The final directory is atomically published and
stored in the database by its relative `power-monitor-TIMESTAMP-RUNPREFIX`
identifier. Raw host paths are never returned to the browser.

Every completed backup automatically receives exactly one verification job.
The verifier resolves the identifier below `/data/backups`, rejects symlinks
and traversal, checks the manifest and every artifact checksum, decrypts into
tmpfs when configured, creates a uniquely named temporary PostgreSQL 17
database, restores with `pg_restore --exit-on-error`, confirms the Alembic
revision, required table inventory, and status-layout revision integrity, and
drops only that temporary database. A dump is never called verified merely
because `pg_dump` or `pg_restore --list` succeeded.

Failures become `Backup failed` or `Verification failed` and retain a safe
stage, error code, summary, attempt count, and exit code. Administrators can
use **Verify now** or **Retry verification**. **Restore** is enabled only for a
verified row.

Deletion requires the word `DELETE`, the row's eight-character ID prefix, and
a reason. The service atomically moves the directory below `.trash`, removes
only that validated directory, and keeps the audit row. The last verified
backup is protected. Retention uses the same queued deletion path and therefore
cannot remove the final verified restore point.

For local development, the tools profile returns a backup run UUID:

```bash
run_id=$(./scripts/backup.sh | tail -1)
./scripts/verify-backup.sh "$run_id"
./scripts/restore.sh "$run_id" power_monitor_restore_test
```

The restore target must be a separate test database. Never run a destructive
restore over production.

Existing records can be inspected without mutation:

```bash
python tools/reconcile_backups.py --dry-run --backup-root /data/backups
```

`--apply` may mark proven missing/invalid artifacts failed, mark abandoned
unowned work interrupted, and queue at most one valid stuck backup for
verification. It reports orphaned, incomplete, trash, and duplicate artifacts;
it never bulk-deletes them.

For disaster recovery, preserve `.env`/master key separately, provision a clean host, stop API/worker, inspect and verify the backup, and restore by backup run UUID into a clean database. Restore firmware/config/report and `rate-source-artifacts.tar.gz` archives into their named volumes, apply migrations, start services, inspect readiness and audit, then allow each device's microSD backlog to fill records beyond the restored contiguous cursor.

The production scheduler runs nightly at `BACKUP_SCHEDULE_UTC` (02:17 UTC by
default) with a stable per-day idempotency key. Test recovery quarterly and copy
verified encrypted backup sets off-host.

## Application-log exports

API, worker, enrollment, device/rate synchronization, backup, and restore events
are also written as daily structured files under `/data/logs`. Completed days are
compressed and files outside the 90-day window are removed without deleting the
current day. This dataset is separate from logical backup artifacts.

Administrators use **Administration > Backups > Application logs** to select a
date range and service. The default is the last seven days. The server prepares a
bounded temporary ZIP, re-redacts every line, adds `manifest.json` with SHA-256
hashes and the administrator audit identifier, streams it to the browser, and
deletes the temporary archive after delivery. Snapshot or replicate the logs
volume separately if log history is part of disaster-recovery policy.
