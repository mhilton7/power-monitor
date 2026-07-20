#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source /srv/scripts/container-secrets.sh
source /srv/scripts/container-log.sh
load_file_backed_variable PGPASSWORD
prepare_backup_key
maintain_application_logs
write_application_log restore.started info

if [[ $# -ne 3 || $3 != "--yes" ]]; then
  echo "usage: restore-container.sh BACKUP_DIR TARGET_DATABASE --yes" >&2
  exit 64
fi
backup_dir=$(realpath "$1")
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
printf 'restored %s into %s\n' "$backup_dir" "$target_database"
write_application_log restore.completed info
