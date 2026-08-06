from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Device,
    DeviceCredential,
    DeviceNonce,
    HeadlessAgentBoot,
)
from app.security.protocol import (
    ProtocolAuthError,
    SecretCipher,
    canonical_path_and_query,
    sha256_hex,
)

AGENT_PROTOCOL = "pm-agent/2.0.0"
AGENT_CANONICAL_PREFIX = "PM-AGENT-HMAC-SHA256-V2"
AGENT_DEVICE_TO_SERVER_INFO = b"pm-agent-device-to-server-v2"
AGENT_SERVER_TO_DEVICE_INFO = b"pm-agent-server-to-device-v2"
MAX_AGENT_COUNTER = 2**63 - 1


def derive_agent_key(secret: bytes, direction: str) -> bytes:
    info = (
        AGENT_DEVICE_TO_SERVER_INFO
        if direction == "device-to-server"
        else AGENT_SERVER_TO_DEVICE_INFO
    )
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(secret)


def canonical_agent_request(
    *,
    device_id: str,
    boot_id: str,
    counter: int,
    nonce: str,
    method: str,
    target: str,
    body_sha256: str,
) -> bytes:
    return "\n".join(
        (
            AGENT_CANONICAL_PREFIX,
            AGENT_PROTOCOL,
            str(UUID(device_id)),
            str(UUID(boot_id)),
            str(counter),
            nonce.lower(),
            method.upper(),
            canonical_path_and_query(target),
            body_sha256,
        )
    ).encode()


def calculate_agent_request_signature(
    *,
    secret: bytes,
    device_id: str,
    boot_id: str,
    counter: int,
    nonce: str,
    method: str,
    target: str,
    body_sha256: str,
) -> str:
    return hmac.new(
        derive_agent_key(secret, "device-to-server"),
        canonical_agent_request(
            device_id=device_id,
            boot_id=boot_id,
            counter=counter,
            nonce=nonce,
            method=method,
            target=target,
            body_sha256=body_sha256,
        ),
        hashlib.sha256,
    ).hexdigest()


def canonical_agent_response(
    *, request_nonce: str, request_counter: int, status: int, body_sha256: str
) -> bytes:
    return "\n".join(
        (
            AGENT_CANONICAL_PREFIX,
            "RESPONSE",
            request_nonce.lower(),
            str(request_counter),
            str(status),
            body_sha256,
        )
    ).encode()


def calculate_agent_response_signature(
    *, secret: bytes, request_nonce: str, request_counter: int, status: int, body: bytes
) -> tuple[str, str]:
    digest = sha256_hex(body)
    signature = hmac.new(
        derive_agent_key(secret, "server-to-device"),
        canonical_agent_response(
            request_nonce=request_nonce,
            request_counter=request_counter,
            status=status,
            body_sha256=digest,
        ),
        hashlib.sha256,
    ).hexdigest()
    return digest, signature


@dataclass(frozen=True)
class VerifiedAgentRequest:
    device: Device
    credential: DeviceCredential
    secret: bytes
    boot_id: str
    counter: int
    nonce: str


def _required_header(headers: dict[str, str], name: str) -> str:
    value = headers.get(name.lower())
    if not value:
        raise ProtocolAuthError("missing_header", f"Required header {name} is missing")
    return value


