# Sensor storage verification matrix

This matrix separates automated evidence from physical acceptance gates. A PASS
means the stated command completed successfully against the current working
tree. Physical tests remain NOT RUN until a disposable card and supervised
sensor are available; production cards are never used for destructive tests.

| Area | Evidence | Result |
| --- | --- | --- |
| Policy validation and pressure thresholds | native C++ storage-policy tests | PASS |
| Eligibility: active/corrupt/unacknowledged/recent/minimum window | native C++ and Python model | PASS |
| Oldest-first cleanup to percentage/absolute target | Python filesystem model | PASS |
| Full-card reserve and explicit sequence gap | Python filesystem model | PASS |
| Power loss at every cleanup journal stage | Python filesystem model | PASS |
| Corrupt, unknown, missing, and ambiguous journal evidence | Python filesystem model | PASS |
| Seven-day outage/reconnect accelerated simulation | Python filesystem model | PASS |
| Contiguous event acknowledgement and explicit retained boundary | backend integration tests | PASS |
| Existing-policy heartbeat fallback and desired/effective merge | backend integration tests | PASS |
| Migration model contract | backend migration contract tests | PASS |
| PostgreSQL 17 upgrade/downgrade/clean migration | isolated container from revision 0001 and prior schema | PASS |
| Viewer versus manager permissions and storage state rendering | backend permission and frontend component tests | PASS |
| ESP32-S3 release compilation | PlatformIO release environment | PASS |
| Full backend suite | `python -m pytest` | PASS (246 passed, 3 environment-gated skips) |
| Full frontend suite/build | lint, typecheck, 116 tests, build | PASS |
| OpenAPI/contracts | generation and `validate_contracts.py` | PASS |
| API, frontend, and backup container builds | local Docker Desktop production Dockerfiles | PASS |
| Compose migration and service health | PostgreSQL, API, worker, frontend, and Caddy; revision `20260731_0025` | PASS |
| Existing full browser matrix | 290 Playwright cases across seven projects | NOT PASS (156 passed, 48 skipped, 86 unrelated existing mock/visual-baseline failures) |
| Disposable-card threshold/age/emergency/block tests | physical sensor and disposable card | NOT RUN |
| Power interruption during real card cleanup | physical sensor and disposable card | NOT RUN |
| Blank-card replacement without reset/reenrollment | physical sensor and disposable card | NOT RUN |
| Long-duration physical outage/recovery soak | physical sensor | NOT RUN |

The physical and soak rows are mandatory before labeling the feature fully
qualified for unattended production-card cleanup. Host tests establish logic and
recovery behavior but do not substitute for flash-media failure evidence.

The broad browser failures are not storage-control assertion failures. They are
dominated by stale full-page visual snapshots and existing tests that allow
unmocked requests to fall through to a non-running development API at
`127.0.0.1:8000`. Storage-specific adapter and component assertions pass, but the
repository-wide browser gate remains reported as NOT PASS until that independent
harness/baseline work is resolved.
