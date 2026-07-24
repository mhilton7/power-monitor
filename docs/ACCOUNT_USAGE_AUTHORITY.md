# Account usage authority

Tier progression is a whole-utility-account fact. A partial CT, one branch
circuit, or an arbitrary sum of sensors must not advance the account through
tiers. Each utility account therefore has an explicit, audited usage authority.

## Authority types

- **Full-account aggregate**: an aggregate explicitly scoped
  `full_account`, with topology that avoids parent/child double counting.
- **Selected full-account sensors**: active sensors assigned to the account,
  explicitly reviewed as complete.
- **Manual cumulative usage**: a timestamped utility meter/bill value with a
  source note and optional evidence reference.
- **Imported account usage**: interval, daily, cumulative, cycle-date, or bill
  evidence committed through the preview/import workflow.
- **Partial monitored usage**: suitable for electrical history and
  `energy_only` costs, but not authoritative for tier progression.

The authority records completeness, confidence, source reference, selected
devices/aggregate, revision, actor, and timestamp. Optimistic revision checks
prevent an administrator from overwriting a concurrent change.

## Complete and partial views

For a complete account, readings are deduplicated by device and sequence,
normalized, checked against topology, and summed chronologically. Coverage and
gaps remain visible.

For a partial circuit, the server can calculate circuit energy cost only when
an independent whole-account cumulative value identifies the tier context. The
circuit's energy is allocated within that context and does not move the
account's cumulative total. Without that evidence, tiered cost is unavailable.

Fixed charges, account credits, and account taxes apply only to a reviewed
complete-account scope and only once per utility account. One-CT devices remain
`energy_only` by default.

## Administration

Open **Administration > Sites & accounts**, select an account, and configure
**Account usage authority**. For a measured authority, choose only devices or a
full-account aggregate assigned to that account. For manual evidence, record
the cumulative kWh, exact effective timestamp, source note, and idempotency
key. Every mutation is server-authorized, CSRF protected, site scoped, and
audited.

See [Utility accounts](UTILITY_ACCOUNTS.md), [Usage imports](USAGE_IMPORTS.md),
and [Billing cycles](BILLING_CYCLES.md).
