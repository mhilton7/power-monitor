#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source /srv/scripts/container-secrets.sh
source /srv/scripts/container-log.sh
load_file_backed_variable PGPASSWORD
prepare_backup_key
maintain_application_logs
write_application_log backup.started info

backup_root=/data/backups
retention_days=${BACKUP_RETENTION_DAYS:-30}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_id="power-monitor-${stamp}"
partial="${backup_root}/.${backup_id}.incomplete"
final="${backup_root}/${backup_id}"
run_id=$(cat /proc/sys/kernel/random/uuid)

cleanup() {
  write_application_log backup.failed error || true
  rm -rf -- "$partial"
  remove_temporary_backup_key
}
trap cleanup ERR INT TERM
mkdir -p "$partial"

psql -v ON_ERROR_STOP=1 -c "INSERT INTO backup_runs (id, started_at, status, verification_details) VALUES ('${run_id}', now(), 'running', '{}'::jsonb)"
pg_dump --format=custom --compress=zstd:6 --no-owner --no-privileges --file="$partial/database.dump"
tar -C /data -czf "$partial/firmware.tar.gz" firmware
tar -C /data -czf "$partial/config.tar.gz" config
tar -C /data -czf "$partial/reports.tar.gz" reports
tar -C /app/data -czf "$partial/rate-source-artifacts.tar.gz" rate-source-artifacts
pg_restore --list "$partial/database.dump" >/dev/null

artifacts=(database.dump firmware.tar.gz config.tar.gz reports.tar.gz rate-source-artifacts.tar.gz)
encrypted=false
if [[ -n "$BACKUP_KEY_PATH" ]]; then
  for artifact in "${artifacts[@]}"; do
    encrypt_backup_artifact "$partial/$artifact"
  done
  artifacts=(database.dump.enc firmware.tar.gz.enc config.tar.gz.enc reports.tar.gz.enc rate-source-artifacts.tar.gz.enc)
  encrypted=true
fi

(
  cd "$partial"
  sha256sum "${artifacts[@]}" > checksums.sha256
  manifest_hash=$(sha256sum checksums.sha256 | cut -d' ' -f1)
  printf '{\n  "format": "power-monitor-backup/v2",\n  "created_at": "%s",\n  "database": "%s",\n  "encrypted": %s,\n  "database_artifact": "%s",\n  "checksums_file": "checksums.sha256",\n  "checksums_sha256": "%s"\n}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PGDATABASE" "$encrypted" "${artifacts[0]}" "$manifest_hash" > manifest.json
)
manifest_hash=$(sha256sum "$partial/manifest.json" | cut -d' ' -f1)
mv -- "$partial" "$final"
trap - ERR INT TERM
remove_temporary_backup_key

psql -v ON_ERROR_STOP=1 -c "UPDATE backup_runs SET completed_at=now(), status='completed_unverified', path='${final}', manifest_hash='${manifest_hash}' WHERE id='${run_id}'"

find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name 'power-monitor-*' -mtime "+${retention_days}" -exec rm -rf -- {} +
printf '%s\n' "$final"
write_application_log backup.completed info
