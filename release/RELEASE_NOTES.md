# Power Monitor Server 1.0.0

Initial self-hosted fleet-management release implementing `pm-protocol/1.0.0`.

Highlights include unique device enrollment and rotation, bidirectional HMAC/replay protection, push/pull sequence recovery, normalized PostgreSQL storage, editable effective-dated SCE tariffs, explicit aggregate topology, alert debounce/silence/maintenance workflows, encrypted SMTP/webhook configuration with delivery retries, exports/generated reports, signed firmware, a responsive React UI, and a hardened Compose deployment with restore-verified logical backup tooling.

Database migration: `20260720_0001`. Images: `power-monitor-api:1.0.0`, `power-monitor-frontend:1.0.0`, and `power-monitor-backup:1.0.0`.

The software provides monitored-energy cost estimates and is not a revenue-grade meter or an exact reproduction of a utility bill. Validation with real ESP32-S3/PZEM hardware remains an installation responsibility.

Portable verification on 2026-07-20 passed 21 Python tests, 5 frontend unit tests, 9 Chromium E2E tests, contract/static/migration-render/audit gates, and a 100-device/18,000-record retry load test. The build host did not contain Docker or PostgreSQL client tools, so live Compose health, image sizes, PostgreSQL migration, and temporary-database restore verification remain mandatory packaging-host gates; see `docs/TESTING.md`.
