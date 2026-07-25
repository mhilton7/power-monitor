# Single-pass repair test results

Final status: **PASS** on 2026-07-25.

## Static, contracts, and backend

| Command/gate | Result |
| --- | --- |
| `python -m ruff check backend worker simulator` | PASS |
| `python -m ruff format --check backend worker simulator` | PASS, 118 files |
| `python -m mypy backend/app worker/app simulator/simulated_device` | PASS, 69 files |
| `python scripts/validate_contracts.py` | PASS |
| `python -m pytest -q -c backend/pyproject.toml --ignore-glob=*pytest-cache-files-*` | PASS, 179 passed / 3 opt-in gates skipped |
| PostgreSQL opt-in integration gate | PASS, 1 test |
| 100-device opt-in load gate | PASS, 1 test |

The three normal-suite skips were executed separately where applicable:
PostgreSQL passed, the 100-device load gate passed, and the underlying
TrueNAS workflow passed directly with its rendered deployment.

Two diagnostic invocations were intentionally not counted as product
failures:

- invoking pytest without `backend/pyproject.toml` did not load the required
  async configuration;
- a later sandboxed collection encountered Windows ACL denial while scanning
  stale generated `pytest-cache-files-*` directories.

The final configured suite excluded only those generated cache directories
and passed all 179 product tests.

## Frontend

| Command/gate | Result |
| --- | --- |
| `npm run lint` | PASS |
| `npm run typecheck` | PASS |
| `npm run test` | PASS, 5 files / 16 tests |
| `npm run build` | PASS, 1,732 modules transformed |
| `npm run architecture` | PASS |
| `npm run e2e` | PASS, 72 passed / 8 inapplicable selector cases skipped |
| `npm run e2e:repair` against Vite preview | PASS, 24 tests |
| `npm run e2e:repair` against production nginx container | PASS, 24 tests |

The final verified local bundle contains 12 chunks, 574,326 bytes of
JavaScript, the
45.90 kB generated CSS asset, and no legacy frontend modules.

## Docker and TrueNAS

| Gate | Result |
| --- | --- |
| `docker compose build` | PASS, API and frontend |
| backup image build | PASS |
| `docker compose up -d --wait` | PASS |
| standard stack health | PASS: API, worker, PostgreSQL, frontend, Caddy |
| `docker compose config --quiet` | PASS |
| static Compose hardening validator | PASS |
| TrueNAS template + ICMP overlay validator | PASS |
| rendered TrueNAS deployment validator | PASS |
| TrueNAS-equivalent end-to-end workflow | PASS |

The isolated TrueNAS workflow reported:

```text
services=7
devices=3
readings=90
utility_accounts=2
network_cidrs=1
backup=verified
restore=verified
ports=verified
```

Migration service exit was zero at head `20260724_0015`. Encrypted archive
checksums for database, firmware, config, reports, and rate-source artifacts
were valid. The dump restored into a clean database at the same migration
head.

## Visual and accessibility evidence

All required visual states match committed deterministic snapshots at the
four required viewport sizes. The browser tests verify focus placement,
visible focus, labelled dialog/tab semantics, keyboard tab/disclosure
navigation, Escape, focus return, hidden-content exclusion, modal/body
cleanup, nonzero geometry, z-order, horizontal overflow, and bounding-box
collisions.
