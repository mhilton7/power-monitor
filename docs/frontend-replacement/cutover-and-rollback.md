# Single Home frontend cutover and rollback

The frontend replacement does not change the ESP32 protocol, sensor firmware,
raw readings, PostgreSQL identities, rate engine, PDF parser, alerts, backup
records, or audit history.

## Before cutover

1. Confirm only one home is active. Disabled and removed homes may remain.
2. From **Settings → Data & Backups**, request **Back up now** and wait until the
   isolated backup service reports a verified run. Copy its encrypted artifacts
   and checksum manifest off-system.
3. In TrueNAS **Datasets**, take recursive ZFS snapshots of all Power Monitor
   datasets.
4. Record the complete old API, frontend, and backup image references including
   SHA-256 digests, and save the installed App YAML.
5. Build and test the new frontend image. The build must report `no legacy
   modules`.
6. Run the feature-parity, route, accessibility, visual, backend regression,
   migration, container, and Compose gates.

## TrueNAS production cutover

Publish all release images under one immutable semantic version. Render
`deploy/truenas/compose.yaml` with the resulting digest-pinned references.
Through **Apps → Installed → power-monitor → Edit → YAML**, paste the validated
YAML and save it. Do not manage the production App with direct Docker commands.

The frontend service name, internal port, gateway route, health check, runtime
UID/GID, secrets, datasets, and external gateway port are unchanged. Only the
contents of the frontend image change. Migration must exit successfully before
the API and worker can start.

After save:

1. Confirm all services are healthy and only the gateway port is published.
2. Sign in, then open Home, History, Billing, and Settings.
3. Confirm an old `/devices` bookmark lands on Settings → Sensors and an old
   `/rates` bookmark lands on Billing.
4. Confirm the global alert drawer, live data, exact history cost, active rate,
   PDF review, users, notifications, sensor enrollment, and backup evidence.
5. Confirm a signed sensor heartbeat and historical backfill without changing
   firmware.

## Rollback

If a frontend-only defect appears and no newer database write must be retained,
edit the TrueNAS App YAML and restore the complete prior digest-pinned image set.
If the release introduced or wrote a newer schema, stop application writes,
follow `deploy/truenas/backup-and-restore.md`, restore the pre-upgrade logical
backup or ZFS snapshots, and then restore the old image set. Never run an older
application against a database that has accepted incompatible newer writes.

The old source directory is not a runtime rollback mechanism. Rollback uses the
previous immutable production image and saved YAML.
