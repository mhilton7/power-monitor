# OTA validation matrix

This matrix defines the evidence required before a firmware deployment is reported as complete. The API is authoritative: the browser renders `FirmwareDeploymentView.verification` and does not infer sensor health from elapsed time, enrollment, or a progress animation.

## Lifecycle rendering

| Persisted state | User-facing state | Progress rule | Available action |
| --- | --- | --- | --- |
| `waiting_canary`, `scheduled`, `offered` | Waiting for sensor | Indeterminate | Cancel where the server allows it |
| `manifest_authenticated` | Preparing download | Indeterminate | Cancel |
| `waiting_for_schedule` | Waiting for update window | Indeterminate | Cancel |
| `download_started`, `downloading` | Downloading | Determinate only when the API marks authenticated progress as determinate | Cancel where safe |
| `binary_verified` | Verifying | Determinate only with authenticated evidence | Cancel where safe |
| `partition_written` | Writing partition | Determinate only with authenticated evidence | Observe |
| `rebooting` | Rebooting | Indeterminate | Observe |
| `post_boot_validation` | Validating locally | Indeterminate | Observe |
| `validated` | Waiting for server stabilization | Indeterminate | Observe |
| `awaiting_heartbeat` | Waiting for first reading or server stabilization, selected by the authoritative blocker | Indeterminate | Observe |
| `completed` | Completed | Terminal; no spinner | Start another update |
| `failed` | Failed | Terminal; no spinner | Retry |
| `rollback_detected`, `rolled_back` | Rolled back | No success animation; `rolled_back` is terminal | Retry after terminalization |
| `cancelled` | Cancelled | Terminal; no spinner | Retry or start another update |

## Post-update evidence

The firmware screen must display the exact API evidence for:

- expected and observed target version;
- expected and observed target build hash;
- observed target boot ID;
- PZEM health;
- writable microSD storage;
- trusted time;
- received and required verification heartbeats;
- elapsed and required stabilization time;
- first accepted post-update durable reading;
- blocking critical-alert count;
- last sensor activity and last OTA report;
- previous boot stage and reset reason, when reported;
- rollback state; and
- exact failure code.

The server supplies one current `verification.blocker`. The browser displays its code, title, detail, and action semantics without replacing it with a generic “waiting for heartbeat” message.

## Terminal reconciliation outcomes

| Evidence classification | Expected terminal code | Expected UI |
| --- | --- | --- |
| Sensor absent after offer/write/reboot | `ota_sensor_did_not_return` | Failed, retained evidence, Retry |
| Reboot interrupted download before install | `ota_interrupted_before_install` | Failed, previous stage/reset evidence, Retry |
| Target observed and previous image returned | `ota_rollback_detected` | Rolled back, restored identity, Retry |
| Target did not stabilize | `ota_post_boot_timeout` | Failed, incomplete checklist and blocker, Retry |
| Authenticated download stopped reporting | `ota_update_timed_out` | Failed, last report and byte evidence, Retry |
| Legacy deployment lacks sufficient identity evidence | `ota_legacy_evidence_incomplete` | Failed and actionable; never left active indefinitely |
| Manifest expired before retrieval | `ota_manifest_expired` | Failed, no spinner, Retry |
| Canary/deployment window expired | `ota_deployment_expired` | Failed, no spinner, Retry |

## Automated validation

| Test surface | Required assertion | Evidence location |
| --- | --- | --- |
| Runtime adapter | Checklist, blocker, exact identity, timestamps, recovery and rollback fields survive snake-case conversion | `frontend/tests/adapters.test.ts` |
| One-sensor dialog | Current blocker and full checklist render; first-reading and stabilization states are distinct | `frontend/tests/firmwareUpdate.test.tsx` |
| One-sensor dialog | Terminal timeout has no spinner/progress loop and exposes Retry | `frontend/tests/firmwareUpdate.test.tsx` |
| Fleet workflow | Terminal rows stop polling/spinning; Cancel and Retry follow persisted state | `frontend/tests/firmwareFleet.test.tsx` |
| Browser workflow | A verified image enters Preparing download with indeterminate authenticated-progress messaging | `frontend/e2e/single-home.spec.ts` |
| Backend lifecycle | Monotonic transitions, stale-attempt rejection, idempotency, retry reset, locking, stale terminalization | `backend/tests/test_firmware_lifecycle.py`, `backend/tests/test_existing_trust_ota.py` |

### Measured automated result

The focused backend OTA lifecycle and fault-injection selection completed
**30/30 tests successfully** on 2026-08-03. The run covered:

- exact duplicate report idempotency;
- rejection of stale, lower `evidence_sequence` reports;
- the permitted equal-sequence forward replay and monotonic downloading cases;
- prevention of a delayed older failure from terminalizing a later state;
- attempt isolation and retry evidence reset;
- per-deployment locking and worker-driven reconciliation; and
- timeout and stale-deployment terminalization.

The complete frontend unit/component suite (163 tests), default Playwright
matrix (265 passed, 62 intentionally skipped), and repair E2E matrix (36/36)
also passed with the OTA adapter, one-sensor dialog, fleet workflow, and browser
coverage listed above included in those runs.

Automated OTA lifecycle status: **PASS**. Physical OTA status: **PENDING**.

Physical canary duration, exact firmware checksum, observed heartbeats/readings, rollback trial, and post-restart evidence belong in `docs/ota/HARDWARE_CANARY_RESULTS.md`. They must not be inferred from these automated tests.
