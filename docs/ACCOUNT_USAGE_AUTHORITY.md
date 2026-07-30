# Account usage authority

Tier progression is a whole-utility-account fact. A partial CT, one branch
circuit, or an arbitrary sum of sensors must not advance the account through
tiers. Each utility account therefore has an explicit, audited usage authority.

## Authority types

- **Full-account aggregate**: an aggregate explicitly scoped
  `full_account`, with topology that avoids parent/child double counting.
- **Selected full-account sensors**: active sensors assigned to the account,
  explicitly reviewed as complete.
- **Advanced external correction**: a separately entered timestamped
  cumulative or interval value with provenance and explicit confirmation that
  it changes tier progression. A normal uploaded bill cannot create this.
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
a separately confirmed advanced whole-account correction identifies the tier
context. The
circuit's energy is allocated within that context and does not move the
account's cumulative total. Without that evidence, tiered cost is unavailable.

Fixed charges, account credits, and account taxes apply only to a reviewed
complete-account scope and only once per utility account. One-CT devices remain
`energy_only` by default.

## Administration

Open **Billing > How usage is measured** and select one reviewed whole-home
sensor or exactly two non-overlapping service-leg sensors. This is the normal
default. Advanced corrections and external meter imports are collapsed under
**Advanced** and require the exact amount, timestamp, provenance, and the
`ALTER TIER PROGRESSION` confirmation. Every mutation is server-authorized,
CSRF protected, site scoped, and audited.

See [Utility accounts](UTILITY_ACCOUNTS.md), [Usage imports](USAGE_IMPORTS.md),
and [Billing cycles](BILLING_CYCLES.md).
