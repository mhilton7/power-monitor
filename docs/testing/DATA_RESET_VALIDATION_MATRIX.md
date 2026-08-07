# Data-only reset validation matrix

Status values below reflect the 2026-08-06 source-tree validation. An automated
pass does not substitute for a listed physical or live-infrastructure gate.

| ID | Requirement | Automated evidence | Physical evidence | Status |
|---|---|---|---|---|
| PLAN-01 | Planning is read-only and includes site, categories, sensor classifications, exact row/file counts, byte estimates, pricing baseline, generation, boundary, revision and expiry | `test_data_reset_plan_inventory.py`, `test_data_reset_api.py` | Not required | Automated pass |
| PLAN-02 | Material site/sensor/pricing/boundary/generation changes invalidate a plan | `test_data_reset_api.py::test_material_site_change_invalidates_plan_before_execution`; plan inventory stale-scope tests | Not required | Automated pass |
| AUTH-01 | Only built-in admin plus `system.data_reset` can see/use reset | `test_data_reset_contract_foundation.py::test_reset_permission_is_high_risk_builtin_admin_only`; frontend permission/workflow tests | Not required | Automated pass |
| AUTH-02 | CSRF, recent password/MFA reauthentication, reason, acknowledgement and exact phrases are server-enforced | Data-reset API negative cases and `dataResetWorkflow.test.tsx` | Not required | Automated pass |
| IDEM-01 | Plan execution/idempotency and retry are payload-bound; duplicate clicks/requests do not duplicate work | `test_data_reset_api.py::test_data_reset_plan_execute_and_action_idempotency` | Duplicate prepare/commit during canary | Automated pass; physical canary not run |
| STATE-01 | Operation and participant states survive API/worker/database restart | Durable-operation/failure cases in `test_data_reset_execution.py`; UI reload-resume test | Optional service restart during canary | Automated durability pass; live PostgreSQL/service restart unavailable |
| BACKUP-01 | Default backup exists, hashes, opens, restores in isolation, and contains required tables before deletion | Backup restore-evidence and manifest-reverification cases in `test_data_reset_execution.py` | Verify opaque backup evidence | Automated pass; physical evidence not run |
| BACKUP-02 | Backup failure blocks deletion; permanent no-backup is separately confirmed and reported irreversible | Missing/inconclusive backup cases in `test_data_reset_execution.py`; no-backup API test | Not required | Automated pass |
| SERVER-01 | Selected raw readings, normalized intervals, device/site rollups, coverage/peak and gaps are gone | Deletion assertions in `test_data_reset_execution.py` and `test_data_reset_multi_sensor_integration.py` | History empty after canary | Host-fixture pass; live PostgreSQL and physical canary unavailable |
| SERVER-02 | Measurement heartbeats/status/events/audits/logs are deleted or sanitized without losing security evidence | Redaction/quarantine cases in `test_data_reset_execution.py`, `test_data_reset_regressions.py`, and contract foundation tests | Safe liveness still visible | Automated pass; physical canary not run |
| SERVER-03 | Cost runs/results/rollups, tier allocations/summaries/projections, old cycles and usage inputs are gone | `test_data_reset_pricing_history.py` and `test_data_reset_post_reset_cost.py` | Billing starts at zero | Host-fixture pass; live PostgreSQL and physical canary unavailable |
| PRICE-01 | Current account, plan, exact prices, tiers, TOU, thresholds, baselines, charges, adjustments and future assignments survive | Canonical dependency/hash checks in `test_data_reset_pricing_history.py`; first-cost proof in `test_data_reset_post_reset_cost.py` | First new cost matches expected rate | Automated pass; physical canary not run |
| PRICE-02 | Historical rate/source objects are deleted only when no active/future/cross-site dependency exists | Dependency-closure and cross-site cases in `test_data_reset_pricing_history.py` and `test_data_reset_site_mutation_barriers.py` | Not required | Automated pass |
| BILL-01 | Bill documents/artifacts remain by default and cannot recreate usage | Assigned-bill retention/barrier cases in `test_data_reset_billing_barriers.py`; reset pricing tests | UI warning reviewed | Automated pass; operator review not run |
| BILL-02 | Bill documents delete only under the separate privacy option | Plan allow-list and execution cases in the backend data-reset suite | Optional | Automated pass |
| OUTPUT-01 | Scoped reports/exports/jobs/files and measurement log copies are quiesced and removed; definitions remain | Export/log races and quarantine recovery in `test_data_reset_site_mutation_barriers.py`, `test_data_reset_execution.py`, and regression tests | Download endpoints return no old artifact | Automated pass; physical canary not run |
| GEN-01 | Generation increments once; cursor and sequence boundary never regress; new sequence is above boundary | `test_data_reset_ingestion_gate.py`, execution verification, enrollment/reenrollment regression cases, and final sensor native/sanitized suites | Sequence journal/new sample evidence | Automated pass; physical evidence not run |
| GEN-02 | Old/missing/ahead generation and at/below-boundary payloads receive typed failures and cannot update measurement state | Generation/replay cases in `test_data_reset_ingestion_gate.py`, `test_data_reset_regressions.py`, and final sensor protocol/storage suites | Replay captured old batch after reset | Automated pass; physical replay not run |
| SENSOR-01 | Prepare validates exact plan/card/build/generation, pauses safely, drains storage and is cancellable | Final sensor Python `116/116`, native and sanitized suites passed; focused enrollment/reset/storage cases included | Prepare/cancel with readings unchanged | Automated pass; physical canary not run |
| SENSOR-02 | Commit persists authorization first and resumes at every power-cut checkpoint | Final sensor native and sanitized suites each passed 128 randomized sequences / 14,080 events | Optional controlled reboot | Automated pass; physical canary not run |
| SENSOR-03 | Only reading segments/indexes/exports/metadata/trash are removed; manifest/events/unknown diagnostics/config/OTA/coredumps survive | Final sensor Python/native/sanitized/storage suites and `tools/check_repo.py` passed | SD inventory before/after | Automated pass; physical canary not run |
| SENSOR-04 | Reading backlog/queues/accumulator are empty and indexes are canonical after commit | Final sensor native/sanitized reset and storage suites passed | Device status and card inspection | Automated pass; physical canary not run |
| ENERGY-01 | PZEM reset is never called; raw counter survives; logical application energy starts at zero and rises | Final sensor Python/native/sanitized reset suites passed | Raw counter and two post-reset samples | Automated pass; physical canary not run |
| CONFIG-01 | Wi-Fi/static IP/server URL/CA/enrollment/UUID/admin/PZEM/CT/timezone/desired config are identical | Durable-source preservation digest, credential-reset identity regressions, and final sensor Python/native/sanitized suites passed | Device reconnects without provisioning | Automated pass; physical canary not run |
| FLEET-01 | Connected sensors prepare/commit/verify before their gate opens | `test_data_reset_multi_sensor_integration.py`, connected execution cases, and final sensor matrix | Connected canary | Automated pass; physical canary not run |
| FLEET-02 | Disconnected sensor cannot restore history and completes reset on reconnect | Disconnected execution/replay cases, multi-sensor integration, and final sensor matrix | Disconnect/reconnect canary | Automated pass; physical canary not run |
| UI-01 | Protected data-only wizard shows scope, categories, sensor state, default backup, separate privacy/no-backup warnings, exact phrases, progress and structured results | `dataResetWorkflow.test.tsx`, permission tests, and `data-reset.spec.ts` | Operator review | Automated pass; operator review not run |
| RECOVERY-01 | Precommit cancel restores quarantine and sensor gates without mutations | Cancel/quarantine cases in `test_data_reset_execution.py` and `test_data_reset_regressions.py`; final sensor matrix | Prepare/cancel canary | Automated pass; physical canary not run |
| RECOVERY-02 | Postcommit failures resume forward and never auto-restore or lower a boundary | Forward-recovery and commit-boundary cases in `test_data_reset_execution.py`; final sensor matrix and credential-reset boot regression | Optional interruption canary | Automated pass; physical canary not run |
| CAPACITY-01 | Reset admission remains available after a worst-case completed reset journal is retained | Event/cleanup/drain terminal-copy compaction, validated terminal-slot credit, exact/below thresholds, GC-floor assertions, and maximum tombstone bounds pass in the final native/sanitized suites | Real enrolled-device NVS inventory before two resets | Automated pass; physical two-reset evidence not run |
| BUILD-01 | Backend Ruff/format/Mypy/Pytest/contracts and migration upgrade pass | Ruff, format, Mypy, contracts/OpenAPI passed; Pytest `441 passed, 5 skipped`; migration/offline suite `25 passed` | Not required | Automated source gates pass; live Docker/PostgreSQL migration unavailable |
| BUILD-02 | Production frontend lint/typecheck/unit/build/e2e pass on pinned Node/npm | Pinned lint/typecheck, `168` unit tests, and production build passed; same-source Playwright `277 passed, 62 skipped` | Not required | Automated pass |
| BUILD-03 | Sensor Python/web/native/sanitized/release/debug/sim builds and repo checks pass | Python `116/116`; web format, `36/36` tests and build; native/sanitized; release/debug/simulated-meter builds; `tools/check_repo.py` all passed | Not required | Automated pass |
| PHYS-01 | Approximately one-hour connected and disconnected physical canary passes | Automated prerequisites only | Required when hardware is available and separately authorized | Not run — no hardware authorization to flash or test in this task |

