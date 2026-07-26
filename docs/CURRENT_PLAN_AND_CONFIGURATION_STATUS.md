# Current plan assignment and Configuration Status

## Root cause

The assignment transaction was already capable of persisting an effective
`rate_assignments` row, but the Single Home frontend did not read that same
authoritative context after the write. The production
`GET /api/v1/utility-accounts` route returned only legacy account columns and
omitted `rate_context`. The greenfield adapter therefore had no current plan,
version, price, or assignment revision to render, even after a successful
commit. Billing consequently continued to show `Not configured`.

The old success path also refetched a fragmented subset of queries and accepted
an untyped response. That allowed a generic success state without proving which
Electric Service, plan identity, and version had actually been assigned.

The repair makes the normal account read use the canonical account serializer,
returns a typed assignment result, updates the current-assignment cache from
that result, and explicitly refetches every dependent query.

## Assignment transaction and invariant

`POST /api/v1/rates/assignments/replace` performs the following in one database
transaction:

1. resolves and locks the Electric Service;
2. checks the optional Electric Service and current-assignment revisions;
3. loads the exact plan version and verifies that it is published, belongs to
   an active plan, and has a complete validated rate document;
4. checks the requested UTC effective window for conflicts;
5. ends the replaced assignment at the new boundary;
6. creates the new assignment and increments the Electric Service revision;
7. preserves all historical rows and queues affected unfinalized cost work;
8. writes the existing audit record and commits as one unit.

The service-layer interval checks and the PostgreSQL
`trg_rate_assignment_no_overlap` trigger enforce at most one effective
assignment for an Electric Service at any instant. Any exception rolls the
transaction back. Stale clients receive an optimistic-concurrency error with
structured blockers instead of overwriting newer state.

The response identifies the assignment, Electric Service, plan, version,
effective window, current or scheduled state, replaced assignment, recalculation
job, warnings, and resulting service revision. The frontend never reports
success unless this response passes runtime validation.

## Canonical reads and cache behavior

The following reads now agree on the effective assignment table:

- `GET /api/v1/utility-accounts`
- `GET /api/v1/electric-services/default/current-rate-assignment`
- `GET /api/v1/configuration-status`
- Billing, Home, History, and cost-calculation contexts

After a successful current-plan change, the browser:

1. closes the assignment dialog;
2. writes the returned plan/version/assignment into the Electric Service cache;
3. invalidates and refetches Electric Services, current assignment, managed
   plans and versions, Billing plan library, Home summary, Billing cycle,
   History cost context, and Configuration Status;
4. displays a visible current/scheduled result.

No full-page reload is required and rate-version documents are fetched only
once per version-list query.

## Configuration Status

`GET /api/v1/configuration-status` is the server-authoritative resolver for the
Single Home UI. It returns one of:

- `ready`
- `setup_needed`
- `partially_configured`
- `waiting_for_data`
- `attention_required`
- `error`

Stable issues cover the Electric Service, assignment conflicts or absence,
invalid assigned versions, billing-cycle context, sensors and heartbeat
freshness, notification delivery, backup verification, and managed rate-source
failures. Each issue says what is wrong, why it matters, how to fix it, whether
it blocks operation, and provides a direct in-app target.

The shared status chip is rendered in the application header, Billing service
card, and Home setup surface. It is a keyboard-accessible dialog trigger,
announces the state and issue count, traps focus while open, closes on Escape or
the close control, and returns focus to its trigger. This shared surface keeps
Home, Billing, History, Sensors, Notifications, Backups, and Sources linked to
the same status rather than independently guessing readiness.

Direct actions include:

| Issue | Resolution target |
| --- | --- |
| No current plan or assignment conflict | `/billing?advanced=rates&tab=versions` |
| Invalid or incomplete version | `/billing?advanced=rates&tab=plans&action=validate` |
| Billing-cycle setup | `/billing?configuration=billing-cycle` |
| Missing or stale sensor data | `/settings/sensors` |
| Notification delivery | `/settings/notifications` |
| Backup verification | `/settings/data` |
| Rate-source failure | `/billing?advanced=rates&tab=sources` |

A configured rate plan with an enrolled sensor awaiting its first signed
heartbeat resolves as `Waiting for data`, not a rate-setup failure.

## Verification matrix

The targeted automated coverage verifies:

- a published, complete version becomes current;
- canonical account and current-assignment reads return the committed version;
- the missing-plan issue disappears after assignment;
- stale Electric Service revisions are rejected;
- current-plan UI state moves without a reload;
- Billing, Home, and History render the same assigned plan;
- the status dialog explains what, why, and how and routes to the exact fix;
- Escape and focus behavior are accessible;
- production-bundle browser coverage runs at desktop, light, tablet, and mobile
  viewports.

The release gate remains the repository commands documented in
`docs/TESTING.md`, including backend lint/type/tests, frontend lint/type/unit,
production build and browser tests, OpenAPI/contract checks, migration upgrade,
container builds, Compose health checks, and TrueNAS Compose validation.

## Deployment and rollback

No database migration, new service, new port, or new secret is introduced by
this repair. Build and publish one immutable release image set, render the
TrueNAS Compose file with those exact image digests, take the documented
logical backup and ZFS snapshot, then update through the TrueNAS Apps web UI.

For rollback, stop the failed application update in the TrueNAS UI, restore the
previous immutable Compose/image version, and only restore the pre-upgrade ZFS
snapshot or verified logical backup when the documented data rollback is
actually required. Historical assignments remain compatible because this
change is additive at the API/UI layer.

ESP32 firmware files changed: **0**.
