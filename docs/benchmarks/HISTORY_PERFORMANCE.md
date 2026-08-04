# History performance evidence

Date: 2026-08-03
Starting revision: `7ad6f4724ad80e3faa4e8043ea1346040af9fb6b`
Measured platform: Docker PostgreSQL 17.5, Windows host, Python service path

## Baseline

The authenticated seven-day, two-sensor, whole-home energy-plus-cost page took
roughly 15-27 seconds in the production observation recorded in
`docs/audits/FULL_PROJECT_STABILITY_AUDIT.md`. The old raw path hydrated complete
ORM rows, performed unrequested rate/tier and individual-series work, and applied
pagination only after the entire response was calculated.

The old 250,000-row guard rejected the required eight-sensor/30-day matrix
(345,600 minute readings), so that baseline produced no 30-day result. This is a
functional baseline rather than a latency number; no percentage is invented for
a request that previously failed.

## Implemented query path

- Requested metrics create an execution plan. Unrequested electrical columns,
  normalized energy, rate context, cost allocation, and individual series are
  not loaded or built.
- Hourly and daily ranges use exact Decimal SQL overlap aggregation over immutable
  raw readings. Quality flags are expanded, distinct, and bounded in SQL rather
  than hydrating every flagged row.
- Exact current billing-cycle tier segments are used. Account-level/manual tier
  segments with a NULL normalized-interval reference deliberately fall back to
  the authoritative raw allocation path so they are neither dropped nor
  multiplied across devices.
- PostgreSQL first materializes the eligible immutable tier facts once, including
  their exact fixed bucket index, and then joins that bounded relation to the
  requested bucket windows. The former window-first plan rescanned the same
  86,400 facts hundreds of times. Fully contained segments reuse their stored
  exact `NUMERIC` energy and charge; only genuine boundary crossings use
  proportional timestamp arithmetic.
- Measurement and pricing aggregates run concurrently on at most one auxiliary
  pool connection per process. Both transactions import the same exported
  PostgreSQL `REPEATABLE READ` snapshot. If the bounded slot or pool connection
  is unavailable the service uses the serial exact path, preserving device-ingest
  headroom and correctness. PostgreSQL JIT is disabled only for these interactive
  transactions because compilation exceeded the bounded aggregate cost.
- Browser authentication refreshes `sessions.last_seen_at` before the route is
  entered. The History query/export routes explicitly commit that authentication
  transaction before creating their `REPEATABLE READ` query snapshot. An ASGI,
  real-cookie/CSRF PostgreSQL regression proves both that `last_seen_at` persists
  and that the pricing connection executes `SET TRANSACTION SNAPSHOT`.
- Page one computes one exact full-range summary snapshot. The response includes
  a ten-minute, session-bound HMAC continuation token containing that exact
  summary, rate provenance, warnings, source choice, and reading snapshot time.
- Continuation pages validate the token and query only their own bucket windows.
  They do not recompute the full-range summary or cost timeline. Missing,
  modified, expired, cross-session, or query-mismatched tokens are rejected.
- The token also binds an exact SHA-256 digest of every derived rate-assignment
  window, normalized tariff/adjustment document, rate-version provenance, device
  account mapping, and overlapping billing-cycle/recalculation revision. The
  immutable tier facts are bound to that revision rather than re-hashed row by
  row on every request. The digest is checked before and after each paged calculation. A rate
  publication, assignment edit, or tier recalculation between pages fails closed
  with HTTP 409 and requires a new page-one query; pages are never mixed across
  pricing revisions.
- All source paths filter `ingested_at` by the snapshot carried across pages, so
  readings arriving between page requests do not duplicate or reorder the result.
- PostgreSQL covering indexes are installed by migration 0029 with concurrent
  create/drop operations. The production SQL itself is captured and passed to
  `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`; the test also proves index capability
  when PostgreSQL's dense-fixture cost model prefers a sequential scan.

## PostgreSQL 17 scale matrix

The fixture contains 1,071,360 immutable one-minute rows. Each case runs once
cold and four times warm. Warm p95 uses nearest-rank selection and is therefore
also the maximum of the four warm samples. All values are end-to-end service
times for `query_history`, including scope resolution, exact aggregation,
summary construction, validation, and serialization.

