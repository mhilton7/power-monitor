# Dependency audit

Runtime dependencies are exact-pinned in `backend/pyproject.toml`, `frontend/package.json`, and `frontend/package-lock.json`. Production images pin Python 3.13.5, Node 24.4.0, PostgreSQL 17.5, Caddy 2.10.0, and nginx-unprivileged 1.29.0. Versions were selected and installed on 2026-07-19.

Executed local results:

- `npm audit --json`: 0 known vulnerabilities across the resolved frontend dependency graph.
- `pip-audit`: run as a release gate with a workspace-local cache; output is stored under `release/`.
- SBOMs use CycloneDX JSON and license inventories are stored under `release/`.

The initial `npm install` progress summary briefly reported six advisories, but the completed audit against the final lockfile reported none. The authoritative recorded result is the final `npm audit` output. Container image scanning remains an operator/CI gate because it depends on the selected registry and host scanner.

Updates must change direct pins and lockfiles together, regenerate SBOM/license reports, rerun unit/integration/contract/E2E gates, and clone rate versions rather than mutating used financial inputs.
