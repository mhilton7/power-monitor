#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source /srv/scripts/container-secrets.sh
source /srv/scripts/container-log.sh
source /srv/scripts/backup-paths.sh
load_file_backed_variable PGPASSWORD
prepare_backup_key
maintain_application_logs
write_application_log backup.started info

backup_root=$(backup_root_path)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
if [[ $# -gt 1 ]] || { [[ $# -eq 1 ]] && [[ ! "$1" =~ ^[0-9a-f-]{36}$ ]]; }; then
  printf 'usage: backup-container.sh [backup-run-uuid]\n' >&2
  exit 64
fi
run_id=${1:-$(cat /proc/sys/kernel/random/uuid)}
backup_id="power-monitor-${stamp}-${run_id:0:8}"
partial="${backup_root}/.${backup_id}.incomplete"
final="${backup_root}/${backup_id}"
failure_stage=initialization
failure_code=BACKUP_FAILED
published=false

cleanup() {
  local exit_code=$?
  write_application_log backup.failed error || true
  rm -rf -- "$partial"
  if [[ "$published" == true && -d "$final" && ! -L "$final" ]]; then
    rm -rf -- "$final"
  fi
  remove_temporary_backup_key
  psql -v ON_ERROR_STOP=1 \
    -v run_id="$run_id" \
    -v failed_stage="$failure_stage" \
    -v safe_error_code="$failure_code" \
    -v exit_code="$exit_code" <<'SQL' || true
UPDATE backup_runs
SET status='backup_failed', completed_at=now(), updated_at=now(),
    failed_stage=:'failed_stage', safe_error_code=:'safe_error_code',
    safe_error_summary='The backup service could not complete the logical backup',
    exit_code=:'exit_code'
WHERE id=:'run_id' AND status='creating';
SQL
  exit "$exit_code"
}
trap cleanup ERR INT TERM
mkdir -p "$partial"

transitioned=$(psql --quiet --tuples-only --no-align \
  -v ON_ERROR_STOP=1 -v run_id="$run_id" <<'SQL'
INSERT INTO backup_runs (
  id, started_at, status, verification_details, trigger_type, encrypted,
  verification_attempt_count, updated_at
)
VALUES (
  :'run_id', now(), 'creating', '{}'::jsonb, 'direct', false, 0, now()
)
ON CONFLICT (id) DO UPDATE
SET status='creating', updated_at=now(), failed_stage=NULL,
    safe_error_code=NULL, safe_error_summary=NULL, exit_code=NULL
WHERE backup_runs.status IN ('queued','backup_failed')
RETURNING id;
SQL
)
if [[ "$transitioned" != "$run_id" ]]; then
  failure_stage=status_transition
  failure_code=STATUS_UPDATE_FAILED
  false
fi
failure_stage=database_dump
failure_code=DATABASE_DUMP_FAILED
pg_dump --format=custom --compress=zstd:6 --no-owner --no-privileges --file="$partial/database.dump"
failure_stage=application_artifacts
failure_code=BACKUP_ARTIFACT_FAILED
tar -C /data -czf "$partial/firmware.tar.gz" firmware
tar -C /data -czf "$partial/config.tar.gz" config
tar -C /data -czf "$partial/reports.tar.gz" reports
tar -C /app/data -czf "$partial/rate-source-artifacts.tar.gz" rate-source-artifacts
failure_stage=database_inventory
failure_code=DATABASE_DUMP_INVALID
pg_restore --list "$partial/database.dump" >/dev/null

artifacts=(database.dump firmware.tar.gz config.tar.gz reports.tar.gz rate-source-artifacts.tar.gz)
encrypted=false
if [[ -n "$BACKUP_KEY_PATH" ]]; then
  failure_stage=encryption
  failure_code=ENCRYPTION_FAILED
  for artifact in "${artifacts[@]}"; do
    encrypt_backup_artifact "$partial/$artifact"
  done
  artifacts=(database.dump.enc firmware.tar.gz.enc config.tar.gz.enc reports.tar.gz.enc rate-source-artifacts.tar.gz.enc)
  encrypted=true
fi

failure_stage=checksums
failure_code=CHECKSUM_WRITE_FAILED
(
  cd "$partial"
  sha256sum "${artifacts[@]}" > checksums.sha256
  manifest_hash=$(sha256sum checksums.sha256 | cut -d' ' -f1)
  printf '{\n  "format": "power-monitor-backup/v2",\n  "created_at": "%s",\n  "database": "%s",\n  "encrypted": %s,\n  "database_artifact": "%s",\n  "checksums_file": "checksums.sha256",\n  "checksums_sha256": "%s"\n}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PGDATABASE" "$encrypted" "${artifacts[0]}" "$manifest_hash" > manifest.json
)
manifest_hash=$(sha256sum "$partial/manifest.json" | cut -d' ' -f1)
failure_stage=atomic_publish
failure_code=BACKUP_PUBLISH_FAILED
mv -- "$partial" "$final"
published=true
remove_temporary_backup_key

size_bytes=$(du -sb "$final" | cut -f1)
failure_stage=status_update
failure_code=STATUS_UPDATE_FAILED
updated=$(psql --quiet --tuples-only --no-align -v ON_ERROR_STOP=1 \
  -v run_id="$run_id" \
  -v backup_id="$backup_id" \
  -v manifest_hash="$manifest_hash" \
  -v size_bytes="$size_bytes" \
  -v encrypted="$encrypted" <<'SQL'
UPDATE backup_runs
SET completed_at=now(), status='completed_unverified', path=:'backup_id',
    manifest_hash=:'manifest_hash', size_bytes=:'size_bytes'::bigint,
    encrypted=:'encrypted'::boolean, updated_at=now(), failed_stage=NULL,
    safe_error_code=NULL, safe_error_summary=NULL, exit_code=NULL
WHERE id=:'run_id' AND status='creating'
RETURNING id;
SQL
)
[[ "$updated" == "$run_id" ]]
published=false
trap - ERR INT TERM

printf '%s\n' "$run_id"
write_application_log backup.completed info
