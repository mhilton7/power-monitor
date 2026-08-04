# Full Project Stability Audit

## Audit identity

- Audit started: 2026-08-03 (America/Los_Angeles)
- Server baseline: `7ad6f4724ad80e3faa4e8043ea1346040af9fb6b`
- Sensor baseline: `b6b800953c664703faf74a21f5b5d1d87bd6dbd6`
- Deployed sensor build identity at baseline: firmware `1.0.15`, source identity
  `5c98b6939764`, built `2026-08-03T22:36:45Z`
- Shared protocol: `pm-protocol/1.0.0`
- Planned server release: `1.0.33` at Alembic head `20260803_0030`
- Audit branches (both repositories):
  `codex/full-stability-history-ota-hardening`

The server worktree already contained modified Playwright reference images and
an untracked `.npm-cache/` directory when this audit began. Those user-owned
files are excluded from this change set unless a later, explicit visual gate
proves that a particular reference image must be updated.

## Reproducible toolchain baseline

| Component | Pinned or observed version |
| --- | --- |
| Python | 3.13.14 |
| Bundled test Node.js | 24.14.0 |
| Frontend package manager | npm 11.4.2 (lockfile contract) |
| PlatformIO Core | 6.1.19 |
| Espressif32 PlatformIO platform | 6.13.0 |
| Arduino-ESP32 framework | 3.20017.241212 (`dcc1105b`) |
| ESP32-S3 toolchain | 8.4.0+2021r2-patch5 |
| PostgreSQL production image | 17.5-bookworm, digest pinned |
| Docker Engine | 29.6.1 |
| Docker Compose | 5.3.0 |

The frontend declares Node.js `>=24 <25`; commands in the acceptance run use
the bundled Node.js 24 runtime rather than the host Node.js 26 installation.

## Tracked-source audit manifest

The audit covers both tracked repositories, generated release inputs, and the
deployed runtime paths that cross their boundary.

The per-file inventory is generated from both repositories' tracked indexes
and is retained at
[`TRACKED_FILE_AUDIT_MANIFEST.csv`](TRACKED_FILE_AUDIT_MANIFEST.csv). It
currently contains 1,310 records (898 server and 412 sensor). A record is not
marked `reviewed-and-validated` until its applicable final subsystem gates have
passed; rerun `python scripts/generate_stability_audit_manifest.py --validated`
only after that acceptance run.

| Area | Audit method | Baseline result |
| --- | --- | --- |
| Firmware scheduling and ownership | Task creation, priority/core, watchdog, queue, mutex and high-memory lease tracing | Measurement remains isolated, but TLS admission can be starved by permanent internal-DRAM reservations. |
| Firmware networking | DNS cache, TLS admission, heartbeat, reading batch, retry and acknowledgement tracing | Wi-Fi remains connected while local TLS admission rejects all server traffic on one sensor. |
| Firmware storage | Immutable sequence, SD queue, reconciliation, retention and backlog tracing | Backlog remains durable and grows while heartbeats are locally deferred. |
| Firmware OTA | Manifest, signature, partition, reboot, stage-ledger and rollback tracing | Automated fault and hardware validation remain acceptance gates. |
| Server ingestion and device state | HMAC, heartbeat, batch, raw-reading and latest-state tracing | No evidence of credential or ingestion bypass; stalled device has no fresh server traffic. |
| History backend | Request, SQL projection, topology, Decimal rate/tier, bucketing and pagination tracing | Correct on the small live case, but performs unrequested work and cannot satisfy required scale matrices. |
| History frontend | Query keys, cancellation, SSE invalidation, adapter and chart render tracing | Duplicate/unbounded refresh paths and avoidable chart rebuild work are present. |
| Server OTA lifecycle | Release, deployment, report, heartbeat, retry, timeout and rollback tracing | Stale reconciliation is request-triggered and several nonterminal stages can remain stuck indefinitely. |
| Deployment | Migration chain, image provenance, digest pins, Compose isolation and health semantics | Final validation must be repeated after migrations and images are rebuilt. |

