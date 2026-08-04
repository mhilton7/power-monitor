# Power Monitor Server 1.0.31

## Stability, History, OTA, and release provenance

- Supersedes the unpublished 1.0.30 candidate after correcting its embedded
  release-notes identity; no existing image tag or release asset is overwritten.
- Adds metric-aware exact PostgreSQL History aggregation, signed continuation
  pages, repeatable-read pricing snapshots, and bounded/coalesced browser refresh.
- Centralizes authenticated OTA deployment reconciliation in the worker with
  monotonic evidence, attempt isolation, expiry handling, and actionable terminal
  states.
- Coordinates the canary-only sensor 1.0.16 internal-memory and fail-closed OTA
  repair while preserving `pm-protocol/1.0.0`, TLS verification, configuration,
  enrollment, sequence state, and immutable microSD history.
- GHCR publication and production-YAML promotion remain gated on the exact
  physical firmware canary; this candidate is not a stable firmware promotion.
- Makes source archive contents deterministic across release hosts by disabling
  `core.autocrlf` conversion during `git archive`, so embedded source evidence
  hashes always bind to the immutable Git-object bytes.
- Bounds Windows Playwright release concurrency to prevent host socket-buffer
  exhaustion from being misreported as a production asset or route failure.

## OTA interruption recovery and protected permission changes

- Reconciles interrupted, stale, post-boot, and rolled-back sensor firmware
  deployments from authenticated heartbeat evidence instead of leaving a
  deployment indefinitely at `Downloading 0%`.
- Records source firmware/build/boot identity and bounded interruption evidence,
  adds the append-only `20260803_0027` migration, and presents truthful
  indeterminate download progress with elapsed and last-activity timestamps.
- Integrates the sensor's allocation-stable OTA recovery ledger and bounded
  milestone reporting while preserving `pm-protocol/1.0.0`, enrolled-device
  credentials, existing CA trust, and historical deployment evidence.
- Adds the complete protected-change confirmation flow for permission updates:
  the interface prompts for the current password or MFA code, the server issues
  and validates a short-lived confirmation, and invalid or cancelled
  confirmation never changes permissions.
- Releases server `1.0.29` alongside sensor firmware `1.0.15`. The physical
  1.0.14 USB bootstrap passed Wi-Fi/enrollment/CA preservation and repeated TLS
  heartbeat/config/manifest checks; managed-OTA and one-hour canary validation
  remain explicit post-publication acceptance gates.

## Existing-trust OTA firmware updates

- Adds owner-only ESP32-S3 firmware upload, validation, release, canary, batch,
  retry, abort, recovery, and audit workflows without introducing a second
  certificate authority or per-release signing-key ceremony.
- Authenticates canonical `pm-ota-manifest/2` documents with a device-specific
  HKDF-derived HMAC key while preserving the existing Caddy CA, HTTPS hostname
  verification, enrolled device secret, and `pm-protocol/1.0.0` contract.
- Validates ESP32-S3 application images before storage, including image format,
  chip target, project, semantic version, protocol, checksum, and appended hash.
- Adds append-only migration `20260802_0026`, firmware view/manage/deploy
  capabilities, per-device deployment state, post-boot validation, explicit
  rollback reporting, generated schemas/vectors, and production UI coverage.
- Separates firmware low-total-memory faults from ordinary heap fragmentation,
  adds operation-aware TLS admission and recovery grace periods, and keeps the
  sensor Web UI and measurement/storage paths responsive during OTA work.
- Releases sensor firmware `1.0.11` and server `1.0.28`; physical-device OTA and
  rollback verification remain deployment acceptance steps, not simulated PASS
  claims.

## Detailed actionable notifications

- Replaces vague alert rows with a typed notification contract containing affected resource,
  first/last observations, occurrence count, observed/expected evidence, impact, recovery,
  remediation, acknowledgement, silence, and safe delivery state.
- Adds distinct operational, optional setup, and delivery-failure kinds backed by one
  authoritative catalog and the existing alert pipeline.
- Adds reversible, audited user/Home suppression for the optional SMTP recommendation while
  explicitly rejecting permanent suppression of operational, security, meter, storage,
  history, backup, firmware, and server faults.
- Adds immutable paginated lifecycle/delivery history, detailed delivery attempt stages and
  safe SMTP error codes, effective-permission actions, accessible responsive drawer sections,
  and organized Notification settings.
- Adds append-only Alembic revision `20260731_0023`, regenerated OpenAPI, backend lifecycle
  tests, frontend runtime-contract tests, and notification-center interaction coverage.

