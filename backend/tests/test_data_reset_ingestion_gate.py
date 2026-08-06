from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.device_protocol import event_batch
from app.db.models import (
    Device,
    DeviceDataState,
    DeviceEvent,
    RawReading,
    Site,
    SyncCursor,
    new_uuid,
)
from app.ingestion.service import enforce_data_generation_gate, ingest_readings
from app.problem import ProblemError
from app.schemas import DeviceEventBatch, Reading


def reading(*, generation: int, sequence: int, now: datetime) -> Reading:
    return Reading(
        data_generation=generation,
        sequence=sequence,
        boot_id=new_uuid(),
        interval_start=now - timedelta(minutes=1),
        interval_end=now,
        time_trusted=True,
        power_avg=Decimal("600"),
        interval_energy_wh=Decimal("10"),
        energy_method="power_integration",
        ct_rating_amps=Decimal("100"),
        quality_flags=[],
        firmware_version="1.0.18",
    )


@pytest.mark.asyncio
async def test_generation_gate_rejects_reset_races_and_pre_reset_replay(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    site = Site(id=new_uuid(), name="Generation Gate Site", code="generation-gate-site")
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="generation-gate-sensor",
        name="Generation Gate Sensor",
    )
    state = DeviceDataState(
        device_id=device.id,
        site_id=site.id,
        data_generation=2,
        reset_boundary=10,
        ingestion_gate="preparing",
        reset_required_on_reconnect=False,
        generation_updated_at=now,
        updated_at=now,
    )
    session.add_all([site, device, state])
    await session.commit()

    with pytest.raises(ProblemError) as gated:
        await enforce_data_generation_gate(
            session,
            device_id=device.id,
            readings=[reading(generation=2, sequence=11, now=now)],
            batch_generation=2,
        )
    assert gated.value.code == "sensor_reset_required"

    state.ingestion_gate = "open"
    await session.commit()
    with pytest.raises(ProblemError) as obsolete:
        await enforce_data_generation_gate(
            session,
            device_id=device.id,
            readings=[reading(generation=1, sequence=11, now=now)],
            batch_generation=1,
        )
    assert obsolete.value.code == "data_generation_obsolete"

    with pytest.raises(ProblemError) as boundary:
        await enforce_data_generation_gate(
            session,
            device_id=device.id,
            readings=[reading(generation=2, sequence=10, now=now)],
            batch_generation=2,
        )
    assert boundary.value.code == "reading_precedes_reset_boundary"

    with pytest.raises(ProblemError) as ahead:
        await enforce_data_generation_gate(
            session,
            device_id=device.id,
            readings=[reading(generation=3, sequence=11, now=now)],
            batch_generation=3,
        )
    assert ahead.value.code == "data_generation_ahead"

    accepted = await enforce_data_generation_gate(
        session,
        device_id=device.id,
        readings=[reading(generation=2, sequence=11, now=now)],
        batch_generation=2,
    )
    assert accepted == 2


