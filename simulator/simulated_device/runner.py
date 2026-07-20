from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
import uvicorn

from simulator.simulated_device.api import create_device_app
from simulator.simulated_device.model import SimulatedDevice
from simulator.simulated_device.push import push_once


async def run_device_server(device: SimulatedDevice, port: int) -> None:
    config = uvicorn.Config(
        create_device_app(device), host="127.0.0.1", port=port, log_level="warning"
    )
    await uvicorn.Server(config).serve()


async def clear_after(
    device: SimulatedDevice, attribute: str, value: object, seconds: int
) -> None:
    await asyncio.sleep(seconds)
    setattr(device.fault, attribute, value)


async def apply_scenario(
    devices: list[SimulatedDevice], scenario: dict[str, object]
) -> None:
    started = time.monotonic()
    clear_tasks: list[asyncio.Task[None]] = []
    raw_faults = scenario.get("faults", [])
    if not isinstance(raw_faults, list):
        return
    faults = sorted(
        (item for item in raw_faults if isinstance(item, dict)),
        key=lambda item: int(item.get("at_second", 0)),
    )
    for fault in faults:
        await asyncio.sleep(
            max(0, int(fault.get("at_second", 0)) - (time.monotonic() - started))
        )
        device_index = int(fault.get("device", -1))
        if not 0 <= device_index < len(devices):
            continue
        device = devices[device_index]
        fault_type = str(fault.get("type", ""))
        if fault_type == "offline":
            device.fault.offline = True
            clear_task = asyncio.create_task(
                clear_after(
                    device, "offline", False, int(fault.get("duration_seconds", 60))
                )
            )
            clear_tasks.append(clear_task)
        elif fault_type == "ip_change":
            device.address_epoch += 1
        elif fault_type == "pzem_failure":
            device.fault.pzem_failure = True
        elif fault_type.startswith("sd_"):
            device.fault.sd_state = fault_type.removeprefix("sd_")
        elif fault_type == "clock_untrusted":
            device.fault.clock_trusted = False
        elif fault_type == "counter_reset":
            device.reset_counter()
        elif fault_type == "duplicate" and device.stored:
            device.stored.append(device.stored[-1].model_copy(deep=True))
        elif fault_type == "gap_then_fill":
            missing = device.sequence + 1
            device.inject_gap()
            device.fill_gap(missing)
        elif fault_type == "invalid_signature":
            device.fault.authentication_failure = True
        elif fault_type == "replay":
            device.events.append({"category": "security", "action": "replay_scheduled"})
        elif fault_type == "protocol_mismatch":
            device.fault.firmware_mismatch = True
        elif fault_type == "ota_failure":
            device.fault.ota_result = "failure"
        elif fault_type == "slow_response":
            device.fault.response_delay_seconds = float(fault.get("delay_seconds", 8))
    if clear_tasks:
        await asyncio.gather(*clear_tasks)


async def run(count: int, base_port: int, server_url: str, scenario_path: Path) -> None:
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    devices = [
        SimulatedDevice(index=index, server_url=server_url) for index in range(count)
    ]
    for device in devices:
        for _ in range(int(scenario.get("initial_records", 10))):
            device.generate_reading()
    servers = [
        asyncio.create_task(run_device_server(device, base_port + index))
        for index, device in enumerate(devices)
    ]
    scenario_task = asyncio.create_task(apply_scenario(devices, scenario))
    heartbeat_seconds = int(scenario.get("heartbeat_seconds", 15))
    durable_record_seconds = int(scenario.get("durable_record_seconds", 60))
    live_update_seconds = int(scenario.get("live_update_seconds", 5))
    next_heartbeat = time.monotonic()
    next_durable_record = time.monotonic() + durable_record_seconds
    async with httpx.AsyncClient(
        base_url=server_url, verify=True, timeout=10
    ) as client:
        try:
            while True:
                current = time.monotonic()
                if current >= next_durable_record:
                    for device in devices:
                        device.generate_reading()
                    next_durable_record += durable_record_seconds
                if current >= next_heartbeat:
                    results = await asyncio.gather(
                        *(push_once(client, device) for device in devices),
                        return_exceptions=True,
                    )
                    failures = sum(isinstance(result, Exception) for result in results)
                    if failures:
                        print(
                            {
                                "simulator_push_failures": failures,
                                "devices": len(devices),
                            }
                        )
                    next_heartbeat += heartbeat_seconds
                await asyncio.sleep(
                    min(
                        live_update_seconds,
                        max(0.05, next_heartbeat - time.monotonic()),
                        max(0.05, next_durable_record - time.monotonic()),
                    )
                )
        finally:
            for task in servers:
                task.cancel()
            scenario_task.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description="Power Monitor multi-device simulator")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--base-port", type=int, default=9100)
    parser.add_argument("--server", default="https://localhost")
    parser.add_argument(
        "--scenario",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "scenarios" / "normal.json",
    )
    args = parser.parse_args()
    asyncio.run(run(args.count, args.base_port, args.server, args.scenario))


if __name__ == "__main__":
    main()