The detailed file-level evidence is recorded in the linked architecture,
benchmark, OTA and release documents. Generated binaries are audited by
provenance and checksum rather than treated as independent source files.
Source-frozen archives explicitly run Git with `core.autocrlf=false`, and both
release-evidence generation and pre-archive verification read committed source
artifacts with `git cat-file blob <release-commit>:<path>`. This keeps migration,
lockfile, and OTA-source hashes in the same immutable Git-byte domain as the
archive even when a clean Windows checkout contains CRLF-filtered worktree bytes.
The final archive gate caught both independent line-ending defects before any
publication: archive creation had originally allowed checkout conversion, and
the corrected archive then exposed worktree-based evidence hashing.

## Reproduced production symptoms

### Sensor interruption

At the beginning of the audit both sensors answered the allocation-free local
health endpoint and remained associated with Wi-Fi. Outdoor-AC continued to
complete TLS heartbeats. Indoor-AC did not.

A single-sensor, one-second-cadence, ten-minute probe collected 596 successful
responses and zero failures from Indoor-AC. The boot ID did not change.

| Evidence | Baseline value |
| --- | ---: |
| Idle internal free-memory median | 61,524 bytes |
| Idle largest-block median | 32,756 bytes |
| Minimum idle largest block | 29,172 bytes |
| TLS admission requirement | 65,536 bytes total and 32,768 bytes contiguous |
| Local TLS heap rejections during probe | +193 |
| Successful heartbeats during probe | +0 |
| Successful reading batches during probe | +0 |
| Durable backlog | 43 -> 53 |
| Probe responses | 596/596 |
| Reboots | 0 |

This proves the interruption is not a Wi-Fi disconnect or sensor reboot. The
recurring transport owner rejects work before opening TLS because permanent
internal-DRAM reservations leave the healthy idle heap just below both
admission guards. The durable SD queue correctly retains new readings.

### History latency

The authenticated production History page remained in “Calculating history
and exact interval costs” for more than 12.5 seconds. It completed between the
next 15-second observation, yielding a roughly 15–27 second observed load for
the seven-day, whole-home, energy-plus-cost view. The result contained 169
hourly intervals, two contributing sensors, 35.41% coverage, 28.7 kWh and an
estimated $8.85.

Static tracing confirms that the request hydrates full reading ORM objects,
loads rate/tier context, calculates cost, and constructs combined plus every
individual series before applying response pagination. The 250,000 source-row
limit rejects required fine-grained matrices before bucketing:

- 32 sensors x 7 days x 1,440 readings/day = 322,560 rows
- 8 sensors x 30 days x 1,440 readings/day = 345,600 rows
- 1 sensor x 366 days x 1,440 readings/day = 527,040 rows

### OTA lifecycle

Firmware deployment reconciliation existed only in the deployment-list API
handler. The worker did not call it. A deployment could therefore remain
nonterminal until an administrator opened the firmware screen. Scheduled,
offered, canary-wait, reboot, post-boot and awaiting-heartbeat states also
lacked complete independent expiry handling. Retry retained evidence from the
prior attempt, allowing old boot and timing fields to contaminate a new try.

## Root causes and corrective ownership

1. **Sensor uptime:** internal DRAM committed to long-lived task stacks and
   response/checkpoint storage leaves one production unit below unchanged TLS
   safety guards. Repair reclaims the reserve and makes health telemetry use
   the current heap snapshot.
2. **History latency:** the backend lacks an execution plan based on requested
   metric/display mode, repeatedly scans tier segments, and pages only after
   all computation. The frontend adds duplicate initial keys, ignores abort
   signals, and globally invalidates History on every reading event.
3. **OTA terminalization:** lifecycle transition logic is split between API
   reports, heartbeats and a GET-triggered reconciler. Repair centralizes
   transitions and adds bounded worker reconciliation with persisted timing.

### Corrective History evidence

The repaired service plans work from the requested metrics, performs exact
hour/day overlap aggregation in PostgreSQL, and materializes eligible immutable
tier facts with a fixed bucket key before joining them to chart windows. Exact
measurement and pricing aggregates share one exported `REPEATABLE READ` snapshot
and may use one bounded auxiliary pool connection; pool pressure falls back to
the serial exact path. Authenticated API routes first persist the intentional
browser-session `last_seen_at` refresh so production requests, not only direct
benchmarks, enter the repeatable-read path.

