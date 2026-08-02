from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import Settings
from app.db.models import Device, DeviceHeartbeat, RawReading

logger = structlog.get_logger(__name__)

MeasurementSource = Literal["heartbeat_live", "committed_reading"]
HeartbeatFreshnessState = Literal["never_received", "online", "offline"]
FreshnessState = Literal[
    "live",
    "waiting",
    "stale",
    "offline",
    "unavailable",
    "invalid",
    "needs_attention",
]


@dataclass(frozen=True)
class LatestMeasurement:
    device_id: str
    site_id: str
    circuit_id: str | None
    measurement_role: str
    measured_at: datetime | None
    received_at: datetime | None
    sequence: int | None
    power_watts: Decimal | None
    voltage_volts: Decimal | None
    current_amps: Decimal | None
    frequency_hz: Decimal | None
    power_factor: Decimal | None
    source: MeasurementSource | None
    freshness_state: FreshnessState
    invalid_metrics: tuple[str, ...] = ()
    validation_reason: str | None = None
    heartbeat_received_at: datetime | None = None
    heartbeat_age_seconds: int | None = None
    heartbeat_freshness: HeartbeatFreshnessState = "never_received"
    offline_after_seconds: int = 30
    previous_outage_reason: str | None = None

    @property
    def is_reporting(self) -> bool:
        return self.freshness_state == "live" and self.power_watts is not None


@dataclass(frozen=True)
class _Candidate:
    measured_at: datetime
    received_at: datetime
    sequence: int | None
    power_watts: Decimal | None
    voltage_volts: Decimal | None
    current_amps: Decimal | None
    frequency_hz: Decimal | None
    power_factor: Decimal | None
    source: MeasurementSource
    invalid_metrics: tuple[str, ...]
    validation_reason: str | None

    @property
    def valid_for_live_power(self) -> bool:
        return self.power_watts is not None and self.validation_reason is None


@dataclass(frozen=True)
class HeartbeatFreshness:
    received_at: datetime | None
    age_seconds: int | None
    state: HeartbeatFreshnessState
    offline_after_seconds: int

    @property
    def online(self) -> bool:
        return self.state == "online"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def evaluate_heartbeat_freshness(
    heartbeat: DeviceHeartbeat | None,
    settings: Settings,
    *,
    now: datetime,
) -> HeartbeatFreshness:
    """Evaluate connectivity only from a heartbeat actually received by this server."""

    received_at = _as_utc(heartbeat.received_at) if heartbeat is not None else None
    if received_at is None:
        return HeartbeatFreshness(
            received_at=None,
            age_seconds=None,
            state="never_received",
            offline_after_seconds=settings.device_offline_after_seconds,
        )
    elapsed = max(timedelta(0), now - received_at)
    age_seconds = int(elapsed.total_seconds())
    return HeartbeatFreshness(
        received_at=received_at,
        age_seconds=age_seconds,
        state=(
            "online"
            if elapsed <= timedelta(seconds=settings.device_offline_after_seconds)
            else "offline"
        ),
        offline_after_seconds=settings.device_offline_after_seconds,
    )


def previous_outage_reason_from_heartbeat(heartbeat: DeviceHeartbeat | None) -> str | None:
    """Return an allowlisted explanation only after the sensor reports evidence.

    The server cannot observe a local pre-TLS deferral while the sensor is unable to
    contact it, so absence of this additive evidence always produces ``None``.
    """

    if heartbeat is None or not isinstance(heartbeat.payload, dict):
        return None
    resources = heartbeat.payload.get("resources")
    if not isinstance(resources, dict):
        return None
    synchronization = resources.get("synchronization")
    nested = synchronization if isinstance(synchronization, dict) else {}
    reason = resources.get("last_local_deferral_reason") or nested.get("last_local_deferral_reason")
    if reason in {
        "internal_heap_fragmented",
        "internal_heap_reserve_low",
        "tls_internal_heap_fragmented",
        "PM-TLS-006",
    }:
        return "Sensor previously deferred synchronization because internal heap was fragmented."
    if reason in {"tls_stack_margin_low", "internal_stack_reserve_low"}:
        return "Sensor previously deferred synchronization to preserve task stack safety."
    return None


