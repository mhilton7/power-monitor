# Custom rate plans

Open **Rates > Custom plan** to use the four-step editor: identity and scope,
seasons and day schedules, adjustments, then validation and activation.
Incomplete schedules can be saved as drafts; blocking gaps, overlaps, missing
annual coverage, invalid dates, or unsupported decimal values prevent
activation.

Rates and adjustments are JSON decimal strings and are calculated with
`Decimal`. Whole-account fixed charges and baseline credits are disabled for
`energy_only` monitoring. CCA and Direct Access assumptions must be selected
explicitly. Preview output is an estimate, not an SCE bill.

Active or used versions cannot be edited. Choose **Create new version** or
**Clone** instead. JSON import creates an inactive schema-validated draft;
export includes the normalized document and its SHA-256 integrity value, never
credentials. Assignment records pin the exact version and effective period.
