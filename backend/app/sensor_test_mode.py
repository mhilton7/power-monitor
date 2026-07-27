from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.schemas import SensorTestModeSensorUpdate, SensorTestModeUpdate, SensorTestModeWrite

_ZERO = Decimal("0")
_MILLISECOND = Decimal("0.001")
_WATT_HOURS_PER_KWH = Decimal("3600000")
_MAX_HISTORY_POINTS = 32 * 24 * 60


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _decimal(value: float | int | str | Decimal) -> Decimal:
    return Decimal(str(value))


@dataclass
class TestSensor:
    id: str
    index: int
    name: str
    offline: bool = False
    load_override_w: Decimal | None = None
    current_power_w: Decimal = _ZERO
    energy_kwh: Decimal = _ZERO


@dataclass
class TestPoint:
    recorded_at: datetime
    sensor_id: str
    sensor_name: str
    online: bool
    power_w: Decimal
    interval_energy_kwh: Decimal


@dataclass
class TestSession:
    id: str
    site_id: str | None
    started_at: datetime
    expires_at: datetime | None
    sensor_count: int
    load_profile: str
    offline_sensor_indexes: set[int]
    custom_load_w: Decimal | None
    base_load_w: Decimal
    variation_percent: Decimal
    sample_interval_seconds: int
    cost_preview_enabled: bool
    paused: bool
    sensors: list[TestSensor] = field(default_factory=list)
    history: list[TestPoint] = field(default_factory=list)
    last_sample_at: datetime | None = None


