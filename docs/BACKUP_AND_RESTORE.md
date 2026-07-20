# Backup and restore

`scripts/backup.sh` runs a PostgreSQL 17 tool container. It creates a mode-077 atomic incomplete directory, writes a custom zstd-compressed logical dump plus firmware/config/report and rate-source-evidence archives, verifies the dump catalog, writes SHA-256 checksums and a JSON manifest, atomically renames the directory, records status in `backup_runs`, and enforces bounded retention under `/data/backups`.

Verification is mandatory:

```bash
path=$(./scripts/backup.sh | tail -1)
./scripts/verify-backup.sh "$path"
```

The verifier rechecks every hash, creates a uniquely named temporary database, restores with `--exit-on-error`, confirms the Alembic revision and table inventory, records verification, and drops only that temporary database. A dump is not called verified merely because `pg_dump` succeeded.

For disaster recovery, preserve `.env`/master key separately, provision a clean host, stop API/worker, inspect and verify the backup, then run `./scripts/restore.sh BACKUP_DIR TARGET_DATABASE` (PowerShell requires `-ConfirmDestructiveRestore`). Restore firmware/config/report and `rate-source-artifacts.tar.gz` archives into their named volumes, apply migrations, start services, inspect readiness and audit, then allow each device's microSD backlog to fill records beyond the restored contiguous cursor.

The systemd unit/timer runs nightly with randomized delay. Test recovery quarterly. Copy verified backup sets off-host. Optional at-rest encryption should wrap the completed directory with `age` or AES-256 using a key stored outside the repository; decrypt to a protected temporary directory before verification.

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
