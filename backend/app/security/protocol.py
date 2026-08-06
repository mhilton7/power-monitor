from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, quote, urlsplit
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device, DeviceCredential, DeviceNonce

PROTOCOL = "pm-protocol/1.0.0"
CANONICAL_PREFIX = "PM-HMAC-SHA256-V1"
DEVICE_TO_SERVER_INFO = b"pm-device-to-server-v1"
SERVER_TO_DEVICE_INFO = b"pm-server-to-device-v1"
DATA_RESET_RECEIPT_INFO = b"pm-data-reset-receipt-v1"


class ProtocolAuthError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_path_and_query(target: str) -> str:
    split = urlsplit(target)
    path = split.path or "/"
    pairs = parse_qsl(split.query, keep_blank_values=True, strict_parsing=False)
    encoded = sorted((quote(key, safe="~-._"), quote(value, safe="~-._")) for key, value in pairs)
    query = "&".join(f"{key}={value}" for key, value in encoded)
    return f"{path}?{query}" if query else path


def canonical_string(
    method: str, target: str, timestamp: str, nonce: str, content_sha256: str
) -> str:
    return "\n".join(
        (
            CANONICAL_PREFIX,
            method.upper(),
            canonical_path_and_query(target),
            timestamp,
            nonce,
            content_sha256,
        )
    )


def derive_key(secret: bytes, direction: str) -> bytes:
    info = DEVICE_TO_SERVER_INFO if direction == "device-to-server" else SERVER_TO_DEVICE_INFO
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(secret)


def derive_data_reset_receipt_key(secret: bytes, device_id: str) -> bytes:
    """Derive the device-to-server key used to bind durable reset receipts."""

    canonical_device_id = str(UUID(device_id))
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=canonical_device_id.encode("utf-8"),
        info=DATA_RESET_RECEIPT_INFO,
    ).derive(derive_key(secret, "device-to-server"))


def canonical_data_reset_receipt(receipt: dict[str, object]) -> bytes:
    """Return the portable canonical JSON covered by a reset receipt HMAC."""

    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}

    def reject_floats(value: object) -> None:
        if isinstance(value, float):
            raise ValueError("data reset receipt decimals must be JSON strings")
        if isinstance(value, dict):
            for child in value.values():
                reject_floats(child)
        elif isinstance(value, list):
            for child in value:
                reject_floats(child)

    reject_floats(unsigned)
    return json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def calculate_data_reset_receipt_digest(
    secret: bytes, device_id: str, receipt: dict[str, object]
) -> str:
    return hmac.new(
        derive_data_reset_receipt_key(secret, device_id),
        canonical_data_reset_receipt(receipt),
        hashlib.sha256,
    ).hexdigest()


def verify_data_reset_receipt_digest(
    secret: bytes, device_id: str, receipt: dict[str, object]
) -> bool:
    supplied = receipt.get("receipt_digest")
    if not isinstance(supplied, str):
        return False
    return hmac.compare_digest(
        supplied,
        calculate_data_reset_receipt_digest(secret, device_id, receipt),
    )


def calculate_signature(
    secret: bytes,
    direction: str,
    method: str,
    target: str,
    timestamp: str,
    nonce: str,
    content_sha256: str,
) -> str:
    message = canonical_string(method, target, timestamp, nonce, content_sha256).encode()
    return hmac.new(derive_key(secret, direction), message, hashlib.sha256).hexdigest()


