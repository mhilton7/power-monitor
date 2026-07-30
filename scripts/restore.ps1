param(
  [Parameter(Mandatory)][ValidatePattern('^[0-9a-f-]{36}$')][string]$BackupRunId,
  [Parameter(Mandatory)][ValidatePattern('^[A-Za-z][A-Za-z0-9_]{2,62}$')][string]$TargetDatabase,
  [switch]$ConfirmDestructiveRestore
)
$ErrorActionPreference = 'Stop'
if (-not $ConfirmDestructiveRestore) { throw 'Pass -ConfirmDestructiveRestore to replace the named target database.' }
docker compose --profile tools run --rm backup /srv/scripts/restore-container.sh $BackupRunId $TargetDatabase --yes
if ($LASTEXITCODE -ne 0) { throw "Restore failed with exit code $LASTEXITCODE" }
