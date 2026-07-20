# Production installation

For TrueNAS Community Edition 25.10, use the dedicated
[TrueNAS Install via YAML guide](../deploy/truenas/installation.md). Its production
Compose file pulls only versioned digest-pinned images, publishes only the TLS
gateway, uses file-backed secrets, and includes TrueNAS dataset/ACL, upgrade,
backup, and rollback procedures. The generic instructions below are for a
maintained standalone Docker host, not TrueNAS Apps.

## Host

Use a maintained 64-bit Linux distribution on x86_64 or ARM64 with Docker Engine 27+ and Compose v2. PostgreSQL and application images are pinned in `compose.yaml`. Start with 2 CPU cores, 4 GiB RAM, and SSD storage. Capacity is workload-dependent: at one 60-second row per device, 100 devices create about 52.6 million rows per year before indexes and rollups. Monitor real growth and reserve at least twice the database size for maintenance and verified backups.

## DNS and HTTPS

Create a DNS record for the host and set `CADDY_SITE_ADDRESS`. Public names use Caddy ACME automatically and require inbound TCP 80/443. LAN-only deployments can replace `deploy/Caddyfile` with `deploy/examples/Caddyfile.internal`, distribute the Caddy root CA to clients, and use local DNS. A user certificate example is also provided. Do not publish PostgreSQL or ESP32 API ports.

## Secrets and startup

```bash
git clone <your-release-location> /opt/power-monitor
cd /opt/power-monitor
cp .env.example .env
chmod 600 .env
openssl rand -base64 32                 # APP_MASTER_KEY input must be a valid Fernet key
openssl rand -hex 32                    # SESSION_PEPPER
openssl rand -base64 36                 # BOOTSTRAP_SECRET
openssl rand -base64 36                 # POSTGRES_PASSWORD
```

Generate the Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Replace every `CHANGE_ME`; never reuse the bootstrap secret or database password. Then:

```bash
./scripts/bootstrap.sh
docker compose ps
curl --fail --silent https://YOUR_NAME/health/ready
```

The API container applies Alembic before serving. A failed migration prevents readiness. The database has no host port in production. Named volumes persist database, Caddy state, firmware, reports, backups, and application attachments.

Resource limits vary by fleet and are deliberately operator-set. Begin with API 1 CPU/1 GiB, worker 2 CPU/2 GiB, frontend/Caddy 0.5 CPU/256 MiB each, and PostgreSQL 2 CPU/2 GiB; watch latency, connections, and memory before tightening.
