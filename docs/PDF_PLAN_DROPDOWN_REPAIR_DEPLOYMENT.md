# Normalized bill and rate-plan lifecycle deployment

This release advances the database to append-only Alembic revision
`20260725_0016`. It does not change the ESP32 protocol, services, host ports,
container identities, secrets, datasets, networks, or TrueNAS ACL model.

## Upgrade

1. In the Power Monitor Backups view, generate and verify a fresh logical
   backup. Copy the encrypted backup and checksum manifest off the NAS.
2. In TrueNAS Datasets, take recursive ZFS snapshots of every Power Monitor
   dataset.
3. Preserve the currently installed App YAML and every current image digest.
4. Render the release YAML with one matching immutable version and digest set.
   Validate the rendered file before pasting it into **Apps > Installed >
   power-monitor > Edit > YAML**.
5. Save through the TrueNAS web interface. The one-shot `migrate` service must
   exit successfully before API and worker startup.
6. Confirm every long-running service is healthy and migration head is
   `20260725_0016`.
7. Upload the sanitized test bill. Confirm recognized values are grouped,
   missing optional values are separate, required missing values block Apply,
   and evidence/normalized diagnostics are readable.
8. Verify Replace, Remove from Electric Service, Retire, Remove, Removed, and
   Restore against a non-production plan. Historical assignments, costs,
   reports, bill/source evidence, and audit records must remain available.
9. Generate and verify the first post-upgrade logical backup.

## Rollback

If the migration or health checks fail, do not force the new API healthy.
Restore the saved previous App YAML and immutable image digests in the TrueNAS
web interface. When application rollback alone is insufficient:

1. stop the App from the web interface;
2. restore the pre-upgrade ZFS snapshots, or restore the verified logical
   backup into a clean PostgreSQL dataset with its matching encryption key;
3. restore the previous App YAML and digest set;
4. start the App and verify its documented migration head and health checks.

Alembic downgrade is tested for development/CI compatibility, but production
rollback should use the snapshot or verified logical backup so normalized
artifacts, extraction revisions, assignments, and audit history remain
transactionally consistent.

## Verification and limitations

- Reprocessing requires the retained original PDF. It creates another
  extraction revision; it never rewrites a confirmed or published revision.
- A required value that is genuinely absent remains missing and blocks Apply.
  The interface does not invent a value or convert absence into confidence.
- Restore makes a removed plan available; it intentionally does not recreate
  an assignment.
- Retire or remove remains blocked while active or future assignments exist.
- Browser downloads for normalized JSON, redacted text, and evidence require
  an authenticated administrator session.
- No firmware file or shared device-protocol identifier changed.
