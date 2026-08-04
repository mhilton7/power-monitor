from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any, cast

import asyncpg
import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.bills.extraction import extract_bill
from app.config import Settings, get_settings
from app.db.models import (
    AlertInstance,
    AlertRule,
    Device,
    DeviceCapability,
    DeviceConfigVersion,
    DeviceCredential,
    FirmwareDeployment,
    FirmwareRelease,
    Site,
    SyncCursor,
    User,
)
from app.db.session import get_session
from app.history import history_csv, query_history
from app.main import app
from app.notifications import load_notification_views
from app.rates.engine import RateEngine
from app.schemas import HistoryQueryRequest
from app.security.browser import SessionPrincipal
from app.security.protocol import PROTOCOL, SecretCipher, sign_headers

pytestmark = [
    pytest.mark.integration,
    pytest.mark.load,
    pytest.mark.skipif(
        os.getenv("RUN_LOAD_TEST") != "1",
        reason="set RUN_LOAD_TEST=1 on a host with Docker",
    ),
]

SITE_ID = "compound-load-site"
DEVICE_IDS = [f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 7)]
HISTORY_DEVICE_IDS = DEVICE_IDS[4:]
BENCHMARK_END = datetime(2026, 8, 1, tzinfo=UTC)


async def _migrate(backend: Path, database_url: str) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
        cwd=backend,
        env={**os.environ, "DATABASE_URL": database_url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, (stdout + stderr).decode()


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def _summary(values: list[float]) -> dict[str, float]:
    assert values
    return {
        "p50_seconds": round(statistics.median(values), 4),
        "p95_seconds": round(_percentile(values, 0.95), 4),
        "max_seconds": round(max(values), 4),
    }


def _principal() -> SessionPrincipal:
    return SessionPrincipal(
        user=cast(Any, None),
        session=cast(Any, None),
        roles=frozenset({"admin"}),
        permissions=frozenset({"readings.view"}),
        all_sites=True,
        site_ids=frozenset(),
    )


def _signed(
    secret: bytes,
    device_id: str,
    method: str,
    target: str,
    body: bytes = b"",
) -> dict[str, str]:
    return sign_headers(
        secret=secret,
        device_id=device_id,
        direction="device-to-server",
        method=method,
        target=target,
        body=body,
    )


def _heartbeat(device_id: str, sequence: int) -> dict[str, Any]:
    measured_at = datetime.now(UTC)
    return {
        "protocol_version": PROTOCOL,
        "schema_version": "heartbeat/1.0.0",
        "device_id": device_id,
        "boot_id": "10000000-0000-0000-0000-000000000001",
        "firmware_version": "1.0.15",
        "firmware_build_hash": "a" * 64,
        "uptime_seconds": 1_000 + sequence,
        "reboot_reason": "power_on",
        "connection_mode": "push",
        "latest": {
            "measured_at": measured_at.isoformat().replace("+00:00", "Z"),
            "voltage_v": "120.1",
            "current_a": "1.00",
            "power_w": "120.1",
            "power_factor": "1.0",
            "frequency_hz": "60.0",
            "energy_wh": "2.001667",
        },
        "pzem": {"ok": True, "status": "ok"},
        "sd": {"ok": True, "status": "ok"},
        "oldest_stored_sequence": 1,
        "newest_stored_sequence": sequence,
        "server_ack_sequence": max(0, sequence - 1),
        "backlog_estimate": 1,
        "configuration_version": 1,
        "time": {"trusted": True, "source": "sntp"},
        "resources": {"heap": 100_000},
        "queue": {"pending": 1},
    }


def _reading_batch(device_id: str, sequence: int) -> dict[str, Any]:
    end = datetime.now(UTC) - timedelta(seconds=sequence)
    start = end - timedelta(minutes=1)
    return {
        "protocol_version": PROTOCOL,
        "schema_version": "reading-batch/1.0.0",
        "device_id": device_id,
        "readings": [
            {
                "sequence": sequence,
                "boot_id": "10000000-0000-0000-0000-000000000001",
                "interval_start": start.isoformat().replace("+00:00", "Z"),
                "interval_end": end.isoformat().replace("+00:00", "Z"),
                "time_trusted": True,
                "voltage_avg": "120.1",
                "current_avg": "1.0",
                "power_avg": "120.1",
                "power_factor": "1.0",
                "frequency_hz": "60.0",
                "interval_energy_wh": "2.001667",
                "energy_method": "power_integration",
                "ct_rating_amps": "100",
                "quality_flags": [],
                "firmware_version": "1.0.15",
            }
        ],
    }


def _tiered_plan() -> dict[str, Any]:
    return {
        "code": "COMPOUND-LOAD-TIERED",
        "timezone": "UTC",
        "pricing_model": "tiered",
        "billing_cycle": {
            "expected_start_day": 1,
            "threshold": {
                "basis": "fixed_cycle_kwh",
                "rounding_policy": "none",
                "seasonal_baselines": [],
            },
        },
        "tiers": [
            {
                "tier_id": "tier-1",
                "name": "Tier 1",
                "order": 0,
                "lower_bound_inclusive_kwh": "0",
                "upper_bound_exclusive_kwh": "40",
                "lower_bound_multiplier": None,
                "upper_bound_multiplier": None,
                "price_per_kwh": "0.20",
                "tou_prices": {},
            },
            {
                "tier_id": "tier-2",
                "name": "Tier 2",
                "order": 1,
                "lower_bound_inclusive_kwh": "40",
                "upper_bound_exclusive_kwh": None,
                "lower_bound_multiplier": None,
                "upper_bound_multiplier": None,
                "price_per_kwh": "0.35",
                "tou_prices": {},
            },
        ],
        "adjustments": [],
    }


def _ocr_tsv() -> str:
    lines = [
        "Southern California Edison",
        "Rate Plan: DOMESTIC",
        "Billing Period: Jul 1, 2026 - Jul 31, 2026",
        "Total Usage: 951 kWh",
        "Energy Charges: $322.50",
        "Bill Total: $355.00",
        "Tier 1 | 0-579 kWh | 579 kWh | $0.30/kWh | $173.70",
        "Tier 2 | 580+ kWh | 372 kWh | $0.40/kWh | $148.80",
    ]
    rows = [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    ]
    for line_number, line in enumerate(lines, start=1):
        for word_number, word in enumerate(line.split(), start=1):
            rows.append(f"5\t1\t1\t1\t{line_number}\t{word_number}\t20\t20\t20\t18\t94\t{word}")
    return "\n".join(rows)


def _ocr_runner(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
    stdout = _ocr_tsv() if "tesseract" in command[0].lower() else ""
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


async def _seed(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> tuple[dict[str, bytes], str, str, dict[str, Any]]:
    now = datetime.now(UTC)
    secrets = {device_id: hashlib.sha256(device_id.encode()).digest() for device_id in DEVICE_IDS}
    cipher = SecretCipher(settings.app_master_key)
    artifact = b"compound-load-firmware" * 8_192
    settings.firmware_path.mkdir(parents=True, exist_ok=True)
    artifact_path = settings.firmware_path / "compound-load.bin"
    artifact_path.write_bytes(artifact)
    digest = hashlib.sha256(artifact).hexdigest()
    async with factory() as session:
        session.add(
            User(
                id="compound-load-user",
                email="compound-load@example.invalid",
                display_name="Compound load runner",
                password_hash="not-a-login-credential",
                is_active=True,
                lifecycle_state="active",
                is_protected=False,
                all_sites=True,
            )
        )
        site = Site(
            id=SITE_ID,
            name="Compound load site",
            code="compound-load",
            timezone="UTC",
            currency="USD",
            locale="en-US",
            unit_system="imperial",
            allowed_cidrs=[],
            allowed_domains=[],
            allow_public_polling=False,
            lifecycle_state="active",
            is_default=True,
        )
        session.add(site)
        # Flush parent rows explicitly because the load fixture assigns scalar
        # foreign-key ids rather than ORM relationships. This keeps insertion
        # ordering deterministic across PostgreSQL/SQLAlchemy versions.
        await session.flush()
        for index, device_id in enumerate(DEVICE_IDS):
            device = Device(
                id=device_id,
                site_id=SITE_ID,
                hardware_id=f"compound-load-hardware-{index:02d}",
                name=f"Compound sensor {index:02d}",
                connection_mode="push",
                measurement_role="branch",
                include_in_default_site_total=True,
                protocol_version=PROTOCOL,
                firmware_version="1.0.15",
                firmware_build_hash="a" * 64,
                status="online_synchronized",
                lifecycle_status="active",
            )
            session.add(device)
            await session.flush()
            session.add(
                DeviceCredential(
                    id=f"compound-load-credential-{index:02d}",
                    device_id=device_id,
                    encrypted_secret=cipher.encrypt(secrets[device_id]),
                    fingerprint=hashlib.sha256(secrets[device_id]).hexdigest(),
                    valid_from=now - timedelta(minutes=1),
                    valid_until=None,
                    revoked_at=None,
                    delivered_at=now,
                    confirmed_at=now,
                    created_at=now,
                )
            )
            session.add(
                DeviceCapability(
                    device_id=device_id,
                    hardware_target="esp32-s3-pzem004t-v4",
                    pzem_model="PZEM-004T V4.0",
                    sd_required=True,
                    features={
                        "ota": {
                            "supported": True,
                            "protocol_version": 2,
                            "authentication_mode": "existing_device_hmac",
                            "rollback_supported": True,
                            "partition_size_bytes": 6 * 1024 * 1024,
                        }
                    },
                    reported_at=now,
                )
            )
            session.add(
                SyncCursor(
                    device_id=device_id,
                    highest_contiguous_sequence=0,
                    maximum_seen_sequence=0,
                    updated_at=now,
                )
            )
            session.add(
                DeviceConfigVersion(
                    id=f"compound-load-config-{index:02d}",
                    device_id=device_id,
                    version=1,
                    desired_config={"measurement_interval_seconds": 60},
                    config_hash=hashlib.sha256(b'{"measurement_interval_seconds":60}').hexdigest(),
                    status="pending",
                    report=None,
                    created_at=now,
                    reported_at=None,
                )
            )
        release = FirmwareRelease(
            id="compound-load-release",
            version="1.0.16",
            channel="canary",
            trust_mode="existing_device_hmac",
            project_name="power-monitor-sensor",
            hardware_target="esp32-s3",
            protocol_min=PROTOCOL,
            protocol_max=PROTOCOL,
            artifact_path=artifact_path.name,
            size_bytes=len(artifact),
            sha256=digest,
            build_hash="b" * 64,
            git_commit="c" * 40,
            build_timestamp=now,
            original_filename="compound-load.bin",
            verification_status="verified",
            verification_evidence={"fixture": True},
            artifact_verified_at=now,
            release_notes="Deterministic compound-load fixture",
            verified_at=now,
            active=True,
        )
        session.add(release)
        await session.flush()
        manifest_deployment = FirmwareDeployment(
            id="compound-load-manifest-deployment",
            firmware_release_id=release.id,
            device_id=DEVICE_IDS[2],
            status="scheduled",
            state="scheduled",
            attempt=1,
            scheduled_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(hours=1),
            source_version="1.0.15",
            source_build_hash="a" * 64,
            source_boot_id="10000000-0000-0000-0000-000000000001",
            state_changed_at=now,
            created_by="compound-load-user",
            created_at=now,
        )
        report_deployment = FirmwareDeployment(
            id="compound-load-report-deployment",
            firmware_release_id=release.id,
            device_id=DEVICE_IDS[3],
            status="offered",
            state="offered",
            attempt=1,
            scheduled_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(hours=1),
            source_version="1.0.15",
            source_build_hash="a" * 64,
            source_boot_id="10000000-0000-0000-0000-000000000001",
            state_changed_at=now,
            created_by="compound-load-user",
            created_at=now,
        )
        session.add_all([manifest_deployment, report_deployment])
        rule = AlertRule(
            id="compound-load-alert-rule",
            name="Compound load alert",
            rule_type="reading_stale",
            severity="warning",
            enabled=True,
            site_id=SITE_ID,
            debounce_seconds=0,
            resolve_seconds=0,
            configuration={},
        )
        session.add(rule)
        await session.flush()
        session.add_all(
            [
                AlertInstance(
                    id=f"compound-load-alert-{index:03d}",
                    rule_id=rule.id,
                    device_id=DEVICE_IDS[index % 4],
                    site_id=SITE_ID,
                    status="open",
                    severity="warning",
                    opened_at=now - timedelta(minutes=index),
                    last_seen_at=now - timedelta(seconds=index),
                    occurrence_count=index + 1,
                    evidence={"age_seconds": index + 1},
                )
                for index in range(100)
            ]
        )
        await session.commit()
    return (
        secrets,
        release.id,
        report_deployment.id,
        {
            "sha256": digest,
            "size": len(artifact),
        },
    )


async def _seed_history(connection: asyncpg.Connection[Any]) -> int:
    result = await connection.execute(
        """
        INSERT INTO raw_readings (
            id, device_id, site_id, sequence, boot_id,
            interval_start, interval_end, time_trusted,
            voltage_avg, voltage_min, voltage_max,
            current_avg, current_min, current_max,
            power_avg, power_min, power_max,
            power_factor, frequency_hz,
            device_interval_energy_wh, energy_method, ct_rating_amps,
            quality_flags, firmware_version, record_hash, original_payload,
            ingestion_source, ingested_at
        )
        SELECT
            md5(device.device_id || sample.interval_start::text), device.device_id, $3,
            sample.sequence::integer, '20000000-0000-0000-0000-000000000001',
            sample.interval_start, sample.interval_start + interval '1 minute', true,
            120.0, 119.5, 120.5, 1.0, 0.9, 1.1,
            120.0, 118.0, 122.0, 1.0, 60.0, 2.0,
            'device_interval', 100.0, '[]'::json, 'compound-load/1', repeat('d', 64),
            NULL, 'push', $2::timestamptz
        FROM unnest($1::varchar[]) AS device(device_id)
        CROSS JOIN LATERAL generate_series(
            $2::timestamptz - interval '7 days',
            $2::timestamptz - interval '1 minute',
            interval '1 minute'
        ) WITH ORDINALITY AS sample(interval_start, sequence)
        """,
        HISTORY_DEVICE_IDS,
        BENCHMARK_END,
        SITE_ID,
    )
    await connection.execute("ANALYZE raw_readings")
    return int(result.rsplit(" ", maxsplit=1)[-1])


async def _logical_backup(container_id: str, username: str, database: str) -> dict[str, Any]:
    container_dump_path = "/power-monitor-compound.dump"
    restore_database = f"{database}_compound_restore"
    started = perf_counter()
    dump = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        container_id,
        "pg_dump",
        "-U",
        username,
        "-d",
        database,
        "--format=custom",
        f"--file={container_dump_path}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await dump.communicate()
    assert dump.returncode == 0, (stdout + stderr).decode()
    verify = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        container_id,
        "pg_restore",
        "--list",
        container_dump_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    listing, verify_error = await verify.communicate()
    assert verify.returncode == 0, verify_error.decode()
    assert b"TABLE DATA" in listing
    checksum = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        container_id,
        "sha256sum",
        container_dump_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    checksum_output, checksum_error = await checksum.communicate()
    assert checksum.returncode == 0, checksum_error.decode()
    checksum_value = checksum_output.decode().split(maxsplit=1)[0]
    assert len(checksum_value) == 64

    create = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        container_id,
        "createdb",
        "-U",
        username,
        restore_database,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    create_stdout, create_stderr = await create.communicate()
    assert create.returncode == 0, (create_stdout + create_stderr).decode()
    device_rows = 0
    reading_rows = 0
    try:
        restore = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            container_id,
            "pg_restore",
            "-U",
            username,
            "-d",
            restore_database,
            "--exit-on-error",
            container_dump_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        restore_stdout, restore_stderr = await restore.communicate()
        assert restore.returncode == 0, (restore_stdout + restore_stderr).decode()
        restored_counts = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            container_id,
            "psql",
            "-U",
            username,
            "-d",
            restore_database,
            "-At",
            "-F,",
            "-c",
            (
                "SELECT (SELECT count(*) FROM alembic_version), "
                "(SELECT count(*) FROM devices), (SELECT count(*) FROM raw_readings)"
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        counts_output, counts_error = await restored_counts.communicate()
        assert restored_counts.returncode == 0, counts_error.decode()
        migration_rows, device_rows, reading_rows = [
            int(value) for value in counts_output.decode().strip().split(",")
        ]
        assert migration_rows == 1
        assert device_rows == len(DEVICE_IDS)
        assert reading_rows >= 20_160
    finally:
        cleanup = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            container_id,
            "dropdb",
            "-U",
            username,
            "--if-exists",
            restore_database,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        cleanup_stdout, cleanup_stderr = await cleanup.communicate()
        assert cleanup.returncode == 0, (cleanup_stdout + cleanup_stderr).decode()
    return {
        "seconds": round(perf_counter() - started, 4),
        "verified": True,
        "restored_to_clean_database": True,
        "sha256": checksum_value,
        "listing_bytes": len(listing),
        "restored_devices": device_rows,
        "restored_readings": reading_rows,
    }


@pytest.mark.asyncio
async def test_postgres_compound_workload_keeps_device_protocol_responsive(
    tmp_path: Path,
) -> None:
    backend = Path(__file__).resolve().parents[1]
    with PostgresContainer("postgres:17.5-alpine") as postgres:
        database_url = postgres.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+asyncpg://"
        )
        await _migrate(backend, database_url)
        runtime = tmp_path / "compound-load"
        settings = Settings(
            database_url=database_url,
            app_master_key=Fernet.generate_key().decode(),
            session_pepper="compound-load-session-pepper-with-at-least-32-bytes",
            bootstrap_secret="compound-load-bootstrap-secret",
            public_origin="http://compound.test",
            cookie_secure=False,
            firmware_path=runtime / "firmware",
            report_path=runtime / "reports",
            backup_path=runtime / "backups",
            log_path=runtime / "logs",
            utility_bill_artifact_path=runtime / "utility-bills",
        )
        engine = create_async_engine(
            database_url,
            pool_size=6,
            max_overflow=0,
            pool_timeout=2,
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        secrets, release_id, report_deployment_id, firmware = await _seed(factory, settings)
        connection = await asyncpg.connect(
            database_url.replace("postgresql+asyncpg://", "postgresql://")
        )
        try:
            assert await _seed_history(connection) == 20_160
            before_db = dict(
                await connection.fetchrow(
                    """
                    SELECT xact_commit, blks_read, blks_hit, temp_files, temp_bytes
                    FROM pg_stat_database WHERE datname = current_database()
                    """
                )
                or {}
            )
            pool_waits: list[float] = []
            active_session_contexts = 0
            maximum_session_contexts = 0
            checked_out_connections = 0
            maximum_checked_out_connections = 0
            pool_failures = 0

            def connection_checked_out(*_args: Any) -> None:
                nonlocal checked_out_connections, maximum_checked_out_connections
                checked_out_connections += 1
                maximum_checked_out_connections = max(
                    maximum_checked_out_connections,
                    checked_out_connections,
                )

            def connection_checked_in(*_args: Any) -> None:
                nonlocal checked_out_connections
                checked_out_connections -= 1

            event.listen(engine.sync_engine, "checkout", connection_checked_out)
            event.listen(engine.sync_engine, "checkin", connection_checked_in)

            @asynccontextmanager
            async def measured_session() -> AsyncIterator[AsyncSession]:
                nonlocal active_session_contexts, maximum_session_contexts, pool_failures
                started = perf_counter()
                try:
                    async with factory() as session:
                        await session.connection()
                        pool_waits.append(perf_counter() - started)
                        active_session_contexts += 1
                        maximum_session_contexts = max(
                            maximum_session_contexts,
                            active_session_contexts,
                        )
                        try:
                            yield session
                        finally:
                            active_session_contexts -= 1
                except Exception:
                    pool_failures += 1
                    raise

            async def override_session() -> AsyncIterator[AsyncSession]:
                async with measured_session() as session:
                    yield session

            app.dependency_overrides[get_session] = override_session
            app.dependency_overrides[get_settings] = lambda: settings
            latencies: dict[str, list[float]] = {
                key: []
                for key in (
                    "heartbeat",
                    "reading_batch",
                    "configuration",
                    "manifest",
                    "ota_report",
                    "firmware_download",
                )
            }
            statuses: dict[str, list[int]] = {key: [] for key in latencies}

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://compound.test",
                timeout=10,
            ) as client:
                report_payload = {
                    "device_id": DEVICE_IDS[3],
                    "deployment_id": report_deployment_id,
                    "release_id": release_id,
                    "attempt": 1,
                    "state": "manifest_authenticated",
                    "current_firmware_version": "1.0.15",
                    "current_build_hash": "a" * 64,
                    "target_version": "1.0.16",
                    "target_sha256": firmware["sha256"],
                    "bytes_received": 0,
                    "image_size": firmware["size"],
                    "progress": 0,
                    "boot_id": "10000000-0000-0000-0000-000000000001",
                    "evidence_sequence": 1,
                }
                report_body = json.dumps(report_payload, separators=(",", ":")).encode()
                report_target = "/api/v1/device-firmware/report"
                primed = await client.post(
                    report_target,
                    content=report_body,
                    headers={
                        **_signed(
                            secrets[DEVICE_IDS[3]],
                            DEVICE_IDS[3],
                            "POST",
                            report_target,
                            report_body,
                        ),
                        "Content-Type": "application/json",
                    },
                )
                assert primed.status_code == 200, primed.text

                async def timed_request(
                    key: str,
                    request: Callable[[int], Any],
                    samples: int = 12,
                ) -> None:
                    for index in range(1, samples + 1):
                        started = perf_counter()
                        response = await request(index)
                        latencies[key].append(perf_counter() - started)
                        statuses[key].append(response.status_code)
                        assert response.status_code == 200, response.text

                async def heartbeat_request(index: int) -> httpx.Response:
                    payload = _heartbeat(DEVICE_IDS[0], index)
                    body = json.dumps(payload, separators=(",", ":")).encode()
                    target = "/api/v1/device-heartbeats"
                    return await client.post(
                        target,
                        content=body,
                        headers={
                            **_signed(secrets[DEVICE_IDS[0]], DEVICE_IDS[0], "POST", target, body),
                            "Content-Type": "application/json",
                        },
                    )

                async def reading_request(index: int) -> httpx.Response:
                    payload = _reading_batch(DEVICE_IDS[0], index)
                    body = json.dumps(payload, separators=(",", ":")).encode()
                    target = "/api/v1/device-readings/batch"
                    return await client.post(
                        target,
                        content=body,
                        headers={
                            **_signed(secrets[DEVICE_IDS[0]], DEVICE_IDS[0], "POST", target, body),
                            "Content-Type": "application/json",
                        },
                    )

                async def configuration_request(_index: int) -> httpx.Response:
                    target = "/api/v1/device-config/effective"
                    return await client.get(
                        target,
                        headers=_signed(secrets[DEVICE_IDS[1]], DEVICE_IDS[1], "GET", target),
                    )

                async def manifest_request(_index: int) -> httpx.Response:
                    target = "/api/v1/device-firmware/manifest"
                    return await client.get(
                        target,
                        headers=_signed(secrets[DEVICE_IDS[2]], DEVICE_IDS[2], "GET", target),
                    )

                async def report_request(_index: int) -> httpx.Response:
                    return await client.post(
                        report_target,
                        content=report_body,
                        headers={
                            **_signed(
                                secrets[DEVICE_IDS[3]],
                                DEVICE_IDS[3],
                                "POST",
                                report_target,
                                report_body,
                            ),
                            "Content-Type": "application/json",
                        },
                    )

                async def download_request(_index: int) -> httpx.Response:
                    target = (
                        f"/api/v1/device-firmware/{release_id}/download"
                        f"?deployment_id={report_deployment_id}"
                    )
                    return await client.get(
                        target,
                        headers=_signed(secrets[DEVICE_IDS[3]], DEVICE_IDS[3], "GET", target),
                    )

                history_request = HistoryQueryRequest.model_validate(
                    {
                        "scope": {"type": "devices", "device_ids": HISTORY_DEVICE_IDS},
                        "display_mode": "combined",
                        "metrics": ["power_w", "energy_kwh"],
                        "start_utc": BENCHMARK_END - timedelta(days=7),
                        "end_utc": BENCHMARK_END,
                        "bucket": "1h",
                        "timezone": "UTC",
                        "page": 1,
                        "page_size": 500,
                    }
                )

                async def history_and_export_workload() -> dict[str, Any]:
                    sizes: list[int] = []
                    for _ in range(6):
                        async with measured_session() as session:
                            response = await query_history(session, _principal(), history_request)
                        rendered = history_csv(response)
                        assert "interval_start_utc" in rendered
                        sizes.append(len(rendered.encode()))
                    return {"runs": len(sizes), "export_bytes": sum(sizes)}

                async def notification_workload() -> dict[str, int]:
                    counts: list[int] = []
                    for _ in range(12):
                        async with measured_session() as session:
                            views = await load_notification_views(
                                session,
                                user_id="compound-load-user",
                                permissions={"alerts.view", "alerts.acknowledge"},
                                all_sites=True,
                                site_ids=set(),
                                requested_site_id=SITE_ID,
                            )
                        counts.append(len(views))
                    assert set(counts) == {100}
                    return {"runs": len(counts), "views_per_run": counts[0]}

                async def rate_recalculation_workload() -> dict[str, Any]:
                    def calculate() -> str:
                        engine_instance = RateEngine(_tiered_plan())
                        result = None
                        for index in range(8_000):
                            result = engine_instance.calculate(
                                start=BENCHMARK_END - timedelta(minutes=15),
                                end=BENCHMARK_END,
                                energy_kwh=Decimal("0.25"),
                                cumulative_usage_before_kwh=Decimal(index) / Decimal("100"),
                                cycle_start=BENCHMARK_END - timedelta(days=30),
                                cycle_end=BENCHMARK_END,
                            )
                        assert result is not None
                        return str(result.total)

                    return {"calculations": 8_000, "last_total": await asyncio.to_thread(calculate)}

                async def pdf_ocr_workload() -> dict[str, Any]:
                    fixture_root = Path(__file__).parent / "fixtures" / "bills"
                    text_pdf = fixture_root / "sanitized-sce-domestic-bill.pdf"
                    scanned_pdf = fixture_root / "scanned-tiered-bill.pdf"

                    def extract() -> list[str]:
                        text = extract_bill(text_pdf.read_bytes(), settings, pdf_path=text_pdf)
                        ocr = extract_bill(
                            scanned_pdf.read_bytes(),
                            settings,
                            pdf_path=scanned_pdf,
                            runner=_ocr_runner,
                        )
                        return [text.extraction_method, ocr.extraction_method]

                    methods = await asyncio.to_thread(extract)
                    assert methods == ["text", "ocr"]
                    return {"documents": 2, "methods": methods}

                container_id = postgres.get_wrapped_container().id
                endpoint_tasks = [
                    timed_request("heartbeat", heartbeat_request),
                    timed_request("reading_batch", reading_request),
                    timed_request("configuration", configuration_request),
                    timed_request("manifest", manifest_request),
                    timed_request("ota_report", report_request),
                    timed_request("firmware_download", download_request),
                ]
                background_tasks = [
                    history_and_export_workload(),
                    notification_workload(),
                    rate_recalculation_workload(),
                    pdf_ocr_workload(),
                    _logical_backup(
                        container_id,
                        postgres.username,
                        postgres.dbname,
                    ),
                ]
                gathered = await asyncio.gather(*endpoint_tasks, *background_tasks)
                background_results = gathered[len(endpoint_tasks) :]

            after_db = dict(
                await connection.fetchrow(
                    """
                    SELECT xact_commit, blks_read, blks_hit, temp_files, temp_bytes
                    FROM pg_stat_database WHERE datname = current_database()
                    """
                )
                or {}
            )
            endpoint_metrics = {key: _summary(value) for key, value in latencies.items()}
            for key, metrics in endpoint_metrics.items():
                assert len(latencies[key]) == 12
                assert set(statuses[key]) == {200}
                assert metrics["p95_seconds"] <= 2.0
                assert metrics["max_seconds"] <= 5.0
            assert pool_waits
            pool_metrics = _summary(pool_waits)
            assert pool_metrics["p95_seconds"] <= 1.0
            assert pool_metrics["max_seconds"] <= 2.0
            assert pool_failures == 0
            assert active_session_contexts == 0
            assert checked_out_connections == 0
            assert maximum_checked_out_connections <= 6
            assert cast(Any, engine.pool).checkedout() == 0
            evidence = {
                "postgres_version": "17.5",
                "history_seed_rows": 20_160,
                "endpoint_samples": 12,
                "endpoint_latency": endpoint_metrics,
                "pool": {
                    **pool_metrics,
                    "configured_size": 6,
                    "maximum_session_contexts": maximum_session_contexts,
                    "maximum_checked_out_connections": maximum_checked_out_connections,
                    "failures": pool_failures,
                    "checked_out_after_test": cast(Any, engine.pool).checkedout(),
                    "starved_endpoints": [],
                },
                "background_workloads": background_results,
                "database_delta": {
                    key: int(after_db.get(key, 0) or 0) - int(before_db.get(key, 0) or 0)
                    for key in ("xact_commit", "blks_read", "blks_hit", "temp_files", "temp_bytes")
                },
            }
            print("COMPOUND_POSTGRES_LOAD=" + json.dumps(evidence, sort_keys=True))
        finally:
            app.dependency_overrides.clear()
            await connection.close()
            await engine.dispose()
