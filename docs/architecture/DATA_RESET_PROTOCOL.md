# Coordinated data-only reset protocol

Status: normative design for `data-reset/1.0.0`
Application protocol: additive extension to `pm-protocol/1.0.0`

## Purpose and invariants

The coordinated reset clears electrical measurement, derived cost, selected historical-pricing, and generated-output data while preserving the installation. It is intentionally different from the firmware's credential-erasing factory reset.

The protocol has five invariants:

1. Device identity remains the UUID. Credentials, enrollment, network and meter configuration, administrator state, OTA evidence, events, and coredumps are not reset.
2. Each site and sensor has a monotonically increasing `data_generation`. Each committed sensor boundary is monotonically increasing. Neither generation nor sequence is reset to one or zero.
3. A pre-reset payload can never repopulate History. The server accepts measurement data only when its generation equals the required device generation and every sequence is above the committed reset boundary.
4. Sensor prepare is reversible. Sensor commit authorization is durable before the first irreversible local mutation. The server records its central commit point separately. Cancellation is rejected after either commit point.
5. Logical application energy is re-baselined in software. The PZEM cumulative-energy counter is read for evidence and never reset.

The deletion allow-list and preservation rules are normative in `docs/audits/DATA_ONLY_FACTORY_RESET_SCOPE.md`.

## Browser API

All browser mutations require a valid session, CSRF header, built-in `admin` role, `system.data_reset`, and recent reauthentication at execute/retry/cancel time. Browser responses use RFC 9457-style repository problem details and never expose host paths, secrets, signatures, raw readings, raw sensor receipts, or PZEM values. The authenticated device receipt carries bounded cumulative-energy baseline evidence required by the reset protocol; the worker verifies it and redacts those values from permanent central audit copies.

### Create a read-only plan

`POST /api/v1/system/data-reset/plan`

```json
{
  "site_id": "site-uuid",
  "categories": [
    "measurement_history",
    "cost_history",
    "pricing_history",
    "generated_outputs"
  ],
  "delete_imported_bill_documents": false,
  "disconnected_sensor_policy": "defer_until_reconnect"
}
```

Planning reads current rows, files, device/card evidence, active pricing and boundaries. It does not pause a sensor, write a sensor, change a cursor, enqueue a backup, or delete data. For a reset-capable connected sensor, the authenticated storage probe must report a coherent prepare projection: durable and queued records plus the one active partial interval, if present, with its projected sequence and syncability. A busy or incoherent projection is retryable and cannot authorize a plan. The connected-sensor total is the exact projected record count to delete now; backlog remains a separate sequence-span value and is never substituted for row count. Disconnected inventory is explicitly marked last-reported or unavailable and is not included in the immediate exact total. The durable plan contains an opaque `plan_id`, revision, expiry, reset timestamp proposal, next generation, exact deletion counts, participant classifications, canonical active-pricing hash, per-device projected boundary snapshot, and a request fingerprint.

A material change to site identity/revision, sensor membership/lifecycle/build/generation/card evidence, sequence boundary, selected pricing, categories, deletion counts, or another active reset invalidates execution. Counts are checked again after the ingestion gate is acquired and before the central transaction commits; a mismatch requires a newly reviewed plan.

### Execute a plan

`POST /api/v1/system/data-reset/execute`

```json
{
  "plan_id": "plan-uuid",
  "plan_revision": 1,
  "idempotency_key": "client-uuid",
  "reason": "Commissioning restart after validation",
  "backup_mode": "verified_backup",
  "confirmation_phrase": "RESET ALL READINGS AND PRICING HISTORY",
  "permanent_without_backup_acknowledged": false
}
```

`backup_mode` is `verified_backup` by default. `permanent_without_backup` additionally requires `permanent_without_backup_acknowledged=true` and the exact phrase `PERMANENTLY RESET ALL READINGS AND PRICING HISTORY WITHOUT BACKUP`. Bill-document deletion must already be bound into the plan and displays its own privacy warning.

The same idempotency key plus the same canonical request returns the original operation. Reuse with different input returns `idempotency_conflict`. A plan can create at most one operation.

### Observe and control

```text
GET  /api/v1/system/data-reset/{operation_id}
POST /api/v1/system/data-reset/{operation_id}/retry
POST /api/v1/system/data-reset/{operation_id}/cancel
```

