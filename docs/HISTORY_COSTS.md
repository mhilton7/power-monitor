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
