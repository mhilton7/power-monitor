# Utility-bill PDF imports

Administrators open **Billing > Rate Plans > Custom Plan** and select **Import from utility
bill** inside the existing custom-plan editor. **Upload current bill** on an
account under **Billing > Utility Accounts** opens the same editor with
the account preselected. The workflow processes a password-free PDF on the
Power Monitor server and produces two separate reviewed outputs:

1. a reusable custom rate-plan draft; and
2. a bill-specific billing-cycle draft for the selected utility account.

The importer is a data-entry assistant, not a separate plan editor. After the
administrator saves the evidence review, **Apply all reviewed values** provides
the normal path: it performs fresh server-side rate validation, copies every
nonblank reviewed field, the complete validated tariff document, and available
source evidence into the unsaved Custom Plan, then returns to the editor. Blank
extracted values preserve the current form value.

**Advanced field selection** retains the field-level keep/import/manual
workflow for exceptions. Its Apply action remains disabled until at least one
field or rule group is selected, so an unchanged draft cannot be reported as a
successful import. Both paths update the current form only. The administrator
then uses the normal calculation preview, draft save, publication, and
assignment workflow.

Nothing is activated by upload or by applying extracted fields. Administrator
review, rate-engine validation, explicit publication, and explicit account
assignment remain separate actions.
A single bill often omits tariff rules, so it must not be treated as proof that
a rate definition is complete.

The previous bookmark `/rates/import-bill` is retained as a replace-style
compatibility redirect to `/rates/new?bill_import=open`. It never mounts a
second importer or editor.

The importer can also start without a selected utility account. Tariff
extraction and review remain available, while account assignment and
billing-cycle application are deferred. The server stores such evidence as a
private, user-scoped source. Selecting an account later explicitly attaches the
context; it does not publish, assign, or apply either draft.

Each row under **Prior imports** has its own **Clear** action. Clearing a row
removes only that import from the visible draft history; it does not delete the
linked rate-plan draft, billing-cycle draft, imported usage, sanitized
evidence, source provenance, or audit events. The action is administrator-only,
CSRF protected, revision checked, and audit logged. Re-uploading the same PDF
for the same account restores the existing row instead of creating a duplicate
or repeating OCR.

Account and rate readiness comes from the versioned
`utility-account-rate-context/1.0` response. Plan, assignment, version, and
period values are always present as explicit objects or `null`. An account with
no plan displays a setup state and prefills a new Custom Plan. An account with a
plan uses it only for comparison. Runtime validation converts malformed
responses into a recoverable application error instead of allowing raw
property access. See
[PDF import context stabilization](PDF_IMPORT_STABILIZATION.md).

## Blank-page correction

The former route lazy-loaded the standalone bill-import page without a
route-level error boundary. If that generated JavaScript chunk was unavailable
after an image upgrade or browser cache mismatch, the rejected import escaped
the route tree and React rendered no recoverable content. The importer itself
was not the cause.

The shared route workspace now has a visible loading fallback and a recoverable
error boundary with a **Retry** action. The old URL redirects into the custom
editor, and every importer state renders a loading, empty, warning, error, or
review surface rather than returning `null`. Browser regression tests
deliberately fail the editor chunk and verify the visible error state.

The later `current_plan` regression had a different root cause: the importer
treated the flat legacy utility-account response as a richer management
response and dereferenced a missing `rate_context` parent. The importer now
uses its dedicated explicit-null contract, typed selectors, a discriminated
state model, bounded Retry, direct-refresh reconstruction, and layered error
boundaries. It does not depend on sensors, readings, or an already assigned
rate.

## Supported documents

The importer accepts PDF content with a maximum size and page count configured
by `UTILITY_BILL_MAX_BYTES` and `UTILITY_BILL_MAX_PAGES`. It supports:

- PDFs with a usable text layer;
- scanned or partly scanned pages through local English OCR;
- multiple pages and rotated pages;
- split tier tables and multiple meter identifiers;
- separate delivery and generation totals.

It rejects non-PDF content, malformed files, encrypted/password-protected PDFs,
active PDF actions, and configured size/page-limit violations. The client
filename is never used as a storage path.

Text-layer extraction runs first. OCR runs only for pages without sufficient
text, uses bounded DPI, page count, memory, and execution time, and caches the
result by artifact hash. Power Monitor invokes `pdftoppm` and Tesseract directly
without a shell. No bill is sent to an external OCR or AI service.

Recognized SCE residential bills are routed to the strict
`sce_residential_bill_v1` adapter instead of the generic parser. The adapter
classifies pages and requires the anchored **Details of your new charges**
section before creating reusable rules. Payments, contact instructions,
definitions, regulatory notices, informational component disclosures, and the
rounded usage-by-tier chart are explicitly non-authoritative. See
[Strict SCE residential-bill parser](SCE_BILL_PARSER.md).

Separately exported SCE charge-detail pages are also supported when the text
layer contains the official `sce.com` domain, exact `SCE` generation label,
and the complete anchored charge section. Numeric billing dates and adjacent
usage/baseline label-value regions are normalized without weakening the
utility classifier. Summary-only data such as account identifiers and bill
preparation dates remains null with a review reason when those pages were not
uploaded.

