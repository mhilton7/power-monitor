# Upgrade and rollback

Every upgrade is an immutable-image replacement. Never change an existing tag in
the registry, use `latest`, or deploy a tag without its content digest.

## Pre-upgrade checkpoint

1. If the installed release predates durable application logs, use **Datasets**
   to create `/mnt/Apps/Power/power-monitor/logs` before editing the App. In **Permissions >
   Edit ACL**, add inherited numeric user ACEs for UID 10001 and UID 10003 with
   Modify, traverse, and inherit access. Do not grant `Everyone@` write access.
2. In **Apps > Installed > power-monitor**, record the current complete image
   references and save a protected copy of the current YAML.
3. Trigger and verify a fresh logical backup without using the TrueNAS shell:
   edit the App YAML in the UI, set `BACKUP_RUN_ON_STARTUP: "true"`, save, and
   wait for the backup workload log to report a verified backup. Return the value
   to `"false"` and save again. Confirm the new verified run in the application
   Backups view.
4. In **Datasets**, create recursive ZFS snapshots of the eight application
   datasets. Name them with the release and UTC time, for example
   `pre-upgrade-1.1.0-20260720T220000Z`.
5. Replicate or copy the verified logical backup and checksum manifest off the
   TrueNAS system. Keep the backup encryption key in a separate protected store.
6. Render and validate the new YAML with the new semver tags and digests. Review
   release notes and migration compatibility before saving it.

## Upgrade

1. Open **Apps > Installed > power-monitor > Edit > YAML**.
2. Replace only the approved image references and intentional configuration
   changes. Keep the previous YAML available for rollback.
3. Save. PostgreSQL starts first, `migrate` applies Alembic changes once, and API,
   worker, backup, and gateway cannot report healthy unless migration exits 0.
4. Verify every health indicator, the migration log, sign-in, fleet heartbeats,
   historical readings, an SCE rate preview, and the next verified backup.

If migration fails, do not bypass its dependency or point the old application at
a partly migrated database. Preserve logs and follow the rollback path.

## Rollback

1. Stop the App through **Apps > Installed**.
2. If the failed release did not change persistent data, edit YAML back to the
   exact prior image digests and prior configuration, then start it and verify.
3. If a migration or application write changed persistent data, keep the App
   stopped. Use **Datasets > Snapshots** to clone the pre-upgrade snapshots for
   forensic retention, then roll back the affected datasets as one coordinated
   checkpoint. Destructive snapshot rollback must be approved and must not be
   performed while containers are running.
4. When logical recovery is required, keep API/worker/gateway stopped in the App
   UI and use **Workloads > backup > Shell** to run the documented
   `restore-container.sh BACKUP_DIR TARGET_DATABASE --yes` operation. Restore into
   a clean temporary database first and validate it; replace the production
   database only during the approved maintenance window.
5. Restore the prior YAML with its exact digests, start the App from the UI, and
   repeat all post-upgrade checks.

Do not attempt an undocumented Alembic downgrade. The supported rollback anchors
are the prior immutable images, the verified pre-upgrade logical backup, and the
coordinated ZFS snapshots.
