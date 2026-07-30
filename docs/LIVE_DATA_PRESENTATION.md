# Live-data presentation

The server preserves sensor power as `Decimal` through protocol validation,
database storage, latest-measurement selection, topology aggregation, and API
serialization. The frontend performs presentation rounding only:

- watts use up to two useful decimal places and trim trailing zeroes;
- values at or above 1000 W render as kW with up to two decimal places;
- negative zero renders as `0 W`;
- missing and non-finite values render as an em dash, never as zero.

Consequently `0.8 W` remains `0.8 W` in the header, Home power display, Sensor
Health, device views, History tooltips, and accessible History tables.

`latest_measurement_at`, `latest_received_at`, and `latest_heartbeat_at` remain
distinct. The Home **Sensor data last received** counter uses the newest
contributing measurement's server receipt time. Each Sensor Health row uses
that sensor's own receipt time.

One application-wide `SecondClockProvider` updates presentation state every
second. It derives elapsed time from the authoritative timestamp and a
server-time baseline, so browser sleep or throttled callbacks do not accumulate
drift. Visibility and focus changes recalculate immediately. The clock never
fetches, invalidates a query, polls a sensor, or creates an event stream.
Normal data refresh remains one site-scoped SSE stream with 15-second API
polling fallback.

History coverage keeps its exact backend value. The shared percentage
formatter displays at most two decimals, treats tiny noise above 100 as 100%,
and renders materially invalid values as an em dash. This applies to summary
cards, warnings, chart tooltips, accessible tables, and billing confidence.
