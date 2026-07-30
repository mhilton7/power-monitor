from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Device, DeviceHeartbeat, RawReading, Site
from app.live_measurements import load_latest_measurements


def heartbeat(
    device: Device,
    *,
    now: datetime,
    power: str | None,
    voltage: str | None = "120.4",
    current: str | None = "0.01",
    frequency: str | None = "60.0",
    factor: str | None = "0.83",
    measured_at: datetime | None = None,
) -> DeviceHeartbeat:
    latest = None
    if power is not None:
        latest = {
            "measured_at": (measured_at or now).isoformat(),
            "voltage_v": voltage,
            "current_a": current,
            "power_w": power,
            "frequency_hz": frequency,
            "power_factor": factor,
        }
    return DeviceHeartbeat(
        device_id=device.id,
        boot_id="123e4567-e89b-12d3-a456-426614174000",
        received_at=now,
        device_time=measured_at or now if latest else None,
        current_watts=Decimal(power) if power is not None else None,
        pzem_ok=True,
        sd_ok=True,
        time_trusted=True,
        newest_sequence=0,
        backlog_estimate=0,
        payload={"latest": latest} if latest else {},
    )


def raw_reading(
    device: Device,
    *,
    sequence: int,
    measured_at: datetime,
    power: str,
    voltage: str = "121.0",
) -> RawReading:
    return RawReading(
        device_id=device.id,
        site_id=device.site_id,
        sequence=sequence,
        boot_id="123e4567-e89b-12d3-a456-426614174000",
        interval_start=measured_at - timedelta(minutes=1),
        interval_end=measured_at,
        time_trusted=True,
        voltage_avg=Decimal(voltage),
        current_avg=Decimal("0.02"),
        power_avg=Decimal(power),
        power_factor=Decimal("0.90"),
        frequency_hz=Decimal("60"),
        energy_method="power_integration",
        ct_rating_amps=Decimal("100"),
        quality_flags=[],
        firmware_version="1.0.0",
        record_hash=f"{sequence:064x}",
        original_payload={},
        ingestion_source="push",
        ingested_at=measured_at,
    )


async def device_for(
    session: AsyncSession,
    site: Site,
    *,
    hardware_id: str,
    name: str,
    now: datetime,
) -> Device:
    device = Device(
        site_id=site.id,
        hardware_id=hardware_id,
        name=name,
        status="online_push_only",
        last_seen_at=now,
        include_in_default_site_total=True,
    )
    session.add(device)
    await session.flush()
    return device


