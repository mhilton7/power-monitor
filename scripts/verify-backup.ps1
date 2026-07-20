param([Parameter(Mandatory)][string]$BackupPath)
$ErrorActionPreference = 'Stop'
docker compose --profile tools run --rm backup /srv/scripts/verify-backup-container.sh $BackupPath
if ($LASTEXITCODE -ne 0) { throw "Backup verification failed with exit code $LASTEXITCODE" }