def _decimal(
    value: Any,
    *,
    minimum: Decimal,
    maximum: Decimal,
    metric: str,
    invalid: list[str],
) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        invalid.append(metric)
        return None
    if not parsed.is_finite() or parsed < minimum or parsed > maximum:
        invalid.append(metric)
        return None
    return parsed


def _heartbeat_candidate(
    device: Device,
    heartbeat: DeviceHeartbeat | None,
    *,
    now: datetime,
    maximum_clock_skew: timedelta,
) -> _Candidate | None:
    if heartbeat is None:
        return None
    latest = heartbeat.payload.get("latest") if isinstance(heartbeat.payload, dict) else None
    if not isinstance(latest, dict):
        return None
    measured_at = _as_utc(heartbeat.device_time)
    received_at = _as_utc(heartbeat.received_at)
    if measured_at is None or received_at is None:
        return None
    invalid: list[str] = []
    maximum_power = max(
        Decimal("1000"),
        Decimal(str(device.ct_rating_amps)) * Decimal("300"),
    )
    power = _decimal(
        latest.get("power_w"),
        minimum=Decimal("0"),
        maximum=maximum_power,
        metric="power_watts",
        invalid=invalid,
    )
    voltage = _decimal(
        latest.get("voltage_v"),
        minimum=Decimal("0"),
        maximum=Decimal("300"),
        metric="voltage_volts",
        invalid=invalid,
    )
    current = _decimal(
        latest.get("current_a"),
        minimum=Decimal("0"),
        maximum=Decimal(str(device.ct_rating_amps)),
        metric="current_amps",
        invalid=invalid,
    )
    frequency = _decimal(
        latest.get("frequency_hz"),
        minimum=Decimal("40"),
        maximum=Decimal("70"),
        metric="frequency_hz",
        invalid=invalid,
    )
    factor = _decimal(
        latest.get("power_factor"),
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        metric="power_factor",
        invalid=invalid,
    )
    reason = None
    if measured_at > now + maximum_clock_skew:
        reason = "measurement_timestamp_in_future"
    elif power is None:
        reason = "power_missing_or_invalid"
    return _Candidate(
        measured_at=measured_at,
        received_at=received_at,
        sequence=None,
        power_watts=power,
        voltage_volts=voltage,
        current_amps=current,
        frequency_hz=frequency,
        power_factor=factor,
        source="heartbeat_live",
        invalid_metrics=tuple(sorted(set(invalid))),
        validation_reason=reason,
    )


def _reading_candidate(
    device: Device,
    reading: RawReading | None,
    *,
    now: datetime,
    maximum_clock_skew: timedelta,
) -> _Candidate | None:
    if reading is None:
        return None
    measured_at = _as_utc(reading.interval_end)
    received_at = _as_utc(reading.ingested_at)
    if measured_at is None or received_at is None:
        return None
    invalid: list[str] = []
    maximum_power = max(
        Decimal("1000"),
        Decimal(str(device.ct_rating_amps)) * Decimal("300"),
    )
    power = _decimal(
        reading.power_avg,
        minimum=Decimal("0"),
        maximum=maximum_power,
        metric="power_watts",
        invalid=invalid,
    )
    voltage = _decimal(
        reading.voltage_avg,
        minimum=Decimal("0"),
        maximum=Decimal("300"),
        metric="voltage_volts",
        invalid=invalid,
    )
    current = _decimal(
        reading.current_avg,
        minimum=Decimal("0"),
        maximum=Decimal(str(device.ct_rating_amps)),
        metric="current_amps",
        invalid=invalid,
    )
    frequency = _decimal(
        reading.frequency_hz,
        minimum=Decimal("40"),
        maximum=Decimal("70"),
        metric="frequency_hz",
        invalid=invalid,
    )
    factor = _decimal(
        reading.power_factor,
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        metric="power_factor",
        invalid=invalid,
    )
    reason = None
    if reading.site_id != device.site_id:
        reason = "site_mismatch"
    elif measured_at > now + maximum_clock_skew:
        reason = "measurement_timestamp_in_future"
    elif power is None:
        reason = "power_missing_or_invalid"
    elif not reading.time_trusted:
        reason = "measurement_time_untrusted"
    return _Candidate(
        measured_at=measured_at,
        received_at=received_at,
        sequence=reading.sequence,
        power_watts=power,
        voltage_volts=voltage,
        current_amps=current,
        frequency_hz=frequency,
        power_factor=factor,
        source="committed_reading",
        invalid_metrics=tuple(sorted(set(invalid))),
        validation_reason=reason,
    )


