# History exports

Users with `history.export` can select **Export CSV** on History. The server
re-runs the same authorized, topology-checked, rate-version-aware query and
records a `history.exported` audit event. Export requests use the same maximum
range, sensor, source-row, and bucket limits as the chart.

The CSV begins with scope type/name, site, timezone, display mode, bucket size,
included sensor IDs/names, and topology exclusions. Each combined or individual
row contains UTC and local boundaries, UTC offset, electrical values, TOU period,
rate, estimated energy cost, plan/version IDs, mixed-rate state, contributing
sensor counts, coverage, missing sensor IDs, quality flags, and serialized rate
contributions. This is sufficient to reproduce how a displayed total was formed.

CSV cells derived from names are protected against spreadsheet formula
injection. The export contains no device credentials, API addresses, signing
material, browser tokens, or rate-source secrets.
