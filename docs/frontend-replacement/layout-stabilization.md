# Frontend layout stabilization

## Root-cause record

This diagnosis was captured on 2026-07-24 before any layout repair was
applied. The reproducible evidence is stored under
`docs/screenshots/frontend-layout/before/`.

The failure is not a missing production asset, an incorrect NGINX fallback,
Tailwind/PostCSS content scanning, CSS Modules, a remote font, or a missing
icon font:

- The application imports all four plain-CSS design-system files from
  `src/app/bootstrap.tsx`.
- Vite emits one hashed stylesheet and NGINX and Caddy serve it as
  `text/css` with HTTP 200.
- Local production preview, the standalone frontend image, and the full
  Compose gateway served the same 20,568-byte stylesheet with SHA-256
  `ddc764304483c12367ede696c66de8fc019c2ca0c7a3453194a960ae9b2e36ee`.
- The same visual failure reproduced in development, production preview,
  the frontend container, and the full Compose stack.

The root cause is an incomplete shared design-system contract. Billing,
History, and Advanced Rate Settings render semantic class names such as
`page-stack`, `billing-top-metrics`, `billing-main-grid`, `service-facts`,
and `subnav`, but no imported stylesheet defined those classes. Runtime
inspection confirmed that every one computed to the browser defaults
(`display: block`, no grid columns, normal gap, and no maximum width).
Consequently:

- metric cards became full-width rows;
- service metadata and actions collapsed into an unspaced inline sequence;
- native tab buttons and list bullets leaked into Advanced Rate Settings;
- History controls and summary cards stacked without responsive structure;
- the page content expanded to the full ultra-wide viewport; and
- generic empty-state minimum heights created excessive blank panels.

The existing tokens and the shell-specific CSS were working, which explains
why navigation, colors, buttons, and card borders remained styled while the
page interiors did not. Legacy frontend styles are not imported by the new
bundle and were not involved.

## Repair strategy

The correction belongs in the greenfield frontend design system:

1. define bounded page, grid, toolbar, metadata, tab, disclosure, list,
   table, chart, and compact-empty-state primitives;
2. use those primitives consistently in Billing, History, and Advanced Rate
   Settings;
3. add responsive behavior at shared breakpoints rather than page-specific
   pixel offsets;
4. verify CSS existence and hashes in production output and show the active
   frontend/CSS identity in Advanced diagnostics; and
5. enforce the viewport/zoom matrix with overflow, overlap, accessibility,
   visual, container, and Compose parity checks.

No backend, rate-engine, PDF/OCR, or firmware compatibility change is
required for this repair.

## Final production evidence

The repaired Compose gateway serves `assets/index-YCsUfrue.css` as
`text/css` with HTTP 200. The emitted asset is 30,804 bytes and has SHA-256
`7ab91b28d5046aa30d4ef5e7d557c84b817520e33df1f7fb9e18ddc004acd8f9`.
It contains the required Billing, metadata, History, Advanced Settings, and
structured-list contracts. The complete asset URL, computed layout styles,
tokens, and document overflow measurement are recorded in
`docs/screenshots/frontend-layout/after/compose-final-runtime-evidence.json`.
