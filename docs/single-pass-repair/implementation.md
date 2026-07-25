# Single-pass repair implementation

Completed on 2026-07-25 as one frontend-only production change set.

## Track A: canonical utility-bill importer

- Added `ModalLayer`, a body-level React portal with distinct backdrop and
  content stacking layers.
- Added an accessible dialog lifecycle: labelled dialog, deterministic initial
  focus, focus containment, Escape close, trigger-focus restoration, body
  scroll locking with scrollbar compensation, and unconditional teardown.
- Made `Billing?action=upload` the canonical importer state. Opening pushes the
  state into browser history, refresh reopens it safely, Back closes it, and the
  legacy `/bill-import` route redirects to the canonical state.
- Kept upload, extraction, review, apply, and error states inside the existing
  `BillImportFlow`. A failed server step now remains visible and offers
  **Retry this step** against the same selected file.
- Added bounded responsive modal, workflow, drop-zone, review, and footer
  layout contracts. No importer state can leave the backdrop mounted without
  visible dialog content.

The authenticated upload endpoint, MIME/size/encryption checks, local text
extraction, OCR fallback, strict SCE adapter, evidence retention, Decimal
handling, separate plan/cycle drafts, manual review, and private storage were
not changed.

## Track B: complete progressively disclosed rate editor

`Billing -> Advanced Rate Settings -> Custom editor` now uses one structured,
typed editor with ten sections:

1. Plan details
2. Pricing model
3. Effective dates and seasons
4. Usage tiers
5. Time-of-use schedules
6. Charges, credits, taxes, and adjustments
7. Billing-cycle rules
8. Assignment
9. Preview and validation
10. Save and publish

The editor covers flat, billing-cycle tiered, time-of-use, and hybrid
TOU+tiered plans. It supports arbitrary tiers and periods, fixed-cycle and
daily-baseline thresholds, an open final tier, seasons and day/date
applicability, overnight period splitting, delivery/generation components,
fixed/daily charges, credits, taxes, minimum bill, effective adjustment
windows, billing day/rules, explicit cost scope/provider mode, effective-dated
service assignment, server preview and validation, draft save/update,
immutable activation, and assignment.

The surrounding lifecycle surface now supports clone, new editable version,
dependency review, retire, dependency-aware permanent deletion of an unused
draft, soft removal, and restore. Versions and Evidence fetch typed records
instead of deriving placeholder rows. Sources keep owner-only add/check
actions, show readable source and parser labels, and place raw identifiers
inside collapsed technical details. Removed and Adjustments remain functional.

All monetary, rate, threshold, and energy values remain decimal strings in the
browser and are sent unchanged to the server for Decimal validation.

## Track C: shared Home information architecture

Home now uses the same `Page`, `PageHeader`, `StatGrid`, surfaces, notices,
buttons, and responsive tokens as Billing, History, and Settings.

- The compact primary row presents live status, current load, last data,
  current plan, and price/estimate without a giant empty hero.
- The no-sensor state uses a bounded onboarding surface with one primary
  **Connect sensor** action, optional billing setup, a short three-step
  explanation, and a system-ready assurance card.
- The configured state uses a structured live-power surface for watts, today's
  energy/cost, recent peak, heartbeat/device health, and a History path.
- Secondary cards cover the billing snapshot, active rate, device state,
  synchronization, and alerts without duplicating the shell pills.
- Wide content is constrained, tablet summary grids use two columns, and
  mobile cards stack without document overflow.

## Typed adapter boundary

Typed, null-safe adapters now cover:

- `BillImportSession`
- `RatePlanDraft`
- `RatePlanVersion`
- `RatePlanAssignment`
- `RateSource`
- `RateEvidence`
- `RateAdjustment`
- `HomeDashboardSummary`
- `HomeLiveStatus`
- `HomeBillingSnapshot`

Adapters reject malformed parent objects, normalize explicit nulls, retain
evidence references, and preserve Decimal strings so undefined-property
failures do not escape into the UI.

## Protected systems

No backend, migration, database, PDF/OCR, strict SCE parser, rate-engine,
device protocol, or ESP32 firmware file was modified. The production bundle
still contains only the greenfield Single Home routes and no legacy module.
