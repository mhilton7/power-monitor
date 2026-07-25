# PDF, plan lifecycle, and dropdown acceptance matrix

Date: 2026-07-25

The controlling fixture is
`backend/tests/fixtures/bills/sanitized-sce-domestic-bill.pdf`. The raw PDF,
redacted text, strict parser output, normalized artifact, review decisions,
published plan/version, billing-cycle record, account assignment, and
dashboard queries are covered as one data path.

| Gate | Command or environment | Result |
| --- | --- | --- |
| Python format | `python -m ruff format --check backend worker simulator` | PASS, 119 files |
| Python lint | `python -m ruff check backend worker simulator` | PASS |
| Python types | `python -m mypy backend/app worker/app simulator/simulated_device` | PASS, 69 source files |
| Contracts | `python scripts/validate_contracts.py` | PASS, OpenAPI, JSON Schema, examples, generated bill types, and HMAC vectors |
| Backend | `python -m pytest -q` from `backend` | PASS, 182 passed and 3 separately gated skips |
| Load/deduplication | `RUN_LOAD_TEST=1 ... test_load_resilience.py` | PASS, 100 devices and 18,000 readings in 138.15 seconds |
| PostgreSQL 17 | `RUN_POSTGRES_INTEGRATION=1 ... test_postgres_integration.py` | PASS, legacy upgrade, downgrade/re-upgrade, prior-release upgrade, and clean install |
| Frontend lint/types | `npm run lint`; `npm run typecheck` | PASS |
| Frontend units | `npm run test -- --run` | PASS, 20 tests |
| Browser matrix | `npm run e2e` | PASS, 80 passed and 8 intentional project-specific skips |
| Full bill apply browser path | desktop Playwright acceptance | PASS, review through Billing refresh |
| Frontend production bundle | `npm run build` | PASS, 12 chunks, 588,721 bytes in the production container, hashed CSS, no legacy modules |
| OCI images | normal Compose build plus tools-profile backup build | PASS, API/worker/migrate, frontend, and backup |
| Normal Compose | `docker compose up -d --wait` | PASS, all five long-running services healthy |
| Normal migration | `docker compose exec -T api alembic current` | PASS, `20260725_0016 (head)` |
| Normal backup/restore | backup and verification container scripts | PASS, checksums and clean restore, 98 tables |
| TrueNAS template | renderer, deployment validator, and Compose parser | PASS with immutable versioned digest references |
| TrueNAS-equivalent deployment | `tools/test-truenas-workflow.py` with isolated Docker Desktop translation | PASS, seven services |

The TrueNAS-equivalent workflow used strict internal-CA TLS and never disabled
certificate verification. It verified the successful one-shot migration,
every service health check, and actual Docker port bindings; only gateway TCP
18443 was published. It enrolled three simulated devices, processed signed
heartbeats and 90 historical readings, created two utility accounts and one
network CIDR, exercised SCE pricing, produced an encrypted five-artifact
logical backup, verified every checksum, and restored a clean database at
Alembic `20260725_0016` with 98 public tables.

The browser coverage includes outside pointer/focus, Escape with focus return,
opening another menu, route change, scroll, resize, portal-owned content,
owner unmount, arrow/Home/End navigation, and destructive close-before-dialog
ordering. Lifecycle tests cover unused draft deletion, dependency-blocked
removal, stale dependency tokens, explicit effective-dated unassignment,
retirement, soft removal, Removed view, and restore without reassignment.

Acceptance exposed two test-harness issues and one real dropdown timing defect.
Windows sandbox ACLs prevented Python temporary-directory cleanup, and one
new browser assertion initially matched both a table header and status cell.
The full Python suite passed unchanged in the required unrestricted test
environment, and the browser assertion was narrowed to the status cell.
A clean-install unit rerun also exposed a stale `requestAnimationFrame` that
could close a newly reopened menu after Escape or route navigation. The shared
primitive now cancels pending focus frames and closes synchronously on route
changes; repeated unit and browser runs pass.
