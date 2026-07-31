# API

`shared/openapi/server-api.yaml` is generated from FastAPI by `scripts/generate_openapi.py`; `--check` detects drift. `device-ingest-api.yaml` and `device-api.yaml` define both directions of `pm-protocol/1.0.0`. Run `python scripts/contract_check.py` after route or schema changes.

Browser routes use an opaque `pm_session` cookie and `X-CSRF-Token` on mutations. Device routes use the HMAC headers in [Device protocol](DEVICE_PROTOCOL.md). Errors are `application/problem+json` with type, title, status, detail, instance, stable code, and request ID. Time series uses bounded ranges/cursors; large CSV/JSON output is an asynchronous export job with expiring authorized download.

Key groups are `/api/v1/auth`, `/sites`, `/utility-accounts`, `/circuits`, `/aggregate-sets`, `/devices`, `/readings/history`, `/history/query`, `/history/export`, `/rates`, `/billing`, `/alerts`, `/exports`, `/firmware-*`, `/reports`, `/backups`, `/audit-events`, `/system/info`, and `/events/stream`. Administrator log discovery and export use `/api/v1/admin/logs/availability`, `POST /api/v1/admin/logs/exports`, export status, and the short-lived authorized download route. Safe sensor removal uses `POST /api/v1/admin/devices/{device_id}/unclaim`; it requires CSRF, an administrator, and exact name-or-ID confirmation. Health endpoints are outside `/api/v1`. Metrics are authenticated.

Detailed notifications use `GET /api/v1/notifications`, `GET
/api/v1/notifications/{id}`, acknowledge/silence/end-silence mutations, optional
recommendation dismiss/suppress mutations, `GET /api/v1/notification-suppressions`, the
revision-checked restore route, and paginated `/api/v1/notification-history`.
`/api/v1/alerts` remains compatible and returns the same structured representation. See
[Notifications](NOTIFICATIONS.md).

Single Home current-rate state is server authoritative. `GET
/api/v1/electric-services/default/current-rate-assignment` returns the exact
effective assignment, plan, and version or an explicit null assignment. `POST
/api/v1/rates/assignments/replace` returns
`rate-assignment-result/1.0`, validates optimistic Electric Service and
assignment revisions, and atomically replaces or schedules the selected
published version. `GET /api/v1/configuration-status` returns the shared
actionable readiness model used throughout the greenfield frontend. See
`docs/CURRENT_PLAN_AND_CONFIGURATION_STATUS.md`.

`GET /api/v1/backup-requests` lists authorized request state and
`POST /api/v1/backup-requests` queues either an idempotent `create` or a
confirmed `restore_preflight` operation. Responses never contain secret values
or dataset paths. `POST /api/v1/backups/{backup_id}/verify` idempotently queues
verification for a completed or previously failed run. `DELETE
/api/v1/backups/{backup_id}` requires `DELETE`, the matching ID prefix, and a
reason; the final verified backup is protected. The UID/GID 10003 backup
scheduler enforces one global operation, automatically queues a real temporary
PostgreSQL restore after creation, and processes safe trash-first deletion. A
live database restore is not exposed as a browser API.

`GET /api/v1/fleet/summary` keeps measurement, receipt, heartbeat, and response
time separate through `latest_measurement_at`, `latest_received_at`,
`latest_heartbeat_at`, and `server_now`. The browser uses the receipt and server
timestamps for its local elapsed-time display; it does not poll this route once
per second.

Guided utility-account APIs are under `/api/v1/admin/sites/{site_id}/utility-accounts` and
`/api/v1/admin/utility-accounts/{account_id}`. Subresources provide immutable rate-assignment
history/creation, cost-scope changes, effective-dated adjustments, recalculation, and archive.
The canonical assignment mutations are
`POST /api/v1/rates/assignments/replace` and
`POST /api/v1/rates/assignments/end`. They require an idempotency key, explicit
reason, and confirmation. The server serializes the account, uses half-open
effective intervals, rejects current/future overlap with `409 Conflict`, closes
the prior effective window for an explicit replacement, updates the compatibility
pointer, returns preserved/replaced assignment IDs, and records immutable audit
evidence. `GET /api/v1/rates/assignments/conflicts` and
`POST /api/v1/rates/assignments/conflicts/resolve` provide the explicit Owner
repair workflow for retained legacy conflicts.
`GET /api/v1/sites/{site_id}/setup-readiness` returns separate monitoring and rate/cost readiness.
Sensor network APIs are under `/api/v1/admin/network/policies`, `/cidrs`, `/test-address`,
`/observed-devices`, and `/suggest-current`. All mutations require CSRF and granular server-side
permissions. See [Utility accounts](UTILITY_ACCOUNTS.md) and
[Sensor network policy](SENSOR_NETWORK_POLICY.md).

Reviewed physical-site lifecycle APIs are under `/api/v1/admin/sites`.
They provide filtered list/detail, create/edit, set-default, disable/enable,
dependency inventory and resolution, soft remove, restore, and site audit
history. Mutations use specific `sites.*` permissions, CSRF, site scope,
optimistic revision checks, exact confirmation for removal, and immutable audit
evidence. Sensor and utility-account transfers create effective-dated
assignment rows; raw readings and historical cost/bill evidence are never
rewritten. See [Site management](SITE_MANAGEMENT.md).

