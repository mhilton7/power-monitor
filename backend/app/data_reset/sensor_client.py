from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    Device,
    DeviceAddress,
    DeviceCommand,
    DeviceCredential,
    SensorNetworkCidr,
    SensorNetworkPolicy,
    Site,
)
from app.network_policy import poll_policy_parameters
from app.polling.ssrf import AddressRejected, validate_poll_target
from app.schemas import SensorDataResetResponse
from app.security.protocol import (
    SecretCipher,
    sign_headers,
    verify_data_reset_receipt_digest,
)


@dataclass(frozen=True)
class SensorResetCommunicationError(Exception):
    code: str
    summary: str
    retryable: bool = True
    request_may_have_reached_sensor: bool = True


_CARD_GENERATION = re.compile(r"^[1-9][0-9]{0,19}$")
_MAX_STORAGE_RESPONSE_BYTES = 65_536
_MAX_SIGNED_SEQUENCE = 2**63 - 1


async def _credential(
    session: AsyncSession, device_id: str, now: datetime
) -> DeviceCredential | None:
    return cast(
        DeviceCredential | None,
        await session.scalar(
            select(DeviceCredential)
            .where(
                DeviceCredential.device_id == device_id,
                DeviceCredential.revoked_at.is_(None),
                DeviceCredential.valid_from <= now,
                (DeviceCredential.valid_until.is_(None)) | (DeviceCredential.valid_until >= now),
            )
            .order_by(DeviceCredential.created_at.desc())
            .limit(1)
        ),
    )


async def _address(session: AsyncSession, device_id: str) -> DeviceAddress | None:
    return cast(
        DeviceAddress | None,
        await session.scalar(
            select(DeviceAddress)
            .where(DeviceAddress.device_id == device_id)
            .order_by(
                DeviceAddress.is_manual_override.desc(),
                DeviceAddress.last_seen_at.desc(),
            )
            .limit(1)
        ),
    )


async def _read_poll_policy_parameters(
    session: AsyncSession, site: Site
) -> tuple[list[str], bool, str]:
    """Resolve the effective pull policy without creating migration rows."""

    policy = await session.scalar(
        select(SensorNetworkPolicy).where(
            SensorNetworkPolicy.site_id == site.id,
            SensorNetworkPolicy.direction == "server_pull",
        )
    )
    if policy is None:
        if site.allow_public_polling:
            return list(site.allowed_cidrs), True, "legacy_public_and_listed"
        if site.allowed_cidrs:
            return list(site.allowed_cidrs), False, "allow_listed_private"
        return [], False, "deny_all"
    if policy.mode == "deny_all":
        return [], False, policy.mode
    entries = list(
        await session.scalars(
            select(SensorNetworkCidr).where(
                SensorNetworkCidr.policy_id == policy.id,
                SensorNetworkCidr.enabled.is_(True),
            )
        )
    )
    cidrs = [item.network for item in entries]
    if policy.mode == "allow_all_private":
        cidrs.extend(["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"])
    return cidrs, policy.mode == "legacy_public_and_listed", policy.mode


def _canonical_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _safe_problem_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return "sensor_reset_http_error"
    value = payload.get("code") if isinstance(payload, dict) else None
    return value if isinstance(value, str) and 1 <= len(value) <= 80 else "sensor_reset_http_error"


def _required_nonnegative_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_SIGNED_SEQUENCE
    ):
        raise SensorResetCommunicationError(
            "sensor_probe_response_invalid",
            f"Sensor storage status omitted a valid {field}",
            retryable=False,
        )
    return int(value)


def _required_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise SensorResetCommunicationError(
            "sensor_probe_response_invalid",
            f"Sensor storage status omitted a valid {field}",
            retryable=False,
        )
    return value


