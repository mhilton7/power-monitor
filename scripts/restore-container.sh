#!/usr/bin/env bash
set -Eeuo pipefail

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
[[ -f "$backup_dir/database.dump" ]] || { echo "database.dump is missing" >&2; exit 65; }
(cd "$backup_dir" && sha256sum --check checksums.sha256)
dropdb --if-exists --force "$target_database"
createdb "$target_database"
pg_restore --exit-on-error --no-owner --no-privileges --dbname="$target_database" "$backup_dir/database.dump"
psql --dbname="$target_database" -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version"
printf 'restored %s into %s\n' "$backup_dir" "$target_database"
