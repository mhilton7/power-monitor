from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from worker.app.tasks import reconcile_missing_normalized_intervals

from app.db.models import (
    Device,
    DeviceEvent,
    NormalizedInterval,
    RawReading,
    SequenceGap,
    Site,
)
from app.ingestion.service import ingest_readings, normalize_energy
from app.schemas import Reading, ReadingBatch, UnavailableSequenceRange


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


def test_low_power_interval_uses_decimal_power_when_meter_counter_is_stable() -> None:
    end = datetime(2026, 7, 20, 12, 0, 5, tzinfo=UTC)
    low_power = Reading(
        sequence=1,
        boot_id="123e4567-e89b-12d3-a456-426614174000",
        interval_start=end - timedelta(seconds=5),
        interval_end=end,
        time_trusted=True,
        voltage_avg=Decimal("120.4"),
        current_avg=Decimal("0.01"),
        power_avg=Decimal("1"),
        power_factor=Decimal("0.83"),
        frequency_hz=Decimal("60"),
        pzem_energy_start_wh=Decimal("100"),
        pzem_energy_end_wh=Decimal("100"),
        interval_energy_wh=None,
        energy_method="counter",
        ct_rating_amps=Decimal("100"),
        quality_flags=[],
        firmware_version="1.0.0",
    )

    result = normalize_energy(low_power)

    assert result.selected_method == "server-derived"
    assert result.validation_result == "accepted"
    assert result.selected_energy_wh is not None
    assert abs(result.selected_energy_wh - Decimal("0.001388888888888888888888888889")) < Decimal(
        "0.000000000000000000000000001"
    )

    # The subsequent whole-Wh register step represents energy already
    # retained by earlier sub-Wh intervals. Firmware marks the reconciled
    # interval as power integration so the server must not count that step
    # again.
    reconciled_counter_step = low_power.model_copy(
        update={
            "sequence": 2,
            "pzem_energy_end_wh": Decimal("101"),
            "interval_energy_wh": Decimal("0.001388888888888888888888888889"),
            "energy_method": "power_integration",
        }
    )
    reconciled = normalize_energy(reconciled_counter_step)
    assert reconciled.selected_method == "device-reported"
    assert reconciled.validation_result == "accepted"
    assert reconciled.selected_energy_wh == Decimal("0.001388888888888888888888888889")
    assert result.selected_energy_wh + reconciled.selected_energy_wh < Decimal("0.003")


