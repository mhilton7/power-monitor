# Rate assignment, version lifecycle, adjustments, and source checks

## Baseline checkpoint

- Local checkpoint tag: `codex/checkpoint-rate-assignment-20260725`
- Commit: `566a312ea4f78793f58b462cd476bde047c9f980`
- The pre-existing local edit to `truenas-power-monitor.yaml` is not part of the
  checkpoint or this change set.
- Backend baseline:
  `.venv\Scripts\python.exe -m pytest backend/tests/test_rate_plan_lifecycle.py backend/tests/test_rate_automation.py -q --basetemp .test-tmp-rate-assignment-baseline`
  — 22 passed.
- Single Home frontend baseline: `npm run test` — 22 passed.

## Pre-change meaning audit

### Plan, version, assignment, and “Active”

- `RatePlan.status = active` means the plan identity is available in the rate
  library. It does not mean the plan is assigned to an electric service.
- `RateVersion.is_active = true` and the legacy
  `RateVersion.status = active` identify the latest published version inside one
  plan identity. Multiple plan identities can therefore each have an “active”
  version without being assigned.
- `RateAssignment` is the effective-dated relationship that selects the exact
  `RateVersion` used by a utility account (the Single Home Electric Service).
  The half-open effective interval is `[effective_from, effective_to)`.
- `UtilityAccount.active_rate_version_id` is a compatibility/cache pointer. It
  is not authoritative when effective-dated assignments disagree with it.
- The Single Home frontend used each plan’s latest version/library status and
  rendered it as `Active`. This made several published, unassigned plans look
  current even when the database contained only one effective assignment.

### Assignment safety before this change

- The utility-account assignment endpoint locks the account row and performs
  an application overlap check, but the generic rate-assignment endpoint
  closes every open assignment without an overlap audit or account lock.
- The database checks only that an assignment ends after it starts. It does not
  prevent two assignment ranges for the same account from overlapping.
- Existing conflicting rows cannot be safely repaired by choosing a winner
  automatically because they may already have cost or billing provenance.

### Historical cost provenance

- Cost calculation and interval result records retain the exact
  `rate_version_id`. Historical plan versions must therefore remain immutable
  and cannot be hard-deleted after assignment, cost, bill, candidate, or
  evidence use.

### Source “Check now” before this change

- The button calls `POST /api/v1/admin/rate-sources/check-now`, and the endpoint
  queues a `BackgroundJob`.
- The frontend discards the returned job ID, immediately refreshes the source
  list, and never polls `GET /api/v1/jobs/{job_id}`.
- There is no visible running/progress/completion/error surface, no history
  query, and no per-source retry control.
- Repeated requests create duplicate queued jobs.
- The worker writes final outcomes but commits only after the complete worker
  loop, so progress is not observable while a network fetch or parser is
  running.

### Adjustments before this change

- Rate-rule components are editable in the structured rate-plan editor.
- Utility-account adjustments have list and create endpoints, but no update or
  remove lifecycle.
- The Single Home Adjustments tab only lists electric services and offers no
  adjustment actions.

## Corrective design

The implementation separates publication from assignment:

- Version publication states are `draft`, `published`, `superseded`, `retired`,
  and `removed`.
- Assignment display states are `current`, `scheduled`, `historical`,
  `cancelled`, `unassigned`, or `conflict`.
- Only an effective, non-cancelled `RateAssignment` can produce the `Current`
  badge.
- New assignment writes are serialized by locking the utility account, checked
  as half-open ranges, and protected in PostgreSQL by an overlap-prevention
  trigger. Existing conflicts remain visible for explicit Owner repair.
- Publishing a version never silently reassigns an electric service.
- Adjusting a published version creates a draft revision under the same plan
  identity; editing an unpublished draft updates that draft in place.
- Source checks use deduplicated jobs with observable progress, per-source
  results, history, Retry, and audit events.

## Implementation

### Assignment authority and conflict repair

- `backend/app/rates/assignments.py` is the single assignment mutation service.
  It locks the utility-account row, uses half-open intervals, requires an
  idempotency key, and returns typed conflict details.
- Migration `20260725_0017` adds cancellation and revision evidence, an
  active-job deduplication index, and a PostgreSQL trigger that rejects
  overlapping non-cancelled assignments for one account.
- Existing legacy conflicts are not silently rewritten. Owners see the
  conflicting rows, choose the winning assignment, provide a reason, and create
  an audited repair. Cancellation or interval narrowing remains possible so a
  pre-existing conflict can be repaired without deleting history.
- Make current, Replace current, schedule, cancel schedule, and end-current
  actions all use the same service. Adjacent intervals are valid; overlaps are
  not.

