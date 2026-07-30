#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source /srv/scripts/container-secrets.sh
source /srv/scripts/container-log.sh
source /srv/scripts/backup-paths.sh
load_file_backed_variable PGPASSWORD
prepare_backup_key
maintain_application_logs
write_application_log restore.started info

if [[ $# -ne 3 || $3 != "--yes" || ! "$1" =~ ^[0-9a-f-]{36}$ ]]; then
  echo "usage: restore-container.sh BACKUP_RUN_UUID TARGET_DATABASE --yes" >&2
  exit 64
fi
run_id=$1
backup_value=$(psql --quiet --tuples-only --no-align \
  -v ON_ERROR_STOP=1 -v run_id="$run_id" <<'SQL'
SELECT path
FROM backup_runs
WHERE id=:'run_id' AND status='verified' AND verified_at IS NOT NULL
LIMIT 1;
SQL
)
[[ -n "$backup_value" ]] || {
  echo "a verified backup run is required" >&2
  exit 65
}
backup_dir=$(resolve_backup_directory "$backup_value")
target_database=$2
[[ "$target_database" =~ ^[A-Za-z][A-Za-z0-9_]{2,62}$ ]] || {
  echo "unsafe target database name" >&2
  exit 64
}
database_artifact="$backup_dir/database.dump"
if [[ -f "$backup_dir/database.dump.enc" ]]; then
  database_artifact="$backup_dir/database.dump.enc"
fi
[[ -f "$database_artifact" ]] || { echo "database dump is missing" >&2; exit 65; }
(cd "$backup_dir" && sha256sum --check checksums.sha256)
restore_workspace=$(mktemp -d /tmp/power-monitor-restore.XXXXXX)
trap 'rm -rf -- "$restore_workspace"; remove_temporary_backup_key' EXIT INT TERM
database_dump="$restore_workspace/database.dump"
decrypt_backup_artifact "$database_artifact" "$database_dump"
dropdb --if-exists --force "$target_database"
createdb "$target_database"
pg_restore --exit-on-error --no-owner --no-privileges --dbname="$target_database" "$database_dump"
psql --dbname="$target_database" -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version"
printf 'restored backup run %s into %s\n' "$run_id" "$target_database"
write_application_log restore.completed info
