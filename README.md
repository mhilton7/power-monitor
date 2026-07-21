# Power Monitor Server

Power Monitor Server is a self-hosted control plane for fleets of ESP32-S3 power sensors using a PZEM-004T V4.x, one current transformer, and mandatory microSD storage. It enrolls devices with unique credentials, accepts signed outbound data, pulls retained history from reachable devices, preserves raw readings in PostgreSQL, calculates effective-dated SCE time-of-use estimates, and presents the result in a responsive React application.

The dashboard includes encrypted SMTP notification setup, test delivery and
retry evidence, selectable sensor-disconnect alerts, and configurable power-surge
thresholds and persistence timing.

Administrators can also download redacted, checksum-manifested application-log
archives for any available range in the rolling 90-day window and safely
unclaim sensors without deleting their readings, calculations, alerts, or audit
history. Removed hardware can be re-enrolled only with a new token and secret.

The integrated **Users & Access** workspace provides server-enforced granular
permissions, custom roles, site scope, session revocation, and last-administrator
protection. **Dashboard & Login Text** provides a safe approved-text catalog with
draft, responsive preview, immutable publish, defaults, and rollback. See
[user management](docs/USER_MANAGEMENT.md), [permissions](docs/PERMISSIONS.md),
[site access](docs/SITE_ACCESS.md), and [interface text](docs/INTERFACE_TEXT.md).

The normative interoperability identifier is `pm-protocol/1.0.0`. The browser talks only to this server; device credentials never reach browser code.

The canonical source repository is
[github.com/mhilton7/power-monitor](https://github.com/mhilton7/power-monitor).
Production application images are published as
`ghcr.io/mhilton7/power-monitor-{api,frontend,backup}`.

```text
ESP32 agents -- signed heartbeat / batches --> FastAPI --> PostgreSQL
      ^                                       |  |
      +---------- HMAC pull/sync worker <-----+  +--> rate/alert/report workers
Browser <------------ HTTPS + SSE ---------- Caddy <--- React frontend
```

## Quick development start

Requirements are Python 3.13, Node.js 24 LTS, and PostgreSQL 17. Docker Compose is the simplest way to provide PostgreSQL.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e './backend[dev]'
cd frontend && npm ci && cd ..
cp .env.example .env                 # replace every CHANGE_ME value
docker compose -f compose.yaml -f compose.dev.yaml up -d postgres
cd backend && alembic upgrade head && uvicorn app.main:app --reload
```

In separate terminals run `python -m worker.app.main` and `cd frontend && npm run dev`. Run all portable gates with `make lint typecheck test contract frontend`.

For production use [Installation](docs/INSTALLATION.md), then [First run](docs/FIRST_RUN.md).
TrueNAS Community Edition 25.10 has a first-class, immutable-image deployment
through [Apps > Install via YAML](deploy/truenas/installation.md), including exact
dataset ACLs, three TLS modes, nightly encrypted/restore-tested backups, and an
automated multi-device release gate. Protocol and deployment details are in
[Architecture](docs/ARCHITECTURE.md), [Device protocol](docs/DEVICE_PROTOCOL.md),
[Security](docs/SECURITY.md), and [Operations](docs/OPERATIONS.md).
Rate administration is covered by [SCE synchronization](docs/SCE_RATE_SYNC.md),
[custom plans](docs/CUSTOM_RATE_PLANS.md), [source security](docs/RATE_SOURCE_SECURITY.md),
and [versioning](docs/RATE_VERSIONING.md). See the consolidated
[TrueNAS deployment notes](docs/TRUE_NAS_DEPLOYMENT.md) before installing an
upgrade.

Historical analysis is covered by the [History guide](docs/HISTORY.md),
[historical cost calculation](docs/HISTORY_COSTS.md),
[multi-sensor aggregation](docs/MULTI_SENSOR_AGGREGATION.md), and
[History exports](docs/HISTORY_EXPORTS.md).

Verification evidence is recorded in [Testing](docs/TESTING.md) and [Load test results](docs/LOAD_TEST_RESULTS.md).

## Safety and scope

This software does not control or connect to mains wiring. Installation of PZEM modules and CTs belongs to the separate sensor project and must be performed by a qualified person under applicable electrical rules. Measurements are not represented as revenue-grade. Every displayed cost is an estimate, not a utility bill; monitored coverage, meter accuracy, baseline allocation, CCA/Direct Access, taxes, credits, rounding, tariff changes, and utility adjustments can differ.

Licensed under MIT. See [LICENSE](LICENSE).
