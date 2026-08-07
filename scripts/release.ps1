param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$ReleaseVersion,
    [ValidatePattern('^[0-9]{8}_[0-9]{4}$')]
    [string]$MigrationRevision = '20260806_0033',
    [string]$ReleaseCommit = '',
    [string]$NodeBin = '',
    [string]$NpmBin = ''
)

$ErrorActionPreference = 'Stop'
$python = '.\.venv\Scripts\python.exe'
if (-not $ReleaseCommit) {
    $ReleaseCommit = (git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $ReleaseCommit) {
        throw 'Could not resolve the release commit.'
    }
}
if (-not $NodeBin) { $NodeBin = if ($env:NODE_BIN) { $env:NODE_BIN } else { 'node' } }
if (-not $NpmBin) { $NpmBin = if ($env:NPM_BIN) { $env:NPM_BIN } else { 'npm.cmd' } }
$env:POWER_MONITOR_VERSION = $ReleaseVersion
$env:RELEASE_COMMIT = $ReleaseCommit
$env:NODE_BIN = $NodeBin
$env:NPM_BIN = $NpmBin

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

& $python scripts/release_guard.py preflight --release-commit $ReleaseCommit `
    --version $ReleaseVersion --migration-revision $MigrationRevision `
    --node-bin $NodeBin --npm-bin $NpmBin
Assert-NativeSuccess 'Clean release source and toolchain preflight'

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
$env:RUN_POSTGRES_INTEGRATION = '1'
$env:RUN_HISTORY_PERFORMANCE = '1'
try {
    & '..\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider
    $nativeCode = $LASTEXITCODE
} finally {
    Remove-Item Env:RUN_LOAD_TEST -ErrorAction SilentlyContinue
    Remove-Item Env:RUN_POSTGRES_INTEGRATION -ErrorAction SilentlyContinue
    Remove-Item Env:RUN_HISTORY_PERFORMANCE -ErrorAction SilentlyContinue
    Pop-Location
}
if ($nativeCode -ne 0) { throw "Python release suite failed with exit code $nativeCode." }
& $python scripts/generate_openapi.py --check
Assert-NativeSuccess 'OpenAPI check'
& $python scripts/validate_contracts.py
Assert-NativeSuccess 'Contract check'
& $python tools/validate-truenas-compose.py
Assert-NativeSuccess 'TrueNAS template validation'

Push-Location frontend
& $NpmBin ci
Assert-NativeSuccess 'npm clean install'
& $NpmBin run lint
Assert-NativeSuccess 'Frontend lint'
& $NpmBin run typecheck
Assert-NativeSuccess 'Frontend typecheck'
& $NpmBin test
Assert-NativeSuccess 'Frontend unit tests'
& $NpmBin run build
Assert-NativeSuccess 'Frontend build'
& $NpmBin run e2e
Assert-NativeSuccess 'Frontend E2E tests'
Pop-Location

& $python scripts/secret_scan.py
Assert-NativeSuccess 'Secret scan'
$backendAuditMount = "type=bind,source=$($PWD.Path),target=/workspace"
docker run --rm --platform linux/amd64 --mount $backendAuditMount --workdir /workspace `
    python:3.13.5-slim-bookworm sh -ec `
    'python -m venv /tmp/audit-venv && /tmp/audit-venv/bin/pip install --disable-pip-version-check pip-audit==2.9.0 && /tmp/audit-venv/bin/python -m pip_audit -r backend/requirements.lock --no-deps --format json --output release/backend-audit.json && /tmp/audit-venv/bin/python -m pip_audit -r backend/requirements.lock --no-deps --format cyclonedx-json --output release/backend-sbom.cdx.json'
Assert-NativeSuccess 'Linux/amd64 backend dependency audit and SBOM'
Push-Location frontend
$frontendAudit = (& $NpmBin audit --audit-level=high --json | Out-String)
$frontendAuditCode = $LASTEXITCODE
[IO.File]::WriteAllText(
    (Join-Path $PWD '..\release\frontend-audit.json'),
    $frontendAudit,
    [Text.UTF8Encoding]::new($false)
)
if ($frontendAuditCode -ne 0) {
    throw "Frontend dependency audit failed with exit code $frontendAuditCode."
}
& $NpmBin sbom --sbom-format cyclonedx | Set-Content -Encoding utf8 ..\release\frontend-sbom.cdx.json
Assert-NativeSuccess 'Frontend SBOM generation'
Pop-Location

