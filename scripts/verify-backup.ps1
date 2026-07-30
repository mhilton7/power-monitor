param([Parameter(Mandatory)][ValidatePattern('^[0-9a-f-]{36}$')][string]$BackupRunId)
$ErrorActionPreference = 'Stop'
docker compose --profile tools run --rm backup /srv/scripts/verify-backup-container.sh $BackupRunId
if ($LASTEXITCODE -ne 0) { throw "Backup verification failed with exit code $LASTEXITCODE" }
