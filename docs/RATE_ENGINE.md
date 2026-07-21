# Rate engine

Measurements remain UTC. Pricing converts each instant to the account timezone and splits intervals at every TOU, midnight, season, effective-date, and billing-cycle boundary. Energy is allocated by elapsed UTC seconds, so spring-forward fabricates no missing hour and repeated fall-back times remain distinct by offset.

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
