# OTA deployment reconciliation

## Purpose

Authenticated device reports remain the primary OTA state source, but a report can be lost if a sensor panics or loses power. Deployment state is therefore reconciled with the next signed heartbeat and a bounded inactivity timeout. The server never marks a deployment successful merely to clear the UI.

Starting server commit: `8fddad0e7f5fcd3e9f371e1b219dd6c58771c9a8`.

## Recorded source identity

Every new deployment records the source firmware version, source build hash, and latest source boot ID. Migration `20260803_0027` adds these fields and an audit-safe JSON evidence object without deleting deployment history. Existing active deployments are backfilled from their device identity where possible.

## Reconciliation rules

- A signed heartbeat with the target version and target build hash is authoritative proof that the image booted. A lost final report is reconciled to post-boot heartbeat verification, not failure.
- If a target boot was observed and a later signed heartbeat returns to the recorded source identity, the deployment becomes `rolled_back` with boot and retained OTA evidence.
- If a pre-install deployment is active, the boot ID changes, the source identity returns, and the grace period has elapsed, the deployment becomes `failed` with `ota_interrupted_before_install`.
- If no report advances a manifest/download state within ten minutes, polling the deployment endpoint makes the state terminal with `ota_update_timed_out`. This transition is revisioned and audited.
- A stale or replayed device report remains subject to the existing authenticated attempt, transition, device, and revision checks.

## Progress semantics

Preparing, manifest authentication, download startup, and waiting-for-sensor states are indeterminate. They do not display a numeric 0%. Numeric percentage is displayed only after a current authenticated report supplies a positive byte count in a state where percentage is meaningful. The UI also shows last authenticated activity and a retry action for terminal failures.

## Retained sensor evidence

Signed heartbeat resources may contain a bounded `ota_recovery` object: current and previous stage, boot/firmware/build/deployment identity, reset reason, byte count, Update-open and reboot-expected flags, task/stack, operation context, partition, and last error. It contains no credentials or signatures. The evidence is copied into the deployment audit record only for reconciliation.

## Operational limitation

The original 1.0.11 incident image SHA does not match the retained 1.0.11 release artifact, and no exact panic backtrace is available. The server changes reconcile the confirmed stale state but do not claim an unproven exception source. Full acceptance requires the application-only canary bootstrap, a separately versioned OTA using the repaired client, successful post-boot validation, and the user-authorized one-hour test.
