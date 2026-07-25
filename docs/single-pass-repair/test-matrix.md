# Single-pass repair ordered test matrix

Executed in this order on 2026-07-24 through 2026-07-25.

| Order | Gate | Deterministic coverage | Result |
| ---: | --- | --- | --- |
| 1 | Root-cause baseline | Supplied importer, Sources, and Home screenshots; DOM/CSS/component trace | PASS |
| 2 | Python static quality | Ruff lint/format, mypy on backend/worker/simulator | PASS |
| 3 | Contract validation | OpenAPI, JSON Schema, bill-import types, examples, HMAC vectors | PASS |
| 4 | Frontend lint/types | ESLint and TypeScript project build | PASS |
| 5 | Frontend unit/component | Adapters, Decimal preservation, modal lifecycle, layout primitives, bundle architecture | PASS |
| 6 | Backend regression | Full configured pytest suite | PASS |
| 7 | PostgreSQL migrations | PostgreSQL 17 clean upgrade, old-head upgrade, downgrade/re-upgrade, legacy user schema | PASS |
| 8 | Load/resilience | Explicit 100-device ingestion gate | PASS |
| 9 | Frontend production build | TypeScript + Vite + bundle architecture/CSS/legacy-module inspection | PASS |
| 10 | Full browser regression | Existing Single Home navigation, permissions, states, and visual suite | PASS |
| 11 | Repair browser matrix | Importer, editor, Home, History, collision, overflow, and screenshots at four viewports | PASS |
| 12 | Production-container parity | Same repair matrix against the read-only unprivileged nginx image | PASS |
| 13 | Docker images | API/frontend/backup production Dockerfiles | PASS |
| 14 | Compose validation | Standard Compose config, static hardening, health checks | PASS |
| 15 | TrueNAS validation | Template, optional ICMP overlay, and rendered deployment | PASS |
| 16 | TrueNAS-equivalent workflow | Migration, 3 devices, signed heartbeats/backfill, rates, encrypted backup, clean restore, ports | PASS |

## Browser state matrix

The repair suite captures each state at 3440x1440, 1920x1080, 768x1024, and
390x844:

- Billing simple
- importer upload, review, and recoverable error
- advanced editor details, TOU schedules, and lifecycle
- readable managed Sources
- Home empty, billing-configured/no-live-data, and fully configured
- History no-data and configured

Assertions additionally verify nonzero importer geometry, content z-index
above the backdrop, focus entry/return, Escape, close, Back, refresh, legacy
redirect, body unlock, retry, typed exact-decimal request payloads,
save/validate/publish/assign, retire/remove/restore, source creation, semantic
roles/headings, no horizontal overflow, and no control collisions.

## Rate model matrix

| Model/capability | Unit/adapter | Component/browser | Existing server/engine regression |
| --- | --- | --- | --- |
| Flat | PASS | PASS | PASS |
| Tiered, arbitrary count/open final tier | PASS | PASS | PASS |
| Daily baseline | PASS | PASS | PASS |
| TOU, arbitrary/overnight periods | PASS | PASS | PASS |
| Hybrid TOU+tier matrix | PASS | PASS | PASS |
| Charges/credits/taxes/minimum bill | PASS | PASS | PASS |
| Preview and validation | PASS | PASS | PASS |
| Draft/publish/assign | PASS | PASS | PASS |
| Clone/retire/remove/restore | PASS | PASS | PASS |
| Sources/versions/evidence/adjustments | PASS | PASS | PASS |
