# Install on TrueNAS Community Edition 25.10

This deployment uses **Apps > Discover Apps > Install via YAML**. All production
App lifecycle actions in this guide use the TrueNAS web interface; do not manage
this stack with direct `docker` commands in the TrueNAS shell.

## 1. Prepare the release on an administrator workstation

1. Obtain the three application images (`api`, `frontend`, and `backup`) and the
   approved Caddy and PostgreSQL images as versioned `linux/amd64` OCI references
   containing `@sha256:<64 hex characters>`. A tag alone is not sufficient.
2. Follow [dataset-layout.md](dataset-layout.md) and
   [permissions.md](permissions.md) in the TrueNAS UI.
3. Generate secrets outside the source checkout and outside any synced folder:

   ```text
   python tools/generate-secrets.py --output /private/path/power-monitor-secrets
   ```

   The tool never prints values and refuses a non-empty directory or a directory
   inside a Git worktree. Transfer the eight files to
   `/mnt/POOL/apps/power-monitor/secrets` through a temporary, encrypted,
   administrator-only share, then remove that share. Do not paste a secret into
   YAML, an image build argument, logs, or browser-delivered assets.
4. Select one Caddy mode:

   - LAN/internal CA: upload `deploy/truenas/Caddyfile` as
     `/mnt/POOL/apps/power-monitor/config/Caddyfile`.
   - User certificate: upload `Caddyfile.user-certificate` under that destination
     name and replace `tls.crt`/`tls.key` with the PEM chain and matching key.
   - Public DNS/ACME: upload `Caddyfile.public-acme` under that destination name,
     configure a public FQDN, and expose public TCP 443 to gateway TCP 443. The
     default LAN mapping `8443:443` cannot satisfy a public port-443 challenge.

5. Render a deployment file. The command below is illustrative; every image
   argument must be the exact release reference and digest you approved:

   ```text
   python tools/render-truenas-compose.py --pool MYPOOL --gateway-port 8443 --site-address https://power-monitor.local --public-origin https://power-monitor.local:8443 --api-image ghcr.io/OWNER/power-monitor-api:1.0.0@sha256:DIGEST --frontend-image ghcr.io/OWNER/power-monitor-frontend:1.0.0@sha256:DIGEST --backup-image ghcr.io/OWNER/power-monitor-backup:1.0.0@sha256:DIGEST --postgres-image docker.io/library/postgres:17.5-bookworm@sha256:DIGEST --gateway-image docker.io/library/caddy:2.10.0-alpine@sha256:DIGEST --output rendered-compose.yaml
   python tools/validate-truenas-compose.py --deployment --pool MYPOOL --gateway-port 8443 rendered-compose.yaml
   ```

   `PUBLIC_ORIGIN` must exactly equal the browser origin, including a non-default
   port. Configure LAN DNS so the site hostname resolves to the TrueNAS address.

## 2. Install through the TrueNAS web interface

1. In **Apps > Configuration**, select the Apps pool if one is not already set.
2. Open **Apps > Discover Apps**, open the actions menu, and choose
   **Install via YAML**.
3. Enter an application name such as `power-monitor` (lowercase letters, numbers,
   and hyphens), paste the complete validated `rendered-compose.yaml`, and choose
   **Save**.
4. Watch **Apps > Installed > power-monitor > Workloads**. `postgres` must become
   healthy, `migrate` must exit successfully with code 0, then API, worker,
   frontend, gateway, and backup must become healthy. The dependency chain keeps
   the release unavailable when a migration fails.
5. Review each workload's logs from the App details page. Secret values must not
   appear. Confirm no host ports are listed except gateway TCP 8443 (or the one
   intentionally configured gateway port).

## 3. Establish TLS trust and create the administrator

For internal-CA mode, complete [Internal CA trust](#internal-ca-trust) before
opening the application. For a user or public certificate, verify its chain and
hostname normally.

Open `https://power-monitor.local:8443/`. On the first visit, the application asks
for a new administrator name, email, password, and the one-time value from
`admin_setup_token`. There is no static administrator password. After successful
setup, the endpoint closes permanently; archive or rotate the setup-token file so
it is not reused as an operational credential.

## Internal CA trust

Caddy writes the LAN root certificate to:

`/mnt/POOL/apps/power-monitor/caddy-data/caddy/pki/authorities/local/root.crt`

Export that public certificate through an administrator-only share. Do not export
`root.key` or any other private key.

- Windows: import `root.crt` into **Local Computer > Trusted Root Certification
  Authorities** using Certificate Manager or your managed-device policy.
- macOS/iOS: import it into the System keychain/profile and mark it trusted for
  SSL. On iOS, also enable full trust under Certificate Trust Settings.
- Firefox: if enterprise roots are not enabled, import it under **Privacy &
  Security > Certificates > Authorities** and trust it for websites.
- Android/ChromeOS: install it as a trusted CA through the managed device policy
  appropriate for the fleet.

For ESP32 firmware, embed the same `root.crt` in the protected firmware image or
provisioning partition and configure the TLS client CA store (for Arduino,
`WiFiClientSecure::setCACert`; for ESP-IDF, the PEM certificate bundle field).
Verify both hostname/SAN and chain on every heartbeat, enrollment, configuration,
and firmware request. Never call `setInsecure`, use an empty CA store, suppress a
hostname mismatch, or disable TLS verification as a workaround.

## Optional ICMP diagnostics

Normal operation does not use ICMP. Signed heartbeats supply current addresses,
and the worker uses HTTPS/TCP health and synchronization calls. If optional ping
diagnostics are required, render again with `--enable-icmp`, then validate with
`--icmp-enabled` and paste that complete file into **Edit YAML**. The renderer
applies the separately checked `compose-icmp.yaml` policy: only `NET_RAW` is added
to the worker. Do not add privileged mode or host networking.
