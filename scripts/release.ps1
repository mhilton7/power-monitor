$ErrorActionPreference = 'Stop'
$python = '.\.venv\Scripts\python.exe'

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

& $python -m ruff check backend/app backend/tests backend/alembic worker simulator scripts tools
Assert-NativeSuccess 'Ruff lint'
& $python -m ruff format --check backend/app backend/tests backend/alembic worker simulator scripts tools
Assert-NativeSuccess 'Ruff format check'
Push-Location backend
& '..\.venv\Scripts\python.exe' -m mypy app
$nativeCode = $LASTEXITCODE
Pop-Location
if ($nativeCode -ne 0) { throw "Backend mypy failed with exit code $nativeCode." }
$env:MYPYPATH = (Join-Path $PWD 'backend')
& $python -m mypy worker simulator --explicit-package-bases
Assert-NativeSuccess 'Worker and simulator mypy'
Push-Location backend
$env:RUN_LOAD_TEST = '1'
& '..\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider
$nativeCode = $LASTEXITCODE
Remove-Item Env:RUN_LOAD_TEST
Pop-Location
if ($nativeCode -ne 0) { throw "Python test suite failed with exit code $nativeCode." }
& $python scripts/generate_openapi.py --check
Assert-NativeSuccess 'OpenAPI check'
& $python scripts/validate_contracts.py
Assert-NativeSuccess 'Contract check'
& $python tools/validate-truenas-compose.py
Assert-NativeSuccess 'TrueNAS template validation'
Push-Location frontend
npm ci
Assert-NativeSuccess 'npm clean install'
npm run lint
Assert-NativeSuccess 'Frontend lint'
npm run typecheck
Assert-NativeSuccess 'Frontend typecheck'
npm test
Assert-NativeSuccess 'Frontend unit tests'
npm run build
Assert-NativeSuccess 'Frontend build'
npm run e2e
Assert-NativeSuccess 'Frontend E2E tests'
Pop-Location
& $python scripts/secret_scan.py
Assert-NativeSuccess 'Secret scan'
& $python -m pip_audit -r backend/requirements.lock --no-deps --format cyclonedx-json --output release/backend-sbom.cdx.json
Assert-NativeSuccess 'Backend dependency audit'
Push-Location frontend
npm audit --audit-level=high
Assert-NativeSuccess 'Frontend dependency audit'
npm sbom --sbom-format cyclonedx | Set-Content -Encoding utf8 ..\release\frontend-sbom.cdx.json
Assert-NativeSuccess 'Frontend SBOM generation'
Pop-Location
& $python scripts/generate_release_reports.py
Assert-NativeSuccess 'Release report generation'
docker compose config --quiet
Assert-NativeSuccess 'Docker Compose validation'
docker compose build --pull
Assert-NativeSuccess 'Docker image builds'
docker compose up -d --wait
Assert-NativeSuccess 'Docker health gate'
$backupOutput = docker compose --profile tools run --rm backup /srv/scripts/backup-container.sh
Assert-NativeSuccess 'Backup generation gate'
$backupPath = ($backupOutput | Select-Object -Last 1).Trim()
docker compose --profile tools run --rm backup /srv/scripts/verify-backup-container.sh $backupPath
Assert-NativeSuccess 'Backup restore-verification gate'
docker compose ps
Assert-NativeSuccess 'Docker Compose status'

$requiredTrueNas = @(
    'TRUENAS_COMPOSE_FILE', 'TRUENAS_POOL', 'TRUENAS_GATEWAY_PORT',
    'TRUENAS_BASE_URL', 'TRUENAS_CA_CERTIFICATE', 'TRUENAS_SETUP_TOKEN_FILE'
)
foreach ($name in $requiredTrueNas) {
    if (-not [Environment]::GetEnvironmentVariable($name)) {
        throw "Set $name before the mandatory TrueNAS release workflow gate."
    }
}
& $python tools/validate-truenas-compose.py --deployment --pool $env:TRUENAS_POOL `
    --gateway-port $env:TRUENAS_GATEWAY_PORT $env:TRUENAS_COMPOSE_FILE
Assert-NativeSuccess 'Rendered TrueNAS deployment validation'
& $python tools/test-truenas-workflow.py --compose $env:TRUENAS_COMPOSE_FILE `
    --base-url $env:TRUENAS_BASE_URL --ca-certificate $env:TRUENAS_CA_CERTIFICATE `
    --setup-token-file $env:TRUENAS_SETUP_TOKEN_FILE --gateway-port $env:TRUENAS_GATEWAY_PORT
Assert-NativeSuccess 'TrueNAS deployed workflow gate'

$archive = 'release\power-monitor-server-1.0.0.tar.gz'
git archive --format=tar.gz --prefix=power-monitor-server-1.0.0/ -o $archive HEAD
Assert-NativeSuccess 'Release archive creation'
$digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
Add-Content -Encoding ascii -LiteralPath 'release\checksums.sha256' -Value "$digest  power-monitor-server-1.0.0.tar.gz"
