#!/usr/bin/env bash
set -Eeuo pipefail

python_bin=${PYTHON_BIN:-.venv/bin/python}
: "${RELEASE_VERSION:?set RELEASE_VERSION to a new X.Y.Z version}"
release_version=$RELEASE_VERSION
migration_revision=${MIGRATION_REVISION:-20260806_0031}
release_commit=${RELEASE_COMMIT:-$(git rev-parse HEAD)}
node_bin=${NODE_BIN:-node}
npm_bin=${NPM_BIN:-npm}

[[ "$release_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "RELEASE_VERSION must be strict X.Y.Z semantic versioning" >&2
  exit 2
}
[[ "$migration_revision" =~ ^[0-9]{8}_[0-9]{4}$ ]] || {
  echo "MIGRATION_REVISION must use YYYYMMDD_NNNN" >&2
  exit 2
}
export POWER_MONITOR_VERSION=$release_version
export RELEASE_COMMIT=$release_commit
export NODE_BIN=$node_bin
export NPM_BIN=$npm_bin

"$python_bin" scripts/release_guard.py preflight \
  --release-commit "$release_commit" --version "$release_version" \
  --migration-revision "$migration_revision" --node-bin "$node_bin" \
  --npm-bin "$npm_bin"
"$python_bin" -m ruff check backend/app backend/tests backend/alembic worker simulator scripts tools
"$python_bin" -m ruff format --check backend/app backend/tests backend/alembic worker simulator scripts tools
(cd backend && ../"$python_bin" -m mypy app)
MYPYPATH=backend "$python_bin" -m mypy worker simulator --explicit-package-bases
(cd backend && RUN_LOAD_TEST=1 RUN_POSTGRES_INTEGRATION=1 RUN_HISTORY_PERFORMANCE=1 \
  ../"$python_bin" -m pytest -q -p no:cacheprovider)
"$python_bin" scripts/generate_openapi.py --check
"$python_bin" scripts/validate_contracts.py
"$python_bin" tools/validate-truenas-compose.py
(cd frontend-next && "$npm_bin" ci && "$npm_bin" run lint && \
  "$npm_bin" run typecheck && "$npm_bin" test && "$npm_bin" run build && \
  "$npm_bin" run e2e)
"$python_bin" scripts/secret_scan.py
docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=${PWD},target=/workspace" --workdir /workspace \
  python:3.13.5-slim-bookworm sh -ec \
  'python -m venv /tmp/audit-venv && /tmp/audit-venv/bin/pip install --disable-pip-version-check pip-audit==2.9.0 && /tmp/audit-venv/bin/python -m pip_audit -r backend/requirements.lock --no-deps --format json --output release/backend-audit.json && /tmp/audit-venv/bin/python -m pip_audit -r backend/requirements.lock --no-deps --format cyclonedx-json --output release/backend-sbom.cdx.json'
(cd frontend-next && "$npm_bin" audit --audit-level=high --json \
  > ../release/frontend-audit.json && \
  "$npm_bin" sbom --sbom-format cyclonedx > ../release/frontend-sbom.cdx.json)
"$python_bin" scripts/generate_release_reports.py \
  --version "$release_version" --migration-revision "$migration_revision" \
  --release-commit "$release_commit" --node-bin "$node_bin" --npm-bin "$npm_bin"
"$python_bin" scripts/release_guard.py post-generation --release-commit "$release_commit"
"$python_bin" scripts/verify_release_checksums.py \
  --expected-version "$release_version" --expected-migration "$migration_revision" \
  --expected-commit "$release_commit"

docker compose config --quiet
docker compose --profile tools build --pull

: "${TRUENAS_COMPOSE_FILE:?set TRUENAS_COMPOSE_FILE to the rendered deployment}"
: "${TRUENAS_POOL:?set TRUENAS_POOL}"
: "${TRUENAS_GATEWAY_PORT:?set TRUENAS_GATEWAY_PORT}"
: "${TRUENAS_BASE_URL:?set TRUENAS_BASE_URL}"
: "${TRUENAS_CA_CERTIFICATE:?set TRUENAS_CA_CERTIFICATE to a safe temporary export path}"
: "${TRUENAS_SETUP_TOKEN_FILE:?set TRUENAS_SETUP_TOKEN_FILE}"
: "${TRUENAS_TEST_HOST_ROOT:?set TRUENAS_TEST_HOST_ROOT to an isolated temporary host root}"
host_root=$(realpath "$TRUENAS_TEST_HOST_ROOT")
ca_output=$(realpath -m "$TRUENAS_CA_CERTIFICATE")
case "$ca_output" in
  "$host_root"/*)
    echo "TRUENAS_CA_CERTIFICATE must be outside TRUENAS_TEST_HOST_ROOT" >&2
    exit 2
    ;;
esac
project_version=${release_version//./-}
project_name="pm-release-${project_version}-${release_commit:0:12}-$$"
"$python_bin" tools/validate-truenas-compose.py --deployment --pool "$TRUENAS_POOL" \
  --gateway-port "$TRUENAS_GATEWAY_PORT" "$TRUENAS_COMPOSE_FILE"
"$python_bin" tools/test-truenas-workflow.py --compose "$TRUENAS_COMPOSE_FILE" \
  --base-url "$TRUENAS_BASE_URL" --ca-certificate "$TRUENAS_CA_CERTIFICATE" \
  --setup-token-file "$TRUENAS_SETUP_TOKEN_FILE" --gateway-port "$TRUENAS_GATEWAY_PORT" \
  --docker-desktop-host-root "$TRUENAS_TEST_HOST_ROOT" \
  --docker-desktop-project-name "$project_name" \
  --docker-desktop-local-application-images

archive_name="power-monitor-server-${release_version}.tar.gz"
archive="release/${archive_name}"
if [[ -e "$archive" ]]; then
  echo "release archive already exists; refusing to reuse version ${release_version}" >&2
  exit 2
fi
"$python_bin" scripts/create_release_archive.py --version "$release_version" \
  --release-commit "$release_commit" --output "$archive"
archive_digest=$(sha256sum "$archive")
printf '%s  %s\n' "${archive_digest%% *}" "$archive_name" \
  >> release/checksums.sha256
"$python_bin" scripts/verify_release_checksums.py \
  --expected-version "$release_version" --expected-migration "$migration_revision" \
  --expected-commit "$release_commit" --require-archive
"$python_bin" scripts/release_guard.py post-generation --release-commit "$release_commit"
