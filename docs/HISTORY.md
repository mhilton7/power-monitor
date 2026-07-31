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

## Time scale and chart presentation

The Single Home chart uses the server-returned bucket, range, and Home timezone.
Five- and fifteen-minute buckets retain `:mm` labels; hourly buckets include date
and hour; daily buckets show month and day. Points use their UTC interval start on
a chronological linear axis, so a long empty portion of a selected range remains
truthful rather than being compressed into equally spaced categories. Missing
intervals break the line, and invalid timestamps are reported instead of being
silently moved to the chart edge. Tick marks are selected from actual bucket
boundaries rather than arbitrary subdivisions. When synchronized readings begin
after the selected range starts, the chart explicitly says when readings become
available and leaves the earlier interval empty.

Tooltips and the accessible table share the same exact interval formatter, for
example `Jul 31, 8:15–8:30 AM`. Midnight crossings include both dates, and a
daylight-saving offset change is appended so repeated local times stay distinct.
Every vertical scale has a visible title. Energy + Cost explicitly identifies the
solid left Energy scale and dashed right Cost scale, so color is not the only cue.

Appearance stores Power, Energy, and Estimated cost line colors locally under
`pm-chart-power-color`, `pm-chart-energy-color`, and `pm-chart-cost-color`.
Each color has a native color picker and an editable `#RRGGBB` field. Only
six-digit hexadecimal colors are accepted; invalid stored values fall back to
`#78DFBF`, `#78DFBF`, and `#C9A7FF`. Fill opacity derives from the selected line
color, reset restores all defaults, and the settings surface warns without
overwriting a valid color when contrast is below 3:1.

## Page information hierarchy

History begins with its page identity, one consolidated **Rate context** item,
and the server-authoritative scope controls. Current plan, TOU period, and unit
price are presented together. The selected result summary is the one canonical
Data coverage placement, and Recent peak appears once in the result cards.
API, database, and worker cards are available only under **Administration >
System Health**.

If a scope contains no sensors or no readings, History shows one result empty
state with an appropriate Devices or Enrollment action. The energy, cost,
blended-rate, peak, chart, and interval-table sections remain suppressed until
valid records exist, so unavailable values are never presented as `$0.00`,
`0 kWh`, or `0%`.