@pytest.mark.asyncio
async def test_live_heartbeat_metrics_and_legitimate_zero_are_preserved(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    site = Site(name="Live metrics", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    sensor = await device_for(
        session,
        site,
        hardware_id="live-metrics-1",
        name="Indoor-AC",
        now=now,
    )
    session.add(heartbeat(sensor, now=now, power="0"))
    await session.commit()

    measurements, _, _ = await load_latest_measurements(session, [sensor], test_settings, now=now)
    latest = measurements[sensor.id]
    assert latest.freshness_state == "live"
    assert latest.power_watts == Decimal("0")
    assert latest.voltage_volts == Decimal("120.4")
    assert latest.current_amps == Decimal("0.01")
    assert latest.frequency_hz == Decimal("60.0")
    assert latest.power_factor == Decimal("0.83")
    assert latest.source == "heartbeat_live"


@pytest.mark.asyncio
async def test_waiting_stale_offline_and_invalid_states(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    site = Site(name="Freshness", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    waiting = await device_for(
        session,
        site,
        hardware_id="freshness-waiting",
        name="Waiting",
        now=now,
    )
    stale = await device_for(
        session,
        site,
        hardware_id="freshness-stale",
        name="Stale",
        now=now,
    )
    offline = await device_for(
        session,
        site,
        hardware_id="freshness-offline",
        name="Offline",
        now=now - timedelta(minutes=5),
    )
    invalid = await device_for(
        session,
        site,
        hardware_id="freshness-invalid",
        name="Invalid",
        now=now,
    )
    session.add_all(
        [
            heartbeat(waiting, now=now, power=None),
            heartbeat(
                stale,
                now=now,
                power="4",
                measured_at=now - timedelta(minutes=3),
            ),
            heartbeat(
                offline,
                now=now - timedelta(minutes=5),
                power="5",
                measured_at=now - timedelta(minutes=5),
            ),
            heartbeat(
                invalid,
                now=now,
                power="3",
                measured_at=now + timedelta(minutes=10),
            ),
        ]
    )
    await session.commit()

    measurements, _, _ = await load_latest_measurements(
        session,
        [waiting, stale, offline, invalid],
        test_settings,
        now=now,
    )
    assert measurements[waiting.id].freshness_state == "waiting"
    assert measurements[waiting.id].power_watts is None
    assert measurements[stale.id].freshness_state == "stale"
    assert measurements[offline.id].freshness_state == "offline"
    assert measurements[invalid.id].freshness_state == "invalid"


@pytest.mark.asyncio
async def test_newer_committed_reading_wins_and_sensor_values_do_not_swap(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    site = Site(name="Ordering", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    first = await device_for(
        session,
        site,
        hardware_id="ordering-first",
        name="First",
        now=now,
    )
    second = await device_for(
        session,
        site,
        hardware_id="ordering-second",
        name="Second",
        now=now,
    )
    session.add_all(
        [
            heartbeat(
                first,
                now=now,
                power="1",
                voltage="120",
                measured_at=now - timedelta(seconds=10),
            ),
            raw_reading(
                first,
                sequence=9,
                measured_at=now - timedelta(seconds=2),
                power="7",
                voltage="121",
            ),
            heartbeat(second, now=now, power="22", voltage="122"),
        ]
    )
    await session.commit()

    statements = 0

    def count_selects(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal statements
        if statement.lstrip().upper().startswith("SELECT"):
            statements += 1

    assert session.bind is not None
    event.listen(session.bind.sync_engine, "before_cursor_execute", count_selects)
    try:
        measurements, _, _ = await load_latest_measurements(
            session,
            [first, second],
            test_settings,
            now=now,
        )
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", count_selects)

    assert statements == 2
    assert measurements[first.id].source == "committed_reading"
    assert measurements[first.id].power_watts == Decimal("7")
    assert measurements[first.id].voltage_volts == Decimal("121")
    assert measurements[second.id].power_watts == Decimal("22")
    assert measurements[second.id].voltage_volts == Decimal("122")


@pytest.mark.asyncio
async def test_missing_optional_metric_does_not_hide_power_and_invalid_metric_is_explicit(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    site = Site(name="Optional metrics", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    sensor = await device_for(
        session,
        site,
        hardware_id="optional-metrics-1",
        name="Indoor-AC",
        now=now,
    )
    session.add(
        heartbeat(
            sensor,
            now=now,
            power="1",
            voltage=None,
            factor="1.5",
        )
    )
    await session.commit()

    measurements, _, _ = await load_latest_measurements(session, [sensor], test_settings, now=now)
    latest = measurements[sensor.id]
    assert latest.freshness_state == "live"
    assert latest.power_watts == Decimal("1")
    assert latest.voltage_volts is None
    assert latest.power_factor is None
    assert latest.invalid_metrics == ("power_factor",)


@pytest.mark.asyncio
async def test_committed_reading_with_wrong_site_is_rejected(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    expected_site = Site(name="Expected site", timezone="America/Los_Angeles")
    wrong_site = Site(name="Wrong site", timezone="America/Los_Angeles")
    session.add_all([expected_site, wrong_site])
    await session.flush()
    sensor = await device_for(
        session,
        expected_site,
        hardware_id="site-mismatch-1",
        name="Indoor-AC",
        now=now,
    )
    reading = raw_reading(
        sensor,
        sequence=1,
        measured_at=now,
        power="1",
    )
    reading.site_id = wrong_site.id
    session.add(reading)
    await session.commit()

    measurements, _, _ = await load_latest_measurements(session, [sensor], test_settings, now=now)
    latest = measurements[sensor.id]
    assert latest.freshness_state == "invalid"
    assert latest.validation_reason == "site_mismatch"
    assert latest.power_watts == Decimal("1")