def _required_nullable_sequence(payload: dict[str, Any], field: str) -> int | None:
    if field not in payload:
        raise SensorResetCommunicationError(
            "sensor_probe_response_invalid",
            f"Sensor storage status omitted {field}",
            retryable=False,
        )
    value = payload[field]
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_SIGNED_SEQUENCE
    ):
        raise SensorResetCommunicationError(
            "sensor_probe_response_invalid",
            f"Sensor storage status omitted a valid {field}",
            retryable=False,
        )
    return int(value)


def _required_nullable_bool(payload: dict[str, Any], field: str) -> bool | None:
    if field not in payload:
        raise SensorResetCommunicationError(
            "sensor_probe_response_invalid",
            f"Sensor storage status omitted {field}",
            retryable=False,
        )
    value = payload[field]
    if value is not None and not isinstance(value, bool):
        raise SensorResetCommunicationError(
            "sensor_probe_response_invalid",
            f"Sensor storage status omitted a valid {field}",
            retryable=False,
        )
    return value


def validate_sensor_storage_snapshot(payload: Any) -> dict[str, Any]:
    """Validate and normalize the non-mutating reset inventory response."""

    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise SensorResetCommunicationError(
            "sensor_probe_response_invalid",
            "Sensor storage status did not match schema version 1",
            retryable=False,
        )
    present = _required_bool(payload, "present")
    mounted = _required_bool(payload, "mounted")
    writable = _required_bool(payload, "writable")
    data_generation = _required_nonnegative_int(payload, "data_generation")
    sequence_floor = _required_nonnegative_int(payload, "sequence_floor")
    next_sequence = _required_nonnegative_int(payload, "next_sequence")
    oldest_sequence = _required_nonnegative_int(payload, "oldest_sequence")
    newest_sequence = _required_nonnegative_int(payload, "newest_sequence")
    newest_syncable_sequence = _required_nonnegative_int(payload, "newest_syncable_sequence")
    server_ack_sequence = _required_nonnegative_int(payload, "server_ack_sequence")
    backlog_estimate = _required_nonnegative_int(payload, "unsynchronized_estimate")
    local_record_count = _required_nonnegative_int(payload, "local_record_count")
    projection_consistent = _required_bool(payload, "prepare_projection_consistent")
    if not projection_consistent:
        raise SensorResetCommunicationError(
            "sensor_probe_projection_busy",
            "Sensor storage projection is changing; retry the read-only inventory",
            retryable=True,
        )
    projection_local_record_count = _required_nonnegative_int(
        payload, "prepare_projection_local_record_count"
    )
    projection_next_sequence = _required_nonnegative_int(
        payload, "prepare_projection_next_sequence"
    )
    projection_newest_sequence = _required_nonnegative_int(
        payload, "prepare_projection_newest_sequence"
    )
    projection_newest_syncable_sequence = _required_nonnegative_int(
        payload, "prepare_projection_newest_syncable_sequence"
    )
    drain_records_projected = _required_nonnegative_int(payload, "prepare_drain_records_projected")
    drain_first_sequence_projected = _required_nullable_sequence(
        payload, "prepare_drain_first_sequence_projected"
    )
    drain_last_sequence_projected = _required_nullable_sequence(
        payload, "prepare_drain_last_sequence_projected"
    )
    drain_syncable_records_projected = _required_nonnegative_int(
        payload, "prepare_drain_syncable_records_projected"
    )
    card_generation = payload.get("card_generation")
    card_identity_status = payload.get("card_identity_status")
    if (
        card_generation is not None
        and (
            not isinstance(card_generation, str)
            or _CARD_GENERATION.fullmatch(card_generation) is None
        )
    ) or (not isinstance(card_identity_status, str) or not 1 <= len(card_identity_status) <= 80):
        raise SensorResetCommunicationError(
            "sensor_probe_response_invalid",
            "Sensor storage status omitted valid card identity evidence",
            retryable=False,
        )
    expected_backlog = max(0, newest_syncable_sequence - server_ack_sequence)
    projected_backlog = max(0, projection_newest_syncable_sequence - server_ack_sequence)
    valid_drain_projection = (
        drain_records_projected == 0
        and drain_first_sequence_projected is None
        and drain_last_sequence_projected is None
        and drain_syncable_records_projected == 0
        and projection_next_sequence == next_sequence
        and projection_newest_sequence == newest_sequence
        and projection_newest_syncable_sequence == newest_syncable_sequence
    ) or (
        1 <= drain_records_projected <= 2
        and drain_first_sequence_projected is not None
        and drain_last_sequence_projected is not None
        and drain_first_sequence_projected == next_sequence
        and drain_last_sequence_projected >= drain_first_sequence_projected
        and drain_last_sequence_projected - drain_first_sequence_projected + 1
        == drain_records_projected
        and projection_next_sequence == drain_last_sequence_projected + 1
        and projection_newest_sequence == drain_last_sequence_projected
        and 0 <= drain_syncable_records_projected <= drain_records_projected
        and (
            projection_newest_syncable_sequence == newest_syncable_sequence
            if drain_syncable_records_projected == 0
            else projection_newest_syncable_sequence
            == drain_first_sequence_projected + drain_syncable_records_projected - 1
        )
    )
    if (
        next_sequence < 1
        or next_sequence
        <= max(sequence_floor, newest_sequence, newest_syncable_sequence, server_ack_sequence)
        or backlog_estimate != expected_backlog
        or projection_local_record_count != local_record_count + drain_records_projected
        or projection_next_sequence < next_sequence
        or projection_next_sequence
        <= max(
            sequence_floor,
            projection_newest_sequence,
            projection_newest_syncable_sequence,
            server_ack_sequence,
        )
        or projection_newest_sequence < newest_sequence
        or projection_newest_syncable_sequence < newest_syncable_sequence
        or newest_syncable_sequence > newest_sequence
        or projection_newest_syncable_sequence > projection_newest_sequence
        or not valid_drain_projection
    ):
        raise SensorResetCommunicationError(
            "sensor_probe_response_invalid",
            "Sensor storage sequence evidence is internally inconsistent",
            retryable=False,
        )
    sd_status = (
        "not_present"
        if not present
        else "not_mounted"
        if not mounted
        else "read_only"
        if not writable
        else "writable"
    )
    return {
        "data_generation": data_generation,
        "sequence_floor": sequence_floor,
        "next_sequence": projection_next_sequence,
        "oldest_sequence": oldest_sequence,
        "newest_sequence": projection_newest_sequence,
        "newest_syncable_sequence": projection_newest_syncable_sequence,
        "server_ack_sequence": server_ack_sequence,
        "backlog_estimate": projected_backlog,
        "local_record_count": projection_local_record_count,
        "durable_next_sequence": next_sequence,
        "durable_newest_sequence": newest_sequence,
        "durable_newest_syncable_sequence": newest_syncable_sequence,
        "durable_backlog_estimate": backlog_estimate,
        "durable_local_record_count": local_record_count,
        "prepare_drain_records_projected": drain_records_projected,
        "prepare_drain_first_sequence_projected": drain_first_sequence_projected,
        "prepare_drain_last_sequence_projected": drain_last_sequence_projected,
        "prepare_drain_syncable_records_projected": drain_syncable_records_projected,
        "card_generation": card_generation,
        "card_identity_status": card_identity_status,
        "sd_status": sd_status,
    }


