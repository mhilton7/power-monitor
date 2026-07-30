# Historical cost calculation

History reuses the production `RateEngine`; it does not contain a second rate
calculator and the browser never determines authoritative cost.

For each normalized reading interval, the server resolves the sensor's utility
account, finds the `rate_assignments` row effective at that historical UTC
instant, loads the immutable rate version, and evaluates it in the rate/account
timezone. Reading energy is split proportionally by real elapsed seconds at
display-bucket, assignment, rate-version, provider-adjustment, local midnight,
TOU, season, weekday/weekend/date override, and UTC-offset boundaries. Rate
engine DST handling preserves both fall-back folds and creates no fictional
spring-forward hour.

All energy, rates, and cost use Python `Decimal` and PostgreSQL `NUMERIC` values.
API monetary values are decimal strings. A display bucket crossing a tariff
boundary retains each underlying contribution and reports a blended rate plus
all TOU periods. Selections using different plans or versions are calculated
per account and labeled **Mixed rates**.

History always uses `energy_only` cost scope. It does not duplicate daily
service charges, baseline credits, account taxes, or other whole-account
components for a coil, circuit, or arbitrary sensor group. The interface labels
the result **Estimated energy cost**. If any energy segment lacks a historically
effective assignment, that bucket's cost is unavailable—not `$0.00`—while its
electrical measurements remain visible.

Rate source websites are never contacted by a History request. SCE synchronized,
manually approved SCE, and custom plans all enter History through the same
immutable rate-version document and engine path.

For tiered and hybrid plans, History uses the account cycle's persisted
chronological allocation/recalculation version. Chart buckets do not restart
at Tier 1. A complete-account aggregate advances tier usage once; a partial
circuit can be priced only against independent whole-account cumulative
context. Missing context makes cost unavailable while electrical readings
remain visible.

## Live measurements versus durable History

A signed heartbeat may carry a fresh electrical measurement for low-latency
Home display, but it is not a historical record. History begins only after the
sensor first commits the sequenced sample to microSD and then receives an
authenticated acknowledgement for the corresponding
`/api/v1/device-readings/batch` upload.

The durable path is:

`PZEM sample -> microSD journal -> signed reading batch -> immutable raw_readings
-> normalized_readings -> bounded History query`.

Network or heartbeat failure must grow the sensor backlog rather than discard
samples. The server accepts immutable readings once per `(device_id, sequence)`;
duplicates advance no energy and changed content for an existing sequence is
rejected. A worker reconciliation pass finds raw rows missing normalization so
one malformed row cannot permanently strand later valid readings.

At low load, the PZEM whole-Wh cumulative register can remain unchanged across
several samples. When consecutive measurements are valid, Power Monitor
integrates power over the real UTC elapsed time with `Decimal` precision.
For example, `1 W` for five seconds contributes about
`0.0013888889 Wh`. The next counter advance reconciles that provisional
sub-Wh energy without counting it twice. Missing or invalid readings remain
missing; a measured zero remains zero.

History power and energy remain visible independently of billing readiness.
Missing rate assignment or tier-allocation context makes only the cost
unavailable. Utility-bill usage is supporting whole-account billing context;
it never fabricates sensor History.
