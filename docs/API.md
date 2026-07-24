# API

`shared/openapi/server-api.yaml` is generated from FastAPI by `scripts/generate_openapi.py`; `--check` detects drift. `device-ingest-api.yaml` and `device-api.yaml` define both directions of `pm-protocol/1.0.0`. Run `python scripts/contract_check.py` after route or schema changes.

Browser routes use an opaque `pm_session` cookie and `X-CSRF-Token` on mutations. Device routes use the HMAC headers in [Device protocol](DEVICE_PROTOCOL.md). Errors are `application/problem+json` with type, title, status, detail, instance, stable code, and request ID. Time series uses bounded ranges/cursors; large CSV/JSON output is an asynchronous export job with expiring authorized download.

Key groups are `/api/v1/auth`, `/sites`, `/utility-accounts`, `/circuits`, `/aggregate-sets`, `/devices`, `/readings/history`, `/history/query`, `/history/export`, `/rates`, `/billing`, `/alerts`, `/exports`, `/firmware-*`, `/reports`, `/backups`, `/audit-events`, `/system/info`, and `/events/stream`. Administrator log discovery and export use `/api/v1/admin/logs/availability`, `POST /api/v1/admin/logs/exports`, export status, and the short-lived authorized download route. Safe sensor removal uses `POST /api/v1/admin/devices/{device_id}/unclaim`; it requires CSRF, an administrator, and exact name-or-ID confirmation. Health endpoints are outside `/api/v1`. Metrics are authenticated.

Guided utility-account APIs are under `/api/v1/admin/sites/{site_id}/utility-accounts` and
`/api/v1/admin/utility-accounts/{account_id}`. Subresources provide immutable rate-assignment
history/creation, cost-scope changes, effective-dated adjustments, recalculation, and archive.
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
`GET /api/v1/jobs/{job_id}`. See
`docs/rate-automation-and-custom-plans.md` for request semantics, immutable
activation, source evidence, and custom-plan schema behavior.

Tiered account status is
`GET /api/v1/utility-accounts/{account_id}/tier-status`. Account administration
uses `/api/v1/admin/utility-accounts/{account_id}/usage-authority`,
`/manual-usage`, `/usage-imports`, and `/billing-cycles`. Cycle subresources
support reconciliation adjustments, recalculation, and finalization. Import
mutations require `usage_imports.manage`; recalculation/finalization require
`costs.recalculate`; all are site-scoped and CSRF protected. See
[Tiered and hybrid rates](TIERED_AND_HYBRID_RATES.md), [Billing
cycles](BILLING_CYCLES.md), and [Usage imports](USAGE_IMPORTS.md).

Administrator utility-bill PDF import is under
`/api/v1/admin/utility-bill-imports`. Upload uses
`POST /api/v1/admin/utility-accounts/{account_id}/bill-imports`; list/detail,
page evidence, authenticated original/sanitized downloads, review corrections,
rate validation, exact/display comparison, publish-and-assign, separate
billing-cycle import, retention, and original deletion use the linked import
identifier. Upload and review reuse the existing job, artifact, rate-source,
custom-rate, assignment, cycle, audit, RBAC, and CSRF services. The upload job
is readable through `GET /api/v1/jobs/{job_id}`. See [Utility-bill PDF
imports](UTILITY_BILL_IMPORTS.md).

These bill-import endpoints remain unchanged after the frontend integration.
The browser now invokes them from the existing custom-plan editor; applying
selected extracted fields updates that editor draft and does not invoke the
publish-and-assign compatibility endpoint. Billing-cycle import remains a
separate explicit mutation.

Log exports accept date values rather than filesystem names, limit selection to
the retained 90-day window and known service identifiers, and return a streamed
ZIP with a per-file SHA-256 manifest. The server never exposes its log directory
or temporary path. `GET /api/v1/devices` defaults to active sensors; use
`?lifecycle=decommissioned` for the administrator archived view.

Do not hand-edit generated server OpenAPI. Add schema and authorization in the route, regenerate, validate, and run API/RBAC tests.
