# Testing and release gates

## Tiered and hybrid rate-plan acceptance (2026-07-23)

The release passed Ruff lint/format, strict mypy, OpenAPI and JSON Schema
contract validation, 106 portable backend tests, and the separate PostgreSQL 17
integration gate. PostgreSQL upgraded the complete migration chain to
`20260723_0009`, downgraded to `20260721_0008`, reapplied the tiered revision,
and clean-installed 91 public tables with all 50 Administrator permissions.
Deterministic rate tests cover flat, TOU, two- and three-tier plans, arbitrary
tier counts, exact boundaries, daily baselines for 28/29/30/31-day and leap-year
cycles, seasonal thresholds, rounding, hybrid price matrices, chronological
allocation, imports, managed candidates, and missing-authority behavior.
The separately enabled resilience gate enrolled 100 simulated devices,
accepted 18,000 three-hour backfill readings, accepted zero duplicates on a
complete retry, retained a five-connection pool, and stayed below its 256 MiB
traced-memory ceiling.

The frontend passed a locked Node 24 install, lint, TypeScript checking, 29
unit/component tests, 32 Chromium end-to-end/accessibility scenarios, and the
production build. Coverage includes the flat/tiered/hybrid editor, usage import
preview and commit, billing-cycle tier progress, exact account costs,
source-candidate review, History provenance, and the existing custom TOU flow.

Fresh API, frontend, and backup images built successfully. The standard Compose
stack migrated and reported API, worker, PostgreSQL, frontend, and gateway
healthy. The worker advisory lock stays on a dedicated PostgreSQL connection,
so task commits no longer produce spurious unlock warnings. A five-artifact
logical backup passed every checksum and restored into a clean 91-table database
at `20260723_0009`.

The template, optional NET_RAW-only overlay, and a deployment-mode TrueNAS
render for `/mnt/Apps/Power/power-monitor` passed fail-closed validation and
Docker Compose parsing. The immutable seven-service TrueNAS workflow then
passed through strict internal-CA TLS with only TCP 8443 published: successful
one-shot migration, all health checks, 3 signed simulated devices, 90 backfilled
readings, SCE calculation, 2 utility accounts, 1 canonical sensor CIDR,
encrypted backup/checksums, and clean restore preserving migration and data
counts.

## Utility accounts and sensor network policy acceptance (2026-07-21)

The release passed Ruff lint and formatting, strict mypy, OpenAPI regeneration,
contract validation, and the repository secret scan. The portable backend and
simulator suite passed 90 tests; the three environment-gated cases were then
run separately. PostgreSQL 17 upgraded the populated legacy schema and a clean
database to `20260721_0008`, downgraded and reapplied the revision, and finished
with 81 public tables and all 48 Administrator permissions. The 100-device gate
ingested 18,000 three-hour backfill records in 69.201 seconds, accepted no
duplicates on retry, used a five-connection pool, and peaked at 11.22 MiB of
traced Python memory.

The frontend passed lint, TypeScript checking, 25 unit/component tests, all 31
Chromium end-to-end and accessibility scenarios, and the production build. The
new scenarios complete the seven-step account workflow, resolve the live rate
period and price without a sensor, manage account identity, scope, adjustments,
rate history and archival, configure an explicit private sensor network, test
allowed and blocked addresses without connecting to them, and check mobile
containment. Reviewed captures are stored at
`docs/screenshots/utility-account-management.png` and
`docs/screenshots/sensor-network-policy.png`.

Fresh API, frontend, and backup images built successfully. The standard Compose
stack reached `20260721_0008` with every long-running service healthy and
checksum/restored a five-artifact logical backup into a clean 81-table database.
The normal, TrueNAS template, digest-pinned deployment, and optional NET_RAW-only
ICMP configurations all passed fail-closed validation and Docker Compose
parsing. The immutable seven-service TrueNAS workflow then passed through the
TLS gateway with a successful one-shot migration, only TCP 8443 published,
three signed simulated devices, 90 historical readings, SCE calculation, two
effective utility accounts, one canonical sensor CIDR, an encrypted five-artifact
backup, checksum verification, and a clean restore that preserved the accounts
and policy rule. Python and npm dependency audits found no known vulnerabilities.

Migration preserves the former behavior exactly: empty legacy pull CIDRs become
explicit deny-all, configured legacy CIDRs are copied, and formerly unrestricted
but signed ingress remains in a visible review-required compatibility state
until an administrator chooses an explicit policy.

## Dashboard information architecture acceptance (2026-07-21)