async def probe_sensor_storage(
    session: AsyncSession,
    *,
    device: Device,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Read exact sensor inventory without pausing or writing device state."""

    now = datetime.now(UTC)
    address = await _address(session, device.id)
    credential = await _credential(session, device.id, now)
    site = await session.get(Site, device.site_id)
    if credential is None:
        raise SensorResetCommunicationError(
            "sensor_probe_authentication_failed",
            "No current server-to-sensor credential is available",
            retryable=False,
        )
    if address is None or site is None:
        raise SensorResetCommunicationError(
            "sensor_probe_not_configured",
            "Sensor address or site is unavailable",
        )
    allowed_cidrs, allow_public, policy_mode = await _read_poll_policy_parameters(session, site)
    if policy_mode == "deny_all":
        raise SensorResetCommunicationError(
            "sensor_probe_network_policy_denied",
            "Server-to-sensor access is denied by site policy",
            retryable=False,
        )
    try:
        await validate_poll_target(
            host=address.host,
            port=address.port,
            scheme=address.scheme,
            allowed_cidrs=allowed_cidrs,
            allowed_domains=site.allowed_domains,
            allowed_ports=settings.allowed_poll_ports,
            allow_public=allow_public and settings.poll_public_addresses,
        )
    except AddressRejected as exc:
        raise SensorResetCommunicationError(
            "sensor_probe_target_rejected",
            "Sensor target was rejected by server network policy",
            retryable=False,
        ) from exc
    try:
        secret = SecretCipher(settings.app_master_key).decrypt(credential.encrypted_secret)
    except RuntimeError as exc:
        raise SensorResetCommunicationError(
            "sensor_probe_authentication_failed",
            "The server-to-sensor credential could not be used",
            retryable=False,
        ) from exc
    path = "/api/v1/storage"
    headers = sign_headers(
        secret=secret,
        device_id=device.id,
        direction="server-to-device",
        method="GET",
        target=path,
    )
    timeout = httpx.Timeout(connect=3.0, read=10.0, write=5.0, pool=3.0)
    try:
        async with httpx.AsyncClient(
            base_url=f"{address.scheme}://{address.host}:{address.port}",
            timeout=timeout,
            verify=True,
            transport=transport,
        ) as client:
            response = await client.get(path, headers=headers)
    except httpx.HTTPError as exc:
        raise SensorResetCommunicationError(
            "sensor_probe_unreachable",
            "Sensor storage status is temporarily unreachable",
        ) from exc
    if response.status_code in {401, 403}:
        raise SensorResetCommunicationError(
            "sensor_probe_authentication_failed",
            "Sensor rejected the server-to-sensor authentication",
            retryable=False,
        )
    if response.status_code in {404, 405, 501}:
        raise SensorResetCommunicationError(
            "sensor_probe_unsupported",
            "Sensor firmware does not expose the reset inventory endpoint",
            retryable=False,
        )
    if response.status_code != 200:
        raise SensorResetCommunicationError(
            "sensor_probe_http_error",
            "Sensor rejected the read-only inventory request",
            retryable=response.status_code >= 500 or response.status_code in {408, 425, 429},
        )
    if len(response.content) > _MAX_STORAGE_RESPONSE_BYTES:
        raise SensorResetCommunicationError(
            "sensor_probe_response_invalid",
            "Sensor storage status exceeded the bounded response size",
            retryable=False,
        )
    try:
        payload = response.json()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SensorResetCommunicationError(
            "sensor_probe_response_invalid",
            "Sensor returned invalid storage status JSON",
            retryable=False,
        ) from exc
    return validate_sensor_storage_snapshot(payload)


def _receipt_dict(
    response: dict[str, Any], kind: Literal["prepared", "commit"]
) -> dict[str, Any] | None:
    field = "prepared_receipt" if kind == "prepared" else "commit_receipt"
    value = response.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or len(value.encode("utf-8")) > 65_536:
        raise SensorResetCommunicationError(
            "sensor_reset_receipt_invalid",
            "Sensor returned an invalid reset receipt",
            retryable=False,
        )
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SensorResetCommunicationError(
            "sensor_reset_receipt_invalid",
            "Sensor reset receipt is not canonical JSON",
            retryable=False,
        ) from exc
    if not isinstance(parsed, dict):
        raise SensorResetCommunicationError(
            "sensor_reset_receipt_invalid",
            "Sensor reset receipt must be a JSON object",
            retryable=False,
        )
    return parsed


def verify_reset_response(
    *,
    response: dict[str, Any],
    secret: bytes,
    device_id: str,
    operation_id: str,
    target_generation: int,
) -> dict[str, Any]:
    if (
        response.get("protocol") != "data-reset/1.0.0"
        or response.get("operation_id") != operation_id
        or response.get("device_id") != device_id
        or response.get("target_generation") != target_generation
    ):
        raise SensorResetCommunicationError(
            "sensor_reset_identity_mismatch",
            "Sensor reset status did not match the requested operation",
            retryable=False,
        )
    verified = dict(response)
    for kind, digest_field in (
        ("prepared", "prepared_receipt_digest"),
        ("commit", "commit_receipt_digest"),
    ):
        receipt = _receipt_dict(response, cast(Literal["prepared", "commit"], kind))
        if receipt is None:
            continue
        digest = response.get(digest_field)
        if not isinstance(digest, str):
            raise SensorResetCommunicationError(
                "sensor_reset_receipt_digest_missing",
                "Sensor omitted the keyed reset receipt digest",
                retryable=False,
            )
        digest_input = dict(receipt)
        digest_input["receipt_digest"] = digest
        try:
            digest_valid = verify_data_reset_receipt_digest(
                secret,
                device_id,
                digest_input,
            )
        except (TypeError, ValueError):
            digest_valid = False
        if not digest_valid:
            raise SensorResetCommunicationError(
                "sensor_reset_receipt_digest_invalid",
                "Sensor reset receipt digest could not be verified",
                retryable=False,
            )
        verified[f"_{kind}_receipt_parsed"] = receipt
    return verified


async def request_sensor_reset(
    session: AsyncSession,
    *,
    device: Device,
    settings: Settings,
    action: Literal["prepare", "commit", "status", "cancel"],
    operation_id: str,
    target_generation: int,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    if device.protocol_version == "pm-agent/2.0.0":
        command: DeviceCommand | None = None
        if action == "status":
            command = await session.scalar(
                select(DeviceCommand)
                .where(
                    DeviceCommand.device_id == device.id,
                    DeviceCommand.idempotency_key.like(
                        f"data-reset:{operation_id}:{target_generation}:%"
                    ),
                )
                .order_by(DeviceCommand.created_at.desc(), DeviceCommand.id.desc())
                .limit(1)
                .with_for_update()
            )
        else:
            idempotency_key = f"data-reset:{operation_id}:{target_generation}:{action}"
            command = await session.scalar(
                select(DeviceCommand)
                .where(DeviceCommand.idempotency_key == idempotency_key)
                .with_for_update()
            )
            if command is None:
                command = DeviceCommand(
                    device_id=device.id,
                    command_type=f"data_reset_{action}",
                    state="queued",
                    payload=payload
                    or {
                        "protocol": "data-reset/1.0.0",
                        "operation_id": operation_id,
                        "device_id": device.id,
                        "target_generation": target_generation,
                    },
                    result=None,
                    expected_state={
                        "operation_id": operation_id,
                        "target_generation": target_generation,
                    },
                    idempotency_key=idempotency_key,
                    created_at=now,
                    expires_at=now + timedelta(minutes=30),
                    delivery_attempts=0,
                )
                session.add(command)
                await session.flush()
        if command is None:
            return {"state": "none"}
        if command.state in {"completed", "cancelled"} and isinstance(command.result, dict):
            credential = await _credential(session, device.id, now)
            if credential is None:
                raise SensorResetCommunicationError(
                    "sensor_reset_authentication_failed",
                    "No current headless agent credential is available",
                    retryable=False,
                    request_may_have_reached_sensor=False,
                )
            try:
                secret = SecretCipher(settings.app_master_key).decrypt(credential.encrypted_secret)
            except RuntimeError as exc:
                raise SensorResetCommunicationError(
                    "sensor_reset_authentication_failed",
                    "The headless agent credential could not be used",
                    retryable=False,
                    request_may_have_reached_sensor=False,
                ) from exc
            return verify_reset_response(
                response=command.result,
                secret=secret,
                device_id=device.id,
                operation_id=operation_id,
                target_generation=target_generation,
            )
        if command.state == "failed":
            return {
                "state": "attention_required",
                "failure_code": command.failure_code or "sensor_reset_attention_required",
            }
        return {
            "state": (
                "commit_authorized" if command.command_type == "data_reset_commit" else "preparing"
            )
        }
    address = await _address(session, device.id)
    credential = await _credential(session, device.id, now)
    site = await session.get(Site, device.site_id)
    if address is None or credential is None or site is None:
        raise SensorResetCommunicationError(
            "sensor_reset_not_configured",
            "Sensor address, credential, or site is unavailable",
            request_may_have_reached_sensor=False,
        )
    allowed_cidrs, allow_public, policy = await poll_policy_parameters(session, site)
    if policy.mode == "deny_all":
        raise SensorResetCommunicationError(
            "sensor_reset_network_policy_denied",
            "Server-to-sensor access is denied by site policy",
            retryable=False,
        )
    try:
        await validate_poll_target(
            host=address.host,
            port=address.port,
            scheme=address.scheme,
            allowed_cidrs=allowed_cidrs,
            allowed_domains=site.allowed_domains,
            allowed_ports=settings.allowed_poll_ports,
            allow_public=allow_public and settings.poll_public_addresses,
        )
    except AddressRejected as exc:
        raise SensorResetCommunicationError(
            "sensor_reset_target_rejected",
            "Sensor target was rejected by server network policy",
            retryable=False,
            request_may_have_reached_sensor=False,
        ) from exc
    try:
        secret = SecretCipher(settings.app_master_key).decrypt(credential.encrypted_secret)
    except RuntimeError as exc:
        raise SensorResetCommunicationError(
            "sensor_reset_authentication_failed",
            "The server-to-sensor credential could not be used",
            retryable=False,
            request_may_have_reached_sensor=False,
        ) from exc
    path = f"/api/v1/data-reset/{action}"
    body = b""
    if action == "status":
        path = f"{path}?operation_id={operation_id}&target_generation={target_generation}"
    else:
        body = _canonical_body(payload or {})
    headers = sign_headers(
        secret=secret,
        device_id=device.id,
        direction="server-to-device",
        method="GET" if action == "status" else "POST",
        target=path,
        body=body,
    )
    if body:
        headers["Content-Type"] = "application/json"
    timeout = httpx.Timeout(connect=3.0, read=15.0, write=10.0, pool=3.0)
    try:
        async with httpx.AsyncClient(
            base_url=f"{address.scheme}://{address.host}:{address.port}",
            timeout=timeout,
            verify=True,
        ) as client:
            response = await client.request(
                "GET" if action == "status" else "POST",
                path,
                content=body,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise SensorResetCommunicationError(
            "sensor_reset_unreachable", "Sensor reset endpoint is temporarily unreachable"
        ) from exc
    if response.status_code not in {200, 202}:
        raise SensorResetCommunicationError(
            _safe_problem_code(response),
            "Sensor rejected the coordinated reset request",
            retryable=response.status_code >= 500 or response.status_code in {408, 409, 425, 429},
        )
    try:
        result = response.json()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SensorResetCommunicationError(
            "sensor_reset_response_invalid",
            "Sensor returned invalid reset status JSON",
            retryable=False,
        ) from exc
    if not isinstance(result, dict):
        raise SensorResetCommunicationError(
            "sensor_reset_response_invalid",
            "Sensor reset status must be a JSON object",
            retryable=False,
        )
    try:
        validated = SensorDataResetResponse.model_validate(result)
    except ValidationError as exc:
        raise SensorResetCommunicationError(
            "sensor_reset_response_invalid",
            "Sensor reset status did not match the canonical response contract",
            retryable=False,
        ) from exc
    return verify_reset_response(
        response=validated.model_dump(mode="json", exclude_none=True),
        secret=secret,
        device_id=device.id,
        operation_id=operation_id,
        target_generation=target_generation,
    )