## Sensor-authoritative billing and simplified rate import

- Treats uploaded utility bills as reviewed tariff evidence only: plan identity,
  prices, schedules, and tier thresholds may be imported, while reported kWh,
  tier allocations, subtotals, totals, taxes, and credits remain reference-only.
- Removes the standard bill-import path that created cumulative usage, usage
  authority, tier-progress, and projection seeds from a bill.
- Calculates current usage, chronological tier progression, energy cost, and
  projections from reviewed Power Monitor sensor measurements; missing
  full-account coverage is unavailable rather than replaced with bill usage or
  zero.
- Adds append-only migration `20260730_0021` plus a dry-run-first reconciliation
  tool for legacy bill-derived authority. Unfinalized cycles can be recalculated
  from immutable sensor readings; finalized cycles are preserved and flagged.
- Simplifies Billing into rate plan, billing cycle, sensor usage source, and a
  collapsed advanced area. The four-step bill wizard saves reviewed rate rules,
  labels bill quantities as reference-only, and applies dates only through a
  separate optional action.

## Safe backup consolidation and durable low-power History

- Adds an owner-only **Replace all backups** operation that inventories current
  recovery points, creates an encrypted replacement, verifies checksums,
  restores it into a temporary PostgreSQL database, and only then removes the
  captured older generations.
- Persists operation progress across browser refresh, excludes the replacement
  from deletion, preserves all old backups when creation or verification fails,
  and retains audit tombstones plus targeted retry when cleanup is partial.
- Separates live heartbeat display from durable sequenced History and repairs
  sensor/server acknowledgement recovery without fabricating microSD records.
- Preserves sub-Wh energy at `0.8-1 W` when the PZEM cumulative register has
  not advanced, reconciles later counter changes without double counting, and
  retries normalization for committed raw readings stranded by an earlier bad
  row.
- Adds append-only Alembic revision `20260730_0020`, regenerated API contracts,
  secret-free ingest diagnostics, and backup/History regression coverage.

## Verified backups and precise live-data presentation

- Replaces restart-sensitive, in-memory backup scheduling with database-backed,
  idempotent create/verify jobs and reconciles interrupted or orphaned runs.
- Adds explicit backup verification and protected deletion APIs and UI, atomic
  publication, manifest/checksum validation, PostgreSQL 17 test restoration,
  encryption-aware verification, retention safety, and audited lifecycle
  metadata without exposing host paths.
- Uses one application-level one-second clock for receipt-age labels, based on
  the server receipt timestamp and server clock, without one-second API traffic.
- Preserves fractional live power such as `0.8 W`, distinguishes missing values
  from measured zero, and limits History coverage percentages to two decimals.
- Adds append-only Alembic revision `20260730_0019`, regenerated contracts,
  responsive visual regressions, and TrueNAS backup/recovery documentation.

## Authoritative live sensor measurements

- Resolves heartbeat and committed-reading data through one validated,
  site-scoped latest-measurement service used by devices, fleet summary,
  status indicators, and SSE.
- Fixes the Single Home contradiction where Sensor Health showed a real
  wattage while Home reported zero sensors and `0 W`; missing power is now
  null, while a legitimate measured zero remains `0 W`.
- Adds voltage, current, frequency, power factor, source, sequence,
  measurement time, and shared freshness state to each sensor response and
  compact Sensor Health entry.
- Uses explicit topology for multiple sensors and an unambiguous sole-sensor
  fallback for Single Home Mode, without summing parents with children or
  whole-home sensors with submeters.
- Adds signed retained-sequence bootstrap for an otherwise permanent initial
  microSD gap, structured secret-free ingest/fleet/SSE diagnostics, append-only
  migration `20260729_0018`, and responsive live-status regressions.
- Increases the ESP32 server-sync task reserve and fails a TLS attempt safely
  before stack exhaustion can starve the sensor Web UI.

## System Health and isolated Sensor Test Mode

- Corrected the greenfield Settings System Health request from the missing
  `/api/v1/health/ready` path to the new typed, owner-only
  `/api/v1/system/health` diagnostic contract.
- Added explicit healthy/degraded/unhealthy/unknown component states, safe
  remediation, release compatibility, precise 401/403/404/5xx/timeout states,
  Retry, and legacy route redirects.
- Added owner-only ephemeral Sensor Test Mode with 0–32 stable simulated
  sensors, load/offline/interval/duration controls, pause/reset/expiry,
  persistent labeling, separate Home/History/Billing/Sensors presentation, and
  opt-in unsaved energy-only cost preview.
