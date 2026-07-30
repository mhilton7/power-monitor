# Billing tariff and usage authority boundary

## Root cause

The standard PDF approval path crossed a domain boundary in
`app.bills.service.approve_cycle_draft`. After publishing the reviewed rate
version it also created:

- a committed `cycle_cumulative` `UtilityUsageImport` containing the bill's
  reported kWh;
- a `ManualAccountUsage` row with that same cumulative value; and
- for account-authoritative uploads, an `AccountUsageAuthority` of
  `manual_cycle_usage`.

`app.rates.tiered.calculate_cycle_tier_status` accepted those records as an
initial cumulative tier cursor and as a fallback when monitored intervals were
absent. Projection started from that cursor. Consequently, a historical bill
could determine current usage, tier, projection, and cost even though no sensor
had measured that energy.

The frontend amplified the defect by making `publish-and-assign` and
`import-billing-cycle` one action labelled “Apply plan and billing cycle”, and
by offering “Reviewed utility bill or manual account usage” as the default
usage authority.

## Enforced boundary

| Data | Calculation role | Standard calculation use |
| --- | --- | --- |
| Plan code, pricing model, tier/TOU rates, thresholds, seasons | `tariff_rule` | Permitted after review |
| Bill dates, reported kWh, tier allocation, subtotal, total, taxes, credits | `reference_only` | Never |
| Reviewed whole-account sensor or non-overlapping full-account aggregate | `sensor_measurements` | Normal usage, tier, projection, and energy cost |
| Separately entered external meter correction | `advanced_external_correction` | Advanced workflow only, with explicit confirmation and provenance |

The server stores the calculation role on extracted fields, bill-cycle drafts,
usage imports, manual usage, and account authority. API validation and the rate
calculation service enforce the role; frontend labels are explanatory rather
than a security boundary.

The compatibility endpoint
`POST /api/v1/admin/utility-bill-imports/{id}/import-billing-cycle` now applies
reviewed cycle dates only. It never creates usage, changes usage authority, or
sets a tier/projection cursor.

## Calculation flow

1. A reviewed bill creates/publishes an immutable rate version.
2. A separate optional action applies cycle start/end dates only.
3. A reviewed sensor authority selects immutable normalized sensor intervals.
4. Intervals advance the tier cursor chronologically from zero for the current
   cycle.
5. Projection is derived from monitored usage, elapsed cycle time, and
   coverage.
6. Bill usage and totals remain available only in reference comparison views.

If no complete sensor source exists, tiered usage and cost are unavailable.
The server does not substitute bill usage and does not assume zero.

## Existing-data repair

Migration `20260730_0021` classifies legacy bill-derived calculation records as
`reference_only`, marks affected unfinalized cycles for recalculation, and
flags finalized cycles for review without rewriting them.

Run:

```text
python tools/reconcile_bill_usage_authority.py --dry-run
python tools/reconcile_bill_usage_authority.py --apply
```

Dry-run is the default. The apply mode supersedes legacy bill-derived manual
usage for unfinalized cycles, reverses bill-derived usage imports, selects an
eligible reviewed sensor authority when possible, records old/new provenance,
and marks the cycle for deterministic recalculation. Finalized cycles remain
unchanged and retain the review flag.
