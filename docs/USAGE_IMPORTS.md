# Usage imports

Usage imports are an advanced correction workflow. They never replace
immutable sensor readings and cannot be created by the normal bill upload.
They are account scoped, permission checked, CSRF protected, hashed after
normalization, and audited.

## Supported import kinds

- `interval`: start/end timestamps and exact interval kWh;
- `daily`: local/offset-aware date or timestamp and daily kWh;
- `cycle_cumulative`: timestamp and cumulative account kWh;
- `cycle_dates`: exact cycle start and exclusive end; and
- `bill_total`: cycle dates and final utility total for comparison.

`cycle_dates` and `bill_total` require
`calculation_role=reference_only`. Interval, daily, and cumulative correction
imports require `calculation_role=advanced_external_correction` and the exact
`ALTER TIER PROGRESSION` confirmation.

Naive input timestamps are interpreted only in the explicitly selected import
timezone and normalized to UTC. Decimal strings are retained exactly.

## Safe workflow

1. In **Billing > Utility Accounts**, choose the utility account and **Import usage**.
2. Select the kind, timezone, source name, and field mapping.
3. Preview. Review normalized rows, content SHA-256, duplicate state, monitored
   data overlaps, internal gaps/overlaps, cycle impacts, and finalized-cycle
   conflicts.
4. Select an explicit conflict policy when an import overlaps monitored data.
5. Commit. The server rejects duplicate normalized content and queues only
   affected unfinalized cycles for recalculation.

Reversing an import is an audited state change; evidence remains retained.
Reversal is blocked if it would rewrite an affected finalized cycle. Exact
cycle-date imports cannot silently replace finalized boundaries.

## Authority and reconciliation

Separately confirmed cumulative and account-complete interval/daily imports
can provide advanced tier context when explicitly configured as authority.
Bill uploads and bill totals cannot become usage authority. Reconciliation
adjustments require a separate reason and do not mutate either the import or
calculated estimate.

Never import credentials, access tokens, full account numbers, or unrelated
personal data. Store only the evidence needed for calculation and review.
