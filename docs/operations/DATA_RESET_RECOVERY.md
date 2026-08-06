# Data-only reset operator and recovery runbook

This runbook applies only to the coordinated **data-only** reset. The credential-erasing firmware factory reset is a separate recovery action and must not be substituted.

## Before planning

1. Confirm the operator is the built-in administrator, has `system.data_reset`, and can complete password or MFA reauthentication.
2. Confirm the target site by name and UUID. Inventory active, disconnected, unsupported, revoked, removed, or OTA-busy sensors.
3. Resolve any backup restore/replace-all job, active reset, active firmware change, sensor retention recovery, or unknown SD artifact before execution.
4. Prefer `verified_backup`. Permanent no-backup execution is intentionally irreversible. Imported bill documents remain unless the distinct privacy option is selected.
5. Generate a plan and review every count, sensor boundary/generation, active pricing plan/version, future assignment, preserved-data list, proposed reset time, revision, and expiry. Planning is read-only.
6. If planning reports `data_reset_historical_device_scope_unsafe`, stop. A
   sensor that previously belonged to the site is now assigned elsewhere, and
   its device-wide SD records have no site epoch. Do not reset that sensor from
   the former site or bypass the guard; resolve its backlog and assignment with a
   transfer-boundary migration procedure first.

## Normal execution

1. Select **Create and verify backup before reset**.
2. Enter a reason that identifies the operational purpose without including readings, passwords, tokens, or other sensitive data.
3. Reauthenticate and enter `RESET ALL READINGS AND PRICING HISTORY` exactly.
4. Keep the progress page open or safely refresh it; the operation is durable and status is reload-safe.
5. Do not power-cycle prepared sensors unless needed to validate reboot recovery. Prepared sensors intentionally remain paused through a reboot.
6. Treat `database_reset_committed` as irreversible. Cancel is unavailable after this state or after any sensor has durably authorized commit.
7. At completion, verify History/Billing are empty at the boundary, current pricing is unchanged, connected participants are `verified`, deferred participants are clearly `pending_reconnect`, and new readings/costs begin in the new generation.

The permanent option requires its own acknowledgement and `PERMANENTLY RESET ALL READINGS AND PRICING HISTORY WITHOUT BACKUP`. The system must display and audit that there is no automatic recovery source.

## State-specific response

| State | Meaning | Operator action |
|---|---|---|
| `awaiting_confirmation` | Plan is valid and no mutation occurred | Review or let it expire; regenerate after material changes |
| `preparing_sensors` | Sync/config/OTA gates may be active; no irreversible reset should have happened | Retry transient network errors or cancel if every participant remains precommit |
| `sensors_prepared` | Connected participants are durably paused and reversible | Proceed to backup or cancel; cancellation must receive a signed sensor receipt |
| `backup_running` | Existing backup pipeline is creating/verifying recovery material | Wait; never bypass a failed backup without creating a new permanent no-backup operation |
| `failed_before_commit` | Central data remains intact | Inspect safe error code, verify prepared sensors resumed, then create and review a new plan; this operation is terminal |
| `database_reset_running` | Central transaction/quarantine is in progress | Do not restart services voluntarily; if interrupted, use reconciliation below |
| `database_reset_committed` | Central data boundary is irreversible | Never cancel or restore automatically; resume participant commit/verification |
| `sensor_commit_running` | Sensor checkpoints may be partially complete | Retry idempotently; do not factory-reset or format a card |
| `completed_with_resets_pending_on_reconnect` | Central reset is valid; disconnected sensors remain gated | Reconnect the original card/device and let authenticated prepare/commit finish before reading sync |
| `partial_failure` | Some post-commit work remains | Preserve backup and receipts; retry only failed checkpoints |
| `attention_required` | Safety evidence mismatched, such as card/config digest or unknown storage | Keep reading ingestion gated; investigate physically and follow the mismatch procedure |

## Restart reconciliation

On API, worker, database, or sensor restart:

1. Load the durable operation and participant checkpoints. Never infer completion from missing files alone.
2. Keep every participant reading gate closed until its signed `completed` receipt matches operation ID, plan digest, target generation, approved boundary, card generation and preservation digest.
3. Query authenticated sensor status. An identical checkpoint is safe to retry. A higher generation, different card, changed plan digest, or configuration mismatch becomes `attention_required`.
4. Before central commit, restore file quarantine and cancel only if no participant is at or past `commit_authorized`.
5. At or after central commit, resume forward only: sensor sequence, cursors, allow-listed reading cleanup, logical baseline, verification and output-quarantine purge.
6. Re-run server postconditions and canonical pricing/configuration digests before changing a terminal error to completed.

## Common failures

### Backup failed or cannot be verified

- Deletion must not start.
- Treat missing migration, table-inventory, required-table, status-layout, PostgreSQL-version, size, or manifest-hash evidence as a failed verification even if a stale record says `verified`.
- Keep the `BackupRun` and safe failure evidence.
- Retry creation/verification after storage, encryption-key, PostgreSQL, or artifact errors are resolved.
- Do not change the existing operation to no-backup. Generate a new plan/operation and complete the stronger permanent confirmation if that is the administrator's deliberate choice.

### Sensor unreachable

