# Usage and costs

The **Usage** and **Costs** routes are account views. They use the selected
site's utility account and the server's persisted cycle calculation.

## Usage

Usage shows:

- exact cycle start/end and local day count;
- authoritative usage to date and interval coverage;
- current tier and kWh remaining to the next threshold;
- chronological usage by tier;
- projected cycle usage, projected final tier, and confidence;
- threshold/baseline provenance; and
- current TOU/tier energy context and blended cycle rate.

An unavailable state identifies the missing rate assignment, cycle, or
whole-account authority. It never displays invented zero usage.

## Costs

Costs shows exact energy charges by tier, blended energy rate, adjustment and
scope components, rate/version and recalculation provenance, and optional
utility-bill comparison/reconciliation. Fixed charges and account-only
components are excluded unless complete-account cost scope is explicitly
configured.

## Other product surfaces

- **Overview** uses the same server values for current plan, current period,
  current price, billing-cycle energy, and cycle estimate.
- **Rates** displays flat/TOU/tiered/hybrid model badges and opens the shared
  custom editor and preview.
- **Sites & accounts** configures the effective assignment, cost scope,
  billing day, usage authority, and imports.
- **History** keeps single-sensor and multi-sensor electrical behavior. For
  tiered plans it uses persisted chronological allocation context rather than
  restarting tier usage for each chart bucket. Partial circuit history needs
  independent whole-account tier context.
- Exports retain cycle, tier, TOU, rate-version, authority, and calculation
  provenance.

All cost labels remain **Estimate, not utility bill**.
