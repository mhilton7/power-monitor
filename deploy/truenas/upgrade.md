# Upgrade and rollback

Every upgrade is an immutable-image replacement. Never change an existing tag in
the registry, use `latest`, or deploy a tag without its content digest.

## Pre-upgrade checkpoint

1. If the installed release predates durable application logs, use **Datasets**
   to create `/mnt/Apps/Power/power-monitor/logs` and
   `/mnt/Apps/Power/power-monitor/rate-source-artifacts` before editing the App. In **Permissions >
   Edit ACL**, add inherited numeric user ACEs for UID 10001 and UID 10003 with
   Modify, traverse, and inherit access. Do not grant `Everyone@` write access.
2. In **Apps > Installed > power-monitor**, record the current complete image
   references and save a protected copy of the current YAML.
3. Trigger and verify a fresh logical backup without using the TrueNAS shell:
   edit the App YAML in the UI, set `BACKUP_RUN_ON_STARTUP: "true"`, save, and
   wait for the backup workload log to report a verified backup. Return the value
   to `"false"` and save again. Confirm the new verified run in the application
   Backups view.
4. In **Datasets**, create recursive ZFS snapshots of the nine application
   datasets. Name them with the release and UTC time, for example
   `pre-upgrade-1.1.0-20260720T220000Z`.
5. Replicate or copy the verified logical backup and checksum manifest off the
   TrueNAS system. Keep the backup encryption key in a separate protected store.
6. Render and validate the new YAML with the new semver tags and digests. Review
   release notes and migration compatibility before saving it.

All application images in the rendered YAML must come from the same release.
Confirm the API, frontend, worker/migration, backup, and gateway references as a
set; do not update only the frontend after a bill-import correction. The
frontend now compares its API/import schema versions with the backend before
mounting an authenticated workspace and intentionally blocks a mixed release.

For the Single Home frontend cutover, confirm exactly one site is active before
upgrading. The new production image contains only `frontend-next` and exposes
exactly Home, History, Billing, and Settings; the gateway, internal frontend
port, UID/GID, datasets, secrets, health check, and published gateway port do
not change. Preserve the previous digest-pinned frontend image and App YAML for
rollback. After save, verify legacy bookmarks redirect and follow
`docs/frontend-replacement/cutover-and-rollback.md`. No ESP32 firmware or device
protocol update is required.

## Upgrade

1. Open **Apps > Installed > power-monitor > Edit > YAML**.
2. Replace only the approved image references and intentional configuration
   changes. Keep the previous YAML available for rollback.
3. Save. PostgreSQL starts first, `migrate` applies Alembic changes once, and API,
   worker, backup, and gateway cannot report healthy unless migration exits 0.
4. Verify every health indicator, the migration log, sign-in, fleet heartbeats,
   historical readings, an SCE rate preview, and the next verified backup.

For the PDF import context-stabilization release, migration `20260724_0013`
makes the bill import and bill-cycle account reference nullable so tariff
extraction can start before account assignment. It adds a partial uniqueness
index for a creator's unassigned artifact hash. Existing account-linked imports
and their evidence are unchanged. No dataset, secret, service, capability,
network, mount, or host port is added.

After migration, open **Billing > Rate Plans > Custom Plan > Import rate plan
from bill** first without an account, then with a non-production account that
has no assigned plan. Verify both show an upload/setup state rather than a raw
error. Upload a password-free test bill, refresh the resulting `bill_id` URL,
and confirm the separate plan and billing-cycle drafts remain available. The
System Health diagnostic must show matching frontend/backend release and
`utility-account-rate-context/1.0` schema values.

For the strict SCE parser and rate-plan lifecycle release, migration
`20260724_0014` adds lifecycle revision/removal/restoration evidence to rate
plans, parser-rule and validation evidence to imported fields, lifecycle
indexes/constraints, and `rates.remove`/`rates.restore`. It does not add a
dataset, secret, service, capability, network, mount, or host port. Existing
rate versions, assignments, calculations, reports, bills, managed-source
artifacts, candidates, and audit records remain in place.

Per-import draft-history controls add append-only revision `20260724_0015`.
The new nullable history-visibility metadata lets administrators clear one row
from **Prior imports** without deleting its linked drafts, evidence, billing
data, or audit history. It adds no dataset, secret, service, capability,
network, mount, or host port.

The normalized bill-review and rate-plan lifecycle correction adds append-only
revision `20260725_0016`. It stores the versioned normalized artifact on each
extraction revision and replaces the ambiguous legacy
`administrator_confirmed` confidence label with parser-, arithmetic-, manual-,
missing-, conflict-, and not-applicable states. Existing original PDFs,
redacted text, extraction revisions, billing records, rate versions,
assignments, costs, and audit records are preserved. Missing legacy values are
backfilled as `missing`, never as confirmed. This revision adds no dataset,
secret, service, capability, network, mount, environment variable, or host
port.