- Synthetic sessions create no real sensor, credential, reading, bill, saved
  cost, alert, export, backup, or firmware record. No migration, service,
  dataset, secret, capability, port, or firmware change is required.

## Current-plan assignment and actionable Configuration Status

- Fixed the Single Home current-plan read path so Billing, Home, History, and
  cost context resolve the assignment that was actually committed.
- Added typed atomic assignment results, optimistic concurrency checks, complete
  rate-document validation, and explicit current-assignment revision data.
- Added the server-authoritative Configuration Status resolver and accessible,
  direct-action status surface.
- No database migration, firmware change, service, port, or secret was added.

## Authoritative rate assignments and observable source checks

- Separates plan-library publication from electric-service assignment.
  Published versions are available; only an effective `RateAssignment` is
  labelled Current.
- Enforces zero or one current/scheduled version at each instant with locked,
  idempotent, half-open assignment writes and a PostgreSQL overlap guard. Adds
  explicit Make current, Replace current, end, conflict review, and Owner repair
  workflows while preserving historical pricing provenance.
- Replaces clone-only changes with same-plan **Adjust Rates** drafts. Publishing
  never silently reassigns service; unused drafts can be deleted, while used or
  published versions use audited retirement, soft removal, Removed view, and
  restoration without reassignment.
- Makes **Sources > Check now** a deduplicated observable background job with
  loading/progress, per-source results, last-checked timestamps, candidate and
  artifact counts, history, scoped Retry, safe errors, and end-to-end audit
  records.
- Adds revision-safe, reasoned, effective-dated manual adjustment create/edit/
  remove controls with optional evidence references.
- Adds append-only Alembic revision `20260725_0017`, regenerated OpenAPI,
  backend/frontend/migration regressions, and TrueNAS upgrade/rollback
  guidance. No ESP32 firmware, protocol, service, dataset, secret, capability,
  network, mount, or host port changes.

## Normalized bill review, rate-plan lifecycle, and dropdown repair

- Stores a normative `normalized-utility-bill/1.0` artifact with every
  extraction revision, including sanitized file metadata, separate rate-plan
  and billing-cycle data, exact recognized evidence, validation, and explicitly
  reasoned missing fields.
- Corrects the frontend contract from obsolete `billing_cycle` data to the
  server `cycle_draft`, groups recognized review values by meaning, keeps
  optional missing values out of the primary review, and prevents a null value
  from acquiring confirmed confidence.
- Adds authenticated normalized JSON, redacted extracted-text, and safe
  retained-original reprocessing operations. Reprocessing creates an immutable
  revision and never overwrites confirmed or published data.
- Adds explicit effective-dated rate-plan unassignment, retirement, soft
  removal, impact review tokens, stale-dependency rejection, a complete Removed
  view, and restore without reassignment while retaining costs, reports,
  assignments, bill/source evidence, and audit history.
- Replaces ad-hoc Billing, sensor, and account menus with one accessible
  dropdown primitive that closes on outside interaction, Escape, route change,
  another menu, scroll, resize, and owner teardown. Destructive selections close
  before opening confirmation.
- Adds append-only Alembic revision `20260725_0016`, regenerated OpenAPI and
  normalized-artifact schema, backend/frontend/browser/PostgreSQL regressions,
  and TrueNAS upgrade/rollback verification. No firmware, protocol, service,
  dataset, secret, capability, network, mount, or host port changes.

## Single Home greenfield frontend

- Replaces the production browser bundle with an independent React/TypeScript
  application containing exactly Home, History, Billing, and Settings.
- Adds typed adapters, default-home resolution, shared live state, responsive
  dark/light design tokens, accessible charts and tables, homeowner wording,
  and a persisted nine-step first-run flow.
- Preserves secure sign-in, users and roles, sensors and enrollment, exact
  history costs, custom and managed rates, strict PDF/OCR bill review, alerts,
  backups, audit records, and advanced owner controls through existing server
  APIs.
- Redirects legacy bookmarks into canonical destinations without importing or
  rendering any legacy page. The production Dockerfile and release reports now
  build and inventory only `frontend-next`.
- Adds audited on-demand backup creation and restore-preflight requests. The
  existing UID/GID 10003 backup scheduler handles those jobs and continues
  nightly encrypted, checksum-verified logical backups.
- Passes responsive unit, architecture, browser, migration, load, container,
  and seven-service TrueNAS workflow gates. No ESP32 firmware or protocol file
  changed.

## Reliable active rate-plan switching

