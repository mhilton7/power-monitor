#!/usr/bin/env bash

log_root=${LOG_PATH:-/data/logs}
log_retention_days=${LOG_RETENTION_DAYS:-90}

write_application_log() {
  local event=${1:?event required}
  local level=${2:-info}
  [[ "$event" =~ ^[a-z0-9_.-]+$ && "$level" =~ ^[a-z]+$ ]] || return 64
  mkdir -p "$log_root"
  printf '{"event":"%s","level":"%s","timestamp":"%s","service":"backup","category":"backup","log_format_version":"pm-log/1.0.0"}\n' \
    "$event" "$level" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$log_root/backup-$(date -u +%Y-%m-%d).jsonl"
}

maintain_application_logs() {
  local today
  today=$(date -u +%Y-%m-%d)
  mkdir -p "$log_root"
  find "$log_root" -maxdepth 1 -type f -name 'backup-*.jsonl' ! -name "backup-${today}.jsonl" -exec gzip -f -- {} +
  find "$log_root" -maxdepth 1 -type f \( -name 'backup-*.jsonl' -o -name 'backup-*.jsonl.gz' \) -mtime "+$((log_retention_days - 1))" -delete
}