- If the approved policy is defer-on-reconnect, central commit may proceed with the participant `pending_reconnect` and its ingestion gate closed.
- This deferral applies only to sensors already classified disconnected or unsupported in the confirmed plan. A prepare failure from a planned-connected sensor pauses the operation before central commit and requires explicit retry.
- A signed obsolete-generation heartbeat may update only safe reachability and initiate reset; it may not update measurements or cursors.
- Do not remove the gate or manually advance the participant to verified.

### Unsupported, revoked, or removed inventory

- An active unsupported participant stays gated after central commit. A signed gated heartbeat may refresh only its reset/OTA capability metadata; measurement fields remain rejected. After compatible firmware is advertised, the worker can resume authenticated prepare/commit.
- Revoked or removed inventory is recorded as `not_applicable`. It is not gated, is excluded from pending-participant completion counts, and cannot block resets for unrelated sites.

### Enrollment or reenrollment after a committed reset

- A fresh sensor must advertise `data-reset/1.0.0` and atomically install the
  returned `sync_policy.data_generation` and `reset_boundary` with its local
  enrollment before its first heartbeat. If the claim is rejected with
  `enrollment_data_generation_unsupported`, upgrade the unenrolled sensor; do
  not bypass the gate or manually lower the site generation.
- A decommissioned sensor may reenroll only at its former site after its reset
  generation has verified central and local evidence. The server returns
  `reenrollment_requires_data_reset_recovery` for a pending, unverified, stale,
  missing, or cross-site handoff and leaves the one-time token unconsumed.
- Do not move a card or reuse the UUID at another site. Clear or transfer local
  backlog only through a future authenticated device-wide catch-up workflow.

### Different or missing SD card

- Keep the participant `attention_required`. A replacement card cannot prove the prepared card was cleared.
- Reinsert the original card and retry authenticated status/commit. If the original card is lost, record that external data-bearing media remains outside the verified result; do not claim local-history deletion.
- Never format either card as part of this workflow.

### Unknown SD artifact or active retention transaction

- The sensor fails closed before deletion.
- Complete deterministic retention recovery, classify the artifact in the scope audit and add a test before expanding the allow-list.
- Never use a recursive wildcard cleanup as a workaround.

### PZEM unavailable after commit authorization

- Keep producers and reading sync paused and retain the committed boundary.
- Restore meter communication and retry the same checkpoint. Do not fabricate an energy baseline and do not issue a PZEM reset command.

### Configuration-preservation digest mismatch

- Keep the participant gated in `attention_required`.
- Compare category digests and safe configuration revision evidence; do not log or return the enrollment secret, Wi-Fi password, CA private material, password verifier, or HMAC key.
- Restore configuration only through the normal configuration/recovery workflow with explicit administrator authorization. A reset retry must not overwrite it.

### Sensor reports `data_reset_persistence_capacity_insufficient`

- Treat the `507` as a fail-closed prepare failure; no central deletion or
  sensor reading cleanup may start for that operation.
- Do not erase NVS, enrollment, Wi-Fi, administrator state, OTA evidence, or
  terminal journals to force admission.
- Firmware 1.0.18 compacts completed auxiliary journals and credits only a
  validated terminal slot already included in the next operation's worst-case
  live set. The capacity floor still preserves a complete NVS garbage-
  collection page and rewrite margin; a remaining `507` therefore means the
  currently installed configuration/other namespaces leave insufficient room.
- Record safe NVS used/free/required counts and reproduce on a canary. Do not
  bypass the guard; reduce unrelated authorized NVS use through its owning
  workflow or deploy a separately validated storage-layout release.

### Server pricing hash mismatch

- Stop before central commit if possible. If post-commit, keep the operation `attention_required` and preserve the backup.
- Compare the ordered active `RateVersion` graph, assignments and account adjustments using Decimal values.
- Rematerialize the exact pre-reset active configuration from the verified backup/evidence; never select a different plan as a shortcut.

### Stale readings appear after reset

- Immediately keep/restore the per-device ingestion gate and stop derived workers for the affected site.
- Capture only safe IDs, generation, sequence and error codes. Do not duplicate raw payloads in logs.
- Verify `SiteDataState`, `DeviceDataState`, `SyncCursor`, heartbeat generation, the `(device_id, sequence)` uniqueness constraint, and monotonic reset floors.
- Re-run stale generation/at-boundary push and pull tests. This is a safety defect; do not mark the reset complete.

## Backup restore after a reset

Restoring a pre-reset backup is a separate destructive administrative workflow. It is never automatic because sensors and SD cards may have advanced after the dump.

1. Isolate normal worker polling and ingestion.
2. Verify the backup manifest/hash and perform the standard restore preflight.
3. Inventory the restored reset operation and compare each physical sensor's signed generation, boundary, operation receipt and card generation.
4. Choose and document one recovery direction: keep the live reset generation and reconcile restored central state forward, or perform an explicitly designed fleet rebaseline. Never lower a boundary or accept restored stale data.
5. Re-enable ingestion only after all participant gates and current generations agree.

See `docs/BACKUP_AND_RESTORE.md` for the generic database/artifact restore process.

## Physical canary

Physical work requires separate user authorization. Do not flash sensors solely because this reset was requested. When authorized, limit the canary to about one hour and use one connected sensor first, then a disconnected/reconnect case with its original SD card. Record firmware/build hashes, raw PZEM monotonic evidence, sequence/generation, preserved configuration digests, zero backlog/history, a new reading, and a new calculated cost. If hardware is unavailable, report automated validation separately and do not claim the physical criterion passed.
