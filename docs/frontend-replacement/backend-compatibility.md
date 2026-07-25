# Frontend replacement backend compatibility review

The Single Home frontend is designed to consume the current FastAPI surface
through adapters. No backend change is permitted unless a production
capability has no callable server contract.

## Confirmed exception: backup actions

The access catalog already defines:

- `backups.view`
- `backups.create`
- `backups.restore`

The current API exposes only `GET /api/v1/backups`. It has no endpoint to
request an on-demand logical backup, inspect a restore preflight, or submit a
restore request. A frontend adapter cannot solve this because:

1. browser code must not access Docker, PostgreSQL credentials, host paths, or
   TrueNAS shell commands;
2. the API and worker mount the backup dataset read-only in production;
3. `pg_dump`, checksum, encryption, and clean-database restore verification
   belong to the existing UID 10003 backup container;
4. a live restore must never be attempted from the API process that is using the
   database being restored.

The compatible correction is an additive, authenticated request contract. The
API records audited `backup_create` and `backup_restore_preflight` jobs. The
existing backup scheduler claims them and performs the established create or
verification scripts under UID/GID 10003. A live restore is deliberately not
exposed to the browser: the preflight proves that a selected verified backup is
usable, after which the documented maintenance-window restore procedure
remains authoritative. Existing nightly scheduling, scripts, image contracts,
database records, and TrueNAS datasets remain unchanged.

Required regression coverage:

- permission and CSRF enforcement;
- no credentials or host paths in responses;
- idempotent request submission;
- only completed, verified backups are restore candidates;
- restore preflight requires explicit high-risk confirmation;
- scheduler claim/result state transitions;
- existing nightly backup and automated test-restore verification.

No ESP32 or device-protocol change is required.

Implementation result: the API request contract, scheduler claim loop, and
tests are complete. The seven-service deployment gate also caught and corrected
two scheduler integration defects: it now loads `PGPASSWORD_FILE` before
querying the job queue, suppresses empty `UPDATE 0` command tags, and uses
psql-safe variable substitution through stdin scripts.

## Confirmed exception: seeded alert-rule editing

The server bootstrap creates alert rules such as `worker_failure`,
`backup_failure`, `firmware_failed`, and `rate_source_changed`, but the existing
`AlertRuleWrite` request contract rejects those same persisted rule types. The
new Notifications screen can list the rules, yet cannot safely toggle one
because `PUT /api/v1/alert-rules/{id}` requires a complete request body. A
frontend adapter cannot translate these identifiers without changing their
meaning or creating duplicate rules.

The compatible correction is to make the write schema accept every
server-seeded rule identifier while retaining the existing database model,
evaluation workers, permissions, CSRF checks, and audit logging. This is a
contract repair only; it adds no alert engine and changes no evaluation
semantics.

Required regression coverage:

- every default bootstrap alert is accepted by `AlertRuleWrite`;
- unknown rule identifiers remain rejected;
- update permission, site scope, and CSRF enforcement remain unchanged.
