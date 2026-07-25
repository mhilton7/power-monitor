# Single-pass importer, rate editor, and Home repair: root cause

Recorded on 2026-07-24 before implementation changes.

## Track A: blank bill-import overlay

The importer is mounted, but it is painted behind its own backdrop.

`BillingPage` renders this order:

1. a `modal-layer` wrapper;
2. a button with class `modal-backdrop`; and
3. `BillImportFlow`, whose dialog root has class `workflow`.

The imported design system defines `.modal-backdrop` as a fixed, full-viewport
element at `z-index: 100`. It defines neither `.modal-layer` nor `.workflow`.
The button therefore creates the visible blurred layer above the normally
positioned workflow. The workflow has no fixed positioning, stacking level,
bounded dimensions, overflow contract, or production dialog styling. The
supplied screenshot is the exact visual result: the page and mounted importer
are blurred beneath an interactive full-screen button.

This is not a lazy-chunk failure, missing route, missing provider, permission
denial, parser failure, or API response failure. The dialog component is part
of the Billing chunk and its initial upload state does not depend on an API
response. The failure occurs before upload and is completely explained by
stacking and missing shared CSS contracts. The current implementation also
lacks a focus-entry/focus-return lifecycle, Escape handling, focus containment,
body scroll locking, Back-state synchronization, and guaranteed cleanup.

## Track B: incomplete advanced rate editor

The backend rate engine and lifecycle endpoints remain present. The greenfield
`PlanManager`, however, exposes only a compressed subset:

- name and code;
- pricing-model selection;
- one flat rate or two fixed TOU rates;
- a basic arbitrary tier list; and
- create-draft and clone actions.

It does not provide the specified structured sections for plan metadata,
effective dates and seasons, arbitrary schedules, component rates, charges,
credits, taxes, adjustments, cycle rules, assignment, preview/validation, or
the complete publish/assign/retire/remove lifecycle. Versions and Evidence are
currently plan-derived labels rather than typed version/evidence records.
Sources expose raw URLs and parser identifiers without a readable source-type
presentation. This is a frontend feature-parity gap, not a missing rate-engine
capability.

## Track C: sparse Home dashboard

`HomePage` does not use the shared `Page`, `PageHeader`, `StatGrid`, metadata,
or compact-state primitives used by the repaired Billing and History pages.
Its semantic classes (`home-page`, `hero-empty`, `home-hero-grid`,
`power-hero`, `home-summary-column`, `home-secondary-grid`, and related live
status classes) have no design-system declarations. The browser therefore
falls back to block layout with no bounded grid, deliberate panel height, or
responsive information architecture.

The no-sensor branch returns only a heading and one or two generic surfaces.
At desktop widths this produces one wide, weakly structured empty panel
followed by unused page space. The configured branch contains useful values,
but its missing layout contracts prevent the intended primary/secondary
dashboard hierarchy from rendering.

## Protected systems

No backend, PostgreSQL schema, rate calculation, PDF extraction/OCR, strict SCE
adapter, evidence storage, authentication, ESP32 firmware, or device protocol
change is required to correct these three frontend defects.