| Matrix | Source rows | Buckets | Cold | Warm p50 | Warm p95/max | Result |
|---|---:|---:|---:|---:|---:|---|
| 2 sensors x 7 days, 1h | 20,160 | 168 | 0.1702 s | 0.1535 s | 0.2097 s | PASS |
| 2 sensors x 30 days, 1h | 86,400 | 720 | 0.3596 s | 0.3114 s | 0.3637 s | PASS |
| 32 sensors x 7 days, 1h | 322,560 | 168 | 0.6846 s | 0.6401 s | 0.6675 s | PASS |
| 8 sensors x 30 days, 1h | 345,600 | 720 | 0.7349 s | 0.6899 s | 0.7166 s | PASS |
| 1 sensor x 366 days, 1d | 527,040 | 366 | 0.6552 s | 0.6473 s | 0.6524 s | PASS |

The common two-sensor seven-day warm p95 improved from the measured 15-second
lower-bound baseline to 0.2097 seconds: at least **71.5x faster** and **98.6%
lower latency**. Relative to the observed 27-second upper bound it is 128.8x
faster. The required eight-sensor/30-day case improved from a deterministic
250,000-row rejection to a successful 0.7166-second warm p95 response. The
two-sensor/30-day warm p95 is 0.3637 seconds; there was no directly measured old
two-sensor/30-day latency to compare honestly.

## Exact tiered/cost latency and numerical equivalence

The deterministic tier fixture contains 86,400 immutable normalized intervals
and exact allocation segments across two service-leg sensors. The optimized
coarse result is compared field-for-field with the retained raw ORM/Python
strategy before latency is accepted.

| Exact request | Optimized cold | Optimized warm p50 | Optimized warm p95/max | Same-build raw p95 | Reduction | Result |
|---|---:|---:|---:|---:|---:|---|
| 2 sensors x 7 days, energy + tiered cost | 0.1903 s | 0.1553 s | 0.1643 s | 1.2656 s | 87.01% | PASS |
| 2 sensors x 30 days, energy + tiered cost | 0.4246 s | 0.3981 s | 0.4104 s | 7.4028 s | 94.46% | PASS |

Both comparisons are numerically equivalent. Seven days returns exactly
20.160 kWh and $7.056; 30 days returns exactly 86.400 kWh and $24.240. The
warm production budgets are 0.75 seconds and 1.0 second respectively, and the
required reduction versus the retained reference is at least 70%.

## Continuation evidence

The 720-bucket cases return 500 buckets on page one and 220 on page two. The
signed continuation keeps the exact page-one summary and snapshot unchanged.

| Matrix | Full-range source rows | Page-two source rows | Reduction | Page-two p95 |
|---|---:|---:|---:|---:|
| 2 sensors x 30 days | 86,400 | 26,400 | 69.4% | 0.2270 s |
| 8 sensors x 30 days | 345,600 | 105,600 | 69.4% | 0.4112 s |

The focused regression additionally instruments the backend loader: page one is
given both fixture buckets for its exact summary; page two is given exactly one
page bucket. The second response has byte-for-byte equivalent summary, selected
summary, rate-version provenance, and warnings. A request without a token and a
tampered token both fail closed with HTTP 409. Separate mutation regressions
change the effective assignment, normalized rate-version document, and current
billing-cycle recalculation between pages; each continuation is rejected with
`history_continuation_pricing_changed`.

## Production SQL plan

The benchmark captures the exact SQL emitted by `_load_coarse_measurements`, not
a hand-written approximation. On the dense fixture:

- planning time: 1.132 ms;
- execution time: 360.562 ms;
- shared hit blocks: 3,710;
- shared read blocks: 0.

PostgreSQL rationally chose a sequential strategy for the deliberately dense
fixture. The same captured SQL, explained with sequential scans disabled only as
an index-capability assertion, uses `ix_raw_device_time_end`; migration tests
require that index and its concurrent production installation.

## Browser request and render behavior

