from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BillingCycle,
    Device,
    DeviceEvent,
    NormalizedInterval,
    RawReading,
    SequenceGap,
    SyncCursor,
)
from app.schemas import (
    Reading,
    ReadingBatchResponse,
    RejectedReading,
    UnavailableSequenceRange,
)

logger = structlog.get_logger(__name__)


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
    # PZEM cumulative energy is exposed at whole-Wh resolution. A stable
    # counter therefore does not mean a low-power interval consumed zero
    # energy. Firmware keeps using its monotonic-time power integral while it
    # reconciles a later coarse counter step; using that whole-Wh step again
    # here would double-count the preceding sub-Wh intervals.
    use_power_integration = (
        power_energy is not None
        and power_energy >= Decimal("0")
        and (
            reading.energy_method == "power_integration"
            or (counter_energy == Decimal("0") and power_energy > Decimal("0"))
        )
    )
    server_energy = (
        power_energy
        if use_power_integration
        else counter_energy
        if counter_energy is not None
        else power_energy
    )
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
    sequences = [
        int(value)
        for value in await session.scalars(
            select(RawReading.sequence)
            .where(RawReading.device_id == device_id)
            .where(RawReading.sequence > cursor.highest_contiguous_sequence)
            .order_by(RawReading.sequence)
        )
    ]
    permanent_ranges = list(
        await session.scalars(
            select(SequenceGap)
            .where(
                SequenceGap.device_id == device_id,
                SequenceGap.permanent_loss.is_(True),
                SequenceGap.resolved_at.is_not(None),
                SequenceGap.end_sequence > cursor.highest_contiguous_sequence,
            )
            .order_by(SequenceGap.start_sequence)
        )
    )
    coverage = [(sequence, sequence) for sequence in sequences]
    coverage.extend((gap.start_sequence, gap.end_sequence) for gap in permanent_ranges)
    coverage.sort()
    expected = cursor.highest_contiguous_sequence + 1
    for start_sequence, end_sequence in coverage:
        if end_sequence < expected:
            continue
        if start_sequence > expected:
            break
        expected = max(expected, end_sequence + 1)
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
    maximum_seen = cursor.maximum_seen_sequence
    expected = cursor.highest_contiguous_sequence + 1
    for start_sequence, end_sequence in coverage:
        if expected > maximum_seen:
            break
        if end_sequence < expected:
            continue
        if start_sequence > expected:
            session.add(
                SequenceGap(
                    device_id=device_id,
                    start_sequence=expected,
                    end_sequence=min(start_sequence - 1, maximum_seen),
                    detected_at=now,
                )
            )
        expected = max(expected, end_sequence + 1)
    return cursor


async def _record_permanent_loss_ranges(
    session: AsyncSession,
    *,
    device_id: str,
    ranges: list[UnavailableSequenceRange],
    now: datetime,
) -> None:
    if not ranges:
        return
    declared_sequences = {
        sequence
        for unavailable in ranges
        for sequence in range(
            unavailable.start_sequence,
            unavailable.end_sequence + 1,
        )
    }
    existing_readings = {
        int(value)
        for value in await session.scalars(
            select(RawReading.sequence).where(
                RawReading.device_id == device_id,
                RawReading.sequence.in_(declared_sequences),
            )
        )
    }
    unavailable_sequences = sorted(declared_sequences - existing_readings)
    if not unavailable_sequences:
        return
    existing_gaps = list(
        await session.scalars(select(SequenceGap).where(SequenceGap.device_id == device_id))
    )
    gaps_by_bounds = {(gap.start_sequence, gap.end_sequence): gap for gap in existing_gaps}
    segments: list[tuple[int, int]] = []
    start = previous = unavailable_sequences[0]
    for sequence in unavailable_sequences[1:]:
        if sequence == previous + 1:
            previous = sequence
            continue
        segments.append((start, previous))
        start = previous = sequence
    segments.append((start, previous))
    for start_sequence, end_sequence in segments:
        gap = gaps_by_bounds.get((start_sequence, end_sequence))
        if gap is None:
            gap = SequenceGap(
                device_id=device_id,
                start_sequence=start_sequence,
                end_sequence=end_sequence,
                detected_at=now,
            )
            session.add(gap)
        gap.resolved_at = now
        gap.permanent_loss = True
        logger.warning(
            "READING_RANGE_PERMANENTLY_UNAVAILABLE",
            device_id=device_id,
            start_sequence=start_sequence,
            end_sequence=end_sequence,
            retained_without_trusted_time=True,
        )


