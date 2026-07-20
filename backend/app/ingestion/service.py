from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Device,
    DeviceEvent,
    NormalizedInterval,
    RawReading,
    SequenceGap,
    SyncCursor,
)
from app.schemas import Reading, ReadingBatchResponse, RejectedReading


@dataclass(frozen=True)
class EnergySelection:
    device_energy_wh: Decimal | None
    server_energy_wh: Decimal | None
    selected_energy_wh: Decimal | None
    selected_method: str
    validation_result: str
    validation_reason: str


def reading_content_hash(reading: Reading) -> str:
    data = reading.model_dump(mode="json", exclude={"record_hash"})
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(canonical).hexdigest()


def normalize_energy(
    reading: Reading, tolerance_percent: Decimal = Decimal("10")
) -> EnergySelection:
    seconds = Decimal(str((reading.interval_end - reading.interval_start).total_seconds()))
    power_energy = (
        reading.power_avg * seconds / Decimal("3600") if reading.power_avg is not None else None
    )
    counter_energy = None
    if reading.pzem_energy_start_wh is not None and reading.pzem_energy_end_wh is not None:
        delta = reading.pzem_energy_end_wh - reading.pzem_energy_start_wh
        if delta >= 0:
            counter_energy = delta
    server_energy = counter_energy if counter_energy is not None else power_energy
    device_energy = reading.interval_energy_wh
    if device_energy is None and server_energy is None:
        return EnergySelection(None, None, None, "unknown", "unavailable", "No energy source")
    if device_energy is None:
        return EnergySelection(
            None,
            server_energy,
            server_energy,
            "server-derived",
            "accepted",
            "Device omitted interval energy",
        )
    if server_energy is None:
        return EnergySelection(
            device_energy,
            None,
            device_energy,
            "device-reported",
            "unverified",
            "No independent server estimate",
        )
    denominator = max(abs(server_energy), Decimal("0.001"))
    variance = abs(device_energy - server_energy) / denominator * Decimal("100")
    if variance <= tolerance_percent and "counter_reset" not in reading.quality_flags:
        return EnergySelection(
            device_energy,
            server_energy,
            device_energy,
            "device-reported",
            "accepted",
            f"Independent variance {variance:.3f}%",
        )
    if "time_untrusted" in reading.quality_flags or not reading.time_trusted:
        return EnergySelection(
            device_energy,
            server_energy,
            None,
            "unknown",
            "rejected",
            f"Energy mismatch {variance:.3f}% with untrusted time",
        )
    return EnergySelection(
        device_energy,
        server_energy,
        server_energy,
        "server-derived",
        "corrected",
        f"Device energy differed by {variance:.3f}%",
    )


def _to_raw(
    device_id: str, site_id: str, reading: Reading, source: str, now: datetime
) -> RawReading:
    record_hash = reading.record_hash or reading_content_hash(reading)
    return RawReading(
        device_id=device_id,
        site_id=site_id,
        sequence=reading.sequence,
        boot_id=reading.boot_id,
        interval_start=reading.interval_start,
        interval_end=reading.interval_end,
        time_trusted=reading.time_trusted,
        voltage_avg=reading.voltage_avg,
        voltage_min=reading.voltage_min,
        voltage_max=reading.voltage_max,
        current_avg=reading.current_avg,
        current_min=reading.current_min,
        current_max=reading.current_max,
        power_avg=reading.power_avg,
        power_min=reading.power_min,
        power_max=reading.power_max,
        power_factor=reading.power_factor,
        frequency_hz=reading.frequency_hz,
        pzem_energy_start_wh=reading.pzem_energy_start_wh,
        pzem_energy_end_wh=reading.pzem_energy_end_wh,
        device_lifetime_energy_wh=reading.device_lifetime_energy_wh,
        device_interval_energy_wh=reading.interval_energy_wh,
        energy_method=reading.energy_method,
        ct_rating_amps=reading.ct_rating_amps,
        quality_flags=reading.quality_flags,
        firmware_version=reading.firmware_version,
        record_hash=record_hash,
        original_payload=reading.model_dump(mode="json"),
        ingestion_source=source,
        ingested_at=now,
    )


