#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source /srv/scripts/container-secrets.sh
source /srv/scripts/container-log.sh
source /srv/scripts/backup-paths.sh
load_file_backed_variable PGPASSWORD
maintain_application_logs

if [[ $# -ne 1 || ! "$1" =~ ^[0-9a-f-]{36}$ ]]; then
  echo "usage: delete-backup-container.sh backup-run-uuid" >&2
  exit 64
fi
run_id=$1
backup_value=""
moved=false

record_failure() {
  local exit_code=$?
  trap - ERR
  result=not_removed
  [[ "$moved" == true ]] && result=trash_retained
  psql -v ON_ERROR_STOP=1 \
    -v run_id="$run_id" \
    -v result="$result" \
    -v exit_code="$exit_code" <<'SQL' || true
UPDATE backup_runs
SET status='deletion_failed', artifact_removal_result=:'result',
    failed_stage='artifact_removal', safe_error_code='BACKUP_DELETE_FAILED',
    safe_error_summary='The backup artifact could not be removed safely',
    exit_code=:'exit_code', updated_at=now()
WHERE id=:'run_id';
SQL
  write_application_log backup.delete_failed error || true
  exit "$exit_code"
}
trap record_failure ERR INT TERM

abort_if_pinned() {
  local pinned
  pinned=$(psql --quiet --tuples-only --no-align \
    -v ON_ERROR_STOP=1 -v run_id="$run_id" <<'SQL'
SELECT CASE WHEN EXISTS (
  SELECT 1
  FROM data_reset_operations
  WHERE backup_run_id=:'run_id'
    AND state NOT IN ('completed','cancelled','failed_before_commit')
) THEN 'true' ELSE 'false' END;
SQL
  )
  [[ "$pinned" != "true" ]] && return 0
  if [[ "$moved" == true && -n "${trash_path:-}" && -n "${completed_path:-}" &&
        -d "$trash_path" && ! -e "$completed_path" ]]; then
    mv -- "$trash_path" "$completed_path"
    moved=false
  fi
  psql -v ON_ERROR_STOP=1 -v run_id="$run_id" <<'SQL'
UPDATE backup_runs
SET status=COALESCE(pre_deletion_status, 'deletion_failed'),
    failed_stage='pin_recheck', safe_error_code='BACKUP_PINNED_BY_DATA_RESET',
    safe_error_summary='The backup is pinned by an active coordinated data reset',
    updated_at=now()
WHERE id=:'run_id' AND status='deleting';
SQL
  write_application_log backup.delete_blocked warning
  trap - ERR INT TERM
  exit 75
}

abort_if_pinned

backup_value=$(psql --quiet --tuples-only --no-align \
  -v ON_ERROR_STOP=1 -v run_id="$run_id" <<'SQL'
SELECT COALESCE(path, '__missing__')
FROM backup_runs
WHERE id=:'run_id' AND status='deleting'
LIMIT 1;
SQL
)
[[ -n "$backup_value" ]]
if [[ "$backup_value" == "__missing__" ]]; then
  psql -v ON_ERROR_STOP=1 -v run_id="$run_id" <<'SQL'
UPDATE backup_runs
SET status='deleted', deleted_at=now(), size_bytes=0,
    artifact_removal_result='already_missing', updated_at=now(),
    failed_stage=NULL, safe_error_code=NULL, safe_error_summary=NULL,
    exit_code=NULL
WHERE id=:'run_id' AND status='deleting';
SQL
  write_application_log backup.deleted info
  trap - ERR INT TERM
  exit 0
fi
identifier=$(backup_identifier_from_value "$backup_value")
root=$(backup_root_path)
completed_path="$root/$identifier"
existing_trash_path="$root/.trash/$identifier"
if [[ -L "$completed_path" || -L "$existing_trash_path" ]]; then
  false
fi
if [[ ! -e "$completed_path" && ! -e "$existing_trash_path" ]]; then
  psql -v ON_ERROR_STOP=1 -v run_id="$run_id" <<'SQL'
UPDATE backup_runs
SET status='deleted', deleted_at=now(), size_bytes=0,
    artifact_removal_result='already_missing', updated_at=now(),
    failed_stage=NULL, safe_error_code=NULL, safe_error_summary=NULL,
    exit_code=NULL
WHERE id=:'run_id' AND status='deleting';
SQL
  write_application_log backup.deleted info
  trap - ERR INT TERM
  exit 0
fi
source_path=$(resolve_backup_or_trash_directory "$backup_value")
trash_root="$root/.trash"
mkdir -p "$trash_root"
[[ ! -L "$trash_root" ]]
trash_root=$(realpath "$trash_root")
[[ "$trash_root" == "$root/.trash" ]]
trash_path="$trash_root/$identifier"

abort_if_pinned
if [[ "$source_path" != "$trash_path" ]]; then
  [[ ! -e "$trash_path" ]]
  mv -- "$source_path" "$trash_path"
fi
moved=true
[[ "$trash_path" == "$root/.trash/$identifier" && ! -L "$trash_path" ]]
abort_if_pinned
rm -rf -- "$trash_path"
moved=false

psql -v ON_ERROR_STOP=1 -v run_id="$run_id" <<'SQL'
UPDATE backup_runs
SET status='deleted', deleted_at=now(), size_bytes=0,
    artifact_removal_result='removed', updated_at=now(),
    failed_stage=NULL, safe_error_code=NULL, safe_error_summary=NULL,
    exit_code=NULL
WHERE id=:'run_id' AND status='deleting';
SQL
write_application_log backup.deleted info
