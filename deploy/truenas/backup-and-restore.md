# Backup and restore on TrueNAS

The `backup` service remains running as a scheduler. At `02:17` UTC each night by
default it queues one idempotent PostgreSQL custom-format logical dump plus firmware, config,
report, and archived SCE rate-source evidence archives. It encrypts every artifact when
`BACKUP_ENCRYPTION_KEY_FILE` is configured (the TrueNAS deployment always
configures it), writes SHA-256 checksums and a manifest, atomically publishes the
completed directory. Retention is processed through the protected deletion
state machine; it never removes the final verified backup.

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

1. Submit the request and follow **Queued**, **Creating backup**,
   **Verification queued**, and **Verifying** until the row reports
   **Verified**.
2. Use **Restore** on that row to enqueue a non-destructive restore
   preflight. This rechecks the selected artifact; it never overwrites the live
   database.
3. Copy the completed `power-monitor-YYYYMMDDTHHMMSSZ-RUNPREFIX` directory off-system and
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

## Verification and safe cleanup

Use **Settings > Data & Backups** in the Power Monitor interface. **Verify now**
and **Retry verification** always restore into an isolated temporary PostgreSQL
database; they do not overwrite live data. **Details** shows the migration
revision, table count, attempts, encryption state, manifest fingerprint, and
safe failure evidence.

Deletion requires `DELETE`, the displayed eight-character backup ID prefix, and
a reason. The final verified backup is blocked from deletion. Missing artifacts
can be cleaned up through the same action without granting the browser direct
filesystem access. Manage the production App lifecycle through the TrueNAS Apps
web interface; do not use direct Docker commands in the TrueNAS host shell.

## Replace every backup safely

The owner-only **Advanced backup actions > Replace all backups** workflow is
the supported way to consolidate the backup dataset. It is intentionally not
implemented as a bulk-delete button:

1. The server inventories the currently visible database records and storage
   usage and requires the exact confirmation `REPLACE ALL BACKUPS`.
2. The backup service creates a separate encrypted replacement generation,
   publishes it atomically, verifies every checksum, and restores the
   PostgreSQL dump into a temporary database.
3. Only after that restore succeeds does the service tombstone and remove the
   exact older backup IDs captured at the start. The replacement ID is never
   part of the deletion set.
4. Final checks require one non-deleted, verified database record, one
   completed backup directory, and no incomplete or trash entries.

Creation or verification failure preserves every old recovery point. If one
old artifact cannot be removed, the verified replacement remains available,
the failed row remains visible as **Cleanup failed**, and **Retry cleanup**
retries only that confined artifact. Refreshing or closing the browser does
not cancel the server-side operation. Its progress and audit result remain in
PostgreSQL.

Do not delete dataset contents in the TrueNAS shell to imitate this operation.
Manual deletion bypasses path confinement, audit tombstones, final-backup
protection, and restore verification. Take a ZFS snapshot before using the
workflow when an additional pool-level rollback point is desired.

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