## Evidence and confidence

Every field records its source artifact, page number, retained excerpt,
coordinates when available, extraction method, parser/OCR version, confidence,
warnings, and normalization history. The administrator can inspect this
evidence before confirming or correcting a field.

Confidence and review states distinguish administrator-confirmed, high, medium,
low, missing, conflicting, and not-applicable values. Required low-confidence
or missing rate fields block publication. A tier boundary such as `579 kWh`
always requires an explicit interpretation:

- fixed billing-cycle threshold;
- derived from a daily baseline;
- derived from a baseline multiplier; or
- unknown.

Power Monitor never silently converts an ambiguous bill threshold into a
recurring tariff rule.

## Rate-plan and cycle outputs

The rate-plan draft reuses the custom-rate editor, immutable rate versions, and
managed-source evidence system. It may represent flat, time-of-use, tiered, or
hybrid TOU+tiered pricing. Applying it updates one cloned editor document so an
asynchronous extraction result cannot partially overwrite the live form. The
one-click path imports reviewed nonblank values; the advanced path defaults
each value to **Keep current** until the administrator explicitly chooses
otherwise. Complete tariff coverage and existing rate-engine validation are
required before publication.

The billing-cycle draft retains exact dates, utility-reported usage, reported
tier/TOU/meter allocations, energy subtotal, complete bill total, components,
and reconciliation state. One-time credits, taxes, unexplained differences,
and adjustments stay bill-specific unless an administrator separately
configures a supported recurring rule.

Importing the reviewed cycle never overwrites immutable sensor readings.
Selecting utility-reported usage as tier authority is explicit and carries the
bill provenance.

## Official-source conflicts

An uploaded bill defaults to a **Supporting source**. It may instead be reviewed
as an authoritative account-specific source or reference-only evidence.
Differences from the account configuration or approved managed sources produce
field-level conflicts. Both sources are retained, and unresolved conflicts
block publication and automatic activation.

## Privacy and retention

Original PDFs and sanitized evidence live below the private
`rate-source-artifacts/utility-bills` dataset. They have no static or public URL.
Only administrators with `utility_bills.view` can read evidence, and
`utility_bills.manage` is required for upload, review, retention, publication,
assignment, cycle import, or deletion. Mutations also require the existing CSRF
proof and are audit logged.

Power Monitor stores a SHA-256 content hash and random server-side path. Logs do
not contain PDF text, account numbers, addresses, names, payment details,
barcodes, tokens, or storage paths. Normalized evidence masks account identity
to a suffix and applies targeted redaction.

Available original-document policies are:

- retain;
- retain until a chosen time; or
- delete after approved extraction.

Deleting the original does not remove the sanitized evidence, field-level
provenance, extraction revision, rate source, review decisions, or audit
history. The worker enforces scheduled retention. The artifact dataset is
included in the existing encrypted logical-backup bundle.

Clearing a row from **Prior imports** is also not data deletion. It records who
cleared that individual row and when, while keeping the private evidence and
all dependent records recoverable through audit and duplicate-file reuse.

## Exact values and readable display

Python `Decimal`, PostgreSQL `NUMERIC`, and exact decimal strings remain
authoritative. The UI uses the shared formatters only at the presentation
boundary:

- currency totals and charges: two decimals;
- configured energy rates: two to five decimals;
- derived blended rates: four decimals;
- energy: up to three decimals;
- structured tier bounds: `0–579 kWh` and `580 kWh and above`.

Expanded details and normalized JSON retain the unrounded values. The
calculated energy subtotal is not the complete utility bill: delivery,
generation, taxes, credits, fees, and unexplained differences remain visibly
separate.

Extracted text is decoded as UTF-8, normalized to Unicode NFC, and only repaired
when a targeted legacy-encoding candidate measurably reduces suspicious
mojibake. Application-generated range labels come from numeric bounds rather
than PDF punctuation.

## Troubleshooting

- **Encrypted PDF rejected:** export a password-free copy locally. Power
  Monitor never stores PDF passwords.
- **OCR unavailable:** confirm the API image contains `pdftoppm`, Tesseract, and
  English language data. The production backend image installs these packages.
- **Low confidence:** inspect the page evidence and correct the exact value.
  Do not confirm a value that the bill does not establish.
- **Preview unavailable:** complete the linked custom-rate draft and resolve
  blocking warnings, then run validation again.
- **Editor failed to load:** select **Retry**. If the deployment was just
  upgraded, reload once after the frontend container is healthy so the browser
  requests the current immutable chunk.
- **Plan and bill totals differ:** compare the energy-subtotal difference first.
  A complete utility bill commonly contains non-energy components.
- **Permission denied on TrueNAS:** ensure UID 10001 can modify the
  `rate-source-artifacts` dataset and UID 10003 can read it for backup.

The upload job is also visible through the existing
`GET /api/v1/jobs/{job_id}` endpoint. Re-uploading the same artifact for the
same account returns the existing import and does not repeat OCR.
