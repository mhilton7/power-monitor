# Rate automation and custom plans

## What the subsystem does

Power Monitor checks the four server-approved Southern California Edison
sources once a week (Sunday at 03:15 `America/Los_Angeles` by default). A check
is asynchronous: the API creates a job, the worker obtains the existing
PostgreSQL advisory lock, retrieves enabled sources, archives response bytes and
metadata, runs a versioned parser, validates a normalized document, and produces
a difference candidate. The default `manual_review` policy never changes an
active rate automatically.

The approved source inventory is compiled into the server. Administrators can
enable or disable a source but cannot enter an arbitrary URL. Retrieval requires
HTTPS, an exact approved host and path, public DNS results, bounded redirects,
size and time limits, normal certificate verification, and revalidation of every
redirect target. Conditional `ETag` and `Last-Modified` requests retain a check
record without duplicating an unchanged artifact.

## Administrator workflow

Open **Rate plans** to see active and draft versions, the last successful source
check, source health, review count, and activation policy.

1. Select **Check SCE now** to queue an immediate job. The browser does not wait
   on SCE; check progress is available through `/api/v1/jobs/{job_id}`.
2. Open **Rate source settings**. Inspect the source-check result and archived
   SHA-256 before reviewing any candidate.
3. Compare every material before/after value. Parser output is evidence, not an
   instruction to activate.
4. Approve or reject the candidate. A rejection requires a reason. Approval and
   activation are separate audited actions.
5. Activate only after blocking validation succeeds. A future effective date is
   scheduled; it is not applied early. Historical calculations retain their
   exact `rate_version_id`.

If a source is unavailable, the current active rate remains in service. If a
source is unstructured or a PDF cannot be extracted deterministically, its bytes
are archived and the result is marked for manual review; the parser never
guesses missing prices.

## Custom plan editor

Only administrators and users assigned the `rate-manager` role may change
rates. Select **+ Custom plan** and complete all four steps:

1. **Plan details** — identity, utility, currency, timezone, effective dates,
   ownership, cost scope, provider mode, and source label.
2. **Seasons & schedules** — annual seasons and weekday/weekend/all-day,
   holiday, or explicit-date schedules. Each normal schedule must cover all
   1,440 local minutes with no overlap or gap. Crossing-midnight windows are
   represented as two periods.
3. **Charges & adjustments** — fixed charges, baseline credits, taxes,
   provider components, and explicit custom adjustments. Exact decimals are
   stored as strings in the normalized JSON and as `NUMERIC` in PostgreSQL.
4. **Validate & preview** — blocking errors, warnings, coverage, normalized JSON,
   integrity digest, and a sample calculation.

Save creates an inactive draft. Active or used versions are immutable. Use
**Create version** or **Clone** to make an editable copy. Cloning an official
plan creates a new custom plan and preserves `cloned_from_rate_version_id`; it
never edits official SCE data.

Imports accept only a `power-monitor-rate-plan/1.0` JSON file up to 1 MiB. They
are schema-validated and always become drafts. Exports use exact decimal strings
and contain no device credentials, application secrets, or internal source
paths. An unused draft can be deleted, but a version referenced by a cost run,
assignment, account, report, or audit history cannot be removed.

## Cost scope and assignments

The supported UI scopes are `energy_only`, `allocated_account_estimate`, and
`full_account_estimate`. Legacy stored identifiers remain compatible. A one-CT
device defaults to `energy_only`. Fixed charges and baseline credits apply only
to a full-account estimate and only once per utility account.

Assignments are effective-dated. The cost worker resolves and persists the
exact version used, evaluates tariff windows in the utility-account timezone,
and retains UTC measurement timestamps. It splits intervals at tariff,
midnight, season, effective-date, and DST boundaries. Finalized historical
outputs are never silently rewritten.

## Configuration

Deployment defaults are:

```dotenv
RATE_SYNC_ENABLED=true
RATE_SYNC_CRON=15 3 * * 0
RATE_SYNC_TIMEZONE=America/Los_Angeles
RATE_SYNC_JITTER_MINUTES=20
RATE_SYNC_POLICY=manual_review
RATE_SYNC_MAX_SOURCE_BYTES=10485760
RATE_SYNC_CONNECT_TIMEOUT_SECONDS=10
RATE_SYNC_READ_TIMEOUT_SECONDS=30
RATE_SYNC_TOTAL_TIMEOUT_SECONDS=45
RATE_SYNC_MAX_REDIRECTS=3
RATE_SYNC_MAX_RETRIES=3
RATE_SYNC_ALLOWED_HOSTS=www.sce.com,sce.com
RATE_SYNC_ARTIFACT_PATH=/app/data/rate-source-artifacts
RATE_SYNC_AUTO_MAX_PERCENT_CHANGE=25
RATE_SYNC_RETROACTIVE_AUTO_DAYS=0
```

Schedule and policy values can be changed in the admin page and are stored in
PostgreSQL. Environment values remain safe installation defaults. The strict
auto-activation mode is disabled by default; deployments should keep manual
review unless their change thresholds and source-agreement policy have been
formally approved.

## Evidence, backup, and recovery

Artifacts live outside the web root. TrueNAS binds
`/mnt/Apps/Power/power-monitor/rate-source-artifacts` to
`/app/data/rate-source-artifacts` for API and worker access. The backup service
includes that directory in the encrypted, checksummed logical backup. An
authenticated administrator download rechecks the SHA-256 and rejects path
traversal or missing files.

Restore the database and its matching rate-source artifact generation together.
After restoration, run the automated clean-database restore verification and
confirm that each active official rate version has a source link, artifact hash,
parser identifier/version, approval record, and audit correlation ID.

## Troubleshooting

- **Check stays queued:** verify worker health and the migration revision, then
  inspect `/api/v1/jobs/{id}`. Only one worker owns the advisory lock.
- **Source failed:** inspect the safe error code, DNS/TLS egress, response size,
  and source status. Never disable TLS verification or broaden the URL allowlist
  as a workaround.
- **Candidate cannot activate:** correct every blocking validation error and
  ensure official candidates have an archived artifact, SHA-256, parser ID, and
  parser version.
- **Artifact permission error:** UID 10001 needs Modify and UID 10003 needs
  Read/traverse on the dedicated TrueNAS dataset.
- **Estimate changed retroactively:** inspect rate assignments, effective dates,
  the cost calculation version, and the activation audit. Finalized reports must
  still point to their original version.