## Final automated evidence recorded 2026-08-06

- Server: Ruff check/format, Mypy, contracts, and OpenAPI validation passed;
  full Pytest completed with `441 passed, 5 skipped`. The five skips require
  unavailable external PostgreSQL/Docker infrastructure.
- Migration: migration-contract and offline migration coverage completed with
  `25 passed`. A real PostgreSQL upgrade was not run.
- Deployment configuration: static Compose and TrueNAS checks passed. Docker
  Desktop, a live Compose stack, and a deployed TrueNAS target were unavailable.
- Production frontend: pinned lint, typecheck, `168` unit tests, and build
  passed. The same-source Playwright run completed with `277 passed, 62
  skipped`.
- Sensor: frozen-source fingerprint
  `a3c0aa1b6d8978f567cb854c471d3670231327b9c58ccfbb7fc762a18a699936`;
  Python `116/116`, web format and `36/36` tests/build, native and sanitized
  reliability suites, release/debug/simulated-meter builds, and
  `tools/check_repo.py` all passed. Release RAM was `77,784 / 327,680`
  (`23.7%`) and flash was `1,788,829 / 6,291,456` (`28.4%`); release binary
  SHA-256 was
  `09a4447fcaa8f0811b15e5a44d9803f374a4fffb1da18d1dda7d23ed347c3f0c`.
