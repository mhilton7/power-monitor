from __future__ import annotations

import httpx
import pytest
from simulator.simulated_device.api import create_device_app
from simulator.simulated_device.model import SimulatedDevice

from app.security.protocol import sign_headers


@pytest.mark.asyncio
async def test_simulated_device_contract_and_replay() -> None:
    device = SimulatedDevice(index=1)
    device.generate_reading()
    app = create_device_app(device)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://device"
    ) as client:
        path = "/api/v1/readings?after_sequence=0&limit=500"
        headers = sign_headers(
            secret=device.secret.encode(),
            device_id=device.device_id,
            direction="server-to-device",
            method="GET",
            target=path,
        )
        response = await client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.json()["readings"][0]["sequence"] == 1
        replay = await client.get(path, headers=headers)
        assert replay.status_code == 401
        mismatch = dict(headers)
        mismatch["X-PM-Nonce"] = "f" * 32
        mismatch["X-PM-Protocol"] = "pm-protocol/2.0.0"
        bad = await client.get(path, headers=mismatch)
        assert bad.status_code == 426