def _measurement_for_device(
    device: Device,
    heartbeat: DeviceHeartbeat | None,
    reading: RawReading | None,
    settings: Settings,
    now: datetime,
) -> LatestMeasurement:
    maximum_clock_skew = timedelta(seconds=settings.max_device_clock_skew_seconds)
    candidates = [
        item
        for item in (
            _heartbeat_candidate(
                device,
                heartbeat,
                now=now,
                maximum_clock_skew=maximum_clock_skew,
            ),
            _reading_candidate(
                device,
                reading,
                now=now,
                maximum_clock_skew=maximum_clock_skew,
            ),
        )
        if item is not None
    ]
    valid_candidates = [item for item in candidates if item.valid_for_live_power]
    candidate = max(valid_candidates or candidates, key=lambda item: item.measured_at, default=None)
    heartbeat_freshness = evaluate_heartbeat_freshness(
        heartbeat,
        settings,
        now=now,
    )
    freshness_deadline = now - timedelta(seconds=settings.heartbeat_expectation_seconds * 4)
    heartbeat_is_recent = heartbeat_freshness.online
    previous_outage_reason = (
        previous_outage_reason_from_heartbeat(heartbeat) if heartbeat_is_recent else None
    )

    if candidate is None:
        state: FreshnessState = "waiting" if heartbeat_is_recent else "offline"
        if heartbeat is not None and not heartbeat.pzem_ok:
            state = "unavailable"
        return LatestMeasurement(
            device_id=device.id,
            site_id=device.site_id,
            circuit_id=device.circuit_id,
            measurement_role=device.measurement_role,
            measured_at=None,
            received_at=_as_utc(heartbeat.received_at) if heartbeat else None,
            sequence=None,
            power_watts=None,
            voltage_volts=None,
            current_amps=None,
            frequency_hz=None,
            power_factor=None,
            source=None,
            freshness_state=state,
            validation_reason="no_valid_measurement",
            heartbeat_received_at=heartbeat_freshness.received_at,
            heartbeat_age_seconds=heartbeat_freshness.age_seconds,
            heartbeat_freshness=heartbeat_freshness.state,
            offline_after_seconds=heartbeat_freshness.offline_after_seconds,
            previous_outage_reason=previous_outage_reason,
        )

    if not candidate.valid_for_live_power:
        state = "invalid"
    elif not heartbeat_is_recent:
        state = "offline"
    elif candidate.measured_at < freshness_deadline:
        state = "stale"
    elif heartbeat is not None and (not heartbeat.pzem_ok or not heartbeat.time_trusted):
        state = "needs_attention"
    else:
        state = "live"
    return LatestMeasurement(
        device_id=device.id,
        site_id=device.site_id,
        circuit_id=device.circuit_id,
        measurement_role=device.measurement_role,
        measured_at=candidate.measured_at,
        received_at=candidate.received_at,
        sequence=candidate.sequence,
        power_watts=candidate.power_watts,
        voltage_volts=candidate.voltage_volts,
        current_amps=candidate.current_amps,
        frequency_hz=candidate.frequency_hz,
        power_factor=candidate.power_factor,
        source=candidate.source,
        freshness_state=state,
        invalid_metrics=candidate.invalid_metrics,
        validation_reason=candidate.validation_reason,
        heartbeat_received_at=heartbeat_freshness.received_at,
        heartbeat_age_seconds=heartbeat_freshness.age_seconds,
        heartbeat_freshness=heartbeat_freshness.state,
        offline_after_seconds=heartbeat_freshness.offline_after_seconds,
        previous_outage_reason=previous_outage_reason,
    )