The rate-assignment and source-observability release adds append-only revision
`20260725_0017`. It normalizes legacy published-version labels, adds immutable
version lifecycle and assignment cancellation metadata, adjustment revision
evidence, source-run completion counters, an active-job dedupe index, and a
PostgreSQL assignment-overlap guard. It does not delete or rewrite historical
assignments, costs, bills, source artifacts, candidates, or audit events and
adds no dataset, secret, service, capability, network, mount, environment
variable, or host port.

Before saving the upgraded App YAML, take the logical backup and recursive ZFS
snapshots described above. After migration, open **Billing > Advanced Rate
Settings** and confirm Published and Current are shown separately. If an
existing account reports an assignment conflict, do not guess which rate won:
review its dates and historical evidence, use the explicit conflict-repair
workflow, and retain the selected winner. Verify **Adjust Rates** creates a
draft revision under the same plan, publishing does not silently assign it,
and **Replace current** preserves the prior assignment. Then run **Sources >
Check now**, watch the job complete, and inspect its per-source results and
history.

Rollback must restore both the pre-upgrade logical backup and matching ZFS
snapshots before returning to the prior digest-pinned image set. Do not run an
Alembic downgrade against data written after `20260725_0017`, because newer
assignment cancellations, version lifecycle evidence, source-run counters, and
adjustment revisions would be lost.

After migration, upload the sanitized regression bill and verify that the
review header shows its filename, utility/document type, page count, extraction
method, status, and import time. Confirm recognized charges appear once, absent
optional fields are grouped under **Fields not found on this bill**, and a
required missing value blocks confirmation. In **Billing**, open **More** and
verify Escape, outside click, route change, and another menu close it. Review
dependencies before **Remove from Electric Service**, then confirm that
unassignment preserves prior bill/cost history. Retire/remove must remain
blocked until active and future assignments are resolved; restore makes a plan
available but does not reassign it.

After migration, upload the sanitized SCE test fixture through **Billing >
Rate Plans > Custom Plan > Import from utility bill**. Confirm the adapter is
`sce_residential_bill_v1`, the detailed charge page is authoritative, ignored
pages are listed without extracted tariff values, arithmetic passes, and the
rounded `$0.30/$0.40` chart is display-only. Then remove and restore an
unassigned non-production custom plan. Confirm assigned-plan removal is
blocked, removed plans disappear from Active, and restore does not reassign an
account. Rollback first restores the pre-upgrade logical backup/ZFS snapshot;
do not downgrade the schema while retaining writes made by the newer release.

For the utility-bill PDF import release, migration `20260724_0010` adds
bill-import, immutable extraction revision, field evidence, conflict, and
billing-cycle draft tables plus `utility_bills.view` and
`utility_bills.manage`. The existing `rate-source-artifacts` dataset stores the
private originals and sanitized evidence; no new dataset, secret, service,
capability, network, or host port is introduced. The API image now contains
local Poppler and English Tesseract tools. Before saving the upgraded YAML,
verify UID 10001 has Modify and UID 10003 has Read on that dataset, retain the
six `UTILITY_BILL_*` limits from the release template, and take an encrypted
verified backup. After migration, upload a non-production password-free test
bill, confirm that automatic activation is disabled, inspect page evidence,
and delete the test original through the dashboard retention control.

For the bill-import integration and user-administration cleanup release,
migration `20260724_0011` adds the indexed active/disabled/removed user
lifecycle, removal/restoration evidence, former-access summaries, protected
bootstrap state, and the `users.disable`, `users.remove`, and `users.restore`
permissions. It preserves all current users and maps the existing `is_active`
state without deleting role, site, audit, authorship, or ownership history. No
dataset, secret, service, capability, network, host port, or image privilege is
added. After upgrade, open **Users & Access**, verify the bootstrap
Administrator is marked protected, disable and re-enable a non-production test
user, then remove and restore that test identity. Confirm restoration leaves it
disabled and unassigned. Open **Billing > Rate Plans > Custom Plan > Import from utility
bill** and verify a non-production PDF can be reviewed without losing a value
already entered in the editor. The former `/rates/import-bill` bookmark and
Administration-local user routes should redirect into their canonical
workspaces rather than render a separate page.

For the modern workspace and Physical Sites release, migration
`20260724_0012` adds site lifecycle/default/revision metadata, effective-dated
device and utility-account site assignments, granular `sites.*` permissions,
and one system-authored status-layout revision. Existing site UUIDs, users,
network policies, utility accounts, readings, costs, bills, alerts, and audit
history are preserved. The migration chooses one existing active site as the
default when necessary and backfills current assignments without rewriting raw
readings. It adds no dataset, secret, service, capability, network, port, or
Compose variable.

