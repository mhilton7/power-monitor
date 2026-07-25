# Single-pass repair acceptance

Completed on 2026-07-25. Every mandatory gate is **PASS**.

| # | Acceptance criterion | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | Upload never opens only a backdrop | PASS | Portal content layer is always mounted above backdrop |
| 2 | Importer visible and accessible | PASS | Labelled dialog, nonzero geometry, focus containment |
| 3 | Retry/close/Back/refresh correct | PASS | Four-viewport browser tests |
| 4 | Full advanced editor restored | PASS | Ten structured editor sections |
| 5 | Flat plans configurable | PASS | Typed model/editor/server regression |
| 6 | Tiered plans configurable | PASS | Arbitrary tiers, open final tier, baseline modes |
| 7 | TOU plans configurable | PASS | Arbitrary periods, seasons/day types, overnight split |
| 8 | Hybrid plans configurable | PASS | Tier-period matrix and component rates |
| 9 | Sources/Versions/Evidence/Removed/Adjustments work | PASS | Typed queries, lifecycle and source browser tests |
| 10 | Home layout repaired | PASS | Shared page/grid/surface primitives |
| 11 | Home empty state polished | PASS | Bounded onboarding and billing/support cards |
| 12 | Home configured state useful | PASS | Live energy, status, billing, history, alerts |
| 13 | No giant mostly-empty Home card | PASS | Height and visual snapshots |
| 14 | Desktop space used effectively | PASS | 1920 and 3440 constrained multi-column layouts |
| 15 | Mobile/tablet usable | PASS | 768 and 390 matrices, no overflow/collision |
| 16 | No legacy dashboard restored | PASS | Bundle architecture verifier |
| 17 | No site-management UI returned | PASS | Single Home production routes unchanged |
| 18 | No undefined-property crash | PASS | Null-safe adapters and error states |
| 19 | Visual tests pass | PASS | Required eight states at four viewports |
| 20 | Accessibility tests pass | PASS | Focus, keyboard, dialog/tab/disclosure semantics |
| 21 | Production build passes | PASS | Vite build and architecture verification |
| 22 | Frontend container builds | PASS | Read-only nginx parity run |
| 23 | Docker Compose validates | PASS | Config and static hardening validator |
| 24 | TrueNAS Compose validates | PASS | Template, ICMP overlay, rendered deployment |
| 25 | ESP32 firmware files changed: zero | PASS | Git change audit |
| 26 | PDF/OCR/rate-engine behavior intact | PASS | 179 backend regressions and TrueNAS workflow |

## Change-scope audit

- Frontend files changed: **132** total: 18 greenfield implementation,
  configuration, and test files plus 114 deterministic visual baselines across
  the normal and exact repair viewport projects.
- Backend files changed: **0**.
- Firmware files changed: **0**.
- Database migration files changed: **0**.
- Deployment source files changed by this task: **0**.

The pre-existing local modification to `truenas-power-monitor.yaml` was not
changed or incorporated into this repair.

## Remaining limitations

- Managed source extraction and activation remain server-authoritative and
  still require the existing review policy.
- A reviewed PDF import still creates separate drafts and never auto-publishes
  or auto-assigns a rate plan.
- Registry publication and a live TrueNAS app update require a new immutable
  release version; they were outside this local repair run.
