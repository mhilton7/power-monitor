# Utility-bill PDF imports

Administrators can open **Rates > Import from utility bill**, or choose
**Upload current bill** on an account under **Administration > Sites &
accounts**. The workflow processes a password-free PDF on the Power Monitor
server and creates two linked drafts:

1. a reusable custom rate-plan draft; and
2. a bill-specific billing-cycle draft for the selected utility account.

Nothing is activated by upload. Administrator review, rate-engine validation,
explicit publication, and explicit account assignment remain separate actions.
A single bill often omits tariff rules, so it must not be treated as proof that
a rate definition is complete.

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
hybrid TOU+tiered pricing. Complete tariff coverage and existing rate-engine
validation are required before publication.

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
- **Plan and bill totals differ:** compare the energy-subtotal difference first.
  A complete utility bill commonly contains non-energy components.
- **Permission denied on TrueNAS:** ensure UID 10001 can modify the
  `rate-source-artifacts` dataset and UID 10003 can read it for backup.

The upload job is also visible through the existing
`GET /api/v1/jobs/{job_id}` endpoint. Re-uploading the same artifact for the
same account returns the existing import and does not repeat OCR.