async def latest_heartbeats(
    session: AsyncSession, device_ids: list[str]
) -> dict[str, DeviceHeartbeat]:
    if not device_ids:
        return {}
    ranked = (
        select(
            DeviceHeartbeat,
            func.row_number()
            .over(
                partition_by=DeviceHeartbeat.device_id,
                order_by=(DeviceHeartbeat.received_at.desc(), DeviceHeartbeat.id.desc()),
            )
            .label("latest_rank"),
        )
        .where(DeviceHeartbeat.device_id.in_(device_ids))
        .subquery()
    )
    heartbeat = aliased(DeviceHeartbeat, ranked)
    rows = list(await session.scalars(select(heartbeat).where(ranked.c.latest_rank == 1)))
    return {item.device_id: item for item in rows}


async def latest_raw_readings(
    session: AsyncSession, device_ids: list[str]
) -> dict[str, RawReading]:
    if not device_ids:
        return {}
    ranked = (
        select(
            RawReading,
            func.row_number()
            .over(
                partition_by=RawReading.device_id,
                order_by=(RawReading.interval_end.desc(), RawReading.sequence.desc()),
            )
            .label("latest_rank"),
        )
        .where(RawReading.device_id.in_(device_ids))
        .subquery()
    )
    reading = aliased(RawReading, ranked)
    rows = list(await session.scalars(select(reading).where(ranked.c.latest_rank == 1)))
    return {item.device_id: item for item in rows}


async def load_latest_measurements(
    session: AsyncSession,
    devices: list[Device],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[
    dict[str, LatestMeasurement],
    dict[str, DeviceHeartbeat],
    dict[str, RawReading],
]:
    evaluated_at = _as_utc(now) or datetime.now(UTC)
    ids = [device.id for device in devices]
    heartbeats = await latest_heartbeats(session, ids)
    readings = await latest_raw_readings(session, ids)
    measurements = {
        device.id: _measurement_for_device(
            device,
            heartbeats.get(device.id),
            readings.get(device.id),
            settings,
            evaluated_at,
        )
        for device in devices
    }
    return measurements, heartbeats, readings


def log_measurement_decision(measurement: LatestMeasurement) -> None:
    event = "LIVE_MEASUREMENT_ACCEPTED" if measurement.is_reporting else "LIVE_MEASUREMENT_REJECTED"
    logger.info(
        event,
        device_id=measurement.device_id,
        site_id=measurement.site_id,
        sequence=measurement.sequence,
        measured_at=measurement.measured_at,
        source=measurement.source,
        freshness_state=measurement.freshness_state,
        reason=measurement.validation_reason,
    )
    if measurement.freshness_state == "stale":
        logger.info(
            "STALE_MEASUREMENT",
            device_id=measurement.device_id,
            site_id=measurement.site_id,
            sequence=measurement.sequence,
            measured_at=measurement.measured_at,
            source=measurement.source,
        )
    if measurement.validation_reason == "site_mismatch":
        logger.warning(
            "SITE_MISMATCH",
            device_id=measurement.device_id,
            expected_site_id=measurement.site_id,
            sequence=measurement.sequence,
        )