async def ingest_readings(
    session: AsyncSession,
    *,
    device_id: str,
    readings: list[Reading],
    source: str,
    first_available_sequence: int | None = None,
    unavailable_sequence_ranges: list[UnavailableSequenceRange] | None = None,
    maximum_clock_skew_seconds: int = 300,
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
    accepted_windows: list[tuple[datetime, datetime]] = []
    logger.info(
        "sensor.reading_batch_started",
        device_id=device_id,
        source=source,
        record_count=len(readings),
        first_sequence=min((item.sequence for item in readings), default=None),
        last_sequence=max((item.sequence for item in readings), default=None),
    )
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
    minimum_existing_sequence = await session.scalar(
        select(func.min(RawReading.sequence)).where(RawReading.device_id == device_id)
    )
    first_incoming = min((reading.sequence for reading in readings), default=None)
    if (
        cursor.highest_contiguous_sequence == 0
        and first_incoming is not None
        and first_incoming > 1
        and first_available_sequence == first_incoming
        and (minimum_existing_sequence is None or int(minimum_existing_sequence) == first_incoming)
    ):
        cursor.highest_contiguous_sequence = first_incoming - 1
        cursor.maximum_seen_sequence = first_incoming - 1
        cursor.updated_at = now
        unavailable_gap = await session.scalar(
            select(SequenceGap).where(
                SequenceGap.device_id == device_id,
                SequenceGap.start_sequence == 1,
                SequenceGap.end_sequence == first_incoming - 1,
            )
        )
        if unavailable_gap is None:
            unavailable_gap = SequenceGap(
                device_id=device_id,
                start_sequence=1,
                end_sequence=first_incoming - 1,
                detected_at=now,
            )
            session.add(unavailable_gap)
        unavailable_gap.resolved_at = now
        unavailable_gap.permanent_loss = True
        logger.warning(
            "READING_CURSOR_BOOTSTRAPPED",
            device_id=device_id,
            first_available_sequence=first_incoming,
            recovery_mode=(
                "existing_rows" if minimum_existing_sequence is not None else "first_ingestion"
            ),
            unavailable_start_sequence=1,
            unavailable_end_sequence=first_incoming - 1,
        )
    await _record_permanent_loss_ranges(
        session,
        device_id=device_id,
        ranges=unavailable_sequence_ranges or [],
        now=now,
    )
    for reading in readings:
        if reading.interval_end > now + timedelta(seconds=maximum_clock_skew_seconds):
            rejected.append(
                RejectedReading(
                    sequence=reading.sequence,
                    code="measurement_timestamp_in_future",
                    detail="Reading timestamp exceeds the permitted device clock skew",
                )
            )
            logger.warning(
                "READING_BATCH_REJECTED",
                device_id=device_id,
                sequence=reading.sequence,
                measured_at=reading.interval_end,
                reason="measurement_timestamp_in_future",
            )
            logger.warning(
                "server.reading_rejected",
                device_id=device_id,
                sequence=reading.sequence,
                measured_at=reading.interval_end,
                rejection_code="measurement_timestamp_in_future",
            )
            continue
        incoming_hash = reading.record_hash or reading_content_hash(reading)
        existing = await session.scalar(
            select(RawReading).where(
                RawReading.device_id == device_id, RawReading.sequence == reading.sequence
            )
        )
        if existing is not None:
            if existing.record_hash == incoming_hash:
                duplicates.append(reading.sequence)
                logger.info(
                    "READING_BATCH_DUPLICATE",
                    device_id=device_id,
                    sequence=reading.sequence,
                    measured_at=reading.interval_end,
                )
                logger.info(
                    "server.reading_duplicate",
                    device_id=device_id,
                    sequence=reading.sequence,
                    measured_at=reading.interval_end,
                )
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
                logger.warning(
                    "READING_BATCH_REJECTED",
                    device_id=device_id,
                    sequence=reading.sequence,
                    measured_at=reading.interval_end,
                    reason="conflicting_duplicate",
                )
                logger.warning(
                    "server.reading_rejected",
                    device_id=device_id,
                    sequence=reading.sequence,
                    measured_at=reading.interval_end,
                    rejection_code="conflicting_duplicate",
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
        accepted_windows.append((reading.interval_start, reading.interval_end))
        logger.info(
            "READING_BATCH_ACCEPTED",
            device_id=device_id,
            site_id=device.site_id,
            sequence=reading.sequence,
            measured_at=reading.interval_end,
            source=source,
        )
        logger.info(
            "LATEST_MEASUREMENT_UPDATED",
            device_id=device_id,
            site_id=device.site_id,
            sequence=reading.sequence,
            measured_at=reading.interval_end,
            source="committed_reading",
        )
        logger.info(
            "server.reading_committed",
            device_id=device_id,
            site_id=device.site_id,
            sequence=reading.sequence,
            measured_at=reading.interval_end,
            power_watts=reading.power_avg,
            interval_energy_wh=selected.selected_energy_wh,
            energy_source=selected.selected_method,
        )
    if accepted_windows and device.utility_account_id:
        earliest = min(value[0] for value in accepted_windows)
        latest = max(value[1] for value in accepted_windows)
        affected_cycles = list(
            await session.scalars(
                select(BillingCycle)
                .where(
                    BillingCycle.utility_account_id == device.utility_account_id,
                    BillingCycle.finalized_at.is_(None),
                    BillingCycle.starts_at < latest,
                    BillingCycle.ends_at > earliest,
                )
                .with_for_update()
            )
        )
        for cycle in affected_cycles:
            cycle.status = "recalculating"
            cycle.updated_at = now
    cursor = await _recalculate_cursor(session, device_id, now)
    await session.flush()
    gaps = list(
        await session.scalars(
            select(SequenceGap)
            .where(SequenceGap.device_id == device_id, SequenceGap.resolved_at.is_(None))
            .order_by(SequenceGap.start_sequence)
        )
    )
    logger.info(
        "sensor.reading_batch_accepted",
        device_id=device_id,
        source=source,
        committed_count=len(accepted),
        duplicate_count=len(duplicates),
        rejected_count=len(rejected),
        acknowledged_sequence=cursor.highest_contiguous_sequence,
        gap_count=len(gaps),
    )
    if gaps:
        logger.warning(
            "server.sequence_gap_detected",
            device_id=device_id,
            gap_ranges=[(gap.start_sequence, gap.end_sequence) for gap in gaps],
        )
    return ReadingBatchResponse(
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        highest_contiguous_accepted_sequence=cursor.highest_contiguous_sequence,
        missing_ranges=[(gap.start_sequence, gap.end_sequence) for gap in gaps],
    )
