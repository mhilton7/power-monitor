# Tiered and hybrid rate plans

Power Monitor supports four pricing models in the same effective-dated rate
library:

- `flat`: one energy price for every interval;
- `time_of_use`: the existing season, weekday/weekend, and local-time schedule;
- `tiered`: billing-cycle cumulative usage selects the energy price; and
- `time_of_use_tiered`: cumulative usage selects a tier and local time selects
  the period price inside that tier.

All prices, thresholds, energy, allocation segments, and charges use exact
decimal values. The server evaluates timestamps in the utility account
timezone, persists UTC boundaries, and allocates readings chronologically.
The browser displays server results and does not recalculate a bill.

## Tier definitions

A plan can contain any number of ordered tiers. Tier IDs are stable evidence
keys; names are editable display text. Tiers must start at zero, be contiguous,
have no overlap, and end with exactly one open-ended tier. A value exactly at
an exclusive upper bound belongs to the next tier. If a reading crosses one or
more thresholds, the engine splits that reading at each exact cumulative-kWh
boundary.

Thresholds use one of these evidence-backed bases:

- `fixed_cycle_kwh`: each finite tier stores an exact cumulative-kWh boundary.
- `daily_baseline_kwh`: the cycle's local calendar-day count is multiplied by
  the applicable daily baseline. Seasonal baselines, region/category evidence,
  and `none`, `floor_kwh`, `ceil_kwh`, or `nearest_kwh` rounding are stored in
  the immutable rate version.

No baseline value is inferred from the screenshot, postal address, or sensor.
February, leap years, 30/31-day months, and utility-supplied cycle dates are
therefore evaluated using the exact persisted cycle.

## Hybrid pricing

`time_of_use_tiered` first splits an interval at every timezone, DST, season,
weekday/weekend, date-override, and TOU boundary. It then splits chronologically
at tier boundaries. The immutable plan identifies one reviewed combination
method:

- `tier_period_matrix`: each tier contains an exact price for every TOU period;
- `tier_base_plus_tou_adder`: a tier base is combined with the period adder; or
- `tou_base_plus_tier_adder`: a period base is combined with the tier adder.

The engine records the tier ID, TOU period, cumulative range, exact energy, unit
price, charge, rate version, and algorithm version for every resulting segment.
This preserves the existing TOU behavior for non-tiered plans.

## Custom plans and previews

Open **Billing > Rate Plans > Custom Plan**, choose **Pricing & tiers**, and select a pricing
model. For tiered plans, select the threshold basis, enter its evidence, and
add/reorder/clone/remove tiers. The editor maintains adjacent boundaries and
keeps the last tier open ended. Hybrid plans additionally require the existing
TOU schedule and the selected combination method.

The validation step rejects duplicate IDs, gaps, overlaps, invalid decimals,
missing matrix prices, schedule gaps, and a closed final tier. A sample billing
cycle preview returns allocation by tier, energy charge, blended energy rate,
and the same whole-account-scope warnings as production calculations.

Active or used rate versions remain immutable. Correct a plan by creating a new
version with an effective date. See [Custom rate plans](CUSTOM_RATE_PLANS.md).

## Managed-source candidates

Approved source artifacts are hashed and archived before parsing. A candidate
can contain flat, TOU, tiered, or hybrid documents and retains tier/baseline
citations in its normalized payload. Candidate comparison includes the pricing
model, tier order and bounds, threshold basis, hybrid method, matrix prices,
effective dates, and source evidence.

A pricing-model change always requires explicit review, even when strict
automatic activation is enabled. Existing active versions are not modified.
See [Rate sources](RATE_SOURCES.md).

## Availability and limitations

Tier progress requires an exact billing cycle, an effective rate assignment,
and reviewed complete-service sensor measurements. Uploaded bill kWh never
initializes the cursor or supplies fallback usage. A partial circuit can be
priced only against a separately confirmed advanced whole-account correction
and cannot advance the account tier itself. If sensor authority or readings
are missing, the server reports unavailable instead of assuming zero usage or
Tier 1.

Displayed costs are estimates, not a utility bill. Taxes, meter accuracy,
provider charges, credits, and utility corrections can differ.
