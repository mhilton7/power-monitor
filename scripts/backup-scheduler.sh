#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source /srv/scripts/container-log.sh
source /srv/scripts/container-secrets.sh
load_file_backed_variable PGPASSWORD
maintain_application_logs
write_application_log backup.scheduler_started info

schedule=${BACKUP_SCHEDULE_UTC:-02:17}
[[ "$schedule" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || {
  printf 'BACKUP_SCHEDULE_UTC must use 24-hour HH:MM format\n' >&2
  exit 64
}

touch /tmp/power-monitor-backup-scheduler.ready
last_run_day=""

run_verified_backup() {
  local backup_dir
  backup_dir=$(/srv/scripts/backup-container.sh)
  /srv/scripts/verify-backup-container.sh "$backup_dir"
  date -u +%Y-%m-%dT%H:%M:%SZ >/tmp/power-monitor-backup-scheduler.last-success
}

complete_job() {
  local job_id=$1
  local result_json=$2
  psql -v ON_ERROR_STOP=1 -v job_id="$job_id" -v result_json="$result_json" <<'SQL'
UPDATE background_jobs
SET status='completed', completed_at=now(), result=:'result_json'::jsonb,
    error_code=NULL, error_detail=NULL
WHERE id=:'job_id' AND status='running';
SQL
}

fail_job() {
  local job_id=$1
  psql -v ON_ERROR_STOP=1 -v job_id="$job_id" <<'SQL'
UPDATE background_jobs
SET status='failed', completed_at=now(), error_code='backup_operation_failed',
    error_detail='The isolated backup service could not complete the request'
WHERE id=:'job_id' AND status='running';
SQL
}

claim_backup_job() {
  psql --quiet --tuples-only --no-align -v ON_ERROR_STOP=1 <<'SQL'
WITH candidate AS (
  SELECT id
  FROM background_jobs
  WHERE status='queued'
    AND job_type IN ('backup_create', 'backup_restore_preflight')
    AND COALESCE(scheduled_for, requested_at) <= now()
  ORDER BY requested_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE background_jobs AS job
SET status='running', started_at=now()
FROM candidate
WHERE job.id=candidate.id
RETURNING job.id || '|' || job.job_type || '|' ||
  COALESCE(job.progress->>'backup_run_id', '');
SQL
}

process_backup_request() {
  local claimed job_id job_type backup_run_id backup_dir completed_backup_id
  claimed=$(claim_backup_job)
  [[ -n "$claimed" ]] || return 0
  IFS='|' read -r job_id job_type backup_run_id <<<"$claimed"
  if [[ "$job_type" == "backup_create" ]]; then
    if backup_dir=$(/srv/scripts/backup-container.sh) &&
       /srv/scripts/verify-backup-container.sh "$backup_dir"; then
      completed_backup_id=$(psql --quiet --tuples-only --no-align \
        -v ON_ERROR_STOP=1 -v backup_path="$backup_dir" <<'SQL'
SELECT id
FROM backup_runs
WHERE path=:'backup_path' AND status='verified'
LIMIT 1;
SQL
      )
      complete_job "$job_id" "{\"backup_run_id\":\"${completed_backup_id}\",\"verified\":true}"
      date -u +%Y-%m-%dT%H:%M:%SZ >/tmp/power-monitor-backup-scheduler.last-success
    else
      fail_job "$job_id"
    fi
    return
  fi
  backup_dir=$(psql --quiet --tuples-only --no-align \
    -v ON_ERROR_STOP=1 -v backup_run_id="$backup_run_id" <<'SQL'
SELECT path
FROM backup_runs
WHERE id=:'backup_run_id' AND status='verified'
LIMIT 1;
SQL
  )
  if [[ -n "$backup_dir" ]] && /srv/scripts/verify-backup-container.sh "$backup_dir"; then
    complete_job "$job_id" "{\"backup_run_id\":\"${backup_run_id}\",\"verified\":true,\"maintenance_required\":true}"
  else
    fail_job "$job_id"
  fi
}

if [[ ${BACKUP_RUN_ON_STARTUP:-false} == true ]]; then
  run_verified_backup
  last_run_day=$(date -u +%Y-%m-%d)
fi

while true; do
  process_backup_request
  current_minute=$(date -u +%H:%M)
  current_day=$(date -u +%Y-%m-%d)
  if [[ "$current_minute" == "$schedule" && "$current_day" != "$last_run_day" ]]; then
    run_verified_backup
    last_run_day=$current_day
  fi
  sleep 30
done
