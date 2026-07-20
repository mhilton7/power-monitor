# Backup and restore

`scripts/backup.sh` runs a PostgreSQL 17 tool container. It creates a mode-077 atomic incomplete directory, writes a custom zstd-compressed logical dump plus firmware/config/report archives, verifies the dump catalog, writes SHA-256 checksums and a JSON manifest, atomically renames the directory, records status in `backup_runs`, and enforces bounded retention under `/data/backups`.

Verification is mandatory:

```bash
path=$(./scripts/backup.sh | tail -1)
./scripts/verify-backup.sh "$path"
```

The verifier rechecks every hash, creates a uniquely named temporary database, restores with `--exit-on-error`, confirms the Alembic revision and table inventory, records verification, and drops only that temporary database. A dump is not called verified merely because `pg_dump` succeeded.

For disaster recovery, preserve `.env`/master key separately, provision a clean host, stop API/worker, inspect and verify the backup, then run `./scripts/restore.sh BACKUP_DIR TARGET_DATABASE` (PowerShell requires `-ConfirmDestructiveRestore`). Restore firmware/config/report archives into their named volumes, apply migrations, start services, inspect readiness and audit, then allow each device's microSD backlog to fill records beyond the restored contiguous cursor.

The systemd unit/timer runs nightly with randomized delay. Test recovery quarterly. Copy verified backup sets off-host. Optional at-rest encryption should wrap the completed directory with `age` or AES-256 using a key stored outside the repository; decrypt to a protected temporary directory before verification.