Status is refresh-safe and includes only safe operation metadata, stage, backup verification metadata, deletion counts, preservation hashes, and participant states. Retry resumes an explicitly paused `attention_required` or `partial_failure` checkpoint; it does not create a second reset. Completed, cancelled, and `failed_before_commit` operations are terminal and require a new plan. Cancel succeeds only before `database_reset_committed` and before every participant's `commit_authorized` checkpoint.

## Durable server state machines

Operation states:

```text
planning
  -> awaiting_confirmation
  -> preparing_sensors
  -> sensors_prepared
  -> backup_running
  -> backup_verified
  -> database_reset_running
  -> database_reset_committed
  -> sensor_commit_running
  -> verification_running
  -> completed
     | completed_with_resets_pending_on_reconnect
     | partial_failure
     | attention_required

Pre-commit terminal states: cancelled, failed_before_commit
Explicit-retry pause states: partial_failure or attention_required
```

Participant states:

```text
pending | unreachable | unsupported | prepare_requested | prepared
commit_requested | committed | verified | pending_reconnect | failed
attention_required | not_applicable
```

One partial unique database constraint permits only one nonterminal operation per site. A transaction/advisory lock serializes plan execution and participant transitions. Every transition validates its predecessor and increments the operation revision. Safe audit events record operation/participant IDs, generations, boundary, counts, hashes, reason and error codes—not measurement or credential material.

Disconnected supported sensors become `pending_reconnect` if the plan policy permits deferral. Their server ingestion gate is installed at central commit. On the next signed heartbeat they receive `sensor_reset_required`; the worker runs the same prepare/commit protocol before their reading gate opens. An active unsupported sensor remains gated and can advertise `data-reset/1.0.0` from a signed, measurement-redacted heartbeat after a firmware upgrade. A sensor classified connected at confirmation must prepare successfully before central commit; a later communication failure pauses the operation in `attention_required`. Revoked and removed inventory is explicitly `not_applicable`, is never gated, and does not leave the operation pending. Changed-card and configuration-mismatched devices never silently pass verification.

Sensor reading storage is device-wide and does not carry a site-assignment
epoch. A site-scoped plan therefore fails closed with
`data_reset_historical_device_scope_unsafe` when its historical measurement or
assignment lineage contains a sensor that is now assigned to another site. The
server cannot safely choose between replaying the old site's SD backlog and
deleting the new site's unsynchronized backlog. A future supported migration
requires either a durable transfer sequence boundary or a site epoch on every
local record; current operators must resolve the sensor backlog before transfer.

## Device API

The sensor advertises capability `data-reset/1.0.0`. All four routes use the existing server-to-device HMAC authentication and exact six signed headers. `prepare`, `commit`, and `cancel` are mutations; `status` is read-only but authenticated. A device UUID in a body must equal the authenticated device.

```text
POST /api/v1/data-reset/prepare
POST /api/v1/data-reset/commit
GET  /api/v1/data-reset/status?operation_id=<uuid>&target_generation=<n>
POST /api/v1/data-reset/cancel
```

Canonical prepare body:

```json
{
  "protocol": "data-reset/1.0.0",
  "operation_id": "operation-uuid",
  "device_id": "device-uuid",
  "target_generation": 4,
  "reset_timestamp": "2026-08-06T08:00:00Z",
  "plan_revision": 1,
  "plan_digest": "sha256-hex",
  "categories": ["measurement_history"],
  "expected_boundary": 98211,
  "server_highest_contiguous": 98211,
  "server_maximum_seen": 98211,
  "expected_firmware_version": "1.0.18",
  "expected_build_hash": "build-hash-or-null",
  "expected_card_generation": "card-generation-or-null"
}
```

Prepare validates identity, exact operation parameters, supported categories/build, generation monotonicity, sequence capacity, mounted writable card and card generation, retention recovery, PZEM availability, and absence of an OTA/recovery/configuration mutation. It then:

1. freezes configuration, reenrollment, administrator/network reset, factory reset, desired-configuration apply, and OTA;
2. gates reading upload and new aggregation writes while leaving safe diagnostics/events available;
3. closes and durably drains the partial interval and every already admitted record write through an acknowledged storage barrier without deleting any reading;
4. captures the trustworthy local floor/newest/next, server cursors, card/build/boot and keyed configuration-preservation evidence;
5. writes and reads back the durable prepared record before returning its receipt.

