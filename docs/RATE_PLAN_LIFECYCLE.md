# Rate-plan removal, retirement, and restore

Rate-plan removal is dependency-aware. The canonical action is
`rate_plan.remove`, and the active library contains only plans available for
future use.

## Lifecycle behavior

- An unpublished custom draft with no assignments, calculations, bill/source
  evidence, candidates, clones, or other preservation dependency may be
  permanently deleted after exact typed confirmation.
- A published or historically used custom plan is soft removed. Its versions,
  assignments, calculations, reports, bill imports, source artifacts,
  candidates, and audit history remain unchanged.
- An official SCE plan is locally retired, never deleted. Managed-source
  artifacts and extraction/check history remain intact.
- A plan with an active assignment, future assignment, or active account
  pointer cannot be retired or removed. The administrator must replace the
  assignment or use the explicit **Remove from Electric Service** action first.
  Explicit unassignment closes the effective-dated assignment and clears only
  the matching current pointer; it never deletes assignment, cost, report, bill,
  source-evidence, or audit history.

Removed and retired plans cannot be edited, versioned, activated, or newly
assigned. They appear under **Billing > Rate Plans > Removed / Retired**.
Restore makes the plan available again but never recreates account assignments
or overwrites a newer version.

Every lifecycle mutation requires the existing session, CSRF proof, granular
permission, an optimistic `lifecycle_revision`, the dependency snapshot token
returned by the immediately preceding impact review, and an audit reason.
Removal additionally requires the exact plan name or code. Official plans
require managed-source administration permission.

The dependency token covers the plan revision, versions, assignments, current
account pointers, and dependency counts. A concurrent assignment or lifecycle
change invalidates the token and returns a typed `409 Conflict`; the interface
then reloads the impact review instead of acting on stale data.

## Endpoints

- `GET /api/v1/admin/rate-plans?status=removed`
- `GET /api/v1/admin/rate-plans/{plan_id}/dependencies`
- `DELETE /api/v1/admin/rate-plan-drafts/{plan_id}`
- `POST /api/v1/admin/rate-plans/{plan_id}/unassign`
- `POST /api/v1/admin/rate-plans/{plan_id}/retire`
- `POST /api/v1/admin/rate-plans/{plan_id}/remove`
- `POST /api/v1/admin/rate-plans/{plan_id}/restore`

Stale revisions and unresolved active/future dependencies return typed
`409 Conflict` responses. Repeating an already completed remove/restore is
idempotent. Permissions are `rates.view`, `rates.manage_custom`,
`rates.remove`, `rates.restore`, and `rates.assign`.

Migration `20260724_0014` adds lifecycle revision/removal/restoration evidence,
strict-parser evidence fields, indexes, constraints, and the two new
permissions without changing any existing rate-version or historical foreign
key. Migration `20260725_0016` changes no rate-plan tables; it adds the durable
normalized bill artifact and truthful confidence labels used by the lifecycle
impact and evidence views.
