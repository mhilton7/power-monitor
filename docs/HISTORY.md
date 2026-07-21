# History and comparison

The existing **History** route supports five authorized scopes: one sensor,
two or more sensors, a circuit, the explicitly configured site total, and a
saved aggregate set. Single sensor remains the default and bookmarked
`/history?device_id=<uuid>` links remain valid.

For scopes with more than one sensor, choose **Combined total**, **Individual
sensors**, or **Combined + individual**. The combined series is calculated by
the server. **Select all eligible sensors** excludes known parent/child or
duplicate-circuit overlaps. An ad hoc overlapping combined selection is
rejected; Individual mode remains available for comparisons.

The metric selector includes active power, energy, voltage, current, power
factor, frequency, **Energy cost**, and **Usage + cost**. Usage + cost uses
separate labeled axes. Cost is an estimate of interval energy charges for the
selected sensors; account-level fixed charges, taxes, and credits are excluded.

Automatic buckets keep responses bounded. Manual raw buckets are limited to
two days, all queries are limited to 366 days and 32 sensors, and tables are
paginated. Select interval checkboxes in the accessible table to create or
extend a contiguous range. The resulting energy, cost, weighted rate, power,
coverage, and TOU breakdown are recalculated by the server rather than summed
as browser-authoritative money.

Tooltips and the table show local start/end, UTC offset, included and
contributing sensor counts, energy, average and peak power, TOU period, rate,
energy cost, rate plan/version, coverage, and quality flags. Repeated fall-DST
times remain distinguishable by offset. Missing sensor data is partial and is
never replaced with zero. Enable **Require complete coverage** to withhold a
combined bucket unless every expected sensor contributed.

See [Historical cost calculation](HISTORY_COSTS.md),
[multi-sensor aggregation](MULTI_SENSOR_AGGREGATION.md), and
[History exports](HISTORY_EXPORTS.md).