Potentially slow work may initially return `202` with state `preparing`; status polling returns the stored prepared receipt. Identical retry returns the same receipt. Changed parameters under the same operation return `reset_operation_conflict`.

The prepared receipt includes the exact HMAC-covered fields `prepare_drain_records_added` (zero through two), `prepare_drain_first_sequence` and `prepare_drain_last_sequence` (integers or `null`), and `prepare_drain_syncable_records_added`. They must match the probe's approved projection together with local count, backlog, next/newest sequences, and reset boundary. The zero case requires both sequence fields to be `null` and the syncable count to be zero; a non-empty drain must be a contiguous range whose length matches its record count. The two-record bound covers both a just-finished interval and an already-admitted pre-freeze poll without dropping either. Any unexplained queue, rollover, retention, cursor, or boundary change invalidates the plan before central deletion.

Canonical commit body:

```json
{
  "protocol": "data-reset/1.0.0",
  "operation_id": "operation-uuid",
  "device_id": "device-uuid",
  "target_generation": 4,
  "plan_revision": 1,
  "plan_digest": "sha256-hex",
  "approved_boundary": 98211,
  "prepared_receipt_digest": "hmac-sha256-hex"
}
```

The approved boundary must be no lower than the maximum of all signed server and prepared sensor evidence:

```text
expected boundary
server highest contiguous
server maximum seen
local sequence floor
next sequence minus one
newest stored
newest syncable
server acknowledgement
sensor maximum seen
prepared-removal floor
```

The reset boundary is capped at `9223372036854775805` (`INT64_MAX - 2`). This reserves space for the first post-reset sequence and the allocator's next value while keeping every server protocol-sequence column within PostgreSQL `BIGINT`. Commit revalidates the same mounted card and operation. Under the meter mutex it captures raw PZEM energy once, derives an overflow-checked corrected absolute baseline, and persists `commit_authorized` before mutating any cursor or file.

The durable commit checkpoints are:

```text
commit_authorized
sequence_advanced
cursors_advanced
readings_cleared
baseline_installed
verified
completed
```

At `sequence_advanced`, the journal floor is at least the approved boundary. At `cursors_advanced`, reading acknowledgement and maximum-seen are at least that boundary and the target generation is installed. At `readings_cleared`, only positively classified reading segments/indexes/exports/metadata/trash have been removed; event data, manifest, card generation and unknown diagnostic artifacts are untouched. At `baseline_installed`, application energy becomes:

```text
max(0, energy_offset_wh + raw_pzem_wh - energy_baseline_absolute_wh)
```

At verification, the sensor recomputes the keyed semantic configuration digest and checks card generation, sequence/cursors, reading inventory, PZEM availability, and preserved stores. The signed completion receipt binds the prepared receipt digest and carries `prepared_pzem_energy_wh`, `commit_pzem_energy_wh`, and `verified_pzem_energy_wh`; both later captures must be nondecreasing. The server verifies this ordering and redacts all three raw values from persistent safe/audit copies. The sensor durably reads back the completion receipt before reopening new-generation aggregation/sync. A duplicate commit returns that receipt and does not run cleanup again.

Cancel accepts the exact prepared operation and is valid only before `commit_authorized`. It leaves all pre-prepare records, sequence, cursors, generation and energy baseline unchanged, removes the gates, and resumes normal work. To keep the reviewed deletion count and boundary exact, a connected sensor pauses new measurement recording after its final prepare drain until commit or cancel; this pause includes backup verification time. The sensor persists pause start/end diagnostic evidence (and unavailable-coverage evidence on resume) so the gap is never silent. The Web UI discloses the pause before authorization. A reboot in `prepared` remains paused; a reboot in any commit checkpoint resumes the next incomplete checkpoint before producers start; `attention_required` allows signed status/heartbeat only and no reading upload.

## Wire generation and ingestion gate

`data_generation` is an additive nonnegative integer in the heartbeat, reading-batch root, and each durable reading. Generation zero is the migration value for devices that have never completed a coordinated reset. Once `DeviceDataState.generation > 0`, omission is invalid.