After the migration reports success, open **Administration > Sites & Network >
Physical Sites**. Verify the expected active/default site, inspect every site
scope, and confirm the top-bar selector. Open **Administration > Interface**
and preview the published status layout; older revisions remain auditable and
are mapped into the new semantic zones at render time. Exercise create and
disable/enable with a non-production site before removing anything. Removal is
soft but requires explicit dependency transfers/archives, user-access ending,
an audit reason, and exact confirmation. A restored site returns disabled and
must be reviewed before Enable.

For the Users & Access / Dashboard & Login Text release, migration
`20260720_0005` adds permission definitions, role revisions/permissions,
user-site scope, session reauthentication timestamps, and interface-text
draft/revision/current-pointer tables. No new dataset, secret, port, capability,
or Compose variable is required. Existing users are backfilled to all-site scope
and existing built-in role identifiers remain unchanged. After upgrade, verify
that at least one Administrator opens both new Administration pages before
narrowing any user's site scope.

For the Status Indicators & Layout release, migration `20260720_0006` adds
append-only status-layout revision and draft tables, the current-revision
pointer, and `status_indicators.view` / `status_indicators.manage` grants. It
does not add a Compose service, image, dataset, mount, secret, port, capability,
or environment variable. After the migration succeeds, sign in as an
Administrator, open **Administration > Interface > Status Indicators & Layout**, preview
the default desktop/tablet/mobile layouts, and verify that hiding a test
indicator does not suppress its alert before publishing intentional changes.

For the dashboard information-architecture release, migration
`20260721_0007` preserves the previous published status-layout revision and
creates a new system-authored revision. It moves API/database/worker indicators
to the diagnostics-only System Health zone, disables legacy duplicate fixed
placements, updates the current-revision pointer, and records an audit event.
It does not add a dataset, secret, mount, service, port, capability, or Compose
variable. After upgrade, verify **Administration > Security > System Health**, then open
Overview and History and confirm the Site Summary and single coverage/rate
context placements. The prior revision remains available for review; restoring
it through the dashboard creates another immutable revision and the resolver
still enforces diagnostics and deduplication safeguards.

For the utility-account and sensor-network-policy release, migration
`20260721_0008` extends existing account and rate-assignment tables, creates
effective-dated account adjustments, explicit per-site ingress/pull policies,
canonical CIDRs, and immutable policy revisions, and grants the four new
granular permissions to Administrator. It does not add a dataset, secret,
mount, service, port, capability, or Compose variable. The migration preserves
the old behavior exactly: an empty pull CIDR list becomes explicit deny-all;
existing pull CIDRs are copied; former network-unrestricted signed ingress is
marked as a review-required legacy mode. After upgrade, open **Administration >
Sites & Network > Network Policy**, review both directions for every site, add the intended
private sensor VLANs, test one allowed and one blocked address without scanning,
then save an explicit mode. Open **Billing > Utility Accounts** and create or review each
utility account, rate assignment, and cost scope. Do not select complete-account
scope until topology coverage has been verified.

For the tiered and hybrid rate release, migration `20260723_0009` extends rate
versions with an explicit pricing model, creates immutable tier/baseline
definitions, expands billing-cycle calculation evidence, and adds account usage
authority, manual/imported usage, allocation segments, tier summaries,
projections, and reconciliation adjustments. It grants `usage_imports.manage`
and `costs.recalculate` to Administrator. It does not add a dataset, secret,
mount, service, port, capability, or Compose variable. Existing rate versions
are backfilled as `time_of_use`, preserving their schedules and assignments.
After upgrade, verify one existing TOU account, then create a test tiered draft,
run its preview, configure an account usage authority, and confirm Usage/Costs
show the exact cycle. Do not finalize a cycle until coverage and utility dates
have been reviewed.

If migration fails, do not bypass its dependency or point the old application at
a partly migrated database. Preserve logs and follow the rollback path.

## Rollback

1. Stop the App through **Apps > Installed**.
2. If the failed release did not change persistent data, edit YAML back to the
   exact prior image digests and prior configuration, then start it and verify.
3. If a migration or application write changed persistent data, keep the App
   stopped. Use **Datasets > Snapshots** to clone the pre-upgrade snapshots for
   forensic retention, then roll back the affected datasets as one coordinated
   checkpoint. Destructive snapshot rollback must be approved and must not be
   performed while containers are running.
4. When logical recovery is required, keep API/worker/gateway stopped in the App
   UI and use **Workloads > backup > Shell** to run the documented
   `restore-container.sh BACKUP_DIR TARGET_DATABASE --yes` operation. Restore into
   a clean temporary database first and validate it; replace the production
   database only during the approved maintenance window.
5. Restore the prior YAML with its exact digests, start the App from the UI, and
   repeat all post-upgrade checks.

Do not attempt an undocumented Alembic downgrade. The supported rollback anchors
are the prior immutable images, the verified pre-upgrade logical backup, and the
coordinated ZFS snapshots.
