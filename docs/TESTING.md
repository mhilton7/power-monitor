# Testing and release gates

Run commands from the repository root unless a command starts with `cd`.

## Portable gates

```text
.venv/Scripts/python.exe -m ruff check backend worker simulator scripts tools
.venv/Scripts/python.exe -m ruff format --check backend worker simulator scripts tools
.venv/Scripts/python.exe -m mypy backend/app worker/app simulator/simulated_device
cd backend && ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
.venv/Scripts/python.exe scripts/generate_openapi.py --check
.venv/Scripts/python.exe scripts/validate_contracts.py
cd backend && ../.venv/Scripts/python.exe -m alembic -c alembic.ini upgrade head --sql
cd frontend && npm ci
cd frontend && npm run lint
cd frontend && npm run typecheck
cd frontend && npm run test
cd frontend && npm run build
cd frontend && npm run e2e
.venv/Scripts/python.exe scripts/secret_scan.py
.venv/Scripts/python.exe -m pip_audit -r backend/requirements.lock --no-deps
cd frontend && npm audit --json
```

The 2026-07-20 portable release run passed 34 Python tests, with the load,
live-PostgreSQL, and deployed-TrueNAS tests explicitly gated for their required
environments. It also passed 5 frontend unit tests and 11 Chromium E2E tests,
including SMTP notification setup, trigger timing, and Costs-route removal.
Ruff, format checking, strict mypy, OpenAPI
validation, JSON Schema examples, HMAC vectors, the offline PostgreSQL migration
render, both dependency audits, and the secret scan passed. The production
frontend build's largest JavaScript chunk was 281.41 kB (90.52 kB gzip).

The checked host had Node 26, so npm emitted the expected engine warning; the build image and supported runtime are pinned to Node 24.4.0. No test failure or source change resulted from the warning.

## PostgreSQL, Compose, and restore gates

These commands are mandatory on a Docker/Compose host. They exercise behavior that SQLite and offline SQL generation cannot prove:

```text
docker compose config --quiet
docker compose build --pull
docker compose up -d --wait
docker compose ps
docker compose exec api alembic current
docker compose --profile tools run --rm backup /srv/scripts/backup-container.sh
docker compose --profile tools run --rm backup /srv/scripts/verify-backup-container.sh /data/backups/<backup-directory>
```

The 2026-07-20 Docker release run rebuilt the API, frontend, and backup images,
started the standard stack with every health check passing, confirmed Alembic
revision `20260720_0001`, created an encrypted logical backup, validated all four
artifact checksums, restored into a clean PostgreSQL database, and found 54
public tables. The dedicated live PostgreSQL migration integration test also
passed.

## TrueNAS Compose release gate

Run this on a Docker/Compose CI host or administrator workstation, never as a
production-management procedure in the TrueNAS host shell:

```text
python tools/render-truenas-compose.py <deployment arguments> --output rendered-compose.yaml
python tools/validate-truenas-compose.py --deployment --pool MYPOOL --gateway-port 8443 rendered-compose.yaml
RUN_TRUENAS_COMPOSE_INTEGRATION=1 TRUENAS_COMPOSE_FILE=rendered-compose.yaml TRUENAS_BASE_URL=https://power-monitor.example:8443 TRUENAS_CA_CERTIFICATE=/private/root.crt TRUENAS_SETUP_TOKEN_FILE=/private/admin_setup_token python -m pytest backend/tests/test_truenas_compose_integration.py -v
```

The final 2026-07-20 gate started all seven services from immutable
`linux/amd64` image references, required a successful one-shot migration,
verified every long-running health check and actual Docker port binding, and
used strict internal-CA TLS. It enrolled 3 simulated devices, accepted signed
heartbeats and 90 historical readings, calculated an SCE TOU-D-4-9PM preview,
created and checksum-verified an encrypted backup, restored it into a clean
database, and verified device, heartbeat, reading, and migration state. Only the
configured gateway port was published; PostgreSQL, API, worker, frontend,
migration, and backup published none.

## Hardware boundary

No live mains equipment was connected. Remaining hardware validation is limited to an isolated, electrician-approved ESP32-S3/PZEM-004T V4 installation: CT orientation and rating, split-phase topology, SD removal/full/corruption, Wi-Fi/VLAN behavior, power loss, multi-hour offline backfill, OTA success/rollback, and comparison against an independent reference meter.
