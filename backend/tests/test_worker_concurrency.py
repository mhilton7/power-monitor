from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from worker.app.polling import poll_due_devices

from app.config import Settings
from app.db.models import Device, Site


@pytest.mark.asyncio
async def test_one_slow_device_does_not_stall_other_polling(
    monkeypatch: pytest.MonkeyPatch,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    test_settings: Settings,
) -> None:
    async with session_factory_fixture() as session:
        site = Site(name="Concurrency site", timezone="America/Los_Angeles")
        session.add(site)
        await session.flush()
        for index in range(6):
            session.add(
                Device(
                    site_id=site.id,
                    hardware_id=f"worker-concurrency-{index}",
                    name=f"Device {index}",
                    connection_mode="pull",
                )
            )
        await session.commit()

    completed: dict[str, float] = {}
    started = time.perf_counter()

    async def fake_poll(
        _session: AsyncSession, device: Device, _settings: Settings
    ) -> dict[str, Any]:
        await asyncio.sleep(0.75 if device.name == "Device 0" else 0.02)
        completed[device.name] = time.perf_counter() - started
        return {"device_id": device.id, "status": "ok"}

    monkeypatch.setattr("worker.app.polling.poll_device", fake_poll)
    results = await poll_due_devices(
        session_factory_fixture,
        test_settings,
        concurrency=6,
        per_site_concurrency=6,
    )
    assert len(results) == 6
    assert max(completed[name] for name in completed if name != "Device 0") < 0.5
    assert completed["Device 0"] >= 0.75
