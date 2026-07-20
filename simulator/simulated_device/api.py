from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from simulator.simulated_device.model import SimulatedDevice

from app.security.protocol import PROTOCOL, calculate_signature, sha256_hex


def create_device_app(device: SimulatedDevice) -> FastAPI:
    app = FastAPI(title=f"Simulated device {device.index}")
    nonces: set[str] = set()

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        if not request.url.path.startswith("/api/v1"):
            return await call_next(request)
        body = await request.body()
        headers = request.headers
        required = [
            "x-pm-protocol",
            "x-pm-device-id",
            "x-pm-timestamp",
            "x-pm-nonce",
            "x-pm-content-sha256",
            "x-pm-signature",
        ]
        if any(not headers.get(name) for name in required):
            return JSONResponse(
                {
                    "type": "about:blank",
                    "title": "Authentication required",
                    "status": 401,
                },
                401,
            )
        if (
            headers["x-pm-protocol"] != PROTOCOL
            or headers["x-pm-device-id"] != device.device_id
        ):
            return JSONResponse(
                {"type": "about:blank", "title": "Protocol mismatch", "status": 426},
                426,
            )
        nonce = headers["x-pm-nonce"]
        if nonce in nonces:
            return JSONResponse(
                {"type": "about:blank", "title": "Replay", "status": 401}, 401
            )
        if abs(int(time.time()) - int(headers["x-pm-timestamp"])) > 300:
            return JSONResponse(
                {"type": "about:blank", "title": "Stale request", "status": 401}, 401
            )
        if not hmac.compare_digest(sha256_hex(body), headers["x-pm-content-sha256"]):
            return JSONResponse(
                {"type": "about:blank", "title": "Body mismatch", "status": 401}, 401
            )
        query = request.url.query
        target = request.url.path + (f"?{query}" if query else "")
        expected = calculate_signature(
            device.secret.encode(),
            "server-to-device",
            request.method,
            target,
            headers["x-pm-timestamp"],
            nonce,
            headers["x-pm-content-sha256"],
        )
        if not hmac.compare_digest(expected, headers["x-pm-signature"]):
            return JSONResponse(
                {"type": "about:blank", "title": "Invalid signature", "status": 401},
                401,
            )
        nonces.add(nonce)
        if device.fault.response_delay_seconds:
            await asyncio.sleep(device.fault.response_delay_seconds)
        if device.fault.offline:
            return JSONResponse(
                {"type": "about:blank", "title": "Offline", "status": 503}, 503
            )
        return await call_next(request)

    @app.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL,
            "device_id": device.device_id,
            "status": "degraded"
            if device.fault.pzem_failure or device.fault.sd_state != "ok"
            else "healthy",
            "pzem": {"ok": not device.fault.pzem_failure},
            "sd": {
                "ok": device.fault.sd_state == "ok",
                "status": device.fault.sd_state,
            },
            "time": {"trusted": device.fault.clock_trusted},
            "latest_sequence": device.sequence,
        }

    @app.get("/api/v1/info")
    async def info() -> dict[str, Any]:
        return {
            "protocol_version": "pm-protocol/9.0.0"
            if device.fault.firmware_mismatch
            else PROTOCOL,
            "device_id": device.device_id,
            "hardware_id": device.hardware_id,
            "hardware_target": "esp32-s3-pzem004t-v4",
            "firmware_version": device.firmware_version,
        }

    @app.get("/api/v1/live")
    async def live() -> dict[str, Any]:
        return device.measurement()

    @app.get("/api/v1/readings")
    async def readings(after_sequence: int = 0, limit: int = 500) -> dict[str, Any]:
        selected = sorted(
            (reading for reading in device.stored if reading.sequence > after_sequence),
            key=lambda item: item.sequence,
        )[: min(limit, 500)]
        remaining = any(
            reading.sequence > (selected[-1].sequence if selected else after_sequence)
            for reading in device.stored
        )
        return {
            "protocol_version": PROTOCOL,
            "device_id": device.device_id,
            "readings": [reading.model_dump(mode="json") for reading in selected],
            "has_more": remaining,
            "oldest_sequence": min(
                (item.sequence for item in device.stored), default=0
            ),
            "newest_sequence": max(
                (item.sequence for item in device.stored), default=0
            ),
        }

    @app.get("/api/v1/events")
    async def events(after: int = 0, limit: int = 500) -> dict[str, Any]:
        return {"events": device.events[after : after + min(limit, 500)]}

    @app.get("/api/v1/storage")
    async def storage() -> dict[str, Any]:
        return {
            "status": device.fault.sd_state,
            "present": device.fault.sd_state != "removed",
            "read_only": device.fault.sd_state == "read_only",
            "used_percent": 100 if device.fault.sd_state == "full" else 18,
            "oldest_sequence": min(
                (item.sequence for item in device.stored), default=0
            ),
            "newest_sequence": max(
                (item.sequence for item in device.stored), default=0
            ),
        }

    @app.get("/api/v1/sync-status")
    async def sync_status() -> dict[str, Any]:
        return {
            "server_ack_sequence": device.ack_sequence,
            "newest_sequence": device.sequence,
            "backlog": max(0, device.sequence - device.ack_sequence),
        }

    @app.get("/api/v1/config")
    async def config() -> dict[str, Any]:
        return {
            "version": device.config_version,
            "ct_rating_amps": 100,
            "hardware_pins": "local-only",
        }

    @app.put("/api/v1/config")
    async def put_config(request: Request) -> dict[str, Any]:
        payload = json.loads(await request.body())
        if any("pin" in key.lower() for key in payload):
            return {"status": "partially_applied", "rejected": {"pins": "local-only"}}
        device.config_version = int(payload.get("version", device.config_version))
        return {"status": "applied", "version": device.config_version}

    @app.post("/api/v1/sync/ack")
    async def sync_ack(request: Request) -> dict[str, Any]:
        payload = json.loads(await request.body())
        device.ack_sequence = min(
            int(payload["highest_contiguous_sequence"]), device.sequence
        )
        return {"acknowledged": device.ack_sequence}

    @app.post("/api/v1/actions/reboot")
    async def reboot() -> dict[str, Any]:
        device.boot_id = hashlib.sha256(
            f"{device.boot_id}:{time.time()}".encode()
        ).hexdigest()[:36]
        return {"accepted": True}

    @app.post("/api/v1/ota/apply")
    async def ota_apply() -> dict[str, Any]:
        return {
            "status": "installed" if device.fault.ota_result == "success" else "failed"
        }

    return app
