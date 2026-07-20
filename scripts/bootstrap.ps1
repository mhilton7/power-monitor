$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath '.env')) { throw 'Copy .env.example to .env and replace every CHANGE_ME value first.' }
if (Select-String -LiteralPath '.env' -SimpleMatch 'CHANGE_ME' -Quiet) { throw '.env still contains CHANGE_ME values.' }
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose configuration validation failed.' }
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw 'Compose startup failed.' }
docker compose ps
