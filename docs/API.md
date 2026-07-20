# API

`shared/openapi/server-api.yaml` is generated from FastAPI by `scripts/generate_openapi.py`; `--check` detects drift. `device-ingest-api.yaml` and `device-api.yaml` define both directions of `pm-protocol/1.0.0`. Run `python scripts/contract_check.py` after route or schema changes.

Browser routes use an opaque `pm_session` cookie and `X-CSRF-Token` on mutations. Device routes use the HMAC headers in [Device protocol](DEVICE_PROTOCOL.md). Errors are `application/problem+json` with type, title, status, detail, instance, stable code, and request ID. Time series uses bounded ranges/cursors; large CSV/JSON output is an asynchronous export job with expiring authorized download.

Key groups are `/api/v1/auth`, `/sites`, `/utility-accounts`, `/circuits`, `/aggregate-sets`, `/devices`, `/readings/history`, `/rates`, `/billing`, `/alerts`, `/exports`, `/firmware-*`, `/reports`, `/backups`, `/audit-events`, `/system/info`, and `/events/stream`. Administrator log discovery and export use `/api/v1/admin/logs/availability`, `POST /api/v1/admin/logs/exports`, export status, and the short-lived authorized download route. Safe sensor removal uses `POST /api/v1/admin/devices/{device_id}/unclaim`; it requires CSRF, an administrator, and exact name-or-ID confirmation. Health endpoints are outside `/api/v1`. Metrics are authenticated.

Rate-plan lifecycle endpoints live under `/api/v1/rates/plans`,
`/api/v1/rates/versions`, and `/api/v1/rates/assignments`. Approved-source,
check, artifact, and candidate-review endpoints live under
`/api/v1/admin/rate-*`; asynchronous check status is
`GET /api/v1/jobs/{job_id}`. See
`docs/rate-automation-and-custom-plans.md` for request semantics, immutable
activation, source evidence, and custom-plan schema behavior.

Log exports accept date values rather than filesystem names, limit selection to
the retained 90-day window and known service identifiers, and return a streamed
ZIP with a per-file SHA-256 manifest. The server never exposes its log directory
or temporary path. `GET /api/v1/devices` defaults to active sensors; use
`?lifecycle=decommissioned` for the administrator archived view.

Do not hand-edit generated server OpenAPI. Add schema and authorization in the route, regenerate, validate, and run API/RBAC tests.
