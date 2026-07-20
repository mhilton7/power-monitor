# Testing and release gates

Run commands from the repository root unless a command starts with `cd`.

## Portable gates

```text
.venv/Scripts/python.exe -m ruff check backend worker simulator scripts
.venv/Scripts/python.exe -m ruff format --check backend worker simulator scripts
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

The 2026-07-20 portable release run passed 21 Python tests, with the explicit load and PostgreSQL-only tests excluded from that count; 5 frontend unit tests; and 9 Chromium E2E tests. Ruff, format checking, strict mypy, OpenAPI validation, JSON Schema examples, HMAC vectors, the offline PostgreSQL migration render, both dependency audits, and the secret scan passed. The production frontend build's largest JavaScript chunk was 284.48 kB (90.01 kB gzip).

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

The local 2026-07-20 build host did not have Docker, Compose, or PostgreSQL client binaries installed. Consequently, image sizes, a live PostgreSQL migration, healthy Compose state, and a real `pg_restore` verification are not recorded as passed locally. `scripts/release.sh` and `scripts/release.ps1` keep these as hard release gates and stop on failure.

## Hardware boundary

No live mains equipment was connected. Remaining hardware validation is limited to an isolated, electrician-approved ESP32-S3/PZEM-004T V4 installation: CT orientation and rating, split-phase topology, SD removal/full/corruption, Wi-Fi/VLAN behavior, power loss, multi-hour offline backfill, OTA success/rollback, and comparison against an independent reference meter.
