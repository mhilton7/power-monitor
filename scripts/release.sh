#!/usr/bin/env bash
set -Eeuo pipefail
python_bin=${PYTHON_BIN:-.venv/bin/python}
release_version=${RELEASE_VERSION:-1.0.29}
migration_revision=${MIGRATION_REVISION:-20260803_0027}

[[ "$release_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "RELEASE_VERSION must be strict X.Y.Z semantic versioning" >&2
  exit 2
}
[[ "$migration_revision" =~ ^[0-9]{8}_[0-9]{4}$ ]] || {
  echo "MIGRATION_REVISION must use YYYYMMDD_NNNN" >&2
  exit 2
}
export POWER_MONITOR_VERSION=$release_version
export RELEASE_COMMIT
RELEASE_COMMIT=$(git rev-parse HEAD)

"$python_bin" -m ruff check backend/app backend/tests backend/alembic worker simulator scripts tools
"$python_bin" -m ruff format --check backend/app backend/tests backend/alembic worker simulator scripts tools
(cd backend && ../"$python_bin" -m mypy app)
MYPYPATH=backend "$python_bin" -m mypy worker simulator --explicit-package-bases
(cd backend && RUN_LOAD_TEST=1 ../"$python_bin" -m pytest -q -p no:cacheprovider)
"$python_bin" scripts/generate_openapi.py --check
"$python_bin" scripts/validate_contracts.py
"$python_bin" tools/validate-truenas-compose.py
(cd frontend-next && npm ci && npm run lint && npm run typecheck && npm test && npm run build && npm run e2e)
"$python_bin" scripts/secret_scan.py
"$python_bin" -m pip_audit -r backend/requirements.lock --no-deps --format cyclonedx-json --output release/backend-sbom.cdx.json
(cd frontend-next && npm audit --audit-level=high && npm sbom --sbom-format cyclonedx > ../release/frontend-sbom.cdx.json)
"$python_bin" scripts/generate_release_reports.py \
  --version "$release_version" --migration-revision "$migration_revision"

docker compose config --quiet
docker compose build --pull
docker compose up -d --wait
run_id=$(docker compose --profile tools run --rm backup /srv/scripts/backup-container.sh | tail -1)
docker compose --profile tools run --rm backup /srv/scripts/verify-backup-container.sh "$run_id"
docker compose ps

: "${TRUENAS_COMPOSE_FILE:?set TRUENAS_COMPOSE_FILE to the rendered digest-pinned deployment}"
: "${TRUENAS_POOL:?set TRUENAS_POOL}"
: "${TRUENAS_GATEWAY_PORT:?set TRUENAS_GATEWAY_PORT}"
: "${TRUENAS_BASE_URL:?set TRUENAS_BASE_URL}"
: "${TRUENAS_CA_CERTIFICATE:?set TRUENAS_CA_CERTIFICATE}"
: "${TRUENAS_SETUP_TOKEN_FILE:?set TRUENAS_SETUP_TOKEN_FILE}"
"$python_bin" tools/validate-truenas-compose.py --deployment --pool "$TRUENAS_POOL" \
  --gateway-port "$TRUENAS_GATEWAY_PORT" "$TRUENAS_COMPOSE_FILE"
"$python_bin" tools/test-truenas-workflow.py --compose "$TRUENAS_COMPOSE_FILE" \
  --base-url "$TRUENAS_BASE_URL" --ca-certificate "$TRUENAS_CA_CERTIFICATE" \
  --setup-token-file "$TRUENAS_SETUP_TOKEN_FILE" --gateway-port "$TRUENAS_GATEWAY_PORT"

archive_name="power-monitor-server-${release_version}.tar.gz"
git archive --format=tar.gz --prefix="power-monitor-server-${release_version}/" \
  -o "release/${archive_name}" HEAD
sha256sum "release/${archive_name}" >> release/checksums.sha256
