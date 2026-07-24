# Rate engine

Measurements remain UTC. Pricing converts each instant to the account timezone and splits intervals at every TOU, midnight, season, effective-date, and billing-cycle boundary. Energy is allocated by elapsed UTC seconds, so spring-forward fabricates no missing hour and repeated fall-back times remain distinct by offset.

The same engine supports `flat`, `time_of_use`, billing-cycle `tiered`, and
`time_of_use_tiered` plans. Tiered calculations replay account-authoritative
energy chronologically across the exact persisted billing cycle. A threshold
crossing splits the interval at the exact cumulative-kWh boundary; it does not
restart each reading or display bucket in Tier 1. Fixed thresholds and
daily-baseline thresholds with explicit seasonal evidence and rounding are
supported. See [Tiered and hybrid rates](TIERED_AND_HYBRID_RATES.md).

All quantities and currency use `Decimal`; unrounded component values are preserved and display/report rounding happens last. Each result records algorithm version, input range, rate version, and source. Raw readings remain available for deterministic recalculation.

The SCE seed includes summer/winter and weekday/weekend periods for TOU-D-4-9PM, TOU-D-5-8PM, and TOU-D-PRIME. Bucket identity is preserved even when prices match. Holiday treatment is explicit per version and has no invented default.

`energy_only` applies per-kWh monitored energy and is the one-CT default. `allocated_account` applies explicitly allocated account components. `full_account` is administrator-selected only; it may apply the base service charge once per utility account and limit baseline credit to a configured allocation. CCA/Direct Access replacement, discounts, taxes/surcharges, and manual credits are separate components.

Every cost view/report states: estimate, not utility bill. See [Rate sources](RATE_SOURCES.md).

History queries reuse this engine for every normalized reading segment. The
query layer additionally splits at historical `rate_assignments`, rate-version
effective dates, provider-adjustment dates, and requested display buckets. It
retains per-segment TOU/rate/version provenance and uses `energy_only`, so a
multi-sensor History chart never duplicates whole-account fixed charges. See
[Historical cost calculation](HISTORY_COSTS.md).

For tiered history, the query layer consumes the persisted cycle allocation
version. Partial-circuit readings do not advance whole-account tiers and need
independent manual/imported account context. Backfill marks unfinalized cycles
for full chronological replay; finalized results are immutable.

Utility-bill imports never bypass this engine. A reviewed bill produces a
separate custom rate draft and billing-cycle draft; the rate draft must pass the
same flat, TOU, tiered, or hybrid validation and exact `Decimal` calculation
before an administrator can publish and assign it. The comparison keeps the
utility energy subtotal separate from the complete bill total. See [Utility
bill imports](UTILITY_BILL_IMPORTS.md).
