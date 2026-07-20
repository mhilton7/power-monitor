# Repository instructions

## Product invariants

- The shared protocol identifier is exactly `pm-protocol/1.0.0`; breaking protocol changes require a new version and matching contracts/vectors.
- Browser code calls only this server. Never put device credentials, enrollment secrets, signing keys, or device API URLs into frontend payloads.
- Device identity is the UUID, never an IP address. Signed application health is authoritative; network reachability is supporting evidence only.
- Raw readings are immutable and deduplicated by `(device_id, sequence)`. Pull and push must use the same ingestion service.
- One-CT devices default to `energy_only`. Fixed charges and baseline credits apply only to an explicitly configured `full_account` aggregate and only once per utility account.
- Money and energy calculations use `Decimal`; timestamps persist as UTC and tariffs evaluate in the account timezone.

## Build and test

- Backend: `python -m ruff check backend worker simulator`, `python -m ruff format --check backend worker simulator`, `python -m mypy backend/app worker/app simulator/simulated_device`, and `python -m pytest`.
- Frontend: `npm ci`, `npm run lint`, `npm run typecheck`, `npm run test`, `npm run build`, and `npm run e2e`.
- Contracts: `python scripts/validate_contracts.py`.
- Migrations: set `DATABASE_URL`, then `python -m alembic -c backend/alembic.ini upgrade head`; never use application-startup `create_all`.
- Compose: `docker compose build`, `docker compose up -d`, and `docker compose ps` must report every service healthy before release.

## Security and generated files

- Preserve secure cookie, CSRF, RBAC, HMAC timestamp/nonce/body verification, SSRF, upload, and secret-redaction controls when changing endpoints.
- Never log tokens, passwords, device secrets, full signatures, encryption keys, or sensitive request bodies.
- Migrations are append-only after release. Active rate versions are immutable; corrections are cloned versions.
- `shared/openapi/*.yaml`, `shared/schemas/*.json`, and `shared/auth-test-vectors/*.json` are normative and must remain synchronized with code and contract tests.
- Lockfiles, dependency reports, SBOMs, and release manifests are generated outputs that are committed only after their verification commands pass.
- Do not add runtime CDNs, analytics, trackers, mandatory cloud services, default credentials, placeholder production pages, or fake production readings.

