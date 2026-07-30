#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

source /srv/scripts/container-log.sh
source /srv/scripts/container-secrets.sh
source /srv/scripts/backup-paths.sh
load_file_backed_variable PGPASSWORD
maintain_application_logs

if [[ $# -ne 2 || ! "$1" =~ ^[0-9a-f-]{36}$ || ! "$2" =~ ^[0-9a-f-]{36}$ ]]; then
  echo "usage: replace-all-backups-container.sh job-uuid replacement-backup-uuid" >&2
  exit 64
fi

job_id=$1
replacement_id=$2

progress() {
  local stage=$1 message=$2
  psql -v ON_ERROR_STOP=1 \
    -v job_id="$job_id" -v stage="$stage" -v message="$message" <<'SQL'
UPDATE background_jobs
SET progress=COALESCE(progress, '{}'::json)::jsonb
      || jsonb_build_object(
           'stage', :'stage',
           'message', :'message',
           'updated_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
         )
WHERE id=:'job_id' AND status='running' AND job_type='backup_replace_all';
SQL
}

replacement_verified() {
  psql --quiet --tuples-only --no-align -v ON_ERROR_STOP=1 \
    -v replacement_id="$replacement_id" <<'SQL'
SELECT CASE
         WHEN status='verified' AND verified_at IS NOT NULL THEN 'true'
         ELSE 'false'
       END
FROM backup_runs
WHERE id=:'replacement_id' AND deleted_at IS NULL;
SQL
}

progress preparing "Preparing replacement backup"
write_application_log backup.replace_all_started info

progress creating_replacement "Creating replacement backup"
BACKUP_JOB_ID="$job_id" /srv/scripts/backup-container.sh "$replacement_id" >/dev/null

progress verifying_checksums "Verifying checksums"
BACKUP_JOB_ID="$job_id" /srv/scripts/verify-backup-container.sh "$replacement_id"
[[ "$(replacement_verified)" == "true" ]]

progress replacement_verified "Replacement verified"

mapfile -t old_backup_ids < <(
  psql --quiet --tuples-only --no-align -v ON_ERROR_STOP=1 \
    -v job_id="$job_id" -v replacement_id="$replacement_id" <<'SQL'
SELECT value
FROM background_jobs AS job,
     jsonb_array_elements_text(
       COALESCE(job.progress->'old_backup_ids', '[]'::json)::jsonb
     ) AS value
WHERE job.id=:'job_id'
  AND value <> :'replacement_id'
ORDER BY value;
SQL
)

progress removing_old_backups "Removing old backups"
deleted_count=0
failed_count=0
reclaimed_bytes=0
for old_id in "${old_backup_ids[@]}"; do
  [[ "$old_id" =~ ^[0-9a-f-]{36}$ ]] || {
    failed_count=$((failed_count + 1))
    continue
  }
  transitioned=$(
    psql --quiet --tuples-only --no-align -v ON_ERROR_STOP=1 \
      -v old_id="$old_id" -v replacement_id="$replacement_id" <<'SQL'
WITH replacement AS (
  SELECT requested_by
  FROM backup_runs
  WHERE id=:'replacement_id' AND status='verified' AND verified_at IS NOT NULL
),
eligible AS (
  UPDATE backup_runs
  SET pre_deletion_status=status,
      status='deleting',
      deleted_by=(SELECT requested_by FROM replacement),
      deletion_reason='Replaced by verified backup ' || :'replacement_id',
      original_size_bytes=COALESCE(size_bytes, original_size_bytes, 0),
      replaced_by_backup_id=:'replacement_id',
      updated_at=now()
  WHERE id=:'old_id'
    AND id <> :'replacement_id'
    AND deleted_at IS NULL
    AND status IN (
      'verified','completed_unverified','verification_failed',
      'backup_failed','artifact_missing','deletion_failed'
    )
    AND EXISTS (SELECT 1 FROM replacement)
  RETURNING id, COALESCE(original_size_bytes, 0) AS original_size_bytes
)
SELECT id || '|' || original_size_bytes FROM eligible;
SQL
  )
  if [[ -z "$transitioned" ]]; then
    failed_count=$((failed_count + 1))
    continue
  fi
  old_size=${transitioned#*|}
  if /srv/scripts/delete-backup-container.sh "$old_id"; then
    deleted_count=$((deleted_count + 1))
    reclaimed_bytes=$((reclaimed_bytes + old_size))
  else
    failed_count=$((failed_count + 1))
  fi
done

progress final_checks "Running final checks"
active_count=$(
  psql --quiet --tuples-only --no-align -v ON_ERROR_STOP=1 \
    -v replacement_id="$replacement_id" <<'SQL'
SELECT count(*)
FROM backup_runs
WHERE deleted_at IS NULL
  AND id=:'replacement_id'
  AND status='verified'
  AND verified_at IS NOT NULL;
SQL
)
non_deleted_count=$(
  psql --quiet --tuples-only --no-align -v ON_ERROR_STOP=1 <<'SQL'
SELECT count(*) FROM backup_runs WHERE deleted_at IS NULL;
SQL
)

backup_root=$(backup_root_path)
shopt -s nullglob
completed_directories=("$backup_root"/power-monitor-*)
incomplete_directories=("$backup_root"/.power-monitor-*.incomplete)
trash_entries=("$backup_root"/.trash/*)
shopt -u nullglob
filesystem_count=${#completed_directories[@]}
incomplete_count=${#incomplete_directories[@]}
trash_count=${#trash_entries[@]}

final_ok=false
if [[ "$failed_count" -eq 0 &&
      "$active_count" -eq 1 &&
      "$non_deleted_count" -eq 1 &&
      "$filesystem_count" -eq 1 &&
      "$incomplete_count" -eq 0 &&
      "$trash_count" -eq 0 ]]; then
  final_ok=true
fi

psql -v ON_ERROR_STOP=1 \
  -v audit_id="$(cat /proc/sys/kernel/random/uuid)" \
  -v job_id="$job_id" \
  -v replacement_id="$replacement_id" \
  -v deleted_count="$deleted_count" \
  -v failed_count="$failed_count" \
  -v reclaimed_bytes="$reclaimed_bytes" \
  -v non_deleted_count="$non_deleted_count" \
  -v filesystem_count="$filesystem_count" \
  -v incomplete_count="$incomplete_count" \
  -v trash_count="$trash_count" \
  -v final_ok="$final_ok" <<'SQL'
UPDATE background_jobs
SET progress=COALESCE(progress, '{}'::json)::jsonb
      || jsonb_build_object(
           'stage', CASE WHEN :'final_ok'::boolean THEN 'complete' ELSE 'cleanup_incomplete' END,
           'message', CASE
             WHEN :'final_ok'::boolean THEN 'Backup replacement complete'
             ELSE 'Replacement verified; one or more older artifacts require cleanup'
           END,
           'replacement_backup_id', :'replacement_id',
           'deleted_backup_count', :'deleted_count'::integer,
           'failed_deletion_count', :'failed_count'::integer,
           'reclaimed_bytes', :'reclaimed_bytes'::bigint,
           'remaining_backup_count', :'non_deleted_count'::integer,
           'completed_directory_count', :'filesystem_count'::integer,
           'incomplete_directory_count', :'incomplete_count'::integer,
           'trash_entry_count', :'trash_count'::integer,
           'final_checks_passed', :'final_ok'::boolean
         )
WHERE id=:'job_id' AND status='running';
INSERT INTO audit_events (
  id, occurred_at, actor_type, actor_id, action, object_type, object_id,
  outcome, correlation_id, details
)
SELECT
  :'audit_id', now(), 'service', run.requested_by,
  CASE
    WHEN :'final_ok'::boolean THEN 'backup.replace_all_completed'
    ELSE 'backup.replace_all_cleanup_incomplete'
  END,
  'backup_request', :'job_id',
  CASE WHEN :'final_ok'::boolean THEN 'success' ELSE 'partial' END,
  'backup-replace-all:' || :'replacement_id',
  jsonb_build_object(
    'replacement_backup_id', :'replacement_id',
    'deleted_backup_count', :'deleted_count'::integer,
    'failed_deletion_count', :'failed_count'::integer,
    'reclaimed_bytes', :'reclaimed_bytes'::bigint,
    'remaining_backup_count', :'non_deleted_count'::integer,
    'final_checks_passed', :'final_ok'::boolean
  )
FROM backup_runs AS run
WHERE run.id=:'replacement_id';
SQL

if [[ "$final_ok" == true ]]; then
  write_application_log backup.replace_all_completed info
else
  write_application_log backup.replace_all_cleanup_incomplete warning
fi
