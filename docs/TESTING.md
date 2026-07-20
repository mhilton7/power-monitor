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

The 2026-07-20 dashboard-corrections release run passed 42 Python tests, with the load,
live-PostgreSQL, and deployed-TrueNAS tests explicitly gated for their required
environments. It also passed 5 frontend unit tests and 15 Chromium E2E tests,
including SMTP notification setup, trigger timing, Costs-route removal,
administrator log download, manifest-backed export state, exact sensor-removal
confirmation, archived sensor visibility, corrected dashboard copy, and compact
pointer/visible keyboard focus.
Ruff, format checking, strict mypy, OpenAPI
validation, JSON Schema examples, HMAC vectors, the offline PostgreSQL migration
render, both dependency audits, and the secret scan passed. The production
frontend build's largest JavaScript chunk was 280.79 kB (90.38 kB gzip).

The separately enabled 100-device gate also passed: it ingested 18,000
three-hour backfill records in 71.297 seconds, accepted no duplicates on retry,
used a five-connection pool, and peaked at 9.23 MiB of traced Python memory.

The checked host had Node 26, so npm emitted the expected engine warning; the build image and supported runtime are pinned to Node 24.4.0. No test failure or source change resulted from the warning.

The weekly-rate/custom-plan and managed-source gate passed 60 Python tests with
the 100-device gate enabled, 5 frontend unit tests, and all 21 Chromium E2E
tests. It covers
source retrieval and retry classification, parsing fixtures, evidence and
candidate deduplication, conflict handling, strict automatic-activation
guards, custom-plan validation and calculation, the four-step editor, source
review, accessible activation, account assignment, persisted automatic rate-source
settings, administrator-added approved sources, live-page TOU extraction,
settings-panel containment, and non-busy disabled detail controls. The production frontend
build's largest JavaScript chunk was 281.91 kB (90.76 kB gzip).

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

The original 2026-07-20 Docker release run rebuilt the API, frontend, and backup images,
started the standard stack with every health check passing, created an encrypted logical backup, validated all four
artifact checksums, restored into a clean PostgreSQL database, and found 54
public tables at revision `20260720_0001`.

The dashboard-corrections gate rebuilt all three production images and parsed
both standard Compose profiles. Its dedicated PostgreSQL 17 integration test
upgraded a populated `20260720_0001` database, verified safe legacy-revocation
backfill, then recreated a clean schema and upgraded directly to
`20260720_0002`; both paths finished with 56 public tables. Static Compose
hardening validation and normal plus ICMP-enabled rendered TrueNAS deployment
validation passed. The ICMP gate permits only `NET_RAW` on the worker.

The standard stack was also started from a fresh named-volume deployment. All
five long-running services reported healthy, Alembic reported
`20260720_0002 (head)`, API and worker log files survived controlled container
restarts, and the shared backup logger wrote under UID/GID `10003` through the
documented supplementary log-volume group. The backup image created
`power-monitor-20260720T132746Z`; verification passed all four artifact hashes
and restored a clean database with revision `20260720_0002` and 56 tables.

The managed-source gate then upgraded and clean-installed PostgreSQL 17 at
`20260720_0004`, including a downgrade to `20260720_0002` followed by a
successful re-upgrade. The schema has 67 public tables. Fresh backend,
frontend, and backup OCI images built successfully; the standard five-service
stack reported healthy; and all normal, template, rendered, and optional-ICMP
Compose configurations passed validation. The encrypted backup now verifies
five artifacts, including the content-addressed rate-source evidence archive.

The Users & Access and Dashboard & Login Text release gate passed 69 portable
Python tests, with the Docker-required PostgreSQL and TrueNAS workflows and the
100-device load test run separately. PostgreSQL 17 successfully upgraded a
populated initial schema, downgraded and re-upgraded, upgraded a populated
`20260720_0004` schema, and installed cleanly at `20260720_0005`; all final
schemas contained 74 public tables. The separately enabled load gate accepted
18,000 readings from 100 devices, deduplicated the complete retry, and finished
in 92.84 seconds.

The frontend passed lint, TypeScript checking, 5 unit tests, all 23 Chromium
E2E tests, and its production build. The largest JavaScript chunk was 294.61 kB
(94.26 kB gzip). Backend and frontend dependency audits found zero known
vulnerabilities, and the contract, offline migration, secret-scan, normal
Compose, TrueNAS template, rendered-deployment, and optional ICMP-overlay gates
passed. Fresh API, frontend, and backup images built successfully. The isolated
standard stack reported every long-running service healthy, reached migration
`20260720_0005`, exposed only the gateway, and restored a five-artifact logical
backup into a clean 74-table database.

## TrueNAS Compose release gate

Run this on a Docker/Compose CI host or administrator workstation, never as a
production-management procedure in the TrueNAS host shell:

```text
python tools/render-truenas-compose.py <deployment arguments> --output rendered-compose.yaml
python tools/validate-truenas-compose.py --deployment --pool Apps --gateway-port 8443 rendered-compose.yaml
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

The gate was rerun from the final Users & Access/interface-text images and
passed again with 3 devices, 90 historical readings, encrypted five-artifact
backup verification, and clean restore at `20260720_0005` with 74 tables. The
workflow removed its disposable containers, networks, and volumes after the
successful run.

Dashboard-correction screenshot fixtures are generated by setting
`CAPTURE_DASHBOARD_SCREENSHOTS=1` while running the relevant Playwright cases.
The reviewed captures are stored under
`docs/screenshots/dashboard-corrections/`.

## Hardware boundary

No live mains equipment was connected. Remaining hardware validation is limited to an isolated, electrician-approved ESP32-S3/PZEM-004T V4 installation: CT orientation and rating, split-phase topology, SD removal/full/corruption, Wi-Fi/VLAN behavior, power loss, multi-hour offline backfill, OTA success/rollback, and comparison against an independent reference meter.
