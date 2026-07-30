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
retention_days=${BACKUP_RETENTION_DAYS:-30}
[[ "$retention_days" =~ ^[0-9]+$ ]] || {
  printf 'BACKUP_RETENTION_DAYS must be a whole number\n' >&2
  exit 64
}

touch /tmp/power-monitor-backup-scheduler.ready
last_schedule_attempt=""

complete_job() {
  local job_id=$1 result_json=$2
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
    AND job_type IN (
      'backup_create', 'backup_verify', 'backup_restore_preflight', 'backup_delete'
      , 'backup_replace_all'
    )
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

queue_automatic_verification() {
  local create_job_id=$1 run_id=$2 verify_job_id
  verify_job_id=$(cat /proc/sys/kernel/random/uuid)
  psql -v ON_ERROR_STOP=1 \
    -v create_job_id="$create_job_id" \
    -v run_id="$run_id" \
    -v verify_job_id="$verify_job_id" <<'SQL'
BEGIN;
UPDATE background_jobs
SET status='completed', completed_at=now(),
    result=jsonb_build_object('backup_run_id', :'run_id', 'verified', false),
    error_code=NULL, error_detail=NULL
WHERE id=:'create_job_id' AND status='running';
UPDATE backup_runs
SET status='verification_queued', updated_at=now()
WHERE id=:'run_id' AND status='completed_unverified';
INSERT INTO background_jobs (
  id, job_type, status, requested_by, requested_at, scheduled_for,
  correlation_id, dedupe_key, idempotency_key, trigger_type, progress, result
)
SELECT
  :'verify_job_id', 'backup_verify', 'queued', requested_by, now(), now(),
  'backup-auto-verify:' || id, 'backup:global', 'auto-verify:' || id,
  'automatic', jsonb_build_object('backup_run_id', id), '{}'::jsonb
FROM backup_runs
WHERE id=:'run_id' AND status='verification_queued'
  AND NOT EXISTS (
    SELECT 1 FROM background_jobs
    WHERE job_type='backup_verify' AND idempotency_key='auto-verify:' || :'run_id'
  );
COMMIT;
SQL
}

process_backup_request() {
  local claimed job_id job_type run_id
  claimed=$(claim_backup_job)
  [[ -n "$claimed" ]] || return 0
  IFS='|' read -r job_id job_type run_id <<<"$claimed"
  if [[ -z "$run_id" ]]; then
    fail_job "$job_id"
    return 0
  fi

  case "$job_type" in
    backup_create)
      if /srv/scripts/backup-container.sh "$run_id" >/dev/null; then
        if queue_automatic_verification "$job_id" "$run_id"; then
          write_application_log backup.verification_queued info
        else
          fail_job "$job_id"
        fi
      else
        psql -v ON_ERROR_STOP=1 -v run_id="$run_id" <<'SQL' || true
UPDATE backup_runs
SET status='backup_failed',
    completed_at=now(),
    failed_stage='replace_all',
    safe_error_code='BACKUP_REPLACE_ALL_FAILED',
    safe_error_summary='The replacement backup could not be created',
    updated_at=now()
WHERE id=:'run_id' AND status IN ('queued', 'creating');
SQL
        fail_job "$job_id"
      fi
      ;;
    backup_verify)
      if /srv/scripts/verify-backup-container.sh "$run_id"; then
        complete_job "$job_id" \
          "{\"backup_run_id\":\"${run_id}\",\"verified\":true}"
        date -u +%Y-%m-%dT%H:%M:%SZ \
          >/tmp/power-monitor-backup-scheduler.last-success
      else
        fail_job "$job_id"
      fi
      ;;
    backup_restore_preflight)
      if /srv/scripts/verify-backup-container.sh "$run_id"; then
        complete_job "$job_id" \
          "{\"backup_run_id\":\"${run_id}\",\"verified\":true,\"maintenance_required\":true}"
      else
        psql -v ON_ERROR_STOP=1 -v run_id="$run_id" <<'SQL' || true
