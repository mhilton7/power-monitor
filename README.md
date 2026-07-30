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
permissions, custom roles, site scope, session revocation, reversible Disable,
audited soft Remove/Restore, and recovery-administrator protection. It is the
only user-management interface; legacy Administration user routes redirect
there. **Dashboard & Login Text** provides a safe approved-text catalog with
draft, responsive preview, immutable publish, defaults, and rollback. See
[user management](docs/USER_MANAGEMENT.md), [permissions](docs/PERMISSIONS.md),
[site access](docs/SITE_ACCESS.md), and [interface text](docs/INTERFACE_TEXT.md).

The production browser application uses **Single Home Mode** and exactly four
destinations: **Home, History, Billing, and Settings**. Alerts open from the
global header instead of becoming a fifth workspace. The independent
`frontend-next` bundle consumes the existing FastAPI services through
runtime-validated adapters; legacy bookmarks redirect to one of the four
destinations and no legacy page is included in the production image. Internal
site, account, circuit, aggregate, and device identities remain intact in
PostgreSQL. See the [replacement architecture](docs/frontend-replacement/architecture.md),
[feature-parity contract](docs/frontend-replacement/feature-parity.md), and
[cutover and rollback guide](docs/frontend-replacement/cutover-and-rollback.md).

Utility-bill PDF import is integrated directly into **Billing > Rate Plans >
Custom plan** as
an optional, selective prefill assistant. Existing draft fields are preserved,
bill-specific cycle data stays separate, and no upload publishes or assigns a
rate. The legacy `/rates/import-bill` bookmark redirects into the editor. See
[utility-bill imports](docs/UTILITY_BILL_IMPORTS.md).

**Status Indicators & Layout** adds a server-owned inventory of compact status
surfaces with permission-aware global/page/role/breakpoint placement, responsive
gap-free rendering, keyboard ordering, drafts, preview, immutable publishing,
and rollback. Hiding an indicator never disables monitoring or alerts. See the
[registry](docs/STATUS_INDICATORS.md), [zones and precedence](docs/STATUS_LAYOUT_ZONES.md),
[administration guide](docs/STATUS_LAYOUT_ADMINISTRATION.md), and
[accessibility notes](docs/STATUS_LAYOUT_ACCESSIBILITY.md). The cleaned
[Overview](docs/OVERVIEW.md) provides one configurable Site Summary, while
[System Health](docs/SYSTEM_HEALTH.md) is the administrator-only home for API,
database, and worker diagnostics.

Owners can also use the isolated, temporary
[Sensor Test Mode](docs/SENSOR_TEST_MODE.md) to preview 0–32 synthetic sensors
on Home, History, Billing, and Settings without creating readings, credentials,
bills, saved costs, alerts, exports, backups, or firmware records.

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
cd frontend-next && npm ci && cd ..
cp .env.example .env                 # replace every CHANGE_ME value
docker compose -f compose.yaml -f compose.dev.yaml up -d postgres
cd backend && alembic upgrade head && uvicorn app.main:app --reload
```

In separate terminals run `python -m worker.app.main` and
`cd frontend-next && npm run dev`. The legacy `frontend` directory remains only
as temporary migration evidence and is not copied into the production image.

For production use [Installation](docs/INSTALLATION.md), then [First run](docs/FIRST_RUN.md).
Browser sign-in behavior is documented in [Authentication](docs/AUTHENTICATION.md)
and [Browser compatibility](docs/BROWSER_COMPATIBILITY.md).
TrueNAS Community Edition 25.10 has a first-class, immutable-image deployment
through [Apps > Install via YAML](deploy/truenas/installation.md), including exact
dataset ACLs, three TLS modes, nightly encrypted/restore-tested backups, and an
automated multi-device release gate. Protocol and deployment details are in
[Architecture](docs/ARCHITECTURE.md), [Device protocol](docs/DEVICE_PROTOCOL.md),
[Live sensor pipeline](docs/LIVE_SENSOR_PIPELINE.md),
[Security](docs/SECURITY.md), and [Operations](docs/OPERATIONS.md).
Rate administration is covered by [SCE synchronization](docs/SCE_RATE_SYNC.md),
[custom plans](docs/CUSTOM_RATE_PLANS.md), [source security](docs/RATE_SOURCE_SECURITY.md),
the [strict SCE bill parser](docs/SCE_BILL_PARSER.md),
[rate-plan lifecycle](docs/RATE_PLAN_LIFECYCLE.md), and
[versioning](docs/RATE_VERSIONING.md). Flat, TOU, billing-cycle tiered, and
hybrid pricing are documented in [Tiered and hybrid rates](docs/TIERED_AND_HYBRID_RATES.md),
[Billing cycles](docs/BILLING_CYCLES.md), [Account usage authority](docs/ACCOUNT_USAGE_AUTHORITY.md),
[Usage and costs](docs/USAGE_AND_COSTS.md), and [Usage imports](docs/USAGE_IMPORTS.md).
Guided account/rate setup and the
separate sensor-network boundary are documented in
[Utility accounts](docs/UTILITY_ACCOUNTS.md) and
[Sensor network policy](docs/SENSOR_NETWORK_POLICY.md). See the consolidated
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
