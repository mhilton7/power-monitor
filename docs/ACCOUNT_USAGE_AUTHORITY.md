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

The existing `main` measurement role means one meter or CT arrangement that
measures the complete electric service. The `service-leg` role means one
complete incoming service conductor and is eligible only as one member of a
reviewed, non-overlapping pair in the same split-phase group. `branch`,
`submeter`, and `informational` sensors are never complete-service billing
sources.

Changing a branch or submeter to `main` or `service-leg` is a physical-topology
repair, not a billing preference. **Settings > Sensors > Manage assignment**
shows the current device/circuit roles and requires physical-boundary and
billing-effect acknowledgements plus the displayed exact confirmation phrase.
The repair keeps the Device UUID and historical readings unchanged and records
an audit event.

For read-only database diagnosis, run:

```console
python tools/diagnose_usage_authority.py --account <utility-account-uuid>
```

The command uses the same eligibility service as the API and rolls back its
session without changing authority, sensors, readings, or billing history.

See [Utility accounts](UTILITY_ACCOUNTS.md), [Usage imports](USAGE_IMPORTS.md),
and [Billing cycles](BILLING_CYCLES.md).