def sign_headers(
    *,
    secret: bytes,
    device_id: str,
    direction: str,
    method: str,
    target: str,
    body: bytes = b"",
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp_value = str(timestamp if timestamp is not None else int(time.time()))
    nonce_value = nonce or secrets.token_hex(16)
    digest = sha256_hex(body)
    return {
        "X-PM-Protocol": PROTOCOL,
        "X-PM-Device-ID": device_id,
        "X-PM-Timestamp": timestamp_value,
        "X-PM-Nonce": nonce_value,
        "X-PM-Content-SHA256": digest,
        "X-PM-Signature": calculate_signature(
            secret,
            direction,
            method,
            target,
            timestamp_value,
            nonce_value,
            digest,
        ),
    }


class SecretCipher:
    def __init__(self, key: str) -> None:
        if not key:
            raise RuntimeError("APP_MASTER_KEY is required")
        try:
            self._fernet = Fernet(key.encode())
        except ValueError as exc:
            raise RuntimeError("APP_MASTER_KEY must be a URL-safe base64 Fernet key") from exc

    def encrypt(self, secret: bytes) -> bytes:
        return self._fernet.encrypt(secret)

    def decrypt(self, protected: bytes) -> bytes:
        try:
            return self._fernet.decrypt(protected)
        except InvalidToken as exc:
            raise RuntimeError("encrypted secret cannot be decrypted with APP_MASTER_KEY") from exc


@dataclass(frozen=True)
class VerifiedDevice:
    device: Device
    credential: DeviceCredential


def _required_header(headers: dict[str, str], name: str) -> str:
    value = headers.get(name.lower())
    if not value:
        raise ProtocolAuthError("missing_header", f"Required header {name} is missing")
    return value


async def verify_device_request(
    *,
    session: AsyncSession,
    headers: dict[str, str],
    method: str,
    target: str,
    body: bytes,
    cipher: SecretCipher,
    direction: str = "device-to-server",
    now: datetime | None = None,
    clock_window_seconds: int = 300,
) -> VerifiedDevice:
    now = now or datetime.now(UTC)
    protocol = _required_header(headers, "X-PM-Protocol")
    if protocol != PROTOCOL:
        raise ProtocolAuthError(
            "protocol_incompatible",
            f"Supported protocol is {PROTOCOL}",
            status_code=426,
        )
    device_id = _required_header(headers, "X-PM-Device-ID")
    timestamp_text = _required_header(headers, "X-PM-Timestamp")
    nonce = _required_header(headers, "X-PM-Nonce")
    supplied_digest = _required_header(headers, "X-PM-Content-SHA256")
    supplied_signature = _required_header(headers, "X-PM-Signature")
    if len(nonce) < 32 or any(character not in "0123456789abcdefABCDEF" for character in nonce):
        raise ProtocolAuthError(
            "invalid_nonce", "Nonce must contain at least 32 hexadecimal characters"
        )
    try:
        timestamp = int(timestamp_text)
    except ValueError as exc:
        raise ProtocolAuthError("invalid_timestamp", "Timestamp must be Unix seconds") from exc
    if abs(int(now.timestamp()) - timestamp) > clock_window_seconds:
        raise ProtocolAuthError(
            "stale_timestamp", "Request timestamp is outside the allowed window"
        )
    actual_digest = sha256_hex(body)
    if not hmac.compare_digest(actual_digest, supplied_digest):
        raise ProtocolAuthError("body_digest_mismatch", "Request body digest does not match")

    # Every authenticated device request inserts a nonce whose foreign key takes
    # a key-share lock on this row.  Acquire the reset/mutation serialization
    # lock first so concurrent requests cannot deadlock while upgrading that
    # implicit key-share lock later in the transaction.
    device = await session.get(Device, device_id, with_for_update=True)
    if device is None:
        raise ProtocolAuthError("unknown_device", "Device is not enrolled")
    if device.revoked_at is not None:
        raise ProtocolAuthError("device_revoked", "Device credential is revoked", 403)
    credentials = (
        await session.scalars(
            select(DeviceCredential)
            .where(DeviceCredential.device_id == device_id)
            .where(DeviceCredential.revoked_at.is_(None))
            .where(DeviceCredential.valid_from <= now)
            .where((DeviceCredential.valid_until.is_(None)) | (DeviceCredential.valid_until >= now))
        )
    ).all()
    matched: DeviceCredential | None = None
    for credential in credentials:
        secret = cipher.decrypt(credential.encrypted_secret)
        expected = calculate_signature(
            secret,
            direction,
            method,
            target,
            timestamp_text,
            nonce,
            supplied_digest,
        )
        if hmac.compare_digest(expected, supplied_signature):
            matched = credential
    if matched is None:
        raise ProtocolAuthError("invalid_signature", "Device signature is invalid")

    nonce_hash = sha256_hex(nonce.lower().encode())
    await session.execute(delete(DeviceNonce).where(DeviceNonce.expires_at < now))
    session.add(
        DeviceNonce(
            device_id=device_id,
            direction=direction,
            nonce_hash=nonce_hash,
            expires_at=now + timedelta(seconds=clock_window_seconds),
        )
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ProtocolAuthError("nonce_replay", "Nonce has already been used") from exc
    return VerifiedDevice(device=device, credential=matched)
