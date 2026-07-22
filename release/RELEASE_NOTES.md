# Power Monitor Server 1.0.0

## Utility account and sensor network policy update

- Adds the complete utility-account lifecycle inside **Administration > Sites
  & accounts**: seven-step creation, multiple accounts per site, effective-dated
  rate assignments, explicit energy/full/allocated cost scopes, separately
  sourced adjustments, readiness states, recalculation, and archival.
- Makes rate-library states and cross-page setup actions explicit, and resolves
  the current plan, period, price, next period, and billing-cycle window without
  requiring an enrolled sensor.
- Adds independent sensor-ingress and server-pull policies with reviewed
  allow-listed private, all-private, and deny-all modes; canonical IPv4/IPv6
  CIDRs; no-scan address testing; signed-heartbeat address evidence; trusted
  proxy handling; and worker enforcement.
- Preserves legacy behavior visibly: empty pull CIDRs migrate to deny-all,
  configured CIDRs are copied, and formerly unrestricted signed ingress remains
  review-required until an administrator selects an explicit mode.
- Adds append-only Alembic revision `20260721_0008`, updated contracts,
  administrator permissions and audit evidence, documentation, reviewed UI
  captures, and an expanded digest-pinned TrueNAS acceptance workflow.

## Users & Access and Dashboard & Login Text update

- Adds server-enforced granular permissions, custom role revisions, user-site
  scope, session revocation, short-lived reauthentication, and protected last-
  administrator/self-change safeguards.
- Adds the integrated `/administration/users-access` workspace with user filters,
  effective-access/session/audit detail, role/site diff preview, status actions,
  and custom-role lifecycle.
- Adds the approved interface-text catalog, public login subset with compiled
  fallback, drafts, responsive preview, immutable publication, defaults,
  rollback, and JSON import/export at `/administration/interface-text`.
- Adds append-only Alembic revision `20260720_0005`. TrueNAS requires no new
  dataset or secret; follow `deploy/truenas/upgrade.md` and take the documented
  logical backup and ZFS snapshots first.

Initial self-hosted fleet-management release implementing `pm-protocol/1.0.0`.

Highlights include unique device enrollment and rotation, bidirectional HMAC/replay protection, push/pull sequence recovery, normalized PostgreSQL storage, weekly evidence-backed SCE synchronization, administrator-managed approved SCE sources, deterministic public TOU page extraction, candidate review and activation, a four-step custom-rate editor, effective-dated account assignments, explicit aggregate topology, alert debounce/silence/maintenance workflows, encrypted SMTP/webhook configuration with delivery retries, exports/generated reports, signed firmware, a responsive React UI, and hardened Compose deployment with encrypted, restore-verified logical backup tooling.

TrueNAS Community Edition 25.10 is a first-class target through Apps > Install via YAML. The release includes a no-build, `linux/amd64`, digest-pinned production template; Caddy-only host publication; internal database networking; numeric dataset ACL guidance; file-backed secrets and a one-time administrator setup token; internal-CA, user-certificate, and public-ACME configurations; an optional NET_RAW-only ICMP overlay; nightly encrypted backups with automated clean-database restore; and a full deployed multi-device workflow gate.

Database migration: `20260721_0008`. Images: `power-monitor-api:1.0.0`, `power-monitor-frontend:1.0.0`, and `power-monitor-backup:1.0.0`.

The software provides monitored-energy cost estimates and is not a revenue-grade meter or an exact reproduction of a utility bill. Validation with real ESP32-S3/PZEM hardware remains an installation responsibility.

Final verification on 2026-07-21 passed 90 portable Python tests, plus the
100-device/18,000-record retry load gate, a live PostgreSQL populated upgrade,
downgrade/re-upgrade, prior-schema upgrade, and clean installation. It also
passed 25 frontend unit tests, 31 Chromium E2E tests, the production frontend
build, all three OCI image builds, contract/static/migration-render gates,
dependency audits, and the secret scan. The standard Compose deployment
reported all five long-running services healthy at migration `20260721_0008`;
its five-artifact backup restored into a clean database with 81 public tables.

The digest-pinned TrueNAS deployment also passed the full seven-service release
gate from the final images: migration completion, strict internal-CA TLS,
host-port isolation, 3
simulated device enrollments, signed heartbeat processing, 90 historical
readings, SCE TOU calculation, two effective utility accounts, one canonical
sensor CIDR, encrypted archived-evidence backup verification, and clean-database
restore that preserved the new account and policy records. See `docs/TESTING.md` for the recorded gates and
`deploy/truenas/installation.md` for the supported TrueNAS web-interface flow.
