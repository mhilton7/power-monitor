from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device, DeviceEvent, SequenceGap, Site
from app.ingestion.service import ingest_readings, normalize_energy
from app.schemas import Reading


def reading(sequence: int, energy: str = "10") -> Reading:
    end = datetime(2026, 7, 20, 12, sequence % 50, tzinfo=UTC)
    return Reading(
        sequence=sequence,
        boot_id="123e4567-e89b-12d3-a456-426614174000",
        interval_start=end - timedelta(minutes=1),
        interval_end=end,
        time_trusted=True,
        voltage_avg=Decimal("120"),
        current_avg=Decimal("5"),
        power_avg=Decimal("600"),
        power_factor=Decimal("1"),
        frequency_hz=Decimal("60"),
        pzem_energy_start_wh=Decimal("100"),
        pzem_energy_end_wh=Decimal("110"),
        interval_energy_wh=Decimal(energy),
        energy_method="counter",
        ct_rating_amps=Decimal("100"),
        quality_flags=[],
        firmware_version="1.0.0",
    )


def test_normalization_prefers_valid_device_energy() -> None:
    result = normalize_energy(reading(1))
    assert result.selected_energy_wh == Decimal("10")
    assert result.selected_method == "device-reported"
    mismatch = normalize_energy(reading(2, "40"))
    assert mismatch.selected_energy_wh == Decimal("10")
    assert mismatch.validation_result == "corrected"


@pytest.mark.asyncio
async def test_idempotency_conflicts_and_gap_fill(session: AsyncSession) -> None:
    site = Site(name="Test", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    device = Device(site_id=site.id, hardware_id="hw-ingest", name="Ingest")
    session.add(device)
    await session.commit()
    first = await ingest_readings(
        session, device_id=device.id, readings=[reading(1), reading(3)], source="push"
    )
    await session.commit()
    assert first.accepted == [1, 3]
    assert first.highest_contiguous_accepted_sequence == 1
    assert first.missing_ranges == [(2, 2)]
    duplicate = await ingest_readings(
        session, device_id=device.id, readings=[reading(1)], source="pull"
    )
    await session.commit()
    assert duplicate.duplicates == [1]
    conflict = await ingest_readings(
        session, device_id=device.id, readings=[reading(1, "11")], source="push"
    )
    await session.commit()
    assert conflict.rejected[0].code == "conflicting_duplicate"
    assert await session.scalar(select(DeviceEvent.id))
    filled = await ingest_readings(
        session, device_id=device.id, readings=[reading(2)], source="pull"
    )
    await session.commit()
    assert filled.highest_contiguous_accepted_sequence == 3
    assert filled.missing_ranges == []
    assert not list(await session.scalars(select(SequenceGap)))
