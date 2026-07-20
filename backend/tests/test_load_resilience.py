from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import tracemalloc
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import structlog
from cryptography.fernet import Fernet
from simulator.simulated_device.model import SimulatedDevice
from simulator.simulated_device.push import push_once
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.db.base import Base
from app.db.models import Device, DeviceCredential, DeviceHeartbeat, RawReading, Site
from app.db.session import get_session
from app.main import app
from app.security.protocol import SecretCipher

pytestmark = [
    pytest.mark.load,
    pytest.mark.skipif(
        os.environ.get("RUN_LOAD_TEST") != "1",
        reason="set RUN_LOAD_TEST=1 to execute the 100-device load gate",
    ),
]


@pytest.mark.asyncio
async def test_one_hundred_device_backfill_retry_and_bounded_resources() -> None:
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    database_path = Path(__file__).resolve().parents[2] / ".test-runtime" / "load.sqlite"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.unlink(missing_ok=True)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        pool_size=5,
        max_overflow=0,
        connect_args={"timeout": 60},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    key = Fernet.generate_key().decode()
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        app_master_key=key,
        session_pepper="load-session-pepper-with-at-least-32-bytes",
        bootstrap_secret="load-bootstrap-secret-with-at-least-16",
        public_origin="http://load.test",
        cookie_secure=False,
        report_path=database_path.parent / "reports",
        firmware_path=database_path.parent / "firmware",
        backup_path=database_path.parent / "backups",
    )

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    devices = [SimulatedDevice(index=index, server_url="http://load.test") for index in range(100)]
    cipher = SecretCipher(key)
    now = datetime.now(UTC)
    async with factory() as session:
        site = Site(name="Load test site", timezone="America/Los_Angeles")
        session.add(site)
        await session.flush()
        for simulated in devices:
            session.add(
                Device(
                    id=simulated.device_id,
                    site_id=site.id,
                    hardware_id=simulated.hardware_id,
                    name=f"Load device {simulated.index:03d}",
                    connection_mode="push",
                    protocol_version="pm-protocol/1.0.0",
                )
            )
            session.add(
                DeviceCredential(
                    device_id=simulated.device_id,
                    encrypted_secret=cipher.encrypt(simulated.secret.encode()),
                    fingerprint="load-" + simulated.hardware_id,
                    valid_from=now - timedelta(minutes=1),
                    valid_until=None,
                    revoked_at=None,
                    delivered_at=now,
                    confirmed_at=now,
                    created_at=now,
                )
            )
        await session.commit()

    outage_start = now - timedelta(hours=3)
    for simulated in devices:
        for minute in range(180):
            simulated.generate_reading(instant=outage_start + timedelta(minutes=minute + 1))

    tracemalloc.start()
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://load.test",
            timeout=60,
        ) as client:
            write_slots = asyncio.Semaphore(5)

            async def bounded_push(device: SimulatedDevice) -> dict[str, int]:
                async with write_slots:
                    return await push_once(client, device)

            first = await asyncio.gather(*(bounded_push(device) for device in devices))
            first_seconds = time.perf_counter() - started
            assert sum(item["accepted"] for item in first) == 18_000
            for simulated in devices:
                simulated.ack_sequence = 0
            retry = await asyncio.gather(*(bounded_push(device) for device in devices))
            assert sum(item["accepted"] for item in retry) == 0
        _current, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    async with factory() as session:
        reading_count = int(await session.scalar(select(func.count()).select_from(RawReading)) or 0)
        heartbeat_count = int(
            await session.scalar(select(func.count()).select_from(DeviceHeartbeat)) or 0
        )
    assert reading_count == 18_000
    assert heartbeat_count == 200
    assert peak_bytes < 256 * 1024 * 1024
    metrics = {
        "devices": 100,
        "backfill_records": reading_count,
        "backfill_hours_per_device": 3,
        "first_backfill_seconds": round(first_seconds, 3),
        "peak_tracemalloc_mib": round(peak_bytes / 1024 / 1024, 2),
        "database_pool_size": engine.pool.size(),
        "duplicate_records_after_retry": 0,
    }
    print("LOAD_METRICS=" + json.dumps(metrics, sort_keys=True))
    app.dependency_overrides.clear()
    await engine.dispose()
    database_path.unlink(missing_ok=True)
