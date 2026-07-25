# Single-pass repair deployment

## Production evidence

The repository production Dockerfiles built successfully. The local images
used for final parity were:

- API/worker: `sha256:f24c84168e11c74ea8427a6db7680740ad780016420875677c6c4f6f66097302`
- frontend: `sha256:bdf5736efc50e6229d4bbb9fb320bcc5631dfd9371009f36d083ba1231659041`
- backup: built from `deploy/docker/backup.Dockerfile`

The frontend parity container ran read-only, with all capabilities dropped,
`no-new-privileges`, and loopback-only temporary publication. All 24 repair
browser tests passed against that exact nginx image.

The normal Compose stack reported API, worker, PostgreSQL, frontend, and
Caddy healthy. PostgreSQL, API, worker, and frontend had no published host
port. The isolated rendered TrueNAS deployment started gateway, frontend,
API, worker, one-shot migration, PostgreSQL, and backup services; migration
completed before API/worker health.

## TrueNAS-equivalent acceptance run

The deployment was rendered with:

- absolute host root `/mnt/Apps/Power/power-monitor`;
- gateway TCP 8443 only;
- immutable application image references;
- file-backed secrets;
- separate public and internal database networks;
- the normal non-ICMP worker profile.

The structural validator passed the template, optional `NET_RAW`-only ICMP
overlay, and rendered deployment. The workflow then enrolled three simulated
devices, accepted signed heartbeats, backfilled 90 readings, evaluated rate
and utility-account paths, generated encrypted/checksummed logical backups,
verified the archive, restored it into a clean database, and confirmed that
only gateway port 8443 was published.

## TrueNAS web-interface redeploy

1. Publish the API, frontend, and backup images under a new immutable version
   and record their registry digests. Do not reuse an existing tag.
2. Generate a fresh deployment YAML with those digests and the required
   `/mnt/Apps/Power/power-monitor` dataset root.
3. Run the deployment validator with pool `Apps` and gateway port `8443`.
4. In TrueNAS, create a ZFS snapshot and verify a current logical backup
   before changing the app.
5. Open **Apps**, select the Power Monitor custom app, choose **Edit**, replace
   the YAML with the validated rendered YAML, and save.
6. In the TrueNAS web interface, confirm migration completed successfully and
   all long-running services are healthy. Open the gateway URL and verify
   Home, bill import, Advanced Rate Settings, and History.

Do not manage the production custom app with direct Docker commands in the
TrueNAS shell.

## Rollback

If the release does not become healthy:

1. retain the failed-release logs and migration status;
2. restore the pre-upgrade application YAML containing the previous immutable
   image digests through the TrueNAS app editor;
3. if schema/data rollback is required, stop the app through the web
   interface, restore the verified logical backup or the pre-upgrade ZFS
   snapshot according to the documented TrueNAS restore procedure, then
   restart the previous app version;
4. verify API/worker/gateway health and a signed device heartbeat before
   declaring rollback complete.

No repository push, registry publication, or live TrueNAS mutation was
performed by this repair task.
