$ErrorActionPreference = 'Stop'
$python = '.\.venv\Scripts\python.exe'
& $python -m ruff check backend/app backend/tests backend/alembic worker simulator scripts
& $python -m ruff format --check backend/app backend/tests backend/alembic worker simulator scripts
Push-Location backend; & '..\.venv\Scripts\python.exe' -m mypy app; Pop-Location
$env:MYPYPATH = (Join-Path $PWD 'backend')
& $python -m mypy worker simulator --explicit-package-bases
Push-Location backend; $env:RUN_LOAD_TEST = '1'; & '..\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider; Remove-Item Env:RUN_LOAD_TEST; Pop-Location
& $python scripts/generate_openapi.py --check
& $python scripts/validate_contracts.py
Push-Location frontend
npm ci; npm run lint; npm run typecheck; npm test; npm run build; npm run e2e
Pop-Location
& $python scripts/secret_scan.py
& $python -m pip_audit -r backend/requirements.lock --no-deps --format cyclonedx-json --output release/backend-sbom.cdx.json
Push-Location frontend; npm audit --audit-level=high; npm sbom --sbom-format cyclonedx | Set-Content -Encoding utf8 ..\release\frontend-sbom.cdx.json; Pop-Location
& $python scripts/generate_release_reports.py
docker compose config --quiet
docker compose build --pull
docker compose up -d --wait
if ($LASTEXITCODE -ne 0) { throw 'Release gate failed.' }