`GET /api/v1/readings/history` remains backward compatible for one device,
circuit, site, or aggregate selector. `POST /api/v1/history/query` is the bounded
scope-aware interface for aligned multi-sensor series and historically effective
energy cost. It accepts one scope, display mode, metric list, UTC range, bucket,
optional strict coverage, optional selected subrange, and page controls. Every
resolved sensor is authorized server-side and cross-site ad hoc selections are
rejected. `POST /api/v1/history/export` applies the same query and permissions
and returns an audited provenance-rich CSV. See [History](HISTORY.md).

Human access administration uses `/api/v1/admin/users`,
`/api/v1/admin/roles`, and `/api/v1/admin/permissions`, including user-access
revision updates, distinct enable/disable, idempotent session revocation, safe
soft removal/restoration, access history, custom-role cloning/revision/archive,
and server catalog dependencies. `GET /api/v1/admin/users` excludes removed
identities by default; use `status=removed` or `include_removed=true` only with
`users.view`. Lifecycle actions are explicit `POST` subresources:
`/disable`, `/enable`, `/remove`, and `/restore`. Remove requires
`users.remove`, current revision, typed identity confirmation, a reason,
high-risk confirmation, and current-password/TOTP reauthentication. Restore
requires `users.restore` and returns the identity disabled and unassigned. The
legacy `DELETE /api/v1/users/{user_id}` remains a reversible disable for API
compatibility and is not a hard-delete endpoint. High-risk mutations use
`POST /api/v1/auth/reauthenticate` and a bounded confirmation window. Every
mutation requires the existing session and CSRF proof.

Published presentation values use authenticated `GET /api/v1/interface-text`.
The unauthenticated `GET /api/v1/public/interface-text` returns only registered
public keys with revision/ETag caching. Draft, preview, publish, reset,
revision/restore, import, and export endpoints are under
`/api/v1/admin/interface-text`. See [Interface text](INTERFACE_TEXT.md).

Status presentation uses authenticated `GET /api/v1/status-indicators/registry`,
`/layout`, and `/values`. The layout route resolves page, role, breakpoint,
permission, and optional site scope on the server; values are collected in one
bounded batch from the existing status sources. Administrator catalog, draft,
validation, preview, publish, reset, immutable revisions/restore, import, and
export routes are under `/api/v1/admin/status-indicators`. Writes require the
existing CSRF proof and `status_indicators.manage`; reads require
`status_indicators.view` plus the definition's underlying data permission. See
[Status indicator registry](STATUS_INDICATORS.md) and
[layout administration](STATUS_LAYOUT_ADMINISTRATION.md).

Rate-plan lifecycle endpoints live under `/api/v1/rates/plans`,
`/api/v1/rates/versions`, and `/api/v1/rates/assignments`. Approved-source,
check, artifact, and candidate-review endpoints live under
`/api/v1/admin/rate-*`; asynchronous check status is
`GET /api/v1/jobs/{job_id}`. A source check returns an observable, deduplicated
job; `/api/v1/admin/rate-sources/check-runs` exposes run history and
`/api/v1/admin/rate-sources/{source_id}/check-runs` supports a scoped retry.
Each run includes per-source progress, result, last-checked time, candidate
count, artifact count, and a safe error summary. See
`docs/rate-automation-and-custom-plans.md` for request semantics, immutable
activation, source evidence, and custom-plan schema behavior.

Version publication and effective assignment are distinct. Publishing changes
a version from `draft` to `published` and supersedes the previous published
revision under the same plan identity; it does not make the version Current.
`POST /api/v1/rates/plans/{plan_id}/versions` creates or reuses the plan's
unpublished adjustment draft. Version dependency, retire, remove, restore, and
unused-draft delete operations preserve assignment, cost, bill, source, and
audit history.

Effective-dated account adjustments use
`/api/v1/admin/utility-accounts/{account_id}/adjustments`. Create and update
require a reason, update requires the current revision, and delete performs an
audited soft removal. The optional evidence reference is metadata only and
never exposes private bill contents.

Dependency-aware removal uses
`GET /api/v1/admin/rate-plans/{plan_id}/dependencies`,
`DELETE /api/v1/admin/rate-plan-drafts/{plan_id}`,
`POST /api/v1/admin/rate-plans/{plan_id}/unassign`,
`POST /api/v1/admin/rate-plans/{plan_id}/retire`,
`POST /api/v1/admin/rate-plans/{plan_id}/remove`, and
`POST /api/v1/admin/rate-plans/{plan_id}/restore`. The administrator list
accepts active, removed, retired, combined, or all status filters. Removed
plans cannot be assigned, edited, versioned, or activated. Lifecycle mutations
submit the dependency token from the preceding impact review so concurrent
assignment changes fail with `409 Conflict`. Explicit unassignment closes the
effective-dated assignment without deleting historical costs or evidence. See
[Rate-plan lifecycle](RATE_PLAN_LIFECYCLE.md).