### Version lifecycle and rate adjustment

- Publishing marks a validated version `published`; it never assigns the
  version to an electric service.
- Adjust Rates edits an unpublished draft in place. For a published version it
  creates a draft child under the same plan identity and presents a
  current-versus-proposed comparison before publication.
- Published revisions become `superseded` when a newer revision is published.
  Unused drafts can be hard-deleted. Used or published versions use audited
  retirement or soft removal, retain every dependency, and can be restored
  without becoming current.
- The editor comparison includes pricing model, effective date, tiers, TOU
  periods, charges/adjustments, exact server-side sample totals, and estimated
  impact.

### Source checks and adjustments

- Check now returns the ID of a deduplicated background job. The worker commits
  progress and per-source outcomes as they occur, including HTTP status,
  candidate count, artifact count, safe error text, and completion timestamps.
- The frontend polls the job, exposes running/completed/failed state, keeps
  history, supports a scoped Retry, and does not create duplicate runs when the
  same trigger is already active.
- Manual adjustments now support effective dates, reason and optional evidence,
  optimistic revisions, editing, audited soft removal, and server-enforced
  `adjustments.manage` authorization.

## Verification results

Final verification on 2026-07-25:

| Gate | Result |
| --- | --- |
| Ruff lint and format | PASS; 122 Python files formatted |
| Strict mypy | PASS; 70 source files |
| OpenAPI/schema/vector contract validation | PASS |
| Backend, worker, and simulator suite | PASS; 190 passed, 3 separately gated |
| PostgreSQL 17 populated upgrade, downgrade/re-upgrade, prior-schema, and clean install | PASS; head `20260725_0017` |
| 100-device resilience/backfill | PASS; 18,000 immutable readings and retry deduplication |
| Frontend lint and TypeScript | PASS |
| Frontend unit/component tests | PASS; 25 tests |
| Greenfield production build | PASS; 12 chunks, 615,911 bytes, hashed CSS, no legacy modules |
| Browser/accessibility/visual/overlap matrix | PASS; 91 passed, 13 intentional project-specific skips |
| API and frontend production images | PASS |
| Backup production image | PASS |
| Standard Compose migration and health | PASS; all five long-running services healthy at `20260725_0017` |
| TrueNAS template and optional ICMP overlay validation | PASS |
| Digest-pinned seven-service TrueNAS-equivalent workflow | PASS; migration, 3 devices, 90 readings, 2 accounts, 1 CIDR, SCE rate calculation, gateway-only port |
| Encrypted logical backup and clean restore | PASS; all five artifacts checksummed and restored at `20260725_0017` |

The first disposable TrueNAS test endpoint used an IP literal with Caddy's
internal CA and reached healthy state but failed TLS certificate issuance. It
was destroyed automatically and rerun with the supported `localhost` internal
CA name; the complete workflow then passed. Production LAN deployments continue
to use their configured DNS name or user-supplied certificate and never disable
TLS verification.

## Reviewed UI evidence

- Published-versus-current and same-plan revision comparison:
  `frontend/e2e/single-pass-repair.spec.ts-snapshots/rate-editor-adjust-rates-compare-desktop-win32.png`
- Complete source-check outcome with progress, candidate, and artifact counts:
  `frontend/e2e/single-pass-repair.spec.ts-snapshots/rate-editor-source-check-completed-desktop-win32.png`
- Source inventory, per-source action, and history disclosure:
  `frontend/e2e/single-pass-repair.spec.ts-snapshots/rate-editor-sources-desktop-win32.png`
- Version and assignment lifecycle:
  `frontend/e2e/single-pass-repair.spec.ts-snapshots/rate-editor-lifecycle-desktop-win32.png`

Each proof also has light-theme, tablet, and mobile baselines. The full browser
matrix checks page overflow, pairwise control overlap, focus order, ARIA tab
relationships, and responsive containment.

## Files and protected boundaries

- Backend and database: assignment service, rate/account management APIs,
  schemas/models, source worker, append-only migration, contracts, and tests.
- Frontend: typed adapters/models, Billing canonical assignment controls,
  Advanced Rate Settings, structured editor comparison, responsive styling,
  unit/browser tests, and reviewed visual baselines.
- Operations and documentation: TrueNAS upgrade/rollback guidance, API/rate/
  source/testing documentation, release notes, and the deployed workflow gate.
- ESP32 firmware files changed: **0**.
- Protocol identifier remains exactly `pm-protocol/1.0.0`.
- Services, databases, datasets, secrets, capabilities, networks, mounts, and
  host ports added by this change: **0**.

The unrelated pre-existing local image-reference edit in
`truenas-power-monitor.yaml` remains outside this change set.