- Replaces the Rate Plans page's navigation-only assignment button with a
  utility-account selector that clearly shows the current and replacement
  plans.
- Adds explicit **Switch now** and **Schedule a change** paths. Immediate
  switches close the prior effective window atomically, retain immutable
  history and audit evidence, and preserve an existing later schedule.
- Makes archived accounts ineligible, prevents selecting an already effective
  version, reports typed assignment errors inline, refreshes all rate/account
  status queries, and confirms the account that changed.
- Adds backend and frontend regression coverage plus regenerated OpenAPI. No
  migration, service, database, secret, dataset, capability, network, or host
  port is added.

## Per-draft bill-import history controls

- Adds an individual **Clear** action to every entry under **Prior imports** in
  the Custom Plan bill importer.
- Hides only the selected history row while preserving linked rate and
  billing-cycle drafts, imported usage, source evidence, and immutable audit
  records.
- Restores a cleared row when the same PDF is uploaded again, avoiding duplicate
  extraction and OCR work.
- Adds revision-safe, administrator-only, CSRF-protected API handling and
  append-only Alembic revision `20260724_0015`.

## One-click reviewed bill import

- Adds **Apply all reviewed values** to the final Custom Plan bill-import step.
  One action performs fresh server rate-engine validation and copies nonblank
  reviewed metadata, complete tariff rules, and available sanitized evidence
  into the unsaved editor draft.
- Preserves current values whenever extraction is blank, keeps billing-cycle
  application separate, and never publishes, activates, or assigns a plan.
- Moves keep/import/manual controls under **Advanced field selection** and
  prevents that path from reporting success when no value was selected.

## Strict SCE bill parser and safe rate-plan removal

- Replaces broad whole-document extraction for recognized SCE residential
  bills with versioned `sce_residential_bill_v1` page/section classification,
  a strict allowlist, anchored charge-row grammar, per-field evidence, explicit
  null reasons, bounded local OCR, and a normative `sce_bill_v1` schema.
- Keeps payment/contact/definition/regulatory/informational content out of
  tariff drafts. Rounded `$0.30/$0.40` chart values are display-only; exact
  detailed rates, line amounts, usage, subtotal, tax, and total are reconciled
  with `Decimal`.
- Extends parser version `1.1.0` to SCE single charge-detail-page exports whose
  logo is image-only. Recognition still requires the official domain, exact
  generation-provider marker, and complete anchored section; numeric dates and
  split usage/baseline text regions are supported without accepting unrelated
  documents.
- Adds one canonical dependency-aware Remove action, permanent deletion only
  for truly unused custom drafts, soft removal for published/historical custom
  plans, local retirement for official plans, a Removed / Retired view, and
  explicit restore without reassignment.
- Blocks removal while active/future assignments or account pointers remain,
  and preserves versions, historical assignments, costs, reports, bill
  imports, source evidence, candidates, and audit history.
- Adds append-only Alembic revision `20260724_0014`, `rates.remove` and
  `rates.restore`, strict parser/lifecycle tests, updated generated OpenAPI,
  TrueNAS upgrade guidance, and no new service, dataset, secret, capability,
  network, mount, or host port.

## PDF import account-context stabilization

- Permanently fixes the Custom Plan PDF-import crash caused by interpreting the
  legacy flat utility-account response as a management response and
  dereferencing its absent `rate_context.current_plan` parent.
- Adds the explicit-null, runtime-validated
  `utility-account-rate-context/1.0` contract across Pydantic, OpenAPI, JSON
  Schema, generated TypeScript, fixtures, and contract drift checks.
- Supports no account, account without plan, account with plan, new/existing/
  cloned drafts, direct refresh, legacy redirect, loading, previous extraction,
  Retry, and malformed/incompatible response states without blank output.
- Allows private tariff extraction before account assignment while preserving
  separate plan and billing-cycle drafts, administrator review, selective field
  application, PDF/OCR security, immutable published versions, and explicit
  publish/assign/apply actions.
- Adds layered correlation-ID error boundaries and release/schema compatibility
  diagnostics. TrueNAS upgrades must replace every related immutable image from
  one release.
- Adds append-only Alembic revision `20260724_0013`. It introduces no service,
  database, dataset, secret, capability, network, or host port.

## Modern six-workspace UI and physical-site lifecycle update

- Replaces the overloaded page sidebar with exactly six role-aware workspaces:
  Overview, Monitoring, Analytics, Billing, Alerts, and Administration. Child
  pages use one horizontal workspace tab bar, mobile/collapsed navigation stays
  accessible, and legacy routes retain safe replace-style redirects.
