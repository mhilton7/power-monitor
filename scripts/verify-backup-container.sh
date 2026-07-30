#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source /srv/scripts/container-secrets.sh
source /srv/scripts/container-log.sh
source /srv/scripts/backup-paths.sh
load_file_backed_variable PGPASSWORD
prepare_backup_key
maintain_application_logs
write_application_log backup.verify_started info

if [[ $# -ne 1 || ! "$1" =~ ^[0-9a-f-]{36}$ ]]; then
  echo "usage: verify-backup-container.sh backup-run-uuid" >&2
  exit 64
fi
run_id=$1
failure_stage=initialization
failure_code=INTEGRITY_CHECK_FAILED
safe_summary="Backup verification did not complete"
restore_workspace=""
test_db=""

record_failure() {
  local exit_code=$?
  trap - ERR
  if [[ -n "$test_db" ]]; then
    dropdb --if-exists --force "$test_db" >/dev/null 2>&1 || true
  fi
  if [[ -n "$restore_workspace" && -d "$restore_workspace" ]]; then
    rm -rf -- "$restore_workspace"
  fi
  remove_temporary_backup_key
  psql -v ON_ERROR_STOP=1 \
    -v run_id="$run_id" \
    -v failed_stage="$failure_stage" \
    -v safe_error_code="$failure_code" \
    -v safe_summary="$safe_summary" \
    -v exit_code="$exit_code" <<'SQL' || true
UPDATE backup_runs
SET status='verification_failed', verification_completed_at=now(), updated_at=now(),
    failed_stage=:'failed_stage', safe_error_code=:'safe_error_code',
    safe_error_summary=:'safe_summary', exit_code=:'exit_code'
WHERE id=:'run_id';
SQL
  write_application_log backup.verify_failed error || true
  exit "$exit_code"
}
trap record_failure ERR INT TERM

backup_value=$(psql --quiet --tuples-only --no-align \
  -v ON_ERROR_STOP=1 -v run_id="$run_id" <<'SQL'
UPDATE backup_runs
SET status='verifying', verification_started_at=now(),
    verification_completed_at=NULL,
    verification_attempt_count=verification_attempt_count + 1,
    failed_stage=NULL, safe_error_code=NULL, safe_error_summary=NULL,
    exit_code=NULL, updated_at=now()
WHERE id=:'run_id'
  AND status IN (
    'completed_unverified','verification_queued','verification_failed',
    'verified','restore_preflight','verifying'
  )
RETURNING path;
SQL
)
if [[ -z "$backup_value" ]]; then
  failure_stage=status_transition
  failure_code=STATUS_UPDATE_FAILED
  safe_summary="The backup was not in a verifiable state"
  false
fi

failure_stage=path_resolution
failure_code=PATH_NOT_VISIBLE
safe_summary="The configured backup root is not visible to the backup service"
backup_root=$(backup_root_path)
[[ -d "$backup_root" ]]
failure_code=BACKUP_PERMISSION_DENIED
safe_summary="The backup service cannot read the configured backup root"
[[ -r "$backup_root" && -x "$backup_root" ]]
failure_code=BACKUP_DIRECTORY_MISSING
safe_summary="The backup directory is missing or outside the configured backup root"
resolved=$(resolve_backup_directory "$backup_value")

failure_stage=manifest
failure_code=MANIFEST_MISSING
safe_summary="The backup manifest is missing"
[[ -f "$resolved/manifest.json" ]]
failure_code=MANIFEST_INVALID
safe_summary="The backup manifest is not a supported Power Monitor manifest"
grep -Eq '"format"[[:space:]]*:[[:space:]]*"power-monitor-backup/v2"' \
  "$resolved/manifest.json"

failure_stage=checksums
failure_code=CHECKSUM_FILE_MISSING
safe_summary="The backup checksum inventory is missing"
[[ -f "$resolved/checksums.sha256" ]]
failure_code=CHECKSUM_MISMATCH
safe_summary="One or more backup artifact checksums did not match"
recorded_checksums_hash=$(sed -n \
  's/.*"checksums_sha256"[[:space:]]*:[[:space:]]*"\([0-9a-f]\{64\}\)".*/\1/p' \
  "$resolved/manifest.json")
actual_checksums_hash=$(sha256sum "$resolved/checksums.sha256" | cut -d' ' -f1)
[[ -n "$recorded_checksums_hash" && "$recorded_checksums_hash" == "$actual_checksums_hash" ]]
(cd "$resolved" && sha256sum --check checksums.sha256)

failure_stage=database_artifact
failure_code=DATABASE_DUMP_MISSING
safe_summary="The logical PostgreSQL dump is missing"
database_artifact="$resolved/database.dump"
if [[ -f "$resolved/database.dump.enc" ]]; then
  database_artifact="$resolved/database.dump.enc"
fi
[[ -f "$database_artifact" ]]

failure_stage=decryption
failure_code=DECRYPTION_FAILED
safe_summary="The encrypted database artifact could not be decrypted"
restore_workspace=$(mktemp -d /tmp/power-monitor-verify.XXXXXX)
database_dump="$restore_workspace/database.dump"
decrypt_backup_artifact "$database_artifact" "$database_dump"

test_db="pm_verify_$(date -u +%Y%m%d%H%M%S)_$$_${run_id:0:8}"
failure_stage=database_permissions
failure_code=DATABASE_PERMISSION_DENIED
safe_summary="The backup database role cannot create an isolated verification database"
can_create_database=$(psql --quiet --tuples-only --no-align \
  -v ON_ERROR_STOP=1 -c \
  "SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user")
[[ "$can_create_database" == "t" ]]
failure_stage=temp_database_create
failure_code=TEMP_DATABASE_CREATE_FAILED
safe_summary="The isolated verification database could not be created"
createdb "$test_db"

failure_stage=restore
failure_code=RESTORE_FAILED
safe_summary="The logical backup could not be restored into the isolated database"
pg_restore --exit-on-error --no-owner --no-privileges \
  --dbname="$test_db" "$database_dump"

failure_stage=alembic_revision
failure_code=ALEMBIC_REVISION_MISSING
safe_summary="The restored database does not contain an Alembic revision"
revision=$(psql --dbname="$test_db" --tuples-only --no-align \
  -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version LIMIT 1")
[[ -n "$revision" ]]

failure_stage=table_inventory
failure_code=TABLE_INVENTORY_FAILED
safe_summary="The restored database table inventory is incomplete"
tables=$(psql --dbname="$test_db" --tuples-only --no-align \
  -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
[[ "$tables" -gt 20 ]]
required_tables=$(psql --dbname="$test_db" --tuples-only --no-align \
  -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) FROM (VALUES ('users'),('sites'),('devices'),('raw_readings'),('backup_runs')) AS required(name) WHERE to_regclass('public.' || required.name) IS NOT NULL")
[[ "$required_tables" -eq 5 ]]

failure_stage=integrity_checks
failure_code=INTEGRITY_CHECK_FAILED
safe_summary="A restored application integrity check failed"
status_layout_revisions=0
if [[ -n $(psql --dbname="$test_db" --tuples-only --no-align \
  -v ON_ERROR_STOP=1 -c "SELECT to_regclass('public.status_layout_state')") ]]; then
  status_layout_state=$(psql --dbname="$test_db" --tuples-only --no-align \
    -v ON_ERROR_STOP=1 -c \
    "SELECT count(*) FROM status_layout_state AS state JOIN status_layout_revisions AS revision ON revision.id = state.current_revision_id WHERE state.id='current' AND state.current_revision = revision.revision")
  status_layout_revisions=$(psql --dbname="$test_db" --tuples-only --no-align \
    -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM status_layout_revisions")
  [[ "$status_layout_state" -eq 1 && "$status_layout_revisions" -gt 0 ]]
fi

manifest_hash=$(sha256sum "$resolved/manifest.json" | cut -d' ' -f1)
failure_stage=status_update
failure_code=STATUS_UPDATE_FAILED
safe_summary="Verification succeeded but its database status could not be saved"
updated=$(psql --quiet --tuples-only --no-align -v ON_ERROR_STOP=1 \
  -v run_id="$run_id" \
  -v manifest_hash="$manifest_hash" \
  -v revision="$revision" \
  -v tables="$tables" \
  -v status_layout_revisions="$status_layout_revisions" <<'SQL'
UPDATE backup_runs
SET status='verified', verified_at=now(), verification_completed_at=now(),
    manifest_hash=:'manifest_hash',
    verification_details=jsonb_build_object(
      'migration_revision', :'revision',
      'table_count', :'tables'::integer,
      'required_table_count', 5,
      'status_layout_revisions', :'status_layout_revisions'::integer,
      'postgres_major', current_setting('server_version_num')::integer / 10000
    ),
    failed_stage=NULL, safe_error_code=NULL, safe_error_summary=NULL,
    exit_code=NULL, updated_at=now()
WHERE id=:'run_id'
RETURNING id;
SQL
)
[[ "$updated" == "$run_id" ]]

failure_stage=cleanup
failure_code=CLEANUP_FAILED
safe_summary="The isolated verification database or workspace could not be cleaned up"
dropdb --if-exists --force "$test_db"
test_db=""
rm -rf -- "$restore_workspace"
restore_workspace=""
remove_temporary_backup_key
trap - ERR INT TERM
printf 'verified backup_run=%s migration=%s tables=%s\n' \
  "$run_id" "$revision" "$tables"
write_application_log backup.verified info
