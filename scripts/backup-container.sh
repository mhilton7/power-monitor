#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

backup_root=/data/backups
retention_days=${BACKUP_RETENTION_DAYS:-30}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_id="power-monitor-${stamp}"
partial="${backup_root}/.${backup_id}.incomplete"
final="${backup_root}/${backup_id}"
run_id=$(cat /proc/sys/kernel/random/uuid)

cleanup() {
  rm -rf -- "$partial"
}
trap cleanup ERR INT TERM
mkdir -p "$partial"

psql -v ON_ERROR_STOP=1 -c "INSERT INTO backup_runs (id, started_at, status, verification_details) VALUES ('${run_id}', now(), 'running', '{}'::jsonb)"
pg_dump --format=custom --compress=zstd:6 --no-owner --no-privileges --file="$partial/database.dump"
tar -C /data -czf "$partial/firmware.tar.gz" firmware
tar -C /data -czf "$partial/config.tar.gz" config
tar -C /data -czf "$partial/reports.tar.gz" reports
pg_restore --list "$partial/database.dump" >/dev/null

(
  cd "$partial"
  sha256sum database.dump firmware.tar.gz config.tar.gz reports.tar.gz > checksums.sha256
  manifest_hash=$(sha256sum checksums.sha256 | cut -d' ' -f1)
  printf '{\n  "format": "power-monitor-backup/v1",\n  "created_at": "%s",\n  "database": "%s",\n  "checksums_file": "checksums.sha256",\n  "checksums_sha256": "%s"\n}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PGDATABASE" "$manifest_hash" > manifest.json
)
manifest_hash=$(sha256sum "$partial/manifest.json" | cut -d' ' -f1)
mv -- "$partial" "$final"
trap - ERR INT TERM

psql -v ON_ERROR_STOP=1 -c "UPDATE backup_runs SET completed_at=now(), status='completed_unverified', path='${final}', manifest_hash='${manifest_hash}' WHERE id='${run_id}'"

find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name 'power-monitor-*' -mtime "+${retention_days}" -exec rm -rf -- {} +
printf '%s\n' "$final"