Tiered account status is
`GET /api/v1/utility-accounts/{account_id}/tier-status`. Account administration
uses `/api/v1/admin/utility-accounts/{account_id}/usage-authority`,
`/manual-usage`, `/usage-imports`, and `/billing-cycles`. Cycle subresources
support reconciliation adjustments, recalculation, and finalization.
`POST /api/v1/admin/utility-accounts/{account_id}/billing-cycles/current/recalculate`
creates or locks the current mutable cycle before running the same chronological
allocator, so the browser does not need to discover a cycle ID first. Import
mutations require `usage_imports.manage`; recalculation/finalization require
`costs.recalculate`; all are site-scoped and CSRF protected. See
[Tiered and hybrid rates](TIERED_AND_HYBRID_RATES.md), [Billing
cycles](BILLING_CYCLES.md), and [Usage imports](USAGE_IMPORTS.md).

Administrator utility-bill PDF import is under
`/api/v1/admin/utility-bill-imports`. Upload uses
`POST /api/v1/admin/utility-bill-imports` with an optional `account_id`.
The account-specific
`POST /api/v1/admin/utility-accounts/{account_id}/bill-imports` endpoint remains
compatible. `GET /api/v1/admin/utility-bill-import-context` returns the
versioned, explicit-null account/rate context used by the importer. An
unassigned upload may be attached later with
`PUT /api/v1/admin/utility-bill-imports/{bill_id}/account-context`. List/detail,
page evidence, authenticated original/sanitized downloads, review corrections,
rate validation, exact/display comparison, publish-and-assign, separate
billing-cycle import, retention, and original deletion use the linked import
identifier. Upload and review reuse the existing job, artifact, rate-source,
custom-rate, assignment, cycle, audit, RBAC, and CSRF services. The upload job
is readable through `GET /api/v1/jobs/{job_id}`. See [Utility-bill PDF
imports](UTILITY_BILL_IMPORTS.md).

Bill detail includes the normative `normalized-utility-bill/1.0` object.
`GET /api/v1/admin/utility-bill-imports/{bill_id}/normalized` returns that
artifact directly, `/extracted-text` returns the retained redacted text, and
`POST /reprocess` creates a new immutable extraction revision from the retained
private original. The normalized contract places only recognized values in
`evidence`; absent fields are represented separately in `missing_fields` and
cannot be confirmed as though a value had been found.

Recognized SCE residential bills return strict `sce_bill_v1` adapter metadata,
page classifications, ignored-section reasons, per-field parser/validation
evidence, and an exact Decimal reconciliation result. The normative schema is
`shared/schemas/sce-bill-extraction-1.0.json`; unsupported layouts return
explicit null drafts and a typed review warning rather than guessed values.

These bill-import endpoints remain unchanged after the frontend integration.
The browser now invokes them from the existing custom-plan editor; applying
selected extracted fields updates that editor draft and does not invoke the
publish-and-assign compatibility endpoint. Billing-cycle import remains a
separate explicit mutation.

`GET /api/v1/system/compatibility` reports the backend release/commit, API
schema, bill-import context schema, and device protocol version. The
authenticated frontend checks it before mounting workspaces and fails safely
when a mixed release would consume an incompatible response. See
[PDF import context stabilization](PDF_IMPORT_STABILIZATION.md).

Owner diagnostics use `GET /api/v1/system/health`. The typed
`system-health/1.0` response separates overall/component states, safe
remediation, versions, compatibility, and recent findings. It does not expose
credentials, connection strings, secret values, or sensitive paths. Container
readiness remains at `/health/ready`; browser code must not call that probe.

Isolated Sensor Test Mode uses `GET /api/v1/test-mode`, `POST /enable`,
`PUT /api/v1/test-mode`, `POST /disable`, `POST /reset`, plus the `/sensors`
and `/history` subresources. All calls are owner-only; writes require CSRF and
an idempotency key. Responses are explicitly `simulated`/`test_mode` and are
never returned by normal reading, history, billing, export, backup, alert,
device, credential, or firmware endpoints. See [Sensor Test Mode](SENSOR_TEST_MODE.md).

Log exports accept date values rather than filesystem names, limit selection to
the retained 90-day window and known service identifiers, and return a streamed
ZIP with a per-file SHA-256 manifest. The server never exposes its log directory
or temporary path. `GET /api/v1/devices` defaults to active sensors; use
`?lifecycle=decommissioned` for the administrator archived view.

Existing sensor topology is updated with
`PUT /api/v1/admin/devices/{device_id}/measurement-assignment`. The CSRF- and
`topology.manage`-protected body contains `circuit_id`, `utility_account_id`,
`include_in_default_site_total`, and an audit reason. Both referenced resources
must be active members of the sensor's site. `GET /api/v1/devices` exposes the
optional `utility_account_id` alongside its existing circuit fields so browser
adapters can identify incomplete relationships without an N+1 lookup.

Do not hand-edit generated server OpenAPI. Add schema and authorization in the route, regenerate, validate, and run API/RBAC tests.
