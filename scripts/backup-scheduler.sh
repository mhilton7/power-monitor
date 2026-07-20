#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source /srv/scripts/container-log.sh
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

if [[ ${BACKUP_RUN_ON_STARTUP:-false} == true ]]; then
  run_verified_backup
  last_run_day=$(date -u +%Y-%m-%d)
fi

while true; do
  current_minute=$(date -u +%H:%M)
  current_day=$(date -u +%Y-%m-%d)
  if [[ "$current_minute" == "$schedule" && "$current_day" != "$last_run_day" ]]; then
    run_verified_backup
    last_run_day=$current_day
  fi
  sleep 30
done
