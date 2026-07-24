# Billing cycles

Reviewed utility-bill PDFs create a separate billing-cycle draft containing
exact dates and utility-reported cumulative usage. Importing that draft creates
or updates the cycle only through the existing cycle/usage-import services; it
does not overwrite immutable monitored readings. Bill-specific credits,
adjustments, and complete-bill totals remain source evidence rather than
recurring rate rules. See [Utility-bill PDF imports](UTILITY_BILL_IMPORTS.md).

Billing-cycle identity is per utility account. Each persisted cycle has exact
UTC `starts_at` and `ends_at` instants derived from local utility dates or an
explicit utility import. The end is exclusive.

## Boundary sources

By default, the account's configured billing day creates local-midnight
boundaries in the account timezone. Days 29-31 use the last valid day in a
shorter month. DST does not fabricate or discard energy because allocation
uses elapsed UTC time while thresholds use the number of local calendar days.

An administrator with `usage_imports.manage` may preview and commit a
`cycle_dates` import when the utility supplies exact dates. The normalized
timezone and content hash are retained. Conflicting or finalized cycles are
never silently overwritten.

## Lifecycle

- `expected`: account billing-day boundaries are in use.
- `confirmed`: exact meter/utility dates have been reviewed.
- `recalculating`: changed readings, dates, authority, or imports require a
  complete chronological replay.
- `expected` or `confirmed` after calculation: allocation segments, tier
  totals, component totals, projection, rate/version provenance, authority
  provenance, and coverage are persisted at a new recalculation version.
- `finalized`: calculated evidence is immutable. Later backfill is recorded for
  review but never rewrites the finalized result.

Recalculation increments a version and replaces only the unfinalized cycle's
derived segments/summaries. Raw readings remain immutable. Rate assignment and
provider-adjustment boundaries are honored during replay.

## Finalization and reconciliation

Finalization requires an available calculation. The final cycle stores the
calculation version, exact source boundaries, rates, usage-authority evidence,
and confidence. A committed `bill_total` import can be compared with the
estimate. Administrators may add explicit reconciliation adjustments with a
reason and evidence reference; they do not mutate the rate or raw readings.

Use the account **Usage** and **Costs** pages to inspect cycle dates, days
remaining, actual and projected usage, current/projected tier, tier charges,
blended energy rate, coverage, calculation version, and utility-bill
comparison.

## Backfill

Late sensor intervals or corrected imports mark every affected unfinalized
cycle for deterministic chronological replay. The worker never prices each
interval as though it begins in Tier 1. Finalized cycles require an explicit
administrative correction workflow.