UPDATE backup_runs
SET status='restore_failed', updated_at=now()
WHERE id=:'run_id' AND status='verification_failed';
SQL
        fail_job "$job_id"
      fi
      ;;
    backup_delete)
      if /srv/scripts/delete-backup-container.sh "$run_id"; then
        complete_job "$job_id" \
          "{\"backup_run_id\":\"${run_id}\",\"deleted\":true}"
      else
        fail_job "$job_id"
      fi
      ;;
    backup_replace_all)
      if /srv/scripts/replace-all-backups-container.sh "$job_id" "$run_id"; then
        result_json=$(psql --quiet --tuples-only --no-align \
          -v ON_ERROR_STOP=1 -v job_id="$job_id" -v run_id="$run_id" <<'SQL'
SELECT (
  COALESCE(progress, '{}'::json)::jsonb
  || jsonb_build_object(
       'backup_run_id', :'run_id',
       'verified', true,
       'cleanup_complete', COALESCE((progress->>'final_checks_passed')::boolean, false)
     )
)::text
FROM background_jobs
WHERE id=:'job_id';
SQL
        )
        complete_job "$job_id" "$result_json"
        date -u +%Y-%m-%dT%H:%M:%SZ \
          >/tmp/power-monitor-backup-scheduler.last-success
      else
        fail_job "$job_id"
      fi
      ;;
  esac
  return 0
}

enqueue_scheduled_backup() {
  local day=$1 run_id job_id
  run_id=$(cat /proc/sys/kernel/random/uuid)
  job_id=$(cat /proc/sys/kernel/random/uuid)
  psql -v ON_ERROR_STOP=1 \
    -v run_id="$run_id" -v job_id="$job_id" -v day="$day" <<'SQL'
WITH new_run AS (
  INSERT INTO backup_runs (
    id, started_at, status, verification_details, trigger_type, encrypted,
    verification_attempt_count, updated_at
  )
  SELECT
    :'run_id', now(), 'queued', '{}'::jsonb, 'scheduled', false, 0, now()
  WHERE NOT EXISTS (
    SELECT 1 FROM background_jobs
    WHERE job_type='backup_create'
      AND idempotency_key='scheduled-nightly:' || :'day'
  )
  RETURNING id
)
INSERT INTO background_jobs (
  id, job_type, status, requested_at, scheduled_for, correlation_id,
  dedupe_key, idempotency_key, trigger_type, progress, result
)
SELECT
  :'job_id', 'backup_create', 'queued', now(), now(),
  'backup-scheduled:' || :'day', 'backup:global',
  'scheduled-nightly:' || :'day', 'scheduled',
  jsonb_build_object('backup_run_id', id), '{}'::jsonb
FROM new_run;
SQL
}

recover_interrupted_jobs() {
  psql -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
UPDATE backup_runs AS run
SET status=CASE
      WHEN job.job_type='backup_create' THEN 'backup_failed'
      WHEN job.job_type IN ('backup_verify','backup_restore_preflight')
        THEN 'verification_failed'
      WHEN job.job_type='backup_delete' THEN 'deletion_failed'
      ELSE run.status
    END,
    failed_stage='scheduler_restart',
    safe_error_code='INTERRUPTED_OPERATION',
    safe_error_summary='The backup container restarted during this operation',
    updated_at=now()
FROM background_jobs AS job
WHERE job.status='running'
  AND job.job_type IN (
    'backup_create','backup_verify','backup_restore_preflight','backup_delete',
    'backup_replace_all'
  )
  AND run.id=job.progress->>'backup_run_id';
UPDATE background_jobs
SET status='failed', completed_at=now(), error_code='backup_worker_restarted',
    error_detail='The isolated backup service restarted before this operation completed'
WHERE status='running'
  AND job_type IN (
    'backup_create','backup_verify','backup_restore_preflight','backup_delete',
    'backup_replace_all'
  );
COMMIT;
SQL
}

