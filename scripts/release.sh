#!/usr/bin/env bash
set -Eeuo pipefail
python_bin=${PYTHON_BIN:-.venv/bin/python}

"$python_bin" -m ruff check backend/app backend/tests backend/alembic worker simulator scripts
"$python_bin" -m ruff format --check backend/app backend/tests backend/alembic worker simulator scripts
(cd backend && ../"$python_bin" -m mypy app)
MYPYPATH=backend "$python_bin" -m mypy worker simulator --explicit-package-bases
(cd backend && RUN_LOAD_TEST=1 ../"$python_bin" -m pytest -q -p no:cacheprovider)
"$python_bin" scripts/generate_openapi.py --check
"$python_bin" scripts/validate_contracts.py
(cd frontend && npm ci && npm run lint && npm run typecheck && npm test && npm run build && npm run e2e)
"$python_bin" scripts/secret_scan.py
"$python_bin" -m pip_audit -r backend/requirements.lock --no-deps --format cyclonedx-json --output release/backend-sbom.cdx.json
(cd frontend && npm audit --audit-level=high && npm sbom --sbom-format cyclonedx > ../release/frontend-sbom.cdx.json)
"$python_bin" scripts/generate_release_reports.py

docker compose config --quiet
docker compose build --pull
docker compose up -d --wait
path=$(docker compose --profile tools run --rm backup /srv/scripts/backup-container.sh | tail -1)
docker compose --profile tools run --rm backup /srv/scripts/verify-backup-container.sh "$path"
docker compose ps

git archive --format=tar.gz --prefix=power-monitor-server-1.0.0/ -o release/power-monitor-server-1.0.0.tar.gz HEAD
sha256sum release/power-monitor-server-1.0.0.tar.gz >> release/checksums.sha256
