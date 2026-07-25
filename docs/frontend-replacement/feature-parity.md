# Single Home frontend feature parity

This is the production cutover contract for `frontend-next`. Internal sites,
utility accounts, circuits, aggregate sets, and device IDs remain unchanged in
the server model; typed adapters translate them into homeowner language. Every
row below was reviewed against the existing API, permission catalog, and
greenfield production component.

| Legacy capability | Existing server contract | Greenfield location | Verification | Status |
|---|---|---|---|---|
| Session, CSRF, MFA-aware sign in, first owner | Auth session/login/bootstrap/logout APIs | App bootstrap, `SignInPage`, onboarding | adapter/unit tests; backend auth regression | Complete |
| Default site selector | Sites API and session scope | `SingleHomeProvider` default-home resolution | malformed/multiple-home adapter tests; onboarding E2E | Complete |
| Live power, energy today, cost today, peak | Fleet summary and event stream | Home | architecture tests; Home loading/error/empty E2E | Complete |
| Current flat, TOU, tiered, or hybrid rate | Utility account tier status | Home and Billing | typed adapters; backend rate regression | Complete |
| Billing-cycle usage and projection | Billing-cycle and tier-status APIs | Home and Billing | Decimal adapter tests; backend cost regression | Complete |
| Daily trend | History query | Home daily chart | route-boundary E2E; backend history regression | Complete |
| Usage and cost history | History query | History | Decimal adapter tests; backend exact-cost regression | Complete |
| Whole-home and individual-sensor aggregation | Devices and history query | History scope and display controls | adapter tests; backend aggregation regression | Complete |
| Double-count protection, gaps, coverage, provenance | Server history result | History warning, chart, summary, table | backend aggregation regression | Complete |
| History CSV export | History export | History `Export` | canonical route/action review | Complete |
| Active alerts, acknowledge, silence | Alert APIs | Header drawer and Home actionable alerts | backend alert regression | Complete |
| SMTP and notification rules | Notification channels and alert rules | Settings → Notifications | default-rule contract tests; backend notification regression | Complete |
| Sensor list, health, enrollment | Device and enrollment APIs | Settings → Sensors | onboarding and route E2E; backend device regression | Complete |
| Sensor setup, maintenance, credentials, removal | Device config/maintenance/unclaim APIs | Sensor details and guided setup | server authorization regression | Complete |
| Topology and monitored circuit | Circuit and aggregate APIs | Sensor “what it monitors”; Advanced diagnostics | backend topology/SSRF regression | Complete |
| Firmware status and OTA | Firmware release/deployment APIs | Sensor Update; Settings → Advanced | backend firmware regression | Complete |
| Electric service accounts | Utility-account APIs | Billing; Settings → Home | typed adapters; backend account regression | Complete |
| Effective-dated rate assignment | Rate-assignment APIs | Billing current-plan replacement | backend rate-assignment regression | Complete |
| Flat, TOU, tiered, hybrid custom plans | Rate plan/version APIs | Billing; Advanced Rate Settings | backend rate-engine regression | Complete |
| Plan dependency review, retire, remove, restore | Rate lifecycle APIs | Billing Advanced Rates | backend lifecycle regression | Complete |
| Secure utility-bill PDF import | Utility-bill import APIs | Billing five-step import flow | backend PDF/OCR/SCE regression | Complete |
| Strict SCE extraction, OCR, evidence | Existing parser/evidence services | Bill review and evidence summary | deterministic parser fixtures | Complete |
| Past bills and variance | Bill imports and billing cycles | Billing past-bills panel | backend bill regression | Complete |
| Managed rate sources and candidates | Rate-source admin APIs | Billing Advanced Rates | backend source/candidate regression | Complete |
| Family users and custom roles | User, role, permission APIs | Settings → Family Access; Advanced → Permissions | adapter and role-creation E2E; backend authorization/lifecycle regression | Complete |
| Disable, remove, restore, revoke sessions | User lifecycle APIs | Family member actions | backend lifecycle regression | Complete |
| Last-owner/self/bootstrap safeguards | Server lifecycle enforcement | Family action feedback | backend safeguard regression | Complete |
| Home name, timezone, currency | Site APIs | Settings → Home | onboarding E2E; backend site regression | Complete |
| Explicit ingress and pull network policy | Network policy APIs | Settings → Advanced → Network | backend network/SSRF regression | Complete |
| Editable interface text | Interface-text revisions | Settings → Advanced | backend interface-text regression | Complete |
| Status visibility and layout | Status-layout revisions | Appearance basics; Advanced layout | backend status-layout regression | Complete |
| Theme, density, accent, optional Home sections | Browser preferences | Settings → Appearance | desktop/light/tablet/mobile visual E2E | Complete |
| Logical backup, verification, restore preflight | Backup inventory/request APIs and UID 10003 scheduler | Settings → Data & Backups | request tests; full backup/restore Compose gate | Complete |
| Usage export and 90-day log export | Export/log APIs | Settings → Data & Backups | backend export/log regression | Complete |
| System health | Health/system APIs | Settings → Advanced → System Health | standard and TrueNAS container health gates | Complete |
| Audit history and permissions | Audit/role APIs | Settings → Advanced | backend audit/RBAC regression | Complete |
| API and protocol evidence | Compatibility/system APIs | Settings → Advanced | protocol contract validation | Complete |
| Legacy URL compatibility | Client redirects | Four canonical destinations | 32-case responsive route E2E | Complete |
| Exactly four production destinations | Not applicable | Home, History, Billing, Settings | architecture unit test | Complete |
| No legacy production modules | Not applicable | `frontend-next` image only | production-bundle audit and Docker build | Complete |

## Cutover evidence

- `frontend-next`: lint, type check, 10 unit/adapter tests, production build, and
  36 Playwright checks across desktop, light desktop, tablet, and mobile.
- The production bundle contains 11 chunks, 534,990 uncompressed JavaScript
  bytes, and no legacy route or page module token.
- The retained legacy frontend still passes 64 unit/component tests, its build,
  and 51 Chromium regression scenarios, but is not copied into the production
  image.
- Backend/worker/simulator regression: 178 passed; PostgreSQL migration and
  100-device gates passed separately.
- The isolated seven-service TrueNAS workflow completed migration-first
  startup, three-device signed ingestion (90 readings), SCE calculation,
  encrypted five-artifact backup verification, clean restore, and port checks.
- `git diff --name-only -- firmware` is empty.
