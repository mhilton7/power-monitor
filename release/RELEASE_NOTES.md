# Power Monitor Server 1.0.0

Initial self-hosted fleet-management release implementing `pm-protocol/1.0.0`.

Highlights include unique device enrollment and rotation, bidirectional HMAC/replay protection, push/pull sequence recovery, normalized PostgreSQL storage, editable effective-dated SCE tariffs, explicit aggregate topology, alert debounce/silence/maintenance workflows, encrypted SMTP/webhook configuration with delivery retries, exports/generated reports, signed firmware, a responsive React UI, and hardened Compose deployment with encrypted, restore-verified logical backup tooling.

TrueNAS Community Edition 25.10 is a first-class target through Apps > Install via YAML. The release includes a no-build, `linux/amd64`, digest-pinned production template; Caddy-only host publication; internal database networking; numeric dataset ACL guidance; file-backed secrets and a one-time administrator setup token; internal-CA, user-certificate, and public-ACME configurations; an optional NET_RAW-only ICMP overlay; nightly encrypted backups with automated clean-database restore; and a full deployed multi-device workflow gate.

Database migration: `20260720_0001`. Images: `power-monitor-api:1.0.0`, `power-monitor-frontend:1.0.0`, and `power-monitor-backup:1.0.0`.

The software provides monitored-energy cost estimates and is not a revenue-grade meter or an exact reproduction of a utility bill. Validation with real ESP32-S3/PZEM hardware remains an installation responsibility.

Final verification on 2026-07-20 passed 31 portable Python tests (including the
100-device/18,000-record retry load gate), a separate live PostgreSQL migration
test, 5 frontend unit tests, 9 Chromium E2E tests, production frontend and OCI
image builds, contract/static/migration-render gates, dependency audits, and the
secret scan. The standard Compose deployment passed all health checks at
migration `20260720_0001`; its encrypted backup passed four checksum checks and
restored into a clean database with 54 public tables.

The digest-pinned TrueNAS deployment also passed the full seven-service release
gate: migration completion, strict internal-CA TLS, host-port isolation, 3
simulated device enrollments, signed heartbeat processing, 90 historical
readings, SCE TOU calculation, encrypted backup verification, and clean-database
restore. See `docs/TESTING.md` for the recorded gates and
`deploy/truenas/installation.md` for the supported TrueNAS web-interface flow.
