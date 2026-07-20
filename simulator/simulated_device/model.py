from __future__ import annotations

import math
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.schemas import Reading


@dataclass
class FaultState:
    offline: bool = False
    pzem_failure: bool = False
    sd_state: str = "ok"
    clock_trusted: bool = True
    authentication_failure: bool = False
    firmware_mismatch: bool = False
    ota_result: str = "success"
    response_delay_seconds: float = 0.0


@dataclass
class SimulatedDevice:
    index: int
    device_id: str = ""
    hardware_id: str = ""
    secret: str = ""
    firmware_version: str = "1.0.0"
    server_url: str = "http://127.0.0.1:8000"
    sequence: int = 0
    ack_sequence: int = 0
    boot_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    config_version: int = 1
    stored: list[Reading] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    fault: FaultState = field(default_factory=FaultState)
    replay_headers: dict[str, str] | None = None
    address_epoch: int = 0

    def __post_init__(self) -> None:
        # Deterministic simulator-only credentials make fault scenarios reproducible while
        # remaining unique per simulated identity. Production enrollment always uses secrets.
        identity = f"power-monitor-simulator/{self.index}"
        self.device_id = self.device_id or str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
        self.hardware_id = (
            self.hardware_id
            or f"esp32s3-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
        )
        self.secret = self.secret or hashlib.sha384(identity.encode()).hexdigest()

    def measurement(self, instant: datetime | None = None) -> dict[str, Any]:
        instant = instant or datetime.now(UTC)
        phase = (instant.timestamp() / 300 + self.index) % (2 * math.pi)
        watts = Decimal(str(round(850 + self.index * 17 + 420 * math.sin(phase), 3)))
        watts = max(Decimal("25"), watts)
        voltage = Decimal(str(round(120 + math.sin(phase / 3) * 1.7, 3)))
        current = watts / voltage
        return {
            "measured_at": instant,
            "voltage_v": voltage,
            "current_a": current,
            "power_w": watts,
            "power_factor": Decimal("0.96"),
            "frequency_hz": Decimal("60.0"),
            "energy_wh": sum(
                (item.interval_energy_wh or Decimal("0") for item in self.stored),
                Decimal("0"),
            ),
        }

    def generate_reading(
        self, *, instant: datetime | None = None, sequence: int | None = None
    ) -> Reading:
        end = instant or datetime.now(UTC)
        start = end - timedelta(seconds=60)
        live = self.measurement(end)
        self.sequence = sequence if sequence is not None else self.sequence + 1
        energy_wh = Decimal(str(live["power_w"])) / Decimal("60")
        cumulative = sum(
            (item.interval_energy_wh or Decimal("0") for item in self.stored),
            Decimal("0"),
        )
        reading = Reading(
            sequence=self.sequence,
            boot_id=self.boot_id,
            interval_start=start,
            interval_end=end,
            time_trusted=self.fault.clock_trusted,
            voltage_avg=live["voltage_v"],
            voltage_min=Decimal(str(live["voltage_v"])) - Decimal("0.2"),
            voltage_max=Decimal(str(live["voltage_v"])) + Decimal("0.2"),
            current_avg=live["current_a"],
            current_min=Decimal(str(live["current_a"])) * Decimal("0.98"),
            current_max=Decimal(str(live["current_a"])) * Decimal("1.02"),
            power_avg=live["power_w"],
            power_min=Decimal(str(live["power_w"])) * Decimal("0.97"),
            power_max=Decimal(str(live["power_w"])) * Decimal("1.03"),
            power_factor=live["power_factor"],
            frequency_hz=live["frequency_hz"],
            pzem_energy_start_wh=cumulative,
            pzem_energy_end_wh=cumulative + energy_wh,
            device_lifetime_energy_wh=cumulative + energy_wh,
            interval_energy_wh=energy_wh,
            energy_method="pzem-counter-delta",
            ct_rating_amps=Decimal("100"),
            quality_flags=[] if self.fault.clock_trusted else ["time_untrusted"],
            firmware_version=self.firmware_version,
        )
        self.stored.append(reading)
        return reading

    def inject_gap(self) -> Reading:
        self.sequence += 1
        return self.generate_reading()

    def fill_gap(self, sequence: int, instant: datetime | None = None) -> Reading:
        current = self.sequence
        reading = self.generate_reading(instant=instant, sequence=sequence)
        self.sequence = max(current, self.sequence)
        return reading

    def reset_counter(self) -> Reading:
        reading = self.generate_reading()
        reading.quality_flags.append("counter_reset")
        reading.pzem_energy_start_wh = Decimal("9999")
        reading.pzem_energy_end_wh = Decimal("1")
        return reading
