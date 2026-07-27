# System Health

System Health is the owner-only diagnostic surface at **Settings > Advanced >
System health**. Its canonical browser route is
`/settings/advanced/system-health`; `/system-health` and `/health` redirect
there. A direct refresh, browser Back/Forward, and the responsive Settings
navigation all use the same lazy-loaded Settings bundle.

## Corrected 2026-07-26 failure

The greenfield frontend previously requested `/api/v1/health/ready`. FastAPI
exposed the container readiness probe only at `/health/ready`; it did not expose
the API-prefixed URL. Caddy correctly routed `/api/*` to FastAPI, so the request
reached the API and returned the genuine `404 {"detail":"Not Found"}`. The
frontend then collapsed that response into its generic error state. The lazy
chunk, SPA fallback, Caddy routing, authentication, and owner permission were
not the cause.

The repair adds the explicit, typed, owner-only
`GET /api/v1/system/health` contract and makes the frontend call only that
endpoint. It does not alias or expose the readiness probe. Missing endpoint,
permission denial, authentication failure, timeout, server error, incompatible
schema, and release mismatch now have different UI states. Retry repeats the
health request; **View versions** always shows the frontend identity even when
the API is unavailable.

## Response and interpretation

The `system-health/1.0` response contains an overall state, check time,
release/contract/protocol compatibility, recent findings, and these components:

- API
- PostgreSQL and Alembic state
- asynchronous worker freshness
- configured local storage accessibility
- latest logical backup and verification state
- signed real-device live-data freshness
- current rate assignment and managed-source health

Each component is `healthy`, `degraded`, `unhealthy`, or `unknown`, with a
plain-language summary, safe details, useful timestamps/latency, and a
remediation route where applicable. `unknown` is deliberately used for
not-yet-applicable states such as a new installation with no real sensors.
Sensor Test Mode is never counted as real live-data evidence and therefore
cannot make System Health report a healthy live pipeline.

The endpoint never returns database URLs, credentials, keys, secret values, or
sensitive host paths. Container health checks remain `/health/live` and
`/health/ready`; they are intentionally separate from this authenticated
diagnostic contract.

## Operations

If System Health reports:

- **Database unhealthy:** inspect the PostgreSQL and one-shot migration workload
  logs and dataset ACLs.
- **Worker degraded/unhealthy:** open **Application logs** and inspect worker
  freshness and job failures.
- **Storage degraded:** verify the documented numeric TrueNAS ACL entries.
- **Backup degraded/unhealthy:** open **Data & Backups**, create a logical
  backup, verify its checksum, and perform the clean-database restore test.
- **Version mismatch:** deploy the API and frontend images from the same
  immutable release/digest set.
- **Endpoint unavailable:** verify the API image was updated with the frontend,
  then use Retry. Do not add a public API port or alter the SPA fallback.

Use the TrueNAS Apps workload/log views rather than direct Docker commands on
the host. Never disable TLS verification while diagnosing the service.
