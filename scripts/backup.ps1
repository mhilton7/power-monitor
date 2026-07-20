$ErrorActionPreference = 'Stop'
docker compose --profile tools run --rm backup /srv/scripts/backup-container.sh
if ($LASTEXITCODE -ne 0) { throw "Backup failed with exit code $LASTEXITCODE" }
