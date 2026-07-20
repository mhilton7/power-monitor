# API

`shared/openapi/server-api.yaml` is generated from FastAPI by `scripts/generate_openapi.py`; `--check` detects drift. `device-ingest-api.yaml` and `device-api.yaml` define both directions of `pm-protocol/1.0.0`. Run `python scripts/contract_check.py` after route or schema changes.

Browser routes use an opaque `pm_session` cookie and `X-CSRF-Token` on mutations. Device routes use the HMAC headers in [Device protocol](DEVICE_PROTOCOL.md). Errors are `application/problem+json` with type, title, status, detail, instance, stable code, and request ID. Time series uses bounded ranges/cursors; large CSV/JSON output is an asynchronous export job with expiring authorized download.

Key groups are `/api/v1/auth`, `/sites`, `/utility-accounts`, `/circuits`, `/aggregate-sets`, `/devices`, `/readings/history`, `/rates`, `/billing`, `/alerts`, `/exports`, `/firmware-*`, `/reports`, `/backups`, `/audit-events`, `/system/info`, and `/events/stream`. Health endpoints are outside `/api/v1`. Metrics are authenticated.

Do not hand-edit generated server OpenAPI. Add schema and authorization in the route, regenerate, validate, and run API/RBAC tests.
