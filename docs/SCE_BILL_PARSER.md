# Strict SCE residential-bill parser

Power Monitor recognizes supported Southern California Edison residential
electric bills with the versioned `sce_residential_bill_v1` adapter. Its output
schema is `sce_bill_v1`; a bill import is always a review candidate and is
never eligible for automatic publication.

## Why the parser was replaced

The former generic importer searched normalized text across the complete PDF
with broad regular expressions. It had no SCE page classifier, no minimum
section-anchor score, and no distinction between the detailed charge table and
the rounded “Usage by Tier” chart. On the production-shaped six-page layout,
this allowed nearby dates, percentages, notices, and the explanatory
`$0.30/kWh` and `$0.40/kWh` values to compete with tariff fields. Flattened
whole-page text also made informational component disclosures look additive.

The SCE adapter now classifies every page from content/layout signals:

- `account_and_usage_summary`
- `generic_information`
- `new_charge_details`
- `blank_or_separator`
- `regulatory_notice`
- `other`

Only a section with the required charge-detail heading and at least seven known
anchors is authoritative for reusable rate rules. Utility detection requires
the full utility name plus another independent SCE signal; a lone occurrence
of “Edison” is insufficient.

## Allowed output

The normalized adapter result contains only registered bill identity, masked
account/service/meter suffixes, cycle dates, usage/baseline facts, structured
charge rows, printed subtotal/tax/total, and evidenced recurring plan rules.
Each field retains page, region, source text, extraction method, parser rule,
confidence, validation result, review state, and an explicit reason when null.
The strict JSON Schema is
`shared/schemas/sce-bill-extraction-1.0.json`.

Payments/balances, names and addresses, contact/payment instructions, generic
definitions, regulatory notices, proposed rates, informational “charges
include” disclosures, and explanatory charts stay in sanitized raw evidence
or the optional ignored-section inventory. They cannot enter a plan or billing
cycle. The rounded tier chart is explicitly display-only.

## Structured charge rows and validation

Rows must match a complete daily-quantity or kWh-quantity grammar. Section,
season, tier, provider, quantity, exact unit rate, printed amount, and source
geometry remain together. Delivery and generation rows therefore cannot
overwrite one another.

All arithmetic uses Python `Decimal`:

- each exact quantity × rate product is currency-rounded and compared with the
  printed amount;
- each section’s tier usage is compared with total usage;
- non-tax line items are compared with the printed subtotal; and
- subtotal plus tax is compared with printed new charges.

The deterministic fixture produces 951 kWh, a 579.0 kWh baseline allowance,
`$353.86` subtotal, `$0.29` tax, and `$354.15` new charges. Its derived
five-decimal validation rates are `$0.30863/kWh` and `$0.40962/kWh`; the
rounded chart values are not tariff inputs.

An arithmetic difference is retained, surfaced for administrator review, and
blocks publication. It is never silently “corrected.”

## Missing data, OCR, and privacy

Unsupported layouts return `required_section_missing` and null drafts instead
of zero-filled or guessed plans. Missing reusable winter, future, or daily
baseline rules are reported as “Not found on this bill” or “Not applicable”
with the searched area and next administrator action.

The PDF text layer is preferred. Local Poppler/Tesseract OCR is bounded to
candidate pages lacking usable text, cached by artifact hash, and low-confidence
digits require stronger review. The service does not use external OCR or AI.

Original PDFs remain private and are never test fixtures. Tests use the
generated, PII-free `sanitized-sce-domestic-bill.pdf` plus the authoritative
sanitized JSON fixture. Normalized identity is suffix-masked; customer
name/address and complete identifiers are excluded from drafts and logs.