@pytest.mark.asyncio
async def test_generation_gate_preserves_global_monotonic_sequence_identity(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    site = Site(id=new_uuid(), name="Generation Dedupe Site", code="generation-dedupe-site")
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="generation-dedupe-sensor",
        name="Generation Dedupe Sensor",
    )
    session.add_all(
        [
            site,
            device,
            DeviceDataState(
                device_id=device.id,
                site_id=site.id,
                data_generation=2,
                reset_boundary=1,
                ingestion_gate="open",
                reset_required_on_reconnect=False,
                generation_updated_at=now,
                updated_at=now,
            ),
            SyncCursor(
                device_id=device.id,
                highest_contiguous_sequence=1,
                maximum_seen_sequence=1,
                data_generation=2,
                reset_boundary=1,
                updated_at=now,
            ),
            RawReading(
                id=new_uuid(),
                device_id=device.id,
                site_id=site.id,
                data_generation=1,
                sequence=1,
                boot_id=new_uuid(),
                interval_start=now - timedelta(minutes=2),
                interval_end=now - timedelta(minutes=1),
                time_trusted=True,
                power_avg=Decimal("500"),
                device_interval_energy_wh=Decimal("8"),
                energy_method="power_integration",
                ct_rating_amps=Decimal("100"),
                quality_flags=[],
                firmware_version="1.0.17",
                record_hash="a" * 64,
                original_payload=None,
                ingestion_source="push",
                ingested_at=now - timedelta(minutes=1),
            ),
        ]
    )
    await session.commit()

    with pytest.raises(ProblemError) as reused:
        await ingest_readings(
            session,
            device_id=device.id,
            readings=[reading(generation=2, sequence=1, now=now)],
            source="push",
            data_generation=2,
        )
    assert reused.value.code == "reading_precedes_reset_boundary"

    result = await ingest_readings(
        session,
        device_id=device.id,
        readings=[reading(generation=2, sequence=2, now=now)],
        source="push",
        data_generation=2,
    )
    await session.commit()

    assert result.accepted == [2]
    assert result.duplicates == []
    assert result.data_generation == 2
    assert result.reset_boundary == 1
    assert await session.scalar(select(func.count()).select_from(RawReading)) == 2
    generations = set(
        await session.scalars(
            select(RawReading.data_generation).where(RawReading.device_id == device.id)
        )
    )
    assert generations == {1, 2}


@pytest.mark.asyncio
async def test_device_event_ingestion_redacts_obsolete_or_gated_measurements(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    site = Site(id=new_uuid(), name="Event Reset Site", code="event-reset-site")
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        hardware_id="event-reset-sensor",
        name="Event Reset Sensor",
    )
    state = DeviceDataState(
        device_id=device.id,
        site_id=site.id,
        data_generation=2,
        reset_boundary=10,
        ingestion_gate="open",
        reset_required_on_reconnect=False,
        generation_updated_at=now,
        updated_at=now,
    )
    session.add_all([site, device, state])
    await session.commit()
    verified = SimpleNamespace(device=device)

    async def send(*, event_id: str, sequence: int, generation: int) -> None:
        await event_batch(
            DeviceEventBatch.model_validate(
                {
                    "protocol_version": "pm-protocol/1.0.0",
                    "device_id": device.id,
                    "data_generation": generation,
                    "events": [
                        {
                            "event_id": event_id,
                            "occurred_at": now.isoformat(),
                            "category": "security",
                            "severity": "warning",
                            "evidence": {
                                "event_sequence": sequence,
                                "code": "reset-diagnostic",
                                "power_w": 1234,
                            },
                        }
                    ],
                }
            ),
            verified,
            session,
        )

    await send(event_id="obsolete", sequence=1, generation=1)
    obsolete = await session.scalar(
        select(DeviceEvent).where(
            DeviceEvent.device_id == device.id,
            DeviceEvent.event_id == "obsolete",
        )
    )
    assert obsolete is not None
    assert obsolete.evidence["power_w"] == "[redacted-by-data-reset]"
    assert obsolete.evidence["code"] == "reset-diagnostic"

    await send(event_id="current", sequence=2, generation=2)
    current = await session.scalar(
        select(DeviceEvent).where(
            DeviceEvent.device_id == device.id,
            DeviceEvent.event_id == "current",
        )
    )
    assert current is not None
    assert current.evidence["power_w"] == 1234

    state = await session.get(DeviceDataState, device.id, with_for_update=True)
    assert state is not None
    state.ingestion_gate = "committing"
    await session.commit()
    await send(event_id="gated", sequence=3, generation=2)
    gated = await session.scalar(
        select(DeviceEvent).where(
            DeviceEvent.device_id == device.id,
            DeviceEvent.event_id == "gated",
        )
    )
    assert gated is not None
    assert gated.evidence["power_w"] == "[redacted-by-data-reset]"
    assert gated.evidence["code"] == "reset-diagnostic"
