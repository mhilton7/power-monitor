# PDF, rate-plan lifecycle, and dropdown repair root cause

Date: 2026-07-25

## Deterministic trace

The reproduction uses
`backend/tests/fixtures/bills/sanitized-sce-domestic-bill.pdf`.

| Stage | Observed result |
| --- | --- |
| Raw PDF | 4,264 bytes, six unencrypted Letter pages |
| SHA-256 | `cb11e622593d76ebf089fb690a5666e28a1dad0cfce0759684f72e9b46898ce0` |
| Text lengths | page 1: 316, page 2: 237, page 3: 696, page 4: 34, page 5: 179, page 6: 130 characters |
| Extraction | Embedded text; OCR is not selected |
| Parser | `sce_residential_bill_v1` version `1.1.0` |
| Document | Southern California Edison residential electric bill |
| Page classes | account/usage summary, generic information, new-charge details, separator, regulatory notice, other |
| Authoritative page | Page 3, with all 11 required charge-detail anchors |
| Recognized facts | DOMESTIC, Jun 22–Jul 21 2026, 30 days, 951 kWh, 579.0 kWh baseline, $353.86 subtotal, $0.29 tax, $354.15 total |
| Validation | Exact Decimal line, usage, subtotal, and total reconciliation passes |
| Ignored content | payments/balances, definitions, notices, informational breakdowns, and rounded explanatory chart rates |
| Durable server records | private PDF artifact, extraction result, extraction revision, extracted-field evidence, rate-plan draft, and billing-cycle draft |

The strict adapter and arithmetic tests passed before changes, proving that
the source PDF, text extraction, page classification, strict SCE selection,
and Decimal parser were not the regression.

## Exact PDF Review regression

The regression is a frontend/backend response mismatch plus an unsafe review
write:

1. `import_payload` returns the canonical draft under `cycle_draft`.
2. `adaptBillDetail` reads `billing_cycle`, an obsolete envelope key.
3. The Review header therefore loses dates, usage, and total even though the
   server returned them.
4. The adapter flattens every extracted field, including deliberate optional
   nulls, into the same primary card list.
5. `BillImportFlow` displays those nulls as `Unknown` and submits a `confirm`
   decision for every field.
6. `review_import` then changes every non-rejected field to
   `administrator_confirmed`, including fields whose normalized value is
   null.

This explains both supplied symptoms: recognized bill summary data appears
unavailable, and missing values acquire an impossible administrator-confirmed
confidence label. The canonical repair is to expose a versioned normalized
artifact in the API, adapt that artifact explicitly, group optional missing
fields, submit decisions only for present values, and reject confirmation of
null values on the server.

## Artifact metadata gap

The uploaded filename was discarded before the service layer. The artifact
received a generated hash-based name, so Review could not show even a
sanitized form of the uploaded filename. The normalized content was split
across revision columns and nested extraction metadata rather than being
stored as one queryable `NormalizedUtilityBill` document. The repair retains
the existing records while adding a versioned, linked normalized artifact and
a sanitized display filename.

## Dashboard data path

Billing-cycle application already creates the canonical `BillingCycle` and
usage-evidence records, and rate application already creates an immutable
rate version plus an effective-dated assignment. Billing and Home query those
canonical account/cycle/rate records. The repair therefore preserves those
services and fixes query invalidation and Review-to-apply sequencing; it does
not make dashboards read raw PDF fields or candidate rows.

## Rate-plan lifecycle gap

The server already protects draft deletion, soft removal, managed-plan
retirement, and restoration. It also reports assignment, calculation,
evidence, bill, and candidate dependencies. The Single Home `More` menu,
however, exposes only Replace, Versions, Evidence, and a direct Retire
operation. There is no explicit, audited unassignment endpoint, no typed
dependency token covering concurrent dependency changes, and no complete
confirmation dialog in the canonical Billing workspace.

The repair adds explicit assignment unbinding that closes the current
effective-dated assignment without deleting it, clears only the active account
pointer, requires reason/effective time/typed confirmation, and records an
audit event. Plan removal continues to block active or future assignments.

## Dropdown root cause

Billing, sensor, and user menus each own a separate boolean or selected-ID
state and render ad-hoc popovers. None installs shared `pointerdown`,
`focusin`, Escape, route-change, scroll, or resize handling. Consequently:

- outside interaction is never observed;
- multiple ordinary menus can remain open;
- focus is not returned on Escape;
- route and owner teardown do not close the menu;
- destructive actions can begin before the menu has been dismissed.

The repair replaces these implementations with one shared accessible menu
primitive and one global open-menu coordinator.