- Adds a canonical action registry with owner workspace, permission, approved
  surfaces, route, audit identity, and runtime/test duplicate detection. Bill
  import is no longer a standalone Billing tab: the Custom Plan editor owns rate
  extraction while utility-account detail owns statement import.
- Adds complete Physical Sites management under **Administration > Sites &
  Network**: six-step creation, detail/edit, default selection,
  disable/enable, dependency-aware soft removal, restore-to-disabled, scoped
  audit history, and selector invalidation/fallback.
- Preserves readings and historical cost/bill evidence. Sensor and
  utility-account transfers are effective-dated; archives and ended user access
  are explicit. Last-active and default sites cannot be removed, stale
  revisions fail closed, and restoration never silently reactivates access.
- Adds append-only Alembic revision `20260724_0012`, granular site permissions,
  lifecycle metadata, effective-dated assignment tables, and a system status
  layout revision mapping older placements to six semantic zones. No service,
  database, secret, dataset, capability, or host port is added.
- Final verification passed 122 portable Python tests plus separate PostgreSQL
  17, 100-device/18,000-reading, and seven-service TrueNAS gates; 39 frontend
  unit/component and 38 Chromium E2E scenarios; production builds for all
  three application images; migration/contract/static Compose checks; a
  five-artifact backup and clean restore; and dependency audits with no known
  vulnerabilities.

## Bill-import integration and user-administration cleanup

- Moves the complete utility-bill workflow into **Billing > Rate Plans > Custom Plan** while
  reusing the existing upload, extraction/OCR, evidence, rate-draft,
  billing-cycle, validation, and retention services.
- Adds field-level keep/import/manual decisions so in-progress custom-plan
  values remain intact. Applying extracted tariff values updates only the
  editor draft; billing-cycle data remains a separate explicit import and no
  bill upload publishes or assigns a plan.
- Redirects `/rates/import-bill` into the editor with the importer open and
  adds a route error boundary, visible retry state, and explicit importer empty
  states. This fixes the previous blank result when a rejected lazy editor
  chunk escaped the route tree.
- Removes the redundant Administration-local **Users & roles** interface and
  redirects its legacy routes to the main-sidebar **Users & Access** workspace.
  Add-user, custom-role, access, site-scope, session, and audit functionality
  remain available in the canonical page.
- Separates reversible Disable from audited soft Remove. Remove revokes
  sessions, clears active role/site assignments, reserves the identity, and
  preserves audit/authorship/ownership evidence. Restore returns the identity
  disabled and unassigned. Self-removal, protected bootstrap accounts, and the
  final recovery-capable Administrator are server-protected.
- Adds append-only Alembic revision `20260724_0011` and the granular
  `users.disable`, `users.remove`, and `users.restore` permissions. No service,
  database, secret, dataset, capability, or host port was added.

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
  Utility Accounts, Usage, Costs, and History. Current tier/price remains
  unavailable rather than showing a false zero when complete account context is
  missing.
- Adds append-only Alembic revision `20260723_0009`, the shared deterministic
  tier fixture, managed-source candidate review, custom flat/tiered/hybrid
  editing, updated API/schema contracts, and the complete TrueNAS workflow gate.

## Utility account and sensor network policy update

- Adds the complete utility-account lifecycle inside **Billing > Utility
  Accounts**: seven-step creation, multiple accounts per site, effective-dated
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
- Adds the integrated `/administration/access` workspace with user filters,
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

Database migration: `20260724_0011`. Images: `power-monitor-api:1.0.0`, `power-monitor-frontend:1.0.0`, and `power-monitor-backup:1.0.0`.

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

Final bill-integration/user-lifecycle verification on 2026-07-24 passed 118
portable Python tests, the separate PostgreSQL 17 migration gate, and the
100-device/18,000-reading resilience test. The frontend passed 32
unit/component tests, all 37 Chromium scenarios, TypeScript, lint, and the
production Node 24 image build. Fresh API, frontend, and backup images passed
their production builds; Python and npm audits found no known vulnerabilities.

The final digest-pinned seven-service TrueNAS-style gate completed migration
`20260724_0011`, reported every service healthy, published only TCP 8443,
enrolled 3 signed devices, accepted 90 readings, resolved an SCE calculation,
persisted 2 utility accounts and 1 canonical network CIDR, verified all 5
encrypted backup artifacts, and restored a clean 96-table database. The
standard Compose stack also migrated its populated database to the new revision
and passed a separate logical backup/clean-restore verification.