The opt-in PostgreSQL 17.5 benchmark seeded 1,071,360 raw readings and 86,400
tier allocation segments. Exact two-sensor tiered energy/cost warm p95 was
0.1643 seconds for seven days and 0.4104 seconds for 30 days. The same-build raw
reference measured 1.2656 and 7.4028 seconds respectively, reductions of 87.01%
and 94.46%, with field-for-field numerical equivalence. The previously rejected
8-sensor/30-day and 1-sensor/366-day matrices complete at 0.7166 and 0.6524
seconds warm p95. Full evidence and execution-plan details are in
[`../benchmarks/HISTORY_PERFORMANCE.md`](../benchmarks/HISTORY_PERFORMANCE.md).

The PostgreSQL compound-load gate simultaneously exercised signed heartbeat,
reading-batch, configuration, OTA manifest/report/download, exact History and
CSV, notifications, tiered Decimal calculation, text PDF extraction, local OCR,
and logical backup. All 72 measured device requests returned HTTP 200; endpoint
p95/max ranged from 0.4797 to 0.6337 seconds. A six-connection, zero-overflow
pool had 0 failures, 0 starved endpoints, 0 final checkouts, and 0.1365-second
checkout-wait p95. The logical dump was checksummed and restored into a new clean
database containing the current Alembic revision, all six devices, and 20,160
readings. See
[`../benchmarks/COMPOUND_SERVER_LOAD.md`](../benchmarks/COMPOUND_SERVER_LOAD.md).

### Automated server and migration evidence

The complete server acceptance matrix passed after the History and OTA repairs:

- Ruff check and format verification passed for `backend`, `worker`, and
  `simulator`;
- mypy passed for 81 checked source files;
- the final clean-release Python suite reported **342 passed and 1 expected
  opt-in skip**;
- 31 focused migration and release tests passed;
- PostgreSQL 17.5 passed legacy/populated upgrade, upgrade to Alembic head
  `20260803_0030`, downgrade to `0002`, re-upgrade, prior-schema upgrade, and
  clean installation, producing 102 tables at head; and
- the PostgreSQL load gate processed 100 devices and 18,000 immutable records
  with a five-connection pool, zero duplicate retry failures, a first backfill
  time of 82.946 seconds, total gate time of 120.83 seconds, and 12.86 MiB peak
  traced Python memory.

Contract, OpenAPI, secret, static Compose, and TrueNAS Compose validators also
passed. The final release run built the API, frontend, and backup images and
validated an isolated seven-service TrueNAS-style deployment at migration head
`20260803_0030`. All long-running services reported healthy, only the gateway
published a host port, three simulated devices contributed 90 readings, and the
workflow retained two utility accounts and one canonical network CIDR. Its
encrypted logical backup passed checksum verification and restored into a clean
database at the current migration head. GHCR publication, immutable registry
digests, and promotion of the rendered production YAML remain gated on the
physical firmware canary.

### Automated frontend evidence

The greenfield frontend was tested using the pinned Node.js 24/npm 11.4.2
toolchain:

- lint, type checking, and production build passed;
- the complete unit/component suite reported **163 passed**;
- the default Playwright matrix reported **265 passed and 62 intentionally
  skipped** across its configured browser and viewport projects;
- the dedicated repair visual/layout matrix reported **36/36 passed**; and
- the production History performance route rendered 720 points using one
  initial paginated query, coalesced 25 reading events into one refresh, kept
  the closed accessible table out of the DOM, used 221 DOM nodes, and recorded
  zero browser Long Tasks.

The Windows release host runs at most two Playwright workers. A four-worker
candidate run exhausted Chromium's local socket buffers
(`net::ERR_NO_BUFFER_SPACE`) while loading a production asset, leaving an empty
application root even though the route and asset were valid. The bounded gate
retains cross-browser parallelism without allowing host resource exhaustion to
masquerade as an application failure.

The repair matrix includes the responsive touch-menu placement correction and
its bounding-box assertions. Updated visual references are evidence only for
the test cases intentionally regenerated by that matrix; pre-existing,
user-owned reference changes remain outside this audit change set.

### Automated sensor and OTA evidence

The sensor repository's complete automated matrix passed without changing the
shared `pm-protocol/1.0.0` identifier:

- the authoritative Python suite reported **102/102 passed**;
- PlatformIO native tests and the Windows checked-iterator/stack-protector
  environment passed;
- four ESP32-S3 firmware environments compiled and linked successfully:
  production release, debug, simulated, and administrator recovery;
