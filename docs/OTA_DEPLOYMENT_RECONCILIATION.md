# OTA deployment reconciliation

## Purpose

Authenticated device reports remain the primary OTA state source, but a report can be lost if a sensor panics or loses power. Deployment state is therefore reconciled with the next signed heartbeat and a bounded inactivity timeout. The server never marks a deployment successful merely to clear the UI.

`waiting_for_schedule` means the manifest is authenticated and retained but the
sensor's configured maintenance window has not opened. It is bounded by the
manifest expiry, not the short active-download timeout. The sensor persists an
attempt-scoped monotonic `evidence_sequence` with every OTA transition and sends
it in reports and signed heartbeat recovery evidence together with the active
`deployment_id` and `attempt`. The server orders heartbeat evidence only when
both identity fields match the active deployment attempt. This lets the server
reject a delayed same-boot failure without suppressing a genuinely newer one;
legacy reports without the additive field remain accepted until newer healthy
target-heartbeat evidence makes their ordering ambiguous.

The evidence sequence is a durable-state ordering value, not a unique HTTP
report number. If connectivity is unavailable while several checkpoints are
persisted, the sensor reports the missed milestones in graph order using its
latest retained evidence sequence. The server therefore accepts an equal
sequence only for an exact idempotent duplicate, a legal forward transition for
the current deployment attempt, or monotonic same-state `downloading` progress.
It rejects lower sequences, equal-sequence same-state non-duplicates outside
that bounded progress exception, and any equal-sequence report that is not a
legal next transition. Reports from legacy firmware without an evidence
sequence remain governed by device, target, attempt, boot, and state-graph
validation.

Starting server commit: `8fddad0e7f5fcd3e9f371e1b219dd6c58771c9a8`.

## Recorded source identity

Every new deployment records the source firmware version, source build hash, and latest source boot ID. Migration `20260803_0027` adds these fields and an audit-safe JSON evidence object without deleting deployment history. Existing active deployments are backfilled from their device identity where possible. Migration `20260803_0028` adds durable state-transition and terminal timestamps plus reconciliation indexes; it preserves all deployment and evidence rows. Migration `20260803_0030` extends the state constraint with the authenticated update-window wait state without changing existing rows.

## Reconciliation rules

- A signed heartbeat with the target version and target build hash is authoritative proof that the image booted. A lost final report is reconciled to post-boot heartbeat verification, not failure.
- If a target boot was observed and a later signed heartbeat returns to the recorded source identity, the deployment becomes `rolled_back` with boot and retained OTA evidence.
- If a pre-install deployment is active, the boot ID changes, the source identity returns, and the grace period has elapsed, the deployment becomes `failed` with `ota_interrupted_before_install`.
- The asynchronous worker reconciles every nonterminal deployment in bounded, row-locked batches. Each lifecycle state has an independent batch budget, so a large healthy canary or scheduled queue cannot indefinitely hide an overdue download or post-boot attempt. Reconciliation does not depend on an administrator opening the Firmware page.
- Expired manifests and canary waits, stalled downloads, sensors that do not return after partition write, post-boot stabilization timeouts, confirmed rollback, and legacy records with incomplete evidence all receive precise terminal outcomes. Every terminalization is revisioned, timestamped, idempotent, and audited.
- Polling the deployment endpoint invokes the same lock-safe reconciliation service only as a freshness fallback; it is not the lifecycle scheduler.
- A stale or replayed device report remains subject to authenticated device, target, attempt, boot, evidence-sequence, and transition checks. A delayed report with evidence older than the last accepted report cannot terminalize a newer deployment state.
- PostgreSQL transaction-scoped advisory locks serialize Retry, Cancel, and canary promotion across every API worker by rollout group. The same lock class protects idempotent rollout creation; an idempotency key replays only its exact ordered device set and cannot combine an old partial group with new rows.

## Progress semantics

Preparing, manifest authentication, download startup, and waiting-for-sensor states are indeterminate. They do not display a numeric 0%. Numeric percentage is displayed only after a current authenticated report supplies a positive byte count in a state where percentage is meaningful. The server response includes one authoritative verification checklist and current blocker covering target identity and boot, PZEM, storage, trusted time, healthy-heartbeat count, stabilization time, the first durable reading, and critical alerts. Terminal failures include an exact code and retry action.

Post-boot evidence is scoped to the exact deployment target boot. A target-image
reboot invalidates heartbeat, stabilization, and durable-reading proof from the
previous boot, increments the deployment revision, refreshes the state activity
timestamp, and starts a new bounded proof window. An accepted backlog reading
from the source image remains valid History data but cannot satisfy the target
image's post-update reading gate. A byte-identical duplicate from the target
boot can satisfy that gate because its content hash proves the exact reading is
already durable; a conflicting duplicate remains rejected.

## Retained sensor evidence

Signed heartbeat resources may contain a bounded `ota_recovery` object: current and previous stage, boot/firmware/build/deployment identity, reset reason, byte count, Update-open and reboot-expected flags, task/stack, operation context, partition, and last error. It contains no credentials or signatures. The evidence is copied into the deployment audit record only for reconciliation.

## Operational limitation

The original 1.0.11 incident image SHA does not match the retained 1.0.11 release artifact, and no exact panic backtrace is available. The server changes reconcile the confirmed stale state but do not claim an unproven exception source. Full acceptance requires the application-only canary bootstrap, a separately versioned OTA using the repaired client, successful post-boot validation, and the user-authorized one-hour test.