- Capacity: `CAPACITY-01` passes automated validation. Completed event,
  cleanup, and drain evidence replaces both atomic slots with compact
  tombstones. A later prepare credits only semantically validated terminal
  slot allocations and always retains a full 126-entry NVS garbage-collection
  page plus a 32-entry rewrite margin. A real enrolled-device two-reset canary
  remains part of physical validation.
- Hardware: no ESP32 flashing, connected/disconnected canary, PZEM exercise,
  or physical SD inventory was authorized or run.

## Baseline captured before implementation

- Backend: Ruff check/format, Mypy, contracts, and full Pytest passed (`341 passed, 5 skipped`).
- The now-retired comparison frontend passed lint/typecheck/unit/build and 51 e2e checks before removal.
- Production `frontend`: lint/typecheck, `163` unit tests, and build passed. Baseline Playwright completed `264 passed, 62 skipped, 1 failed`; the isolated mobile firmware-menu test could not find `Outdoor-AC` and is tracked as a pre-feature baseline flake.
- Sensor: `104` Python tests, web `36` tests/build, native/native-sanitized, release/debug/simulated-meter builds, and repository checks passed on the pre-reset implementation.
- PostgreSQL/Compose baseline was unavailable because Docker Desktop was not running and no supported host PostgreSQL client/service was installed.
- Host Node/npm were `26.0.0`/`11.12.1`, outside release pins (`24.4.0`/`11.4.2`); release evidence must use the pinned toolchain.

Final release status must link every automated row to a named test/log and must leave `PHYS-01` explicitly unpassed unless the authorized hardware procedure actually ran.
