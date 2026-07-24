# Custom rate plans

Open **Rates > Custom plan** to use the editor: identity and scope, pricing and
tiers, seasons and day schedules, adjustments, then validation and activation.
Incomplete schedules can be saved as drafts; blocking gaps, overlaps, missing
annual coverage, invalid dates, or unsupported decimal values prevent
activation.

Choose flat, time-of-use, billing-cycle tiered, or hybrid TOU+tiered pricing.
Tiered plans can contain any number of contiguous ordered tiers, use fixed
cycle-kWh or daily-baseline thresholds, and require an open-ended final tier.
Hybrid plans combine those tiers with the existing local-time schedule. Preview
shows exact allocation by tier and blended energy rate. See
[Tiered and hybrid rates](TIERED_AND_HYBRID_RATES.md).

Rates and adjustments are JSON decimal strings and are calculated with
`Decimal`. Whole-account fixed charges and baseline credits are disabled for
`energy_only` monitoring. CCA and Direct Access assumptions must be selected
explicitly. Preview output is an estimate, not an SCE bill.

Active or used versions cannot be edited. Choose **Create new version** or
**Clone** instead. JSON import creates an inactive schema-validated draft;
export includes the normalized document and its SHA-256 integrity value, never
credentials. Assignment records pin the exact version and effective period.