- the production release build used 77,232 of 327,680 RAM bytes (23.6%) and
  1,616,329 of 6,291,456 application bytes (25.7%);
- an isolated Linux GCC 12 AddressSanitizer/UndefinedBehaviorSanitizer/leak run
  passed 128 deterministic sequences and 14,080 events with no diagnostics;
- the sensor Web UI passed dependency audit, formatting, 36/36 unit/component
  tests, production build, and 12/12 Chromium/Firefox/WebKit browser tests; and
- the focused server OTA lifecycle and fault-injection suite reported
  **30/30 passed**, including monotonic evidence ordering, idempotency, retry,
  locking, and stale terminalization.

The exact packaged 1.0.16 canary artifact is 1,616,784 bytes with firmware
SHA-256
`8986804382cffdd995ff0f3e11b020e85b52c93d991b911d6ea6f9a3a4b0b0c7`.
Its matching ELF is 36,096,012 bytes with SHA-256
`2982873bc8f22c089181c0edc378ca9ffd46489a825195e650c1b9d35a57d506`.
The PlatformIO application-size figure above is linker capacity evidence, not
the packaged `firmware.bin` file size.

These automated results verify source behavior and host simulations. They do
not prove that a final candidate can self-update, boot, stabilize, preserve
device data, or roll back on either physical sensor. Those exact-artifact
checks remain pending in
[`../ota/HARDWARE_CANARY_RESULTS.md`](../ota/HARDWARE_CANARY_RESULTS.md).

## Acceptance evidence status

This document is updated from measured output after each gate. A missing row
or a `PENDING` value is not a pass.

| Gate | Status | Evidence |
| --- | --- | --- |
| Sensor native tests (baseline) | PASS | PlatformIO native-tests completed before source changes. |
| Sensor release build (baseline) | PASS | 1,588,497-byte application; 25.2% partition use. |
| Ten-minute physical sensor baseline | PASS (reproduction) | 596 samples; 193 rejections; zero heartbeats; no reboot. |
| Focused History correctness | PASS | 17 focused tests; exact raw/coarse equivalence, continuation, pricing mutation, topology and boundary coverage. |
| History performance matrices | PASS | PG17.5: 7d tier/cost p95 0.1643 s; 30d 0.4104 s; 87.01%/94.46% same-build reductions. |
| Compound protocol/server load and clean restore | PASS | 72/72 signed requests HTTP 200; endpoint p95 <= 0.6337 s; pool failures 0; pg_dump restored and queried. |
| Complete backend suite | PASS | Ruff and format PASS; mypy 81 files; final clean-release pytest 342 passed, 1 expected opt-in skip. |
| PostgreSQL migration upgrade/rollback checks | PASS | PG17.5 legacy/populated/prior/clean paths; head 0030; downgrade 0002 and re-upgrade; 102 tables. |
| PostgreSQL 100-device load gate | PASS | 100 devices; 18,000 records; pool 5; 0 duplicate retry failures; 120.83 s total; 12.86 MiB peak traced memory. |
| Complete frontend suite | PASS | 163 unit/component tests; 265 E2E passed and 62 skipped; lint/typecheck/build PASS. |
| Frontend repair and History performance matrix | PASS | 36/36 repair E2E; 720 points; 221 DOM nodes; one coalesced refresh; zero Long Tasks. |
| Complete sensor automated suite | PASS | 102/102 Python; native and checked builds; four ESP32-S3 firmware builds; Linux ASan/UBSan/leak clean; Web UI 36/36 and browser 12/12. |
| OTA lifecycle/fault injection | PASS | 30/30 focused server tests; monotonic ordering, idempotency, retry, locking, and stale terminalization. |
| Container and Compose health | PASS | Final API/frontend/backup image builds and isolated seven-service TrueNAS workflow passed; all long-running services healthy, gateway-only publication, encrypted backup checksum and clean restore at head 0030. |
| One-hour physical canary | PENDING | Exact tested artifact required. |
| Release provenance and hashes | PASS (candidate) | The final clean release gate records the authoritative source commit and archive SHA-256 in `release/versions.json` and `release/checksums.sha256`; exact canary hashes are recorded above. |
| GHCR image digests and promoted TrueNAS YAML | PENDING (physical gate) | Publication and production-YAML promotion are intentionally deferred until the exact 1.0.16 physical canary passes. |
