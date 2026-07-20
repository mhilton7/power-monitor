# Simulator load and resilience result

Date: 2026-07-20. Environment: Windows host, CPython 3.13.14, FastAPI ASGI transport, SQLite portable test database, five-connection pool. The production persistence target remains PostgreSQL 17.5; this result is deliberately not presented as a PostgreSQL throughput benchmark.

Command:

```text
cd backend
RUN_LOAD_TEST=1 ../.venv/Scripts/python.exe -m pytest -q -s -p no:cacheprovider tests/test_load_resilience.py
```

Measured result:

| Measure | Result |
|---|---:|
| Simulated devices | 100 |
| Heartbeat policy | 15 seconds |
| Live-device API availability | 5 seconds |
| Durable record policy | 60 seconds |
| Backfill represented per device | 3 hours |
| Durable records ingested | 18,000 |
| Duplicate records after full retry | 0 |
| Initial backfill wall time | 65.613 seconds |
| Peak traced Python allocation | 9.10 MiB |
| Database pool bound | 5 connections |
| Gate duration | 93.28 seconds |

The load gate submits signed heartbeats and signed 180-record backfill batches through the real server routes for every device, resets client acknowledgements, resubmits every record, and verifies the immutable raw table remains at exactly 18,000 rows. A separate worker-concurrency test injects one 750 ms poll while five peer devices complete without waiting for it, validating global/per-site task isolation.

This supports a tested claim of 100 simulated devices for protocol ingestion and bounded portable resource use. It does not establish a maximum fleet size, WAN behavior, real PostgreSQL throughput, or hardware accuracy.