requeue_stuck_verification() {
  local run_id job_id
  if psql --quiet --tuples-only --no-align -v ON_ERROR_STOP=1 -c \
    "SELECT 1 FROM background_jobs WHERE status IN ('queued','running') AND job_type IN ('backup_create','backup_verify','backup_restore_preflight','backup_delete','backup_replace_all') LIMIT 1" \
    | grep -q 1; then
    return 0
  fi
  run_id=$(psql --quiet --tuples-only --no-align -v ON_ERROR_STOP=1 <<'SQL'
SELECT id
FROM backup_runs
WHERE status IN ('completed_unverified','verification_queued')
ORDER BY completed_at NULLS LAST, started_at
LIMIT 1;
SQL
  )
  [[ -n "$run_id" ]] || return 0
  job_id=$(cat /proc/sys/kernel/random/uuid)
  psql -v ON_ERROR_STOP=1 -v run_id="$run_id" -v job_id="$job_id" <<'SQL'
BEGIN;
UPDATE backup_runs
SET status='verification_queued', updated_at=now()
WHERE id=:'run_id'
  AND status IN ('completed_unverified','verification_queued');
INSERT INTO background_jobs (
  id, job_type, status, requested_at, scheduled_for, correlation_id,
  dedupe_key, idempotency_key, trigger_type, progress, result
)
VALUES (
  :'job_id', 'backup_verify', 'queued', now(), now(),
  'backup-recovery-verify:' || :'run_id', 'backup:global',
  'recovery-verify:' || :'run_id', 'recovery',
  jsonb_build_object('backup_run_id', :'run_id'), '{}'::jsonb
)
ON CONFLICT DO NOTHING;
COMMIT;
SQL
}

enqueue_retention_cleanup() {
  local run_id job_id
  if psql --quiet --tuples-only --no-align -v ON_ERROR_STOP=1 -c \
    "SELECT 1 FROM background_jobs WHERE status IN ('queued','running') AND job_type IN ('backup_create','backup_verify','backup_restore_preflight','backup_delete','backup_replace_all') LIMIT 1" \
    | grep -q 1; then
    return 0
  fi
  run_id=$(psql --quiet --tuples-only --no-align \
    -v ON_ERROR_STOP=1 -v retention_days="$retention_days" <<'SQL'
SELECT id
FROM backup_runs
WHERE completed_at < now() - (:'retention_days' || ' days')::interval
  AND status IN ('verified','completed_unverified','verification_failed','backup_failed')
  AND (
    verified_at IS NULL
    OR (
      SELECT count(*) FROM backup_runs
      WHERE verified_at IS NOT NULL AND deleted_at IS NULL
    ) > 1
  )
ORDER BY CASE WHEN status='verified' THEN 1 ELSE 0 END, completed_at
LIMIT 1;
SQL
  )
  [[ -n "$run_id" ]] || return 0
  job_id=$(cat /proc/sys/kernel/random/uuid)
  psql -v ON_ERROR_STOP=1 -v run_id="$run_id" -v job_id="$job_id" <<'SQL'
BEGIN;
UPDATE backup_runs
SET status='deleting', deletion_reason='Automatic retention cleanup',
    original_size_bytes=size_bytes, updated_at=now()
WHERE id=:'run_id'
  AND status IN ('verified','completed_unverified','verification_failed','backup_failed');
INSERT INTO background_jobs (
  id, job_type, status, requested_at, scheduled_for, correlation_id,
  dedupe_key, idempotency_key, trigger_type, progress, result
)
VALUES (
  :'job_id', 'backup_delete', 'queued', now(), now(),
  'backup-retention-delete:' || :'run_id', 'backup:global',
  'retention-delete:' || :'run_id', 'retention',
  jsonb_build_object('backup_run_id', :'run_id'), '{}'::jsonb
)
ON CONFLICT DO NOTHING;
COMMIT;
SQL
}

recover_interrupted_jobs

if [[ ${BACKUP_RUN_ON_STARTUP:-false} == true ]]; then
  startup_day=$(date -u +%Y-%m-%d)
  enqueue_scheduled_backup "startup-${startup_day}" || true
fi

while true; do
  process_backup_request
  requeue_stuck_verification || true
  enqueue_retention_cleanup || true
  current_minute=$(date -u +%H:%M)
  current_day=$(date -u +%Y-%m-%d)
  schedule_attempt="${current_day}T${current_minute}"
  if [[ ( "$current_minute" == "$schedule" || "$current_minute" > "$schedule" ) &&
        "$schedule_attempt" != "$last_schedule_attempt" ]]; then
    # Mark the attempt before queueing. The unique database idempotency key
    # independently prevents another scheduler or a container restart from
    # creating a second nightly job.
    last_schedule_attempt=$schedule_attempt
    enqueue_scheduled_backup "$current_day" || \
      write_application_log backup.schedule_skipped warning
  fi
  sleep 30
done
