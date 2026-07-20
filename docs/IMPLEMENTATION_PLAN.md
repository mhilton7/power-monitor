# Power Monitor Server implementation plan

1. Capture the official SCE, Espressif, and Peacefair source check; establish pinned dependency and protocol baselines.
2. Define `pm-protocol/1.0.0` JSON Schemas, OpenAPI contracts, deterministic HMAC vectors, and database migrations before application data access.
3. Build secure browser sessions/RBAC, enrollment, device HMAC authentication, heartbeat, idempotent sequence ingestion, gap tracking, topology, rates, billing, alerts, firmware, reports, exports, audit, and administration APIs.
4. Build the PostgreSQL-coordinated worker for polling, synchronization, normalization, costing, alert evaluation, notifications, reports, and backup scheduling.
5. Build the deterministic multi-device simulator early and use its local API, push mode, fault scenarios, replay/conflict cases, and load mode as contract fixtures.
6. Build the responsive React application with first-run, fleet, device, topology, history, billing, rates, alerts, enrollment, firmware, reports, backups, and administration experiences.
7. Package multi-stage containers, Caddy HTTPS, Compose health checks, least-privilege runtime settings, bootstrap/migration/backup/restore scripts, operations documentation, SBOMs, dependency/license audits, and a versioned release archive.
8. Run every locally feasible lint, type, unit, integration, migration, contract, simulator, frontend, E2E, production-build, secret-scan, load, backup, image-build, and Compose-health gate; fix failures and record evidence and any host-tool limitations.

No task item is considered complete merely because its interface exists: its production path, authorization, persistence, validation, audit behavior, failure behavior, tests, and operating documentation are part of the same slice.
