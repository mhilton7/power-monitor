from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Device, DeviceAddress, DeviceCredential, Site, SyncCursor
from app.ingestion.service import ingest_readings
from app.polling.ssrf import AddressRejected, validate_poll_target
from app.schemas import Reading
from app.security.protocol import SecretCipher, sign_headers


@dataclass
class Breaker:
    failures: int = 0
    retry_at: datetime | None = None

    def fail(self) -> None:
        self.failures += 1
        delay = min(900, 2 ** min(self.failures, 9))
        self.retry_at = datetime.now(UTC) + timedelta(seconds=delay)

    def success(self) -> None:
        self.failures = 0
        self.retry_at = None

    @property
    def open(self) -> bool:
        return bool(self.retry_at and self.retry_at > datetime.now(UTC))


BREAKERS: dict[str, Breaker] = {}


async def _credential(
    session: AsyncSession, device_id: str, now: datetime
) -> DeviceCredential | None:
    return await session.scalar(
        select(DeviceCredential)
        .where(
            DeviceCredential.device_id == device_id,
            DeviceCredential.revoked_at.is_(None),
            DeviceCredential.valid_from <= now,
            (DeviceCredential.valid_until.is_(None))
            | (DeviceCredential.valid_until >= now),
        )
        .order_by(DeviceCredential.created_at.desc())
        .limit(1)
    )


async def _address(session: AsyncSession, device_id: str) -> DeviceAddress | None:
    return await session.scalar(
        select(DeviceAddress)
        .where(DeviceAddress.device_id == device_id)
        .order_by(
            DeviceAddress.is_manual_override.desc(), DeviceAddress.last_seen_at.desc()
        )
        .limit(1)
    )


async def poll_device(
    session: AsyncSession, device: Device, settings: Settings
) -> dict[str, Any]:
    breaker = BREAKERS.setdefault(device.id, Breaker())
    if breaker.open:
        return {
            "device_id": device.id,
            "status": "circuit_open",
            "failures": breaker.failures,
        }
    now = datetime.now(UTC)
    address = await _address(session, device.id)
    credential = await _credential(session, device.id, now)
    site = await session.get(Site, device.site_id)
    if address is None or credential is None or site is None:
        return {"device_id": device.id, "status": "not_configured"}
    try:
        await validate_poll_target(
            host=address.host,
            port=address.port,
            scheme=address.scheme,
            allowed_cidrs=site.allowed_cidrs,
            allowed_domains=site.allowed_domains,
            allowed_ports=settings.allowed_poll_ports,
            allow_public=settings.poll_public_addresses and site.allow_public_polling,
        )
    except AddressRejected as exc:
        address.validation_error = str(exc)
        return {"device_id": device.id, "status": "target_rejected", "reason": str(exc)}
    secret = SecretCipher(settings.app_master_key).decrypt(credential.encrypted_secret)
    base_url = f"{address.scheme}://{address.host}:{address.port}"
    timeout = httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)
    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=timeout, verify=True
        ) as client:
            health_path = "/api/v1/health"
            health = await client.get(
                health_path,
                headers=sign_headers(
                    secret=secret,
                    device_id=device.id,
                    direction="server-to-device",
                    method="GET",
                    target=health_path,
                ),
            )
            health.raise_for_status()
            cursor = await session.get(SyncCursor, device.id)
            after = cursor.highest_contiguous_sequence if cursor else 0
            total_accepted = 0
            for _page in range(10):
                path = f"/api/v1/readings?after_sequence={after}&limit=500"
                response = await client.get(
                    path,
                    headers=sign_headers(
                        secret=secret,
                        device_id=device.id,
                        direction="server-to-device",
                        method="GET",
                        target=path,
                    ),
                )
                if response.status_code == 410:
                    device.status = "online_with_backlog"
                    breaker.fail()
                    return {"device_id": device.id, "status": "permanent_data_loss"}
                response.raise_for_status()
                body = response.json()
                readings = [
                    Reading.model_validate(item) for item in body.get("readings", [])
                ]
                if readings:
                    result = await ingest_readings(
                        session, device_id=device.id, readings=readings, source="pull"
                    )
                    await session.commit()
                    total_accepted += len(result.accepted)
                    after = result.highest_contiguous_accepted_sequence
                    ack_body = json.dumps(
                        {"highest_contiguous_sequence": after}, separators=(",", ":")
                    ).encode()
                    ack_path = "/api/v1/sync/ack"
                    ack = await client.post(
                        ack_path,
                        content=ack_body,
                        headers={
                            **sign_headers(
                                secret=secret,
                                device_id=device.id,
                                direction="server-to-device",
                                method="POST",
                                target=ack_path,
                                body=ack_body,
                            ),
                            "Content-Type": "application/json",
                        },
                    )
                    ack.raise_for_status()
                if not body.get("has_more") or not readings:
                    break
            breaker.success()
            address.validation_error = None
            return {
                "device_id": device.id,
                "status": "ok",
                "accepted": total_accepted,
                "health": health.json(),
            }
    except (httpx.HTTPError, ValueError) as exc:
        breaker.fail()
        return {
            "device_id": device.id,
            "status": "failed",
            "error": type(exc).__name__,
            "failures": breaker.failures,
        }


async def poll_due_devices(
    session_factory: Any,
    settings: Settings,
    *,
    concurrency: int = 20,
    per_site_concurrency: int = 5,
) -> list[dict[str, Any]]:
    async with session_factory() as discovery:
        devices = list(
            await discovery.scalars(
                select(Device).where(
                    Device.connection_mode.in_(["pull", "hybrid"]),
                    Device.revoked_at.is_(None),
                )
            )
        )
    random.Random(  # noqa: S311 - deterministic scheduling jitter, not security
        int(datetime.now(UTC).timestamp()) // 60
    ).shuffle(devices)
    semaphore = asyncio.Semaphore(concurrency)
    site_semaphores = {
        site_id: asyncio.Semaphore(per_site_concurrency)
        for site_id in {device.site_id for device in devices}
    }

    async def one(device: Device) -> dict[str, Any]:
        async with semaphore, site_semaphores[device.site_id]:
            await asyncio.sleep(random.uniform(0, 0.25))  # noqa: S311 - scheduling jitter
            async with session_factory() as device_session:
                attached = await device_session.get(Device, device.id)
                if attached is None:
                    return {"device_id": device.id, "status": "removed"}
                result = await asyncio.wait_for(
                    poll_device(device_session, attached, settings), timeout=60
                )
                await device_session.commit()
                return result

    return list(await asyncio.gather(*(one(device) for device in devices)))
