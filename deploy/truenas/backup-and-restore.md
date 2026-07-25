# Backup and restore on TrueNAS

The `backup` service remains running as a scheduler. At `02:17` UTC each night by
default it creates a PostgreSQL custom-format logical dump plus firmware, config,
report, and archived SCE rate-source evidence archives. It encrypts every artifact when
`BACKUP_ENCRYPTION_KEY_FILE` is configured (the TrueNAS deployment always
configures it), writes SHA-256 checksums and a manifest, atomically publishes the
completed directory, and removes backups older than 30 days.

Immediately after creation, the scheduler verifies checksums, decrypts the dump
in tmpfs, restores it into a newly created temporary PostgreSQL database, checks
the Alembic revision and table inventory, verifies that the published status-layout
pointer resolves to one of the restored immutable revisions, records verification
in `backup_runs`, and drops the temporary database. A backup is not operationally
accepted until its status is `verified`.

## On-demand verified backup

Use **Settings → Data & Backups → Create verified backup** in Power Monitor.
The API records an audited, idempotent request and the isolated `backup`
service claims it using its file-backed database password. The browser never
receives a database password, encryption key, or host path.

1. Submit the request and wait for the row to report **Completed / Verified**.
2. Use **Verify restore** on that row to enqueue a non-destructive restore
   preflight. This rechecks the selected artifact; it never overwrites the live
   database.
3. Copy the completed `power-monitor-YYYYMMDDTHHMMSSZ` directory off-system and
   verify its `checksums.sha256` after transfer. Store the encryption key
   separately; losing it makes the encrypted backup unrecoverable.

ZFS snapshots complement logical dumps but do not replace them. Take coordinated
snapshots before upgrades and replicate both snapshot and logical-backup data to
a different failure domain.

The `/mnt/Apps/Power/power-monitor/logs` dataset is intentionally separate from
the logical database dump. Snapshot or replicate it when operational log history
must survive a pool-level disaster. Administrators can download a redacted,
checksummed ZIP for any available range from **Settings → Data & Backups** without
opening a TrueNAS workload shell.

`rate-source-artifacts.tar.gz` (or its encrypted `.enc` form) is included in
the checksum manifest. It preserves the exact downloaded bytes, metadata,
normalized extraction, validation output, and hashes linked to rate versions.
This archive also includes retained private utility-bill originals and
sanitized evidence, so off-system copies require the same confidentiality
controls as the database and must remain encrypted.

## Test restore

From **Apps > Installed > power-monitor > Workloads**, open the `backup` workload
shell. The shell is provided by the managed App workload; do not use the TrueNAS
host shell or invoke the Docker CLI.

```text
/srv/scripts/verify-backup-container.sh /data/backups/power-monitor-TIMESTAMP
/srv/scripts/restore-container.sh /data/backups/power-monitor-TIMESTAMP power_monitor_restore_test --yes
psql -h postgres -U power_monitor -d power_monitor_restore_test -c "SELECT version_num FROM alembic_version"
```

The password is loaded from `PGPASSWORD_FILE`; do not paste it into the shell or
command history. Delete a test database after the validation window using the
same controlled workload shell.

## Production recovery

1. Stop gateway, API, and worker by stopping the App in the TrueNAS UI. Preserve
   the failed datasets and logs before modifying anything.
2. Restore into a clean, differently named database first and verify checksums,
   migration revision, device/heartbeat/reading counts, and a sample SCE result.
3. During an approved maintenance window, restore to a new production database or
   replace the database dataset from the coordinated snapshot. Do not restore over
   a database with active connections.
4. Restore firmware/config and `rate-source-artifacts` datasets from the
   matching backup generation when consistency requires it. Verify the manifest
   before extraction and restore rate evidence only to the dedicated dataset,
   never to a public web directory.
5. Start the immutable application release compatible with that migration,
   verify all health checks, then take a new verified backup.

`manifest.json` uses `power-monitor-backup/v2`. Checksums cover ciphertext so
corruption is detected before decryption; the encryption passphrase never appears
in the manifest, logs, or backup directory.
