# Power Monitor Server 1.0.0

## Utility bill PDF import and formatting update

- Adds a private administrator-only utility-bill workflow that validates local
  PDFs, uses the text layer first, invokes bounded English OCR only for pages
  without usable text, and records page/region/method/confidence evidence for
  every extracted field.
- Creates separate, linked rate-plan and billing-cycle drafts. Publication,
  effective-dated assignment, and cycle import remain distinct reviewed
  actions; an imported bill can never auto-activate a rate.
- Preserves content-addressed source evidence and sanitized provenance while
  supporting original-PDF retention or deletion, duplicate-upload
  idempotency, correction history, optimistic revisions, and conflicts against
  managed rate sources.
- Centralizes exact-versus-display formatting for currency, energy rates,
  energy, and tier bounds. Database/API values retain exact decimal strings
  while the interface uses readable values such as `$0.30/kWh`, `579 kWh`, and
  `580 kWh and above`.
- Normalizes imported text to UTF-8/NFC, repairs known mojibake at the
  structured-label boundary, and adds responsive review, exact-value preview,
  OCR, encoding, privacy, conflict, and no-auto-activation coverage.
- Adds append-only Alembic revision `20260724_0010`. The existing private
  rate-source-artifacts dataset is reused; no database, service, secret,
  capability, or host port was added.

## Tiered and hybrid rate-plan update

- Extends the existing exact-decimal rate engine with flat, time-of-use,
  billing-cycle tiered, and hybrid TOU+tiered pricing while preserving current
  TOU calculation behavior.
- Adds unlimited ordered tiers, fixed-cycle and daily-baseline thresholds,
  seasonal baselines, 28/29/30/31-day and leap-year handling, chronological
  allocation, persisted recalculation evidence, projections, and immutable
  effective-dated versions.
- Adds explicit complete-account and partial-circuit usage authority, reviewed
  utility interval/daily/cycle/bill imports, exact billing-cycle dates,
  duplicate/overlap/gap detection, conflict policy, and reconciliation evidence.
- Integrates tier progress and scope-aware account costs with Overview, Rates,
  Sites & accounts, Usage, Costs, and History. Current tier/price remains
  unavailable rather than showing a false zero when complete account context is
  missing.
- Adds append-only Alembic revision `20260723_0009`, the shared deterministic
  tier fixture, managed-source candidate review, custom flat/tiered/hybrid
  editing, updated API/schema contracts, and the complete TrueNAS workflow gate.

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

Database migration: `20260724_0010`. Images: `power-monitor-api:1.0.0`, `power-monitor-frontend:1.0.0`, and `power-monitor-backup:1.0.0`.

The software provides monitored-energy cost estimates and is not a revenue-grade meter or an exact reproduction of a utility bill. Validation with real ESP32-S3/PZEM hardware remains an installation responsibility.

Final tiered/hybrid verification on 2026-07-23 passed 106 portable Python tests,
the 100-device/18,000-reading retry gate, the live PostgreSQL populated upgrade,
downgrade/re-upgrade, prior-schema upgrade, and clean installation. It also
passed 29 frontend unit tests, 32 Chromium E2E tests, the Node 24 production
frontend build, all three OCI image builds, contract/static/migration-render
gates, and the secret scan. The standard Compose deployment reported all five
long-running services healthy at migration `20260723_0009`; its five-artifact
backup restored into a clean database with 91 public tables.

The digest-pinned TrueNAS deployment also passed the full seven-service release
gate from the final images: migration completion, strict internal-CA TLS,
host-port isolation, 3 simulated device enrollments, signed heartbeat
processing, 90 historical readings, SCE TOU calculation, two effective utility
accounts, one canonical sensor CIDR, encrypted archived-evidence backup
verification, and clean-database restore at `20260723_0009` with 91 tables. See
`docs/TESTING.md` for the recorded gates and
`deploy/truenas/installation.md` for the supported TrueNAS web-interface flow.

Final utility-bill verification on 2026-07-24 passed 114 portable Python tests,
the separate PostgreSQL 17 migration and 100-device/18,000-reading gates, 32
frontend unit/component tests, and all 34 Chromium end-to-end scenarios. Python
and npm dependency audits found no known vulnerabilities after the PDF parser
was upgraded to patched `pypdf 6.14.2`. The production API image performed real
Poppler/Tesseract extraction from the scanned fixture.

The final seven-service TrueNAS-style deployment gate used strict internal-CA
TLS and only TCP 8443, completed migration `20260724_0010`, enrolled 3 simulated
devices, accepted 90 historical readings, resolved an SCE calculation,
persisted 2 utility accounts and 1 canonical network rule, verified all 5
backup artifacts, and restored a clean 96-table database. The deployment-mode
TrueNAS render and optional ICMP overlay also passed fail-closed validation.