The server performs the gate before persisting measurements, status measurement fields, gaps, rollups, or acknowledgements, and rechecks it after pull-network I/O:

- payload generation below required: `409 data_generation_obsolete`;
- generation above required: `409 data_generation_ahead`;
- matching generation but any sequence at/below boundary: `409 reading_precedes_reset_boundary`;
- pending participant/gate: `409 sensor_reset_required`;
- missing generation after first reset: `422 data_generation_required`.

An obsolete signed heartbeat may update only safe reachability/identity evidence needed to coordinate the pending reset. It cannot update watts, energy, backlog, History freshness, reading cursor, or OTA verification fields that could be confused with current-generation evidence.

Raw-reading immutability and deduplication remain `(device_id, sequence)`. The additive generation gate rejects stale payloads, and every reset advances the sequence floor so a sequence is never reused. Pull and push continue through the same ingestion service.

### Enrollment generation handoff

Enrollment into a site that has already committed a reset is itself a generation
transition. The server derives the required site generation as the maximum of
`SiteDataState` and every centrally committed site reset, heals a missing or
stale-low site state under the site mutation lock, and returns that generation
and boundary in `sync_policy`. A client that does not advertise
`data-reset/1.0.0` is rejected before the one-time token is consumed or a
credential is created.

A capable sensor must validate and durably install the returned generation and
boundary as part of the same local enrollment publication that installs the
device UUID and secret. It must not mark enrollment active, send a heartbeat, or
upload a reading first. On boot, a partially published enrollment remains
fail-closed until the complete credential and generation policy can be read
back. This behavior is part of the `data-reset/1.0.0` capability contract.

Reenrollment is narrower. A decommissioned UUID can be reused only at the same
site and only when every nonzero required generation has a verified participant
receipt from a centrally committed reset. Cross-site reuse is rejected because
local SD records carry no site epoch; matching numeric generations do not make
that backlog safe to reattribute. A future authenticated transfer/catch-up
protocol may replace this restriction, but ordinary enrollment cannot.

## Pricing boundary

Before execution, the server hashes a canonical, ordered document containing the effective `RateVersion`, normalized rule children, current assignment context, account adjustment values and future assignments. At reset it keeps the exact active immutable rate graph, ends the old assignment period, creates an equivalent assignment at the reset timestamp, and creates a partial billing cycle/calculation period with zero usage, tier and cost accumulation. A clone is permitted only when deleting source evidence would otherwise retain an unwanted dependency; its canonical hash and every Decimal must match.

Historical versions are deleted only after a cross-site/current/future dependency traversal. Bill PDFs/OCR/source artifacts remain by default. Their old usage/cycle links are detached and `history_cleared_at` is recorded. Permanent bill-document deletion is a separate plan option and occurs only after active pricing is safely rematerialized.

## Backup and recovery boundary

The default execute path queues a `backup_create` operation with stable key `data-reset:<operation-id>:backup` and requires the existing isolated restore verifier to record a nonempty artifact, lowercase SHA-256 manifest hash, migration revision, more than twenty restored tables, all five required tables, status-layout inventory, and PostgreSQL major version. A bare `verified` status or incomplete `verification_details` fails before commit. The manifest is read and hashed again immediately before central deletion. Only then can the operation enter `database_reset_running`.

Filesystem outputs are first moved into an operation-specific, path-validated quarantine journal. A pre-commit failure or cancellation restores them. The database transaction then deletes scoped rows, installs generation/cursors/pricing boundary and records `database_reset_committed`. Quarantine is purged afterward. A post-commit failure is resumed; a backup is never restored automatically.

Restoring a pre-reset backup can resurrect an operation whose sensors have progressed in the physical world. Recovery therefore starts reset workers in reconciliation-only mode, inspects every participant's signed status/card/generation, and requires an administrator decision before cancel/resume. It never automatically repeats central deletion or lowers a sensor boundary.

## Compatibility and versioning

The shared identifier remains exactly `pm-protocol/1.0.0`; the fields are additive and initially optional for generation-zero devices. Reset support is independently advertised as `data-reset/1.0.0`. Sensor firmware implementing this protocol is `1.0.18`; existing `1.0.17` artifacts remain immutable. Unsupported sensors remain gated/pending and cannot upload old history after a central reset.
