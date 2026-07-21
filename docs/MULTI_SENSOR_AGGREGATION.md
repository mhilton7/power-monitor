# Multi-sensor aggregation and topology safety

Combined History aligns each selected sensor to common UTC buckets on the
server. A bucket records expected sensors, actual contributors, unioned valid
duration, missing sensor IDs, quality flags, and coverage percentage.

Aggregation semantics are:

- active power: sum each valid sensor's time-weighted average power;
- interval energy: sum validated interval deltas, never lifetime counters;
- energy cost: apply each historically effective rate to exact energy segments,
  then sum cost;
- voltage: never sum; expose minimum, duration-weighted average, and maximum;
- current: do not combine multiple sensors without phase/electrical-context
  metadata, so the combined value is unavailable and individuals remain visible;
- power factor: weight valid factors by active-power contribution;
- frequency: duration-weight simultaneous valid readings; never sum;
- peak power: combine aligned sensor interval peaks when available and disclose
  partial coverage.

Normalized energy already handles reboot, counter reset, rollover, device/server
variance, and backfill deduplication. History consumes that selected interval
energy and never adds cumulative PZEM lifetime registers.

Parent/child circuits and multiple meters on the same circuit conflict in a
combined ad hoc selection. The API rejects them with
`history_topology_overlap`. Site totals include only sensors with
`include_in_default_site_total`; when that explicit set contains a known
overlap, the child/duplicate is excluded and returned in scope provenance.
Saved aggregate allocation percentages are honored. Individual mode may compare
overlapping sensors but does not present a combined energy/cost summary.

Incomplete topology is disclosed because the server cannot prove that an
unassigned sensor is non-overlapping. Configure circuit relationships before
treating a result as a physical total.
