from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx
from simulator.simulated_device.model import SimulatedDevice
from simulator.simulated_device.push import push_once


def load_devices(server: str, count: int, manifest: Path) -> list[SimulatedDevice]:
    raw: Any = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or len(raw) < count:
        raise ValueError(f"credential manifest must contain at least {count} devices")
    devices: list[SimulatedDevice] = []
    for index, item in enumerate(raw[:count]):
        if not isinstance(item, dict):
            raise ValueError("credential manifest entries must be objects")
        device_id = str(item.get("device_id", ""))
        secret = str(item.get("enrollment_secret", ""))
        if not device_id or len(secret) < 32:
            raise ValueError(
                "each manifest entry requires device_id and enrollment_secret"
            )
        devices.append(
            SimulatedDevice(
                index=index,
                server_url=server,
                device_id=device_id,
                secret=secret,
                hardware_id=str(item.get("hardware_id", "")),
            )
        )
    return devices


async def run(server: str, count: int, rounds: int, manifest: Path) -> None:
    devices = load_devices(server, count, manifest)
    for device in devices:
        for _ in range(180):
            device.generate_reading()
    durations: list[float] = []
    async with httpx.AsyncClient(base_url=server, timeout=30) as client:
        for _ in range(rounds):
            started = time.perf_counter()
            await asyncio.gather(*(push_once(client, device) for device in devices))
            durations.append(time.perf_counter() - started)
    print(
        {
            "devices": count,
            "rounds": rounds,
            "median_round_seconds": statistics.median(durations),
            "maximum_round_seconds": max(durations),
            "records_per_device": 180,
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--devices", type=int, default=100)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument(
        "--credentials",
        type=Path,
        required=True,
        help="JSON enrollment manifest kept outside source control",
    )
    args = parser.parse_args()
    asyncio.run(run(args.server, args.devices, args.rounds, args.credentials))
