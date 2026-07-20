#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source /srv/scripts/container-secrets.sh
source /srv/scripts/container-log.sh
load_file_backed_variable PGPASSWORD
prepare_backup_key
maintain_application_logs
write_application_log backup.verify_started info

if [[ $# -ne 1 ]]; then
  echo "usage: verify-backup-container.sh /data/backups/power-monitor-TIMESTAMP" >&2
  exit 64
fi
backup_dir=$1
backup_root=$(realpath /data/backups)
resolved=$(realpath "$backup_dir")
case "$resolved" in
  "$backup_root"/power-monitor-*) ;;
  *) echo "backup path must be a completed directory under $backup_root" >&2; exit 64 ;;
esac
database_artifact="$resolved/database.dump"
if [[ -f "$resolved/database.dump.enc" ]]; then
  database_artifact="$resolved/database.dump.enc"
fi
[[ -f "$database_artifact" && -f "$resolved/manifest.json" ]] || {
  echo "backup is incomplete" >&2
  exit 65
}

(cd "$resolved" && sha256sum --check checksums.sha256)
restore_workspace=$(mktemp -d /tmp/power-monitor-verify.XXXXXX)
database_dump="$restore_workspace/database.dump"
decrypt_backup_artifact "$database_artifact" "$database_dump"
test_db="pm_verify_$(date -u +%Y%m%d%H%M%S)_$$"
cleanup() {
  dropdb --if-exists --force "$test_db" >/dev/null 2>&1 || true
  rm -rf -- "$restore_workspace"
  remove_temporary_backup_key
}
trap cleanup EXIT INT TERM
createdb "$test_db"
pg_restore --exit-on-error --no-owner --no-privileges --dbname="$test_db" "$database_dump"
revision=$(psql --dbname="$test_db" --tuples-only --no-align -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version LIMIT 1")
tables=$(psql --dbname="$test_db" --tuples-only --no-align -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
[[ -n "$revision" && "$tables" -gt 20 ]] || {
  echo "restored database integrity checks failed" >&2
  exit 66
}
manifest_hash=$(sha256sum "$resolved/manifest.json" | cut -d' ' -f1)
psql -v ON_ERROR_STOP=1 -c "UPDATE backup_runs SET status='verified', verified_at=now(), verification_details=jsonb_build_object('migration_revision', '${revision}', 'table_count', ${tables}) WHERE manifest_hash='${manifest_hash}'"
printf 'verified backup=%s migration=%s tables=%s\n' "$resolved" "$revision" "$tables"
write_application_log backup.verified info