& $python scripts/generate_release_reports.py --version $ReleaseVersion `
    --migration-revision $MigrationRevision --release-commit $ReleaseCommit `
    --node-bin $NodeBin --npm-bin $NpmBin
Assert-NativeSuccess 'Release report generation'
& $python scripts/release_guard.py post-generation --release-commit $ReleaseCommit
Assert-NativeSuccess 'Release source immutability check'
& $python scripts/verify_release_checksums.py --expected-version $ReleaseVersion `
    --expected-migration $MigrationRevision --expected-commit $ReleaseCommit
Assert-NativeSuccess 'Release evidence verification'

docker compose config --quiet
Assert-NativeSuccess 'Docker Compose validation'
docker compose --profile tools build --pull
Assert-NativeSuccess 'Docker image builds'

$requiredTrueNas = @(
    'TRUENAS_COMPOSE_FILE', 'TRUENAS_POOL', 'TRUENAS_GATEWAY_PORT',
    'TRUENAS_BASE_URL', 'TRUENAS_CA_CERTIFICATE', 'TRUENAS_SETUP_TOKEN_FILE',
    'TRUENAS_TEST_HOST_ROOT'
)
foreach ($name in $requiredTrueNas) {
    if (-not [Environment]::GetEnvironmentVariable($name)) {
        throw "Set $name before the mandatory isolated TrueNAS release gate."
    }
}
$hostRoot = [IO.Path]::GetFullPath($env:TRUENAS_TEST_HOST_ROOT).TrimEnd('\', '/')
$caOutput = [IO.Path]::GetFullPath($env:TRUENAS_CA_CERTIFICATE)
if ($caOutput.StartsWith($hostRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'TRUENAS_CA_CERTIFICATE must be outside TRUENAS_TEST_HOST_ROOT to prevent a bind-source self-copy.'
}
$projectVersion = $ReleaseVersion.Replace('.', '-')
$projectName = "pm-release-$projectVersion-$($ReleaseCommit.Substring(0, 12))-$PID"
& $python tools/validate-truenas-compose.py --deployment --pool $env:TRUENAS_POOL `
    --gateway-port $env:TRUENAS_GATEWAY_PORT $env:TRUENAS_COMPOSE_FILE
Assert-NativeSuccess 'Rendered TrueNAS deployment validation'
& $python tools/test-truenas-workflow.py --compose $env:TRUENAS_COMPOSE_FILE `
    --base-url $env:TRUENAS_BASE_URL --ca-certificate $env:TRUENAS_CA_CERTIFICATE `
    --setup-token-file $env:TRUENAS_SETUP_TOKEN_FILE --gateway-port $env:TRUENAS_GATEWAY_PORT `
    --docker-desktop-host-root $env:TRUENAS_TEST_HOST_ROOT `
    --docker-desktop-project-name $projectName --docker-desktop-local-application-images
Assert-NativeSuccess 'Isolated TrueNAS workflow, backup, and restore gate'

$archiveName = "power-monitor-server-$ReleaseVersion.tar.gz"
$archive = Join-Path 'release' $archiveName
if (Test-Path -LiteralPath $archive) {
    throw "Release archive already exists; refusing to reuse version $ReleaseVersion."
}
& $python scripts/create_release_archive.py --version $ReleaseVersion `
    --release-commit $ReleaseCommit --output $archive
Assert-NativeSuccess 'Source-frozen release archive with current evidence creation'
$digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
Add-Content -Encoding ascii -LiteralPath 'release\checksums.sha256' -Value "$digest  $archiveName"
& $python scripts/verify_release_checksums.py --expected-version $ReleaseVersion `
    --expected-migration $MigrationRevision --expected-commit $ReleaseCommit --require-archive
Assert-NativeSuccess 'Final archive and release provenance verification'
& $python scripts/release_guard.py post-generation --release-commit $ReleaseCommit
Assert-NativeSuccess 'Final source immutability check'