The cleanup release passed Ruff lint/format, strict mypy, 83 portable backend
tests, the Docker-backed PostgreSQL 17 migration test, and the bounded
100-device resilience gate. PostgreSQL upgraded populated legacy schemas and a
clean database to `20260721_0007`; the new revision also downgraded to
`20260720_0006` and reapplied successfully. The resulting schema has 77 public
tables and a preserved prior layout plus the corrected immutable revision.

The frontend passed a clean `npm ci`, lint, TypeScript, 22 unit/component tests,
30 Chromium end-to-end and accessibility scenarios, and the production build.
Those tests assert that API, database, and worker health render only on
**Administration > System Health**; normal pages have no duplicate
`metric_identity`; Overview and History have one honest no-data state; measured
zero remains distinct from unavailable data; and desktop/mobile layouts do not
overflow.

Fresh API, frontend, and backup images built successfully. The normal isolated
Compose stack reached healthy state and generated a checksum-verified
five-artifact logical backup that restored at `20260721_0007`. The immutable,
deployment-mode TrueNAS Compose render for pool `Apps` passed the repository
validator and Docker Compose parsing. Its seven-service Docker Desktop workflow
then enrolled two simulated devices, processed signed heartbeats and 60
historical readings, calculated the SCE rate, verified that only TCP 8443 was
published, encrypted and verified a backup, and restored it into a clean
database. The official pytest wrapper repeated that workflow successfully.

## Login autofill and browser acceptance

`frontend/tests/authAutofill.test.tsx` verifies the rendered native form
contract, direct DOM-value submission without React change events, exact
password preservation, failed authentication behavior, same-node password
visibility, public-text success/failure, and first-run autocomplete separation.
The Chromium suite verifies click and Enter submission, successful removal of
the form, stable input identity, keyboard focus, dark-theme readability, mobile
width, and the presence of browser autofill rules.

Run the automated gates with:

```text
cd frontend
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
npm run e2e
```

Automation cannot prove that the real Chrome Password Manager UI offered to
save or update a credential. Complete and record the sanitized manual procedure
in [Browser compatibility](BROWSER_COMPATIBILITY.md) on the exact deployed HTTPS
origin. Never use a production administrator password as test evidence.

The 2026-07-20 autofill release gate passed 79 portable backend tests, the
separate 100-device/18,000-reading load gate, and the live PostgreSQL 17
migration gate. The frontend passed lint, TypeScript, 22 unit/component tests,
all 27 Chromium tests, and the Node 24 production build. API, frontend, and
backup images built successfully. The normal stack reached migration
`20260720_0006`, reported every service healthy through CA-verified HTTPS, and
checksum/restore-verified a five-artifact logical backup with 77 tables. Both
normal and NET_RAW-only ICMP TrueNAS deployments passed fail-closed validation.
The final digest-pinned seven-service TrueNAS workflow passed migration,
health/port checks, three simulated-device enrollments, signed heartbeats, 90
historical readings, SCE calculation, backup verification, and clean restore.
Python and npm production dependency audits reported no known vulnerabilities.

Real Chrome Password Manager save/update prompting remains a manual acceptance
gate because the automated Chromium profile does not expose a genuine password
manager. Its exact remaining steps are maintained in
[Browser compatibility](BROWSER_COMPATIBILITY.md).

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

The History gate includes deterministic two-sensor service-leg readings from
8:00 PM–10:00 PM under a configured `$1.00/kWh` plan. It verifies 4.00 kWh and
exact `$4.00` server-calculated energy cost, combined and individual modes,
summed power and interval energy, non-summed voltage/current behavior,
rate-version provenance, selected-range summary, legacy single-device
compatibility, CSV export, partial coverage, responsive layout, and
parent/child overlap rejection. It never depends on a live SCE page.

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

The cost-aware History and multi-sensor release gate passed 74 portable backend
tests, the PostgreSQL 17 prior-schema/clean-install migration integration test,
6 frontend unit tests, and all 23 Chromium E2E tests. The current API,
frontend, and backup images built successfully. A fresh Compose stack reached
Alembic `20260720_0005` with every long-running service healthy. Its deployed
History workflow verified 2 signed devices, 4 deterministic readings, the
4 kWh/`$4.00` combined range, CSV parity, and topology rejection. A five-artifact
logical backup passed checksums and restored 2 devices, 4 readings, and one
Alembic head into a clean PostgreSQL database.

## Cost-aware History deployed gate

Run the History-specific acceptance workflow against a clean deployed stack
through its HTTPS gateway. Export and trust the stack's internal Caddy CA; do
not disable certificate verification.