- The query key contains the same exact start/end/bucket values sent in the body.
- One minute-key clock is the bounded polling fallback; the History route does
  not run a second React Query interval.
- Reading SSE events carry site, accepted interval bounds, and a watermark.
  Only overlapping selected-home History queries refresh; replayed watermarks are
  ignored, burst refreshes coalesce for 750 ms, and in-flight data is retained.
- The chart parses timestamps once per point, memoizes datasets/options, disables
  routine animation, and mounts the native table only after disclosure. Table
  rows are revealed 100 at a time.
- Playwright asserts one logical initial History query before any user
  interaction. A 720-point result uses one page-one POST plus its required
  signed continuation POST (500 + 220 points); it never starts a second
  page-one query.

### Production-build browser acceptance

`frontend-next/e2e/history-performance.spec.ts` warms the built application on
Home, begins a Chromium Long Tasks observer, and then navigates through the real
client router to History. The deterministic API fixture returns 720 exact points
in the production page size (500 + 220). The test enables collector-gated User
Timing entries around the real response JSON parser, runtime adapter, one-pass
timestamp parser, and chart series preparation. Normal sessions do not retain
these diagnostic entries. The collector attaches the complete JSON result to
the Playwright report.

The same run emits 25 eligible reading SSE events. They result in one logical
refresh (one page-one request plus its continuation), proving the 750 ms burst
coalescer. React Query structurally shares the unchanged response, so chart
preparation is not repeated. The closed accessible disclosure contains no table
or rows in the DOM.

| Production preview | Points/pages | DOM nodes | JSON parse total/max | Adapt total/max | Timestamp parse | Chart prep | Long tasks / max | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Desktop Chromium, 1440 x 1000 | 720 (500 + 220) | 221 | 2.3 / 0.8 ms | 0.5 / 0.4 ms | 0.2 ms | 0.3 ms | 0 / 0 ms | PASS |
| Mobile Chromium, 412 x 839 | 720 (500 + 220) | 221 | 1.7 / 0.6 ms | 0.6 / 0.4 ms | 0.2 ms | 0.2 ms | 0 / 0 ms | PASS |

The asserted representative-route main-thread budget is strictly less than
100 ms for every observed Long Task. Zero qualifying Long Task entries were
observed in both warmed runs; this is recorded as a 0 ms maximum rather than an
invented duration. Timing values are diagnostic evidence, not brittle CI wall
clock thresholds.

## Verification commands

```text
RUN_HISTORY_PERFORMANCE=1 python -m pytest backend/tests/test_history_postgres_performance.py -q -s
python -m pytest backend/tests/test_history_cost_aggregation.py -q
npm run test -- --run tests/historyQuery.test.ts tests/adapters.test.ts tests/liveHomeContext.test.tsx tests/energyChart.test.ts tests/energyChartAccessibility.test.tsx
npm run typecheck
npm run lint
npm run build
npx playwright test e2e/history-performance.spec.ts --project=desktop --project=mobile
```

Measured results for this run:

- PostgreSQL benchmark: 1 passed in 97.59 seconds (including retained raw
  numerical references); the authenticated ASGI snapshot regression is part of
  this same opt-in test;
- focused backend History correctness: 17 passed in 8.40 seconds;
- focused frontend History/chart/adapters/SSE: 39 passed;
- production-build History browser performance: 2 passed (desktop and mobile);
- frontend type check: PASS;
- frontend lint: PASS;
- frontend production build and bundle verification: PASS;
- backend History Ruff and mypy: PASS.

The broader frontend acceptance run that contains these focused checks also
passed: 163 unit/component tests, 265 default Playwright tests with 62
intentional project/fixture skips, and 36/36 dedicated repair visual/layout
tests. These counts are recorded separately from the two focused History
performance cases above so they are not double-counted as benchmark samples.

## Runtime diagnostics

Every query records source and aggregate row counts, returned bucket count,
summary bucket count, page detail offset/count, continuation reuse, snapshot
time, quality and cost row counts, response bytes, and phase timings. Device
credentials, signatures, raw payloads, and billing evidence are never logged.