async def _recalculate_cursor(session: AsyncSession, device_id: str, now: datetime) -> SyncCursor:
    cursor = await session.get(SyncCursor, device_id, with_for_update=True)
    if cursor is None:
        cursor = SyncCursor(
            device_id=device_id,
            highest_contiguous_sequence=0,
            maximum_seen_sequence=0,
            updated_at=now,
        )
        session.add(cursor)
        await session.flush()
    sequences = list(
        await session.scalars(
            select(RawReading.sequence)
            .where(RawReading.device_id == device_id)
            .where(RawReading.sequence > cursor.highest_contiguous_sequence)
            .order_by(RawReading.sequence)
        )
    )
    expected = cursor.highest_contiguous_sequence + 1
    for sequence in sequences:
        if sequence == expected:
            expected += 1
        elif sequence > expected:
            break
    cursor.highest_contiguous_sequence = expected - 1
    maximum = await session.scalar(
        select(func.max(RawReading.sequence)).where(RawReading.device_id == device_id)
    )
    cursor.maximum_seen_sequence = int(maximum or 0)
    cursor.updated_at = now

    await session.execute(
        delete(SequenceGap).where(
            SequenceGap.device_id == device_id, SequenceGap.resolved_at.is_(None)
        )
    )
    tail = [value for value in sequences if value > cursor.highest_contiguous_sequence]
    expected = cursor.highest_contiguous_sequence + 1
    for sequence in tail:
        if sequence > expected:
            session.add(
                SequenceGap(
                    device_id=device_id,
                    start_sequence=expected,
                    end_sequence=sequence - 1,
                    detected_at=now,
                )
            )
        expected = sequence + 1
    return cursor


async def ingest_readings(
    session: AsyncSession,
    *,
    device_id: str,
    readings: list[Reading],
    source: str,
) -> ReadingBatchResponse:
    if source not in {"pull", "push"}:
        raise ValueError("source must be pull or push")
    now = datetime.now(UTC)
    device = await session.get(Device, device_id)
    if device is None:
        raise ValueError("device must exist before reading ingestion")
    accepted: list[int] = []
    duplicates: list[int] = []
    rejected: list[RejectedReading] = []
    for reading in readings:
        incoming_hash = reading.record_hash or reading_content_hash(reading)
        existing = await session.scalar(
            select(RawReading).where(
                RawReading.device_id == device_id, RawReading.sequence == reading.sequence
            )
        )
        if existing is not None:
            if existing.record_hash == incoming_hash:
                duplicates.append(reading.sequence)
            else:
                rejected.append(
                    RejectedReading(
                        sequence=reading.sequence,
                        code="conflicting_duplicate",
                        detail="Sequence already exists with different content",
                    )
                )
                session.add(
                    DeviceEvent(
                        device_id=device_id,
                        event_id=f"conflict-{reading.sequence}-{incoming_hash[:12]}",
                        occurred_at=now,
                        received_at=now,
                        category="security",
                        severity="critical",
                        evidence={
                            "sequence": reading.sequence,
                            "stored_hash": existing.record_hash,
                            "incoming_hash": incoming_hash,
                        },
                    )
                )
            continue
        raw = _to_raw(device_id, device.site_id, reading, source, now)
        session.add(raw)
        await session.flush()
        selected = normalize_energy(reading)
        session.add(
            NormalizedInterval(
                raw_reading_id=raw.id,
                device_id=device_id,
                interval_start=reading.interval_start,
                interval_end=reading.interval_end,
                device_energy_wh=selected.device_energy_wh,
                server_energy_wh=selected.server_energy_wh,
                selected_energy_wh=selected.selected_energy_wh,
                selected_method=selected.selected_method,
                validation_result=selected.validation_result,
                validation_reason=selected.validation_reason,
            )
        )
        accepted.append(reading.sequence)
    cursor = await _recalculate_cursor(session, device_id, now)
    await session.flush()
    gaps = list(
        await session.scalars(
            select(SequenceGap)
            .where(SequenceGap.device_id == device_id, SequenceGap.resolved_at.is_(None))
            .order_by(SequenceGap.start_sequence)
        )
    )
    return ReadingBatchResponse(
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        highest_contiguous_accepted_sequence=cursor.highest_contiguous_sequence,
        missing_ranges=[(gap.start_sequence, gap.end_sequence) for gap in gaps],
    )