class SensorTestModeManager:
    """Process-local synthetic readings that never enter production data tables."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._session: TestSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._operation_results: dict[str, dict[str, Any]] = {}
        self._ended_at: datetime | None = None
        self._end_reason: str | None = None
        self._pending_expiry_audit: dict[str, Any] | None = None

    async def shutdown(self) -> None:
        async with self._lock:
            self._session = None
            task = self._task
            self._task = None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._operation_results.clear()
        self._pending_expiry_audit = None

    async def enable(self, payload: SensorTestModeWrite) -> dict[str, Any]:
        async with self._lock:
            cached = self._operation_results.get(payload.idempotency_key)
            if cached is not None:
                return cached
            now = _utc_now()
            if self._session is not None and (
                self._session.expires_at is None or self._session.expires_at > now
            ):
                result = self._state_locked(now)
                self._remember(payload.idempotency_key, result)
                return result
            session_id = str(uuid4())
            self._ended_at = None
            self._end_reason = None
            self._pending_expiry_audit = None
            session = TestSession(
                id=session_id,
                site_id=payload.site_id,
                started_at=now,
                expires_at=(
                    now + timedelta(minutes=payload.expires_in_minutes)
                    if payload.expires_in_minutes is not None
                    else None
                ),
                sensor_count=payload.sensor_count,
                load_profile=payload.load_profile,
                offline_sensor_indexes=set(payload.offline_sensor_indexes),
                custom_load_w=payload.custom_load_w,
                base_load_w=payload.base_load_w,
                variation_percent=payload.variation_percent,
                sample_interval_seconds=payload.sample_interval_seconds,
                cost_preview_enabled=payload.cost_preview_enabled,
                paused=payload.paused,
            )
            self._reconcile_sensors(session)
            self._session = session
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run(), name="sensor-test-mode")
            self._sample_locked(now)
            result = self._state_locked(now)
            self._remember(payload.idempotency_key, result)
            return result

    async def update(self, payload: SensorTestModeUpdate) -> dict[str, Any]:
        async with self._lock:
            cached = self._operation_results.get(payload.idempotency_key)
            if cached is not None:
                return cached
            session = self._active_session_locked()
            sensor_count = (
                payload.sensor_count if payload.sensor_count is not None else session.sensor_count
            )
            offline_sensor_indexes = (
                set(payload.offline_sensor_indexes)
                if payload.offline_sensor_indexes is not None
                else {index for index in session.offline_sensor_indexes if index <= sensor_count}
            )
            if any(index < 1 or index > sensor_count for index in offline_sensor_indexes):
                raise ValueError("offline sensor indexes must refer to configured sensors")
            load_profile = payload.load_profile or session.load_profile
            custom_load_w = (
                payload.custom_load_w
                if payload.custom_load_w is not None
                else session.custom_load_w
            )
            if load_profile == "custom" and custom_load_w is None:
                raise ValueError("custom load profile requires custom_load_w")

            if payload.sensor_count is not None:
                session.sensor_count = sensor_count
            if payload.load_profile is not None:
                session.load_profile = load_profile
            if payload.sensor_count is not None or payload.offline_sensor_indexes is not None:
                session.offline_sensor_indexes = offline_sensor_indexes
            if payload.custom_load_w is not None:
                session.custom_load_w = custom_load_w
            if payload.base_load_w is not None:
                session.base_load_w = payload.base_load_w
            if payload.variation_percent is not None:
                session.variation_percent = payload.variation_percent
            if payload.sample_interval_seconds is not None:
                session.sample_interval_seconds = payload.sample_interval_seconds
            if "expires_in_minutes" in payload.model_fields_set:
                session.expires_at = (
                    _utc_now() + timedelta(minutes=payload.expires_in_minutes)
                    if payload.expires_in_minutes is not None
                    else None
                )
            if payload.cost_preview_enabled is not None:
                session.cost_preview_enabled = payload.cost_preview_enabled
            if payload.paused is not None:
                session.paused = payload.paused
            self._reconcile_sensors(session)
            self._sample_locked(_utc_now())
            result = self._state_locked()
            self._remember(payload.idempotency_key, result)
            return result

    async def update_sensor(
        self,
        sensor_id: str,
        payload: SensorTestModeSensorUpdate,
    ) -> dict[str, Any]:
        async with self._lock:
            cached = self._operation_results.get(payload.idempotency_key)
            if cached is not None:
                return cached
            session = self._active_session_locked()
            sensor = next((item for item in session.sensors if item.id == sensor_id), None)
            if sensor is None:
                raise LookupError("simulated sensor does not exist")
            if payload.offline is not None:
                sensor.offline = payload.offline
                if payload.offline:
                    session.offline_sensor_indexes.add(sensor.index)
                else:
                    session.offline_sensor_indexes.discard(sensor.index)
            if payload.load_override_w is not None:
                sensor.load_override_w = payload.load_override_w
            if payload.name is not None:
                sensor.name = payload.name
            self._sample_locked(_utc_now())
            result = self._sensor_dict(sensor)
            self._remember(payload.idempotency_key, result)
            return result

    async def disable(self, idempotency_key: str) -> dict[str, Any]:
        async with self._lock:
            cached = self._operation_results.get(idempotency_key)
            if cached is not None:
                return cached
            self._session = None
            self._ended_at = _utc_now()
            self._end_reason = "disabled"
            task = self._task
            self._task = None
            result = self._disabled_state()
            self._remember(idempotency_key, result)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return result

    async def reset(self, idempotency_key: str) -> dict[str, Any]:
        async with self._lock:
            cached = self._operation_results.get(idempotency_key)
            if cached is not None:
                return cached
            session = self._active_session_locked()
            session.history.clear()
            session.last_sample_at = None
            for sensor in session.sensors:
                sensor.current_power_w = _ZERO
                sensor.energy_kwh = _ZERO
            result = self._state_locked()
            self._remember(idempotency_key, result)
            return result

    async def state(self) -> dict[str, Any]:
        async with self._lock:
            self._expire_locked()
            return self._state_locked()

    async def consume_expiry_audit(self) -> dict[str, Any] | None:
        async with self._lock:
            value = self._pending_expiry_audit
            self._pending_expiry_audit = None
            return value

    async def sensors(self) -> list[dict[str, Any]]:
        async with self._lock:
            session = self._active_session_locked()
            return [self._sensor_dict(sensor) for sensor in session.sensors]

    async def history(
        self,
        *,
        sensor_id: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            session = self._active_session_locked()
            values = session.history
            if sensor_id is not None:
                if not any(sensor.id == sensor_id for sensor in session.sensors):
                    raise LookupError("simulated sensor does not exist")
                values = [point for point in values if point.sensor_id == sensor_id]
            return [self._point_dict(point) for point in values[-limit:]]

    async def snapshot_for_cost(self) -> tuple[datetime, datetime, Decimal, str | None] | None:
        async with self._lock:
            self._expire_locked()
            session = self._session
            if session is None or not session.cost_preview_enabled:
                return None
            return (
                session.started_at,
                max(_utc_now(), session.started_at + timedelta(milliseconds=1)),
                sum((sensor.energy_kwh for sensor in session.sensors), _ZERO),
                session.site_id,
            )

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(1)
                async with self._lock:
                    self._expire_locked()
                    session = self._session
                    if session is None:
                        return
                    now = _utc_now()
                    if (
                        session.last_sample_at is None
                        or (now - session.last_sample_at).total_seconds()
                        >= session.sample_interval_seconds
                    ):
                        self._sample_locked(now)
        except asyncio.CancelledError:
            raise

    def _expire_locked(self) -> None:
        if (
            self._session is not None
            and self._session.expires_at is not None
            and self._session.expires_at <= _utc_now()
        ):
            session = self._session
            self._ended_at = _utc_now()
            self._end_reason = "expired"
            self._pending_expiry_audit = {
                "session_id": session.id,
                "ended_at": self._ended_at.isoformat(),
                "discarded_sensor_count": len(session.sensors),
                "discarded_history_points": len(session.history),
                "real_data_changed": False,
            }
            self._session = None

    def _active_session_locked(self) -> TestSession:
        self._expire_locked()
        if self._session is None:
            raise RuntimeError("sensor test mode is not enabled")
        return self._session

    def _reconcile_sensors(self, session: TestSession) -> None:
        existing = {sensor.index: sensor for sensor in session.sensors}
        sensors: list[TestSensor] = []
        for index in range(1, session.sensor_count + 1):
            sensor = existing.get(index)
            if sensor is None:
                sensor = TestSensor(
                    id=str(uuid5(NAMESPACE_URL, f"power-monitor:{session.id}:sensor:{index}")),
                    index=index,
                    name=f"Simulated Sensor {index}",
                )
            sensor.offline = index in session.offline_sensor_indexes
            sensors.append(sensor)
        session.sensors = sensors

    def _profile_power(self, session: TestSession, sensor: TestSensor, now: datetime) -> Decimal:
        if sensor.offline or session.paused:
            return _ZERO
        if sensor.load_override_w is not None:
            return sensor.load_override_w
        total = session.custom_load_w if session.custom_load_w is not None else session.base_load_w
        minute = now.hour * 60 + now.minute + now.second / 60
        phase = 2 * math.pi * minute / 1440
        if session.load_profile == "steady":
            total = session.base_load_w
        elif session.load_profile in {"home_cycle", "variable_household"}:
            total = _decimal(
                float(session.base_load_w)
                * (
                    0.65
                    + 0.35 * (1 + math.sin(phase - 1.2))
                    + float(session.variation_percent) / 100 * math.sin(phase * 6) ** 2
                )
            )
        elif session.load_profile in {"evening_peak", "morning_evening_peaks"}:
            evening_distance = min(abs(minute - 1170), 1440 - abs(minute - 1170))
            morning_distance = min(abs(minute - 450), 1440 - abs(minute - 450))
            morning = (
                2.2 * math.exp(-((morning_distance / 100) ** 2))
                if session.load_profile == "morning_evening_peaks"
                else 0
            )
            total = _decimal(
                float(session.base_load_w)
                * (0.55 + morning + 3.8 * math.exp(-((evening_distance / 125) ** 2)))
            )
        elif session.load_profile == "high_load":
            total = session.base_load_w * Decimal("4")
        elif session.load_profile == "low_load":
            total = session.base_load_w * Decimal("0.25")
        elif session.load_profile == "solar_day":
            daylight = max(0.0, math.sin(math.pi * (minute - 360) / 720))
            total = _decimal(max(100.0, 1800 - 2600 * daylight))
        online = max(1, session.sensor_count - len(session.offline_sensor_indexes))
        variation = Decimal("1") + Decimal(sensor.index - 1) * Decimal("0.04")
        return max(_ZERO, (total / Decimal(online)) * variation).quantize(_MILLISECOND)

    def _sample_locked(self, now: datetime) -> None:
        session = self._active_session_locked()
        previous = session.last_sample_at or now
        elapsed_seconds = max(0.0, min((now - previous).total_seconds(), 120.0))
        for sensor in session.sensors:
            power_w = self._profile_power(session, sensor, now)
            interval_energy = (power_w * _decimal(elapsed_seconds) / _WATT_HOURS_PER_KWH).quantize(
                Decimal("0.00000001")
            )
            sensor.current_power_w = power_w
            sensor.energy_kwh += interval_energy
            session.history.append(
                TestPoint(
                    recorded_at=now,
                    sensor_id=sensor.id,
                    sensor_name=sensor.name,
                    online=not sensor.offline,
                    power_w=power_w,
                    interval_energy_kwh=interval_energy,
                )
            )
        if len(session.history) > _MAX_HISTORY_POINTS:
            del session.history[: len(session.history) - _MAX_HISTORY_POINTS]
        session.last_sample_at = now

    def _state_locked(self, now: datetime | None = None) -> dict[str, Any]:
        self._expire_locked()
        session = self._session
        if session is None:
            return self._disabled_state()
        now = now or _utc_now()
        online = sum(1 for sensor in session.sensors if not sensor.offline)
        return {
            "enabled": True,
            "session_id": session.id,
            "site_id": session.site_id,
            "started_at": session.started_at,
            "expires_at": session.expires_at,
            "remaining_seconds": (
                max(0, int((session.expires_at - now).total_seconds()))
                if session.expires_at is not None
                else 0
            ),
            "sensor_count": session.sensor_count,
            "online_sensors": online,
            "offline_sensors": session.sensor_count - online,
            "load_profile": session.load_profile,
            "custom_load_w": session.custom_load_w,
            "base_load_w": session.base_load_w,
            "variation_percent": session.variation_percent,
            "sample_interval_seconds": session.sample_interval_seconds,
            "cost_preview_enabled": session.cost_preview_enabled,
            "paused": session.paused,
            "current_power_w": sum((sensor.current_power_w for sensor in session.sensors), _ZERO),
            "total_energy_kwh": sum((sensor.energy_kwh for sensor in session.sensors), _ZERO),
            "source_type": "simulated",
            "environment": "test_mode",
            "ended_at": None,
            "end_reason": None,
            "isolation": {
                "real_readings": True,
                "bills_and_finalized_costs": True,
                "exports_and_backups": True,
                "alerts": True,
                "credentials_and_firmware": True,
            },
            "cost_preview": None,
        }

    def _disabled_state(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "remaining_seconds": 0,
            "sensor_count": 0,
            "online_sensors": 0,
            "offline_sensors": 0,
            "sample_interval_seconds": 5,
            "cost_preview_enabled": False,
            "base_load_w": Decimal("1000"),
            "variation_percent": Decimal("20"),
            "paused": False,
            "current_power_w": _ZERO,
            "total_energy_kwh": _ZERO,
            "source_type": "simulated",
            "environment": "test_mode",
            "ended_at": self._ended_at,
            "end_reason": self._end_reason,
            "isolation": {
                "real_readings": True,
                "bills_and_finalized_costs": True,
                "exports_and_backups": True,
                "alerts": True,
                "credentials_and_firmware": True,
            },
            "cost_preview": None,
        }

    @staticmethod
    def _sensor_dict(sensor: TestSensor) -> dict[str, Any]:
        return {
            "id": sensor.id,
            "name": sensor.name,
            "index": sensor.index,
            "online": not sensor.offline,
            "current_power_w": sensor.current_power_w,
            "energy_kwh": sensor.energy_kwh,
            "load_override_w": sensor.load_override_w,
            "source_type": "simulated",
            "environment": "test_mode",
        }

    @staticmethod
    def _point_dict(point: TestPoint) -> dict[str, Any]:
        return {
            "recorded_at": point.recorded_at,
            "sensor_id": point.sensor_id,
            "sensor_name": point.sensor_name,
            "online": point.online,
            "power_w": point.power_w,
            "interval_energy_kwh": point.interval_energy_kwh,
            "source_type": "simulated",
            "environment": "test_mode",
        }

    def _remember(self, key: str, result: dict[str, Any]) -> None:
        if len(self._operation_results) >= 256:
            self._operation_results.pop(next(iter(self._operation_results)))
        self._operation_results[key] = result


sensor_test_mode = SensorTestModeManager()