async def verify_agent_request(
    *,
    session: AsyncSession,
    headers: dict[str, str],
    method: str,
    target: str,
    body: bytes,
    cipher: SecretCipher,
    now: datetime | None = None,
) -> VerifiedAgentRequest:
    now = now or datetime.now(UTC)
    if _required_header(headers, "X-PM-Agent-Protocol") != AGENT_PROTOCOL:
        raise ProtocolAuthError(
            "protocol_incompatible", f"Supported protocol is {AGENT_PROTOCOL}", 426
        )
    try:
        device_id = str(UUID(_required_header(headers, "X-PM-Device-ID")))
        boot_id = str(UUID(_required_header(headers, "X-PM-Boot-ID")))
    except ValueError as exc:
        raise ProtocolAuthError("invalid_identity", "Device and boot IDs must be UUIDs") from exc
    counter_text = _required_header(headers, "X-PM-Counter")
    nonce = _required_header(headers, "X-PM-Nonce").lower()
    supplied_digest = _required_header(headers, "X-PM-Content-SHA256")
    supplied_signature = _required_header(headers, "X-PM-Signature")
    try:
        counter = int(counter_text)
    except ValueError as exc:
        raise ProtocolAuthError("invalid_counter", "Counter must be an integer") from exc
    if not 1 <= counter <= MAX_AGENT_COUNTER:
        raise ProtocolAuthError("invalid_counter", "Counter is outside the supported range")
    if len(nonce) < 32 or any(character not in "0123456789abcdef" for character in nonce):
        raise ProtocolAuthError("invalid_nonce", "Nonce must be at least 32 hexadecimal characters")
    actual_digest = sha256_hex(body)
    if not hmac.compare_digest(actual_digest, supplied_digest):
        raise ProtocolAuthError("body_digest_mismatch", "Request body digest does not match")

    device = await session.get(Device, device_id, with_for_update=True)
    if device is None:
        raise ProtocolAuthError("unknown_device", "Device is not enrolled")
    if device.revoked_at is not None:
        raise ProtocolAuthError("device_revoked", "Device credential is revoked", 403)
    if device.protocol_version != AGENT_PROTOCOL:
        raise ProtocolAuthError(
            "protocol_incompatible",
            "Device is not enrolled as a headless agent",
            426,
        )
    credentials = list(
        await session.scalars(
            select(DeviceCredential).where(
                DeviceCredential.device_id == device_id,
                DeviceCredential.revoked_at.is_(None),
                DeviceCredential.valid_from <= now,
                (DeviceCredential.valid_until.is_(None)) | (DeviceCredential.valid_until >= now),
            )
        )
    )
    matched: tuple[DeviceCredential, bytes] | None = None
    for credential in credentials:
        secret = cipher.decrypt(credential.encrypted_secret)
        expected = calculate_agent_request_signature(
            secret=secret,
            device_id=device_id,
            boot_id=boot_id,
            counter=counter,
            nonce=nonce,
            method=method,
            target=target,
            body_sha256=supplied_digest,
        )
        if hmac.compare_digest(expected, supplied_signature):
            matched = credential, secret
    if matched is None:
        raise ProtocolAuthError("invalid_signature", "Agent signature is invalid")

    boot = await session.get(HeadlessAgentBoot, (device_id, boot_id), with_for_update=True)
    active_boot = await session.scalar(
        select(HeadlessAgentBoot).where(
            HeadlessAgentBoot.device_id == device_id,
            HeadlessAgentBoot.active.is_(True),
        )
    )
    if boot is not None and not boot.active:
        raise ProtocolAuthError("wrong_boot_id", "A retired boot ID cannot be reused")
    if boot is None:
        if active_boot is not None:
            await session.execute(
                update(HeadlessAgentBoot)
                .where(
                    HeadlessAgentBoot.device_id == device_id,
                    HeadlessAgentBoot.active.is_(True),
                )
                .values(active=False)
            )
        boot = HeadlessAgentBoot(
            device_id=device_id,
            boot_id=boot_id,
            highest_counter=counter,
            active=True,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(boot)
    elif counter <= boot.highest_counter:
        raise ProtocolAuthError("counter_replay", "Request counter was already used")
    else:
        boot.highest_counter = counter
        boot.last_seen_at = now

    nonce_hash = sha256_hex(nonce.encode())
    existing_nonce = await session.get(
        DeviceNonce, (device_id, "agent-device-to-server", nonce_hash)
    )
    if existing_nonce is not None:
        raise ProtocolAuthError("nonce_replay", "Nonce has already been used")
    session.add(
        DeviceNonce(
            device_id=device_id,
            direction="agent-device-to-server",
            nonce_hash=nonce_hash,
            expires_at=now + timedelta(days=7),
        )
    )
    await session.flush()
    return VerifiedAgentRequest(
        device=device,
        credential=matched[0],
        secret=matched[1],
        boot_id=boot_id,
        counter=counter,
        nonce=nonce,
    )
