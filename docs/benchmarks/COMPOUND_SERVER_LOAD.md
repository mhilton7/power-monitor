# Compound server load and backup-restore evidence

Date: 2026-08-03
Database: disposable Docker PostgreSQL 17.5
Command: `RUN_LOAD_TEST=1 python -m pytest backend/tests/test_compound_postgres_load.py -q -s`

## Purpose and workload

This opt-in integration gate verifies that normal signed device traffic remains
responsive while the server performs its expensive household workloads. It uses
the production ASGI application, HMAC verification, database models, ingestion,
firmware routes, History service, notification projection, Decimal rate engine,
PDF text extraction, local OCR fallback, and PostgreSQL logical-backup tools.

Six enrolled protocol-valid UUID devices are present. The measured device sends
12 signed requests to each of these endpoints while all background jobs run:

- heartbeat with a real live measurement;
- immutable reading batch with advancing sequence and normalization;
- effective configuration;
- authenticated OTA manifest;
- idempotent OTA evidence report;
- authenticated firmware download.

Concurrent background work contains six exact 2-sensor/7-day History queries and
CSV renders, twelve 100-notification projections, 8,000 exact tiered Decimal rate
calculations, one text-layer PDF extraction, one local-OCR fallback extraction,
and a custom-format `pg_dump`.

## Endpoint latency

Nearest-rank p95 over 12 samples is intentionally conservative and equals the
maximum sample. Every request returned HTTP 200. The acceptance budgets are p95
at most 2 seconds and maximum at most 5 seconds.

| Signed endpoint | p50 | p95 / maximum | Result |
| --- | ---: | ---: | --- |
| Heartbeat | 0.0644 s | 0.5330 s | PASS |
| Reading batch | 0.0553 s | 0.6337 s | PASS |
| Effective configuration | 0.0557 s | 0.4797 s | PASS |
| OTA manifest | 0.0697 s | 0.4981 s | PASS |
| OTA report | 0.0618 s | 0.5269 s | PASS |
| Firmware download | 0.0770 s | 0.4996 s | PASS |

## Database pool and database evidence

The application engine is deliberately limited to six connections with zero
overflow and a two-second timeout. SQLAlchemy pool checkout/checkin events record
actual connection ownership; active request contexts are not misreported as
checked-out connections.

| Pool measurement | Value | Budget | Result |
| --- | ---: | ---: | --- |
| Checkout wait p50 | 0.0010 s | informational | PASS |
| Checkout wait p95 | 0.1365 s | <= 1.0 s | PASS |
| Checkout wait maximum | 0.3186 s | <= 2.0 s | PASS |
| Maximum checked-out connections | 6 / 6 | <= configured size | PASS |
| Pool failures | 0 | 0 | PASS |
| Checked out after test | 0 | 0 | PASS |
| Starved endpoints | 0 | 0 | PASS |

The workload committed 94 transactions, read 296 blocks, hit 180,573 cached
blocks, and created no PostgreSQL temporary files or bytes. The History fixture
contains 20,160 immutable minute readings.

## Logical backup and clean restore

Backup verification is not limited to checking the archive table of contents.
While the concurrent workload is active, the test:

1. creates a custom-format logical dump;
2. verifies its archive listing;
3. computes and validates a SHA-256 checksum;
4. creates a new empty database inside the disposable PostgreSQL container;
5. restores with `pg_restore --exit-on-error`;
6. queries the restored Alembic version, devices, and readings;
7. removes only that explicitly named disposable restore database.

The measured backup/restore took 3.8529 seconds. The clean database contained
one current Alembic revision, all six devices, and 20,160 readings. The archive
listing was 61,308 bytes and the test-run checksum was
`3989dc58e070d17007d33ef4708aa90abbf0cb42fd257f7b5eec00dbf0a8c3a6`.
The checksum identifies this disposable evidence artifact; it is not a release
or production-backup checksum.

## Result

`1 passed in 12.40s`. No endpoint failed, no pool acquisition timed out, and the
clean-database restore was queryable while signed protocol traffic, History,
notifications, rate calculations, PDF/OCR, export rendering, and backup ran
concurrently.