@pytest.mark.asyncio
async def test_worker_reconciles_orphan_raw_readings_without_bad_row_blocking(
    session: AsyncSession,
) -> None:
    site = Site(name="Normalization repair", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    device = Device(site_id=site.id, hardware_id="hw-normalization-repair", name="Repair")
    session.add(device)
    await session.commit()
    await ingest_readings(
        session,
        device_id=device.id,
        readings=[reading(1), reading(2)],
        source="push",
    )
    await session.commit()
    raws = list(
        await session.scalars(
            select(RawReading)
            .where(RawReading.device_id == device.id)
            .order_by(RawReading.sequence)
        )
    )
    await session.execute(
        delete(NormalizedInterval).where(
            NormalizedInterval.raw_reading_id.in_([item.id for item in raws])
        )
    )
    raws[0].original_payload = {"sequence": "invalid"}
    await session.commit()

    result = await reconcile_missing_normalized_intervals(session)
    await session.commit()

    assert result == {"queued": 2, "completed": 1, "failed": 1}
    assert (
        int(
            await session.scalar(
                select(func.count())
                .select_from(NormalizedInterval)
                .where(NormalizedInterval.device_id == device.id)
            )
            or 0
        )
        == 1
    )
    recovered = await session.scalar(
        select(NormalizedInterval).where(NormalizedInterval.raw_reading_id == raws[1].id)
    )
    assert recovered is not None


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


@pytest.mark.asyncio
async def test_first_retained_sequence_bootstraps_new_server_cursor(
    session: AsyncSession,
) -> None:
    site = Site(name="Retained history", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    device = Device(site_id=site.id, hardware_id="hw-retained", name="Retained")
    session.add(device)
    await session.commit()

    result = await ingest_readings(
        session,
        device_id=device.id,
        readings=[reading(46), reading(47)],
        source="push",
        first_available_sequence=46,
    )
    await session.commit()

    assert result.accepted == [46, 47]
    assert result.highest_contiguous_accepted_sequence == 47
    assert result.missing_ranges == []
    permanent = await session.scalar(
        select(SequenceGap).where(
            SequenceGap.device_id == device.id,
            SequenceGap.permanent_loss.is_(True),
        )
    )
    assert permanent is not None
    assert (permanent.start_sequence, permanent.end_sequence) == (1, 45)
    assert permanent.resolved_at is not None


@pytest.mark.asyncio
async def test_retained_sequence_bootstrap_repairs_preupgrade_stalled_cursor(
    session: AsyncSession,
) -> None:
    site = Site(name="Pre-upgrade retained history", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    device = Device(site_id=site.id, hardware_id="hw-retained-upgrade", name="Retained upgrade")
    session.add(device)
    await session.commit()

    before_upgrade = await ingest_readings(
        session,
        device_id=device.id,
        readings=[reading(46), reading(47)],
        source="push",
    )
    await session.commit()
    assert before_upgrade.accepted == [46, 47]
    assert before_upgrade.highest_contiguous_accepted_sequence == 0
    assert before_upgrade.missing_ranges == [(1, 45)]

    repaired = await ingest_readings(
        session,
        device_id=device.id,
        readings=[reading(46), reading(47)],
        source="push",
        first_available_sequence=46,
    )
    await session.commit()

    assert repaired.duplicates == [46, 47]
    assert repaired.highest_contiguous_accepted_sequence == 47
    assert repaired.missing_ranges == []
    permanent = await session.scalar(
        select(SequenceGap).where(
            SequenceGap.device_id == device.id,
            SequenceGap.permanent_loss.is_(True),
        )
    )
    assert permanent is not None
    assert (permanent.start_sequence, permanent.end_sequence) == (1, 45)
    assert permanent.resolved_at is not None


@pytest.mark.asyncio
async def test_unsyncable_retained_prefix_bootstraps_from_first_syncable_sequence(
    session: AsyncSession,
) -> None:
    site = Site(name="Unsyncable retained prefix", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    device = Device(site_id=site.id, hardware_id="hw-unsyncable-prefix", name="Prefix")
    session.add(device)
    await session.commit()

    result = await ingest_readings(
        session,
        device_id=device.id,
        readings=[reading(46), reading(47)],
        source="push",
        first_available_sequence=46,
    )
    await session.commit()

    assert result.accepted == [46, 47]
    assert result.highest_contiguous_accepted_sequence == 47
    assert result.missing_ranges == []
    permanent = await session.scalar(
        select(SequenceGap).where(
            SequenceGap.device_id == device.id,
            SequenceGap.permanent_loss.is_(True),
        )
    )
    assert permanent is not None
    assert (permanent.start_sequence, permanent.end_sequence) == (1, 45)


@pytest.mark.asyncio
async def test_cursor_does_not_skip_unverified_missing_sequences(
    session: AsyncSession,
) -> None:
    site = Site(name="Gap protected", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    device = Device(site_id=site.id, hardware_id="hw-gap-protected", name="Protected")
    session.add(device)
    await session.commit()

    result = await ingest_readings(
        session,
        device_id=device.id,
        readings=[reading(46)],
        source="push",
        first_available_sequence=45,
    )
    await session.commit()

    assert result.highest_contiguous_accepted_sequence == 0
    assert result.missing_ranges == [(1, 45)]


@pytest.mark.asyncio
async def test_signed_unavailable_ranges_advance_across_unsyncable_retained_records(
    session: AsyncSession,
) -> None:
    site = Site(name="Interspersed unsyncable history", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    device = Device(site_id=site.id, hardware_id="hw-interspersed", name="Interspersed")
    session.add(device)
    await session.commit()

    result = await ingest_readings(
        session,
        device_id=device.id,
        readings=[reading(9), reading(12), reading(13), reading(16)],
        source="push",
        unavailable_sequence_ranges=[
            UnavailableSequenceRange(start_sequence=1, end_sequence=8),
            UnavailableSequenceRange(start_sequence=10, end_sequence=11),
            UnavailableSequenceRange(start_sequence=14, end_sequence=15),
        ],
    )
    await session.commit()

    assert result.accepted == [9, 12, 13, 16]
    assert result.highest_contiguous_accepted_sequence == 16
    assert result.missing_ranges == []
    permanent = list(
        await session.scalars(
            select(SequenceGap)
            .where(
                SequenceGap.device_id == device.id,
                SequenceGap.permanent_loss.is_(True),
            )
            .order_by(SequenceGap.start_sequence)
        )
    )
    assert [(gap.start_sequence, gap.end_sequence) for gap in permanent] == [
        (1, 8),
        (10, 11),
        (14, 15),
    ]


def test_reading_batch_rejects_untrusted_or_ambiguous_sequence_coverage() -> None:
    with pytest.raises(ValueError, match="ordered and non-overlapping"):
        ReadingBatch(
            protocol_version="pm-protocol/1.0.0",
            device_id="sensor",
            readings=[reading(9)],
            unavailable_sequence_ranges=[
                {"start_sequence": 2, "end_sequence": 4},
                {"start_sequence": 4, "end_sequence": 5},
            ],
        )
    with pytest.raises(ValueError, match="cannot also be declared unavailable"):
        ReadingBatch(
            protocol_version="pm-protocol/1.0.0",
            device_id="sensor",
            readings=[reading(9)],
            unavailable_sequence_ranges=[
                {"start_sequence": 1, "end_sequence": 9},
            ],
        )
    with pytest.raises(ValueError, match="must contain readings or unavailable"):
        ReadingBatch(
            protocol_version="pm-protocol/1.0.0",
            device_id="sensor",
            readings=[],
        )


@pytest.mark.asyncio
async def test_unavailable_only_batch_advances_without_fabricating_readings(
    session: AsyncSession,
) -> None:
    site = Site(name="Unavailable-only history", timezone="America/Los_Angeles")
    session.add(site)
    await session.flush()
    device = Device(site_id=site.id, hardware_id="hw-unavailable-only", name="Unavailable only")
    session.add(device)
    await session.commit()

    result = await ingest_readings(
        session,
        device_id=device.id,
        readings=[],
        source="push",
        unavailable_sequence_ranges=[
            UnavailableSequenceRange(start_sequence=1, end_sequence=24),
        ],
    )
    await session.commit()

    assert result.accepted == []
    assert result.highest_contiguous_accepted_sequence == 24
    assert result.missing_ranges == []
