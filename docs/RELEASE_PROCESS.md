# Immutable release process

Production releases use two commits so release evidence can name the exact
source commit without a self-referential Git hash.

1. Finish source changes, migrations, contracts, tests, and documentation.
   Commit that clean tree and record its full commit ID. This is the **source
   commit**.
2. From that clean commit, run `scripts/release.ps1` or `scripts/release.sh`
   with a previously unused semantic version. The scripts reject dirty source,
   require Alembic head `20260803_0030`, require Node.js `v24.4.0` with npm
   `11.4.2`, and label every
   locally built image with the source commit.
3. The scripts run load, PostgreSQL upgrade/downgrade, History performance,
   frontend, contract, image-build, isolated seven-service TrueNAS, backup, and
   clean-database restore gates. The TrueNAS test uses a unique Compose project,
   temporary volumes, and automatic cleanup; it never starts the shared
   development stack.
4. Review and commit only the generated `release/` evidence. Do not change
   application source in this evidence commit. Add the required
   `docs/releases/<version>.md` report before freezing source rather than after
   the validation run.
5. Push a `v<version>` tag on the evidence commit. The publish workflow verifies
   that the requested version matches `release/versions.json`, the recorded
   source commit is an ancestor, no application source changed after it, the
   protocol and migration evidence match, and none of the three GHCR tags
   already exists. All images build successfully before any publish job starts.
6. Record the published digests, render `truenas-power-monitor.yaml`, validate
   it, and run the digest-pinned TrueNAS workflow again before deployment.

Never rerun publication under an existing semantic version. If any image has
already been published, choose a new version even if the earlier run was only
partially successful.

## Required PowerShell environment

The isolated workflow requires:

```powershell
$env:NODE_BIN = '<absolute path to Node.js 24 node.exe>'
$env:NPM_BIN = '<absolute path to the matching npm.cmd>'
$env:TRUENAS_COMPOSE_FILE = '<rendered test deployment YAML>'
$env:TRUENAS_POOL = 'Apps'
$env:TRUENAS_GATEWAY_PORT = '18443'
$env:TRUENAS_BASE_URL = 'https://localhost:18443'
$env:TRUENAS_CA_CERTIFICATE = '<temporary path outside the host root>\root.crt'
$env:TRUENAS_SETUP_TOKEN_FILE = '<test host root>\secrets\admin_setup_token'
$env:TRUENAS_TEST_HOST_ROOT = '<empty temporary directory>'
& $env:NODE_BIN --version # must print v24.4.0
& $env:NPM_BIN --version  # must print 11.4.2
.\scripts\release.ps1 -ReleaseVersion '<new version>'
```

Do not rely on whichever `node` or `npm` happens to be first on `PATH`. Point
`NODE_BIN` and `NPM_BIN` at the same pinned Node.js 24.4.0 installation. The
release guard rejects any other versions before running the expensive matrix.
The release scripts regenerate both dependency-audit JSON reports, and
`versions.json` binds each report to the exact lockfile and report SHA-256.

The generated final TrueNAS YAML must retain:

```yaml
volumes:
  - /mnt/Apps/Power/postgres:/var/lib/postgresql/data
```

Application images must use one matching semantic version and immutable digest.
Only the gateway publishes a host port.