```text
python tools/test-history-workflow.py \
  --base-url https://power-monitor.test \
  --ca-certificate /private/caddy-root.crt \
  --setup-token-file /private/admin_setup_token
```

The workflow bootstraps the one-time administrator, creates an account and two
non-overlapping service-leg circuits, enrolls two signed simulated ESP32
sensors, backfills deterministic 8:00 PM-10:00 PM intervals, activates and
assigns a one-dollar custom rate, and verifies single, individual, combined,
and combined-plus-individual History modes. It requires exactly 4 kWh and
`$4.00`, checks voltage/current semantics and rate provenance, parses the CSV
to reproduce the same total, and finally turns one circuit into a child to
prove the API blocks double counting.

Set `CAPTURE_HISTORY_SCREENSHOT=1` when running Playwright to refresh
`docs/screenshots/history-cost-aggregation.png`. The browser test also checks
the interval selection, cost/rate presentation, export action, and mobile page
width.

Dashboard-correction screenshot fixtures are generated by setting
`CAPTURE_DASHBOARD_SCREENSHOTS=1` while running the relevant Playwright cases.
The reviewed captures are stored under
`docs/screenshots/dashboard-corrections/`.

## Status Indicators & Layout release gate

The Status Indicators & Layout gate passed 79 portable backend tests (with the
live PostgreSQL, 100-device load, and deployed TrueNAS workflows separately
gated), strict Ruff/format/mypy, OpenAPI generation/checking, both contract
validators, and the repository secret scan. The registry completeness contract
checks 37 unique definitions and every required metadata field. Layout tests
cover zero, one, two, three, four, twelve, and all eligible indicators; global,
page, role, and mobile precedence; permission filtering; retired keys; invalid
imports; CSRF; stale revisions; critical page-scoped hides; monitoring/alert
independence; per-field audit evidence; immutable publish; export/import; and
rollback.

The frontend passed lint, TypeScript checking, 14 unit tests, all 24 Chromium
E2E tests, and its Node 24 production build. Renderer tests prove empty zones
emit no wrapper, remaining items have no spacer nodes, hidden labels retain an
accessible name, severity is readable, density modes remain valid, and long
labels survive. The E2E workflow exercises the disabled tray, keyboard ordering,
responsive desktop/tablet/mobile and empty/long-label previews, 200% text
scaling without horizontal overflow, save/preview/publish, live gap removal,
alert persistence, and immutable restoration. The largest JavaScript chunk was
306.29 kB (97.97 kB gzip). Python and npm dependency audits found zero known
vulnerabilities.

PostgreSQL 17 successfully upgraded a populated initial schema, downgraded and
re-upgraded, upgraded a populated `20260720_0004` schema through the intervening
revision, and clean-installed `20260720_0006`. The resulting schema has 77
public tables, a valid seeded current pointer, one immutable compiled-default
revision, and 44 Administrator permissions. Fresh API, frontend, and backup
images built successfully. An isolated Compose stack reported every long-running
service healthy, exposed only its gateway, and reported Alembic head
`20260720_0006` through CA-verified HTTPS.

The deployed acceptance workflow uses the actual API through that HTTPS gateway:

```text
python tools/test-status-layout-workflow.py \
  --base-url https://power-monitor.test \
  --ca-certificate /private/caddy-root.crt \
  --setup-token-file /private/admin_setup_token
```

It requires at least 30 registry definitions, saves and validates a draft,
previews an empty mobile zone, publishes visibility and placement changes,
proves the disabled key is absent without an empty zone, restores revision 1 as
revision 3, and verifies audit events. The post-workflow logical backup passed
all five artifact hashes and restored into a clean 77-table database at
`20260720_0006` with all three immutable status-layout revisions and a valid
current pointer.

Template, rendered normal, and rendered optional-ICMP TrueNAS configurations
passed fail-closed validation and Docker Compose parsing. No Compose service,
dataset, bind mount, secret, port, capability, or environment variable was
added by this feature.

Set `CAPTURE_STATUS_LAYOUT_SCREENSHOTS=1` while running the status-layout E2E
case to refresh reviewed desktop, tablet, mobile, empty-zone, long-label, and
published-mobile fixtures under `docs/screenshots/status-indicators/`.

## Hardware boundary

No live mains equipment was connected. Remaining hardware validation is limited to an isolated, electrician-approved ESP32-S3/PZEM-004T V4 installation: CT orientation and rating, split-phase topology, SD removal/full/corruption, Wi-Fi/VLAN behavior, power loss, multi-hour offline backfill, OTA success/rollback, and comparison against an independent reference meter.
