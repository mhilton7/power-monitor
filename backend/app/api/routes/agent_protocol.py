from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from app.api.deps import AppSettings, DbSession, audit_event
from app.api.routes.device_protocol import (
    _complete_firmware_if_stable,
    _confirm_firmware_reading,
    claim_enrollment,
)
from app.api.routes.firmware import _verified_artifact
from app.data_reset.service import redact_history_values
from app.db.models import (
    DeviceCapability,
    DeviceCommand,
    DeviceConfigVersion,
    DeviceCredential,
    DeviceDataState,
    DeviceEvent,
    DeviceEventSyncCursor,
    DeviceHeartbeat,
    FirmwareDeployment,
    FirmwareRelease,
    Site,
    SyncCursor,
)
from app.firmware_lifecycle import transition_firmware_deployment
from app.ingestion.service import ingest_readings
from app.network_policy import effective_client_ip, evaluate_site_address
from app.ota import OTA_TRUST_MODE, release_compatibility
from app.problem import ProblemError
from app.schemas import (
    SIGNED_BIGINT_MAX,
    DeviceEventInput,
    EnrollmentClaim,
    EnrollmentClaimResponse,
    Reading,
    ReadingBatchResponse,
    UnavailableSequenceRange,
)
from app.security.agent_protocol import (
    AGENT_PROTOCOL,
    VerifiedAgentRequest,
    calculate_agent_response_signature,
    verify_agent_request,
)
from app.security.protocol import ProtocolAuthError, SecretCipher

router = APIRouter(prefix="/api/v2/agent", tags=["headless agent protocol"])
RANGE_PATTERN = re.compile(r"^bytes=(\d+)-(\d+)$")
MAX_FIRMWARE_RANGE_BYTES = 64 * 1024


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentHeartbeat(AgentModel):
    protocol: Literal["pm-agent/2.0.0"]
    device_id: str = Field(min_length=36, max_length=36)
    boot_id: str = Field(min_length=36, max_length=36)
    firmware_version: str = Field(min_length=1, max_length=32)
    build_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    uptime_ms: int = Field(ge=0, le=2**63 - 1)
    reset_reason: str = Field(min_length=1, max_length=64)
    wifi: dict[str, Any] = Field(default_factory=dict)
    latest: dict[str, Any] | None = None
    pzem: dict[str, Any] = Field(default_factory=dict)
    sd: dict[str, Any] = Field(default_factory=dict)
    sequences: dict[str, int]
    reset_projection: dict[str, Any]
    capabilities: dict[str, Any]
    configuration_revision: int = Field(ge=0)
    reset_generation: int = Field(ge=0)
    reset_operation: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)
    task_stack_margins: dict[str, int] = Field(default_factory=dict)
    last_command_result: dict[str, Any] | None = None
    ota: dict[str, Any] = Field(default_factory=dict)
    time_trusted: bool = False


class AgentReadingBatch(AgentModel):
    protocol: Literal["pm-agent/2.0.0"]
    device_id: str = Field(min_length=36, max_length=36)
    data_generation: int = Field(ge=0)
    readings: list[Reading] = Field(min_length=1, max_length=500)
    unavailable_sequence_ranges: list[UnavailableSequenceRange] = Field(
        default_factory=list, max_length=500
    )


class AgentCommandResult(AgentModel):
    protocol: Literal["pm-agent/2.0.0"]
    device_id: str = Field(min_length=36, max_length=36)
    command_id: str = Field(min_length=36, max_length=36)
    state: Literal["accepted", "running", "completed", "failed", "cancelled"]
    result: dict[str, Any] = Field(default_factory=dict)
    failure_code: str | None = Field(
        default=None, max_length=80, pattern=r"^[a-z0-9][a-z0-9_]{0,79}$"
    )


class AgentEventBatch(AgentModel):
    protocol: Literal["pm-agent/2.0.0"]
    device_id: str = Field(min_length=36, max_length=36)
    data_generation: int = Field(default=0, ge=0)
    first_stored_event_sequence: int | None = Field(default=None, ge=1, le=SIGNED_BIGINT_MAX)
    events: list[DeviceEventInput] = Field(min_length=1, max_length=100)


def _safe_int(value: Any, default: int = 0) -> int:
    valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return value if valid else default


def _advance_headless_deployment(
    deployment: FirmwareDeployment, target_state: str, now: datetime
) -> None:
    ordered = [
        "scheduled",
        "offered",
        "manifest_authenticated",
        "download_started",
        "downloading",
        "binary_verified",
        "partition_written",
        "rebooting",
        "post_boot_validation",
        "validated",
    ]
    if deployment.state == target_state:
        return
    if target_state == "failed":
        transition_firmware_deployment(deployment, "failed", now)
        return
    if deployment.state not in ordered or target_state not in ordered:
        return
    current = ordered.index(deployment.state)
    target = ordered.index(target_state)
    while current < target:
        current += 1
        transition_firmware_deployment(deployment, ordered[current], now)


async def _record_headless_ota_result(
    session: DbSession,
    command: DeviceCommand,
    result: dict[str, Any],
    state: str,
    now: datetime,
) -> None:
    if command.command_type != "ota_update":
        return
    deployment_id = result.get("deployment_id")
    release_id = result.get("release_id")
    if not isinstance(deployment_id, str) or not isinstance(release_id, str):
        raise ProblemError(
            422,
            "OTA result invalid",
            "The agent omitted deployment identity",
            "ota_result_invalid",
        )
    deployment = await session.get(FirmwareDeployment, deployment_id, with_for_update=True)
    if (
        deployment is None
        or deployment.device_id != command.device_id
        or deployment.firmware_release_id != release_id
    ):
        raise ProblemError(
            409,
            "OTA result conflict",
            "The OTA result does not match its assigned deployment",
            "ota_result_conflict",
        )
    bytes_received = _safe_int(result.get("bytes_received"))
    image_size = _safe_int(result.get("image_size"))
    progress = _safe_int(result.get("progress"))
    if image_size <= 0 or bytes_received > image_size or progress > 100:
        raise ProblemError(
            422,
            "OTA result invalid",
            "The agent reported invalid OTA progress",
            "ota_result_invalid",
        )
    deployment.bytes_received = max(deployment.bytes_received, bytes_received)
    deployment.progress = max(deployment.progress, progress)
    deployment.last_report_at = now
    deployment.last_report_payload = dict(result)
    reported_stage = result.get("state")
    stage = reported_stage if isinstance(reported_stage, str) else ""
    target = {
        "accepted": "offered",
        "downloading": "downloading",
        "rebooting": "rebooting",
        "validated": "validated",
        "failed": "failed",
    }.get(stage)
    if state == "failed":
        target = "failed"
    if target is not None:
        _advance_headless_deployment(deployment, target, now)
    if target == "downloading":
        deployment.downloaded_at = now if bytes_received == image_size else None
    elif target == "rebooting":
        deployment.installed_at = now
    elif target == "validated":
        deployment.validated_at = now
        deployment.validated_version = result.get("target_version")
        deployment.validated_build_hash = result.get("target_build_hash")
    elif target == "failed":
        deployment.failure_code = "ota_agent_failed"
        deployment.failure_summary = "The headless agent reported an OTA failure."


async def _record_headless_ota_heartbeat(
    session: DbSession,
    payload: AgentHeartbeat,
    settings: AppSettings,
    now: datetime,
) -> None:
    ota = payload.ota if isinstance(payload.ota, dict) else {}
    if ota.get("state") != "validated":
        return
    deployment = await session.scalar(
        select(FirmwareDeployment)
        .where(
            FirmwareDeployment.device_id == payload.device_id,
            FirmwareDeployment.state.in_(["validated", "awaiting_heartbeat"]),
        )
        .order_by(FirmwareDeployment.scheduled_at.desc())
        .limit(1)
        .with_for_update()
    )
    if deployment is None:
        return
    release = await session.get(FirmwareRelease, deployment.firmware_release_id)
    if (
        release is None
        or payload.firmware_version != release.version
        or payload.build_hash != release.build_hash
    ):
        return
    if deployment.state == "validated":
        transition_firmware_deployment(deployment, "awaiting_heartbeat", now)
        deployment.stabilization_started_at = deployment.stabilization_started_at or now
    deployment.last_boot_id = payload.boot_id
    deployment.verification_heartbeats += 1
    await _complete_firmware_if_stable(session, deployment, settings, now)


async def authenticated_agent(
    request: Request, session: DbSession, settings: AppSettings
) -> VerifiedAgentRequest:
    body = await request.body()
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    try:
        agent = await verify_agent_request(
            session=session,
            headers={key.lower(): value for key, value in request.headers.items()},
            method=request.method,
            target=target,
            body=body,
            cipher=SecretCipher(settings.app_master_key),
        )
    except ProtocolAuthError as exc:
        raise ProblemError(
            exc.status_code,
            "Headless agent authentication failed",
            str(exc),
            exc.code,
        ) from exc
    site = await session.get(Site, agent.device.site_id)
    direct = request.client.host if request.client else ""
    decision = None
    if site is not None:
        try:
            source_ip = effective_client_ip(
                direct,
                request.headers.get("x-forwarded-for"),
                settings.trusted_proxy_cidrs,
            )
            decision = await evaluate_site_address(session, site, "device_ingress", source_ip)
        except ProblemError:
            decision = None
    if decision is None or not decision.allowed:
        session.add(
            audit_event(
                action="network_policy.agent_request_blocked",
                actor_type="device",
                actor_id=agent.device.id,
                request=request,
                object_type="device",
                object_id=agent.device.id,
                outcome="blocked",
                details={"direction": "device_ingress"},
            )
        )
        await session.commit()
        raise ProblemError(
            403,
            "Device access denied",
            "The signed agent request is not accepted from this network",
            "device_network_blocked",
        )
    request.state.device_source_ip = decision.address
    return agent


Agent = Annotated[VerifiedAgentRequest, Depends(authenticated_agent)]


def _assert_identity(
    payload_device: str, payload_boot: str | None, agent: VerifiedAgentRequest
) -> None:
    if payload_device != agent.device.id or (
        payload_boot is not None and payload_boot != agent.boot_id
    ):
        raise ProblemError(
            403,
            "Agent identity mismatch",
            "Signed headers and payload identity differ",
            "device_id_mismatch",
        )


def _canonical_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_file_range(path: Path, start: int, length: int) -> bytes:
    with path.open("rb") as stream:
        stream.seek(start)
        return stream.read(length)


def _signed_response(
    agent: VerifiedAgentRequest, payload: dict[str, Any], *, status: int = 200
) -> Response:
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return _signed_bytes_response(agent, body, status=status, media_type="application/json")


def _signed_bytes_response(
    agent: VerifiedAgentRequest,
    body: bytes,
    *,
    status: int,
    media_type: str,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    digest, signature = calculate_agent_response_signature(
        secret=agent.secret,
        request_nonce=agent.nonce,
        request_counter=agent.counter,
        status=status,
        body=body,
    )
    headers = {
        "X-PM-Agent-Protocol": AGENT_PROTOCOL,
        "X-PM-Request-Nonce": agent.nonce,
        "X-PM-Request-Counter": str(agent.counter),
        "X-PM-Content-SHA256": digest,
        "X-PM-Signature": signature,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    headers.update(extra_headers or {})
    return Response(
        content=body,
        status_code=status,
        media_type=media_type,
        headers=headers,
    )


async def _next_command(
    session: DbSession,
    device_id: str,
    now: datetime,
    settings: AppSettings,
) -> dict[str, Any] | None:
    await session.execute(
        update(DeviceCommand)
        .where(
            DeviceCommand.device_id == device_id,
            DeviceCommand.state.in_(["queued", "delivered", "accepted", "running"]),
            DeviceCommand.expires_at <= now,
        )
        .values(state="expired", failure_code="command_expired", completed_at=now)
    )
    command = await session.scalar(
        select(DeviceCommand)
        .where(
            DeviceCommand.device_id == device_id,
            DeviceCommand.state.in_(["queued", "delivered", "accepted", "running"]),
            DeviceCommand.created_at <= now,
            DeviceCommand.expires_at > now,
        )
        .order_by(DeviceCommand.created_at, DeviceCommand.id)
        .limit(1)
        .with_for_update()
    )
    if command is None:
        return None
    if command.state in {"queued", "delivered"}:
        command.state = "delivered"
    command.delivered_at = command.delivered_at or now
    command.delivery_attempts += 1
    command_payload = dict(command.payload)
    credential_rotation_id = command_payload.get("credential_rotation_id")
    if command.command_type == "apply_configuration" and isinstance(credential_rotation_id, str):
        credential = await session.get(DeviceCredential, credential_rotation_id)
        if credential is None or credential.device_id != device_id:
            command.state = "failed"
            command.failure_code = "credential_rotation_missing"
            command.completed_at = now
            return None
        command_payload["credential_secret"] = (
            SecretCipher(settings.app_master_key).decrypt(credential.encrypted_secret).decode()
        )
    return {
        "command_id": command.id,
        "device_id": command.device_id,
        "type": command.command_type,
        "created_at": _canonical_utc(command.created_at),
        "expires_at": _canonical_utc(command.expires_at),
        "expected_current_state": command.expected_state,
        "payload": command_payload,
        "idempotency_key": command.idempotency_key,
    }


@router.post("/enroll", response_model=EnrollmentClaimResponse, status_code=201)
async def enroll_agent(
    payload: EnrollmentClaim,
    request: Request,
    session: DbSession,
    settings: AppSettings,
) -> EnrollmentClaimResponse:
    if payload.protocol_version != AGENT_PROTOCOL:
        raise ProblemError(
            426,
            "Protocol upgrade required",
            f"Use {AGENT_PROTOCOL}",
            "protocol_incompatible",
        )
    return await claim_enrollment(payload, request, session, settings)


@router.post("/heartbeat")
async def heartbeat(
    payload: AgentHeartbeat,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    agent: Agent,
) -> Response:
    _assert_identity(payload.device_id, payload.boot_id, agent)
    now = datetime.now(UTC)
    sequences = payload.sequences
    acknowledgement = _safe_int(sequences.get("server_acknowledgement"))
    maximum_seen = _safe_int(sequences.get("maximum_seen_sequence"))
    floor = _safe_int(sequences.get("sequence_floor"))
    next_sequence = _safe_int(sequences.get("next_sequence"), 1)
    newest_stored = _safe_int(sequences.get("newest_stored_sequence"))
    newest_syncable = _safe_int(sequences.get("newest_syncable_sequence"))
    oldest_stored = _safe_int(sequences.get("oldest_stored_sequence"))
    if next_sequence <= max(floor, maximum_seen, acknowledgement, newest_stored, newest_syncable):
        raise ProblemError(
            422,
            "Sequence evidence invalid",
            "next_sequence must remain above every trusted boundary",
            "sequence_evidence_invalid",
        )
    cursor = await session.get(SyncCursor, agent.device.id, with_for_update=True)
    if cursor is None:
        cursor = SyncCursor(
            device_id=agent.device.id,
            highest_contiguous_sequence=0,
            maximum_seen_sequence=0,
            data_generation=payload.reset_generation,
            reset_boundary=0,
            updated_at=now,
        )
        session.add(cursor)
        await session.flush()
    server_maximum = max(int(cursor.maximum_seen_sequence), maximum_seen)
    server_ack = max(int(cursor.highest_contiguous_sequence), acknowledgement)
    data_state = await session.get(DeviceDataState, agent.device.id, with_for_update=True)
    required_generation = max(
        int(cursor.data_generation),
        int(data_state.data_generation) if data_state is not None else 0,
    )
    reset_required = bool(
        (data_state is not None and data_state.reset_required_on_reconnect)
        or payload.reset_generation < required_generation
    )
    latest_watts: Decimal | None = None
    if payload.latest is not None:
        try:
            latest_watts = Decimal(str(payload.latest.get("watts")))
        except (InvalidOperation, TypeError):
            latest_watts = None
    rssi = payload.wifi.get("rssi_dbm")
    rssi_value = rssi if isinstance(rssi, int) and -127 <= rssi <= 0 else None
    pzem_ok = payload.pzem.get("ok") is True
    sd_ok = payload.sd.get("ok") is True
    backlog = max(0, newest_syncable - server_ack)
    heartbeat_payload = payload.model_dump(mode="json")
    heartbeat_payload.update(
        {
            "protocol_version": AGENT_PROTOCOL,
            "oldest_stored_sequence": oldest_stored,
            "newest_stored_sequence": newest_stored,
            "newest_syncable_sequence": newest_syncable,
            "server_ack_sequence": acknowledgement,
            "server_maximum_seen_sequence": maximum_seen,
            "backlog_estimate": backlog,
        }
    )
    session.add(
        DeviceHeartbeat(
            device_id=agent.device.id,
            boot_id=payload.boot_id,
            received_at=now,
            device_time=None,
            source_ip=getattr(request.state, "device_source_ip", None),
            current_watts=latest_watts,
            rssi_dbm=rssi_value,
            pzem_ok=pzem_ok,
            sd_ok=sd_ok,
            time_trusted=payload.time_trusted,
            data_generation=payload.reset_generation,
            newest_sequence=newest_stored,
            backlog_estimate=backlog,
            payload=heartbeat_payload,
        )
    )
    agent.device.protocol_version = AGENT_PROTOCOL
    agent.device.connection_mode = "push"
    agent.device.firmware_version = payload.firmware_version
    agent.device.firmware_build_hash = payload.build_hash
    agent.device.status = "online" if not reset_required else "reset_required"
    agent.device.last_seen_at = now
    reset_capability = payload.capabilities.get("data_reset", {})
    capability = await session.get(DeviceCapability, agent.device.id)
    capability_features = {
        "agent_protocol": AGENT_PROTOCOL,
        "data_reset": reset_capability,
        "supported_endpoints": ["data-reset/1.0.0"],
        "outbound_commands": True,
        "runtime_http_listener": False,
    }
    if capability is None:
        session.add(
            DeviceCapability(
                device_id=agent.device.id,
                hardware_target="esp32s3",
                pzem_model="PZEM-004T-v3",
                sd_required=True,
                features=capability_features,
                reported_at=now,
            )
        )
    else:
        capability.features = capability_features
        capability.reported_at = now
    await _record_headless_ota_heartbeat(session, payload, settings, now)
    command = await _next_command(session, agent.device.id, now, settings)
    await session.commit()
    return _signed_response(
        agent,
        {
            "protocol": AGENT_PROTOCOL,
            "server_acknowledgement": server_ack,
            "server_maximum_seen_sequence": server_maximum,
            "required_reset_generation": required_generation,
            "reset_required": reset_required,
            "next_sequence_floor": max(server_maximum, floor),
            "desired_configuration_revision": agent.device.desired_config_version,
            "recommended_heartbeat_interval_seconds": settings.heartbeat_expectation_seconds,
            "immediate_backlog_sync_requested": backlog > 0 and not reset_required,
            "server_time": _canonical_utc(now),
            "command": command,
        },
    )


@router.post("/readings")
async def readings(
    payload: AgentReadingBatch,
    session: DbSession,
    settings: AppSettings,
    agent: Agent,
) -> Response:
    _assert_identity(payload.device_id, None, agent)
    result: ReadingBatchResponse = await ingest_readings(
        session,
        device_id=agent.device.id,
        readings=payload.readings,
        source="push",
        data_generation=payload.data_generation,
        unavailable_sequence_ranges=payload.unavailable_sequence_ranges,
        maximum_clock_skew_seconds=settings.max_device_clock_skew_seconds,
    )
    await _confirm_firmware_reading(
        session,
        agent.device.id,
        payload.readings,
        set(result.accepted) | set(result.duplicates),
        settings,
        datetime.now(UTC),
    )
    await session.commit()
    return _signed_response(agent, result.model_dump(mode="json"))


@router.post("/events")
async def events(
    payload: AgentEventBatch,
    session: DbSession,
    agent: Agent,
) -> Response:
    _assert_identity(payload.device_id, None, agent)
    now = datetime.now(UTC)
    data_state = await session.get(DeviceDataState, agent.device.id, with_for_update=True)
    redact_measurements = bool(
        data_state is not None
        and (
            payload.data_generation != data_state.data_generation
            or data_state.ingestion_gate != "open"
        )
    )
    existing = {
        item.event_id: item
        for item in await session.scalars(
            select(DeviceEvent).where(
                DeviceEvent.device_id == agent.device.id,
                DeviceEvent.event_id.in_([event.event_id for event in payload.events]),
            )
        )
    }
    accepted: list[str] = []
    duplicates: list[str] = []
    event_sequences: list[int] = []
    owners: dict[int, str] = {}
    for event in payload.events:
        event_sequence = event.evidence.get("event_sequence")
        if isinstance(event_sequence, int) and not isinstance(event_sequence, bool):
            owner = owners.get(event_sequence)
            if owner is not None and owner != event.event_id:
                raise ProblemError(
                    422,
                    "Event sequence conflict",
                    "One sequence cannot identify multiple events",
                    "event_sequence_conflict",
                )
            owners[event_sequence] = event.event_id
            event_sequences.append(event_sequence)
        stored = existing.get(event.event_id)
        if stored is not None:
            if stored.event_sequence is None and isinstance(event_sequence, int):
                stored.event_sequence = event_sequence
            duplicates.append(event.event_id)
            continue
        event_predates_reset = bool(
            data_state is not None
            and data_state.last_reset_at is not None
            and event.occurred_at.astimezone(UTC) <= data_state.last_reset_at.astimezone(UTC)
        )
        evidence = (
            redact_history_values(event.evidence)
            if redact_measurements or event_predates_reset
            else event.evidence
        )
        session.add(
            DeviceEvent(
                device_id=agent.device.id,
                event_id=event.event_id,
                event_sequence=event_sequence,
                occurred_at=event.occurred_at,
                received_at=now,
                category=event.category,
                severity=event.severity,
                evidence=evidence,
            )
        )
        accepted.append(event.event_id)
    ordered = sorted(set(event_sequences))
    complete = bool(ordered) and len(ordered) == len(payload.events)
    explicit_boundary = (
        complete
        and payload.first_stored_event_sequence is not None
        and payload.first_stored_event_sequence == ordered[0]
    )
    await session.flush()
    cursor = await session.get(DeviceEventSyncCursor, agent.device.id, with_for_update=True)
    if cursor is None:
        cursor = DeviceEventSyncCursor(
            device_id=agent.device.id,
            highest_contiguous_sequence=ordered[0] - 1 if explicit_boundary else 0,
            maximum_seen_sequence=0,
            updated_at=now,
        )
        session.add(cursor)
    if complete:
        cursor.maximum_seen_sequence = max(cursor.maximum_seen_sequence, ordered[-1])
        persisted = set(
            await session.scalars(
                select(DeviceEvent.event_sequence).where(
                    DeviceEvent.device_id == agent.device.id,
                    DeviceEvent.event_sequence.is_not(None),
                    DeviceEvent.event_sequence > cursor.highest_contiguous_sequence,
                    DeviceEvent.event_sequence <= cursor.maximum_seen_sequence,
                )
            )
        )
        while cursor.highest_contiguous_sequence + 1 in persisted:
            cursor.highest_contiguous_sequence += 1
        cursor.updated_at = now
    await session.commit()
    return _signed_response(
        agent,
        {
            "accepted": accepted,
            "duplicates": duplicates,
            "highest_contiguous_event_sequence": (
                cursor.highest_contiguous_sequence if complete else 0
            ),
        },
    )


@router.post("/commands/results")
async def command_result(
    payload: AgentCommandResult,
    session: DbSession,
    agent: Agent,
) -> Response:
    _assert_identity(payload.device_id, None, agent)
    command = await session.get(DeviceCommand, payload.command_id, with_for_update=True)
    if command is None or command.device_id != agent.device.id:
        raise ProblemError(
            404, "Command not found", "Command is not assigned to this device", "command_unknown"
        )
    terminal = {"completed", "failed", "cancelled"}
    if command.state in terminal:
        if command.state != payload.state or (command.result or {}) != payload.result:
            raise ProblemError(
                409,
                "Command result conflict",
                "Terminal result is immutable",
                "command_result_conflict",
            )
    else:
        await _record_headless_ota_result(
            session,
            command,
            payload.result,
            payload.state,
            datetime.now(UTC),
        )
        command.state = payload.state
        command.result = payload.result
        command.failure_code = payload.failure_code
        now = datetime.now(UTC)
        if payload.state == "accepted":
            command.accepted_at = command.accepted_at or now
        elif payload.state == "running":
            command.accepted_at = command.accepted_at or now
            command.started_at = command.started_at or now
        elif payload.state in terminal:
            command.completed_at = now
        if command.command_type == "apply_configuration" and payload.state in terminal:
            revision = command.payload.get("configuration_revision")
            revision_number = revision if type(revision) is int else None
            config = (
                await session.scalar(
                    select(DeviceConfigVersion).where(
                        DeviceConfigVersion.device_id == agent.device.id,
                        DeviceConfigVersion.version == revision_number,
                    )
                )
                if revision_number is not None
                else None
            )
            if config is not None and revision_number is not None:
                applied = payload.state == "completed"
                config.status = "applied" if applied else "rejected"
                config.report = {
                    "protocol_version": AGENT_PROTOCOL,
                    "device_id": agent.device.id,
                    "version": revision_number,
                    "status": config.status,
                    "result": payload.result,
                    "failure_code": payload.failure_code,
                }
                config.reported_at = now
                if applied:
                    agent.device.effective_config_version = max(
                        int(agent.device.effective_config_version), revision_number
                    )
            rotation_id = command.payload.get("credential_rotation_id")
            if payload.state == "completed" and isinstance(rotation_id, str):
                rotated = await session.get(DeviceCredential, rotation_id)
                if rotated is not None and rotated.device_id == agent.device.id:
                    rotated.confirmed_at = now
                    old_credentials = await session.scalars(
                        select(DeviceCredential).where(
                            DeviceCredential.device_id == agent.device.id,
                            DeviceCredential.id != rotated.id,
                            DeviceCredential.revoked_at.is_(None),
                        )
                    )
                    for credential in old_credentials:
                        credential.revoked_at = now
                        credential.valid_until = now
    await session.commit()
    return _signed_response(
        agent, {"recorded": True, "command_id": command.id, "state": command.state}
    )


@router.get("/firmware/{release_id}")
async def firmware_range(
    release_id: str,
    session: DbSession,
    settings: AppSettings,
    agent: Agent,
    range_header: Annotated[str, Header(alias="Range")],
) -> Response:
    now = datetime.now(UTC)
    command = await session.scalar(
        select(DeviceCommand)
        .where(
            DeviceCommand.device_id == agent.device.id,
            DeviceCommand.command_type == "ota_update",
            DeviceCommand.state.in_(["delivered", "accepted", "running"]),
            DeviceCommand.expires_at > now,
        )
        .order_by(DeviceCommand.created_at.desc(), DeviceCommand.id.desc())
        .limit(1)
        .with_for_update()
    )
    if command is None or command.payload.get("release_id") != release_id:
        raise ProblemError(
            404,
            "Firmware unavailable",
            "No active OTA command authorizes this artifact",
            "firmware_unavailable",
        )
    release = await session.get(FirmwareRelease, release_id)
    if (
        release is None
        or release.trust_mode != OTA_TRUST_MODE
        or release.verification_status != "verified"
    ):
        raise ProblemError(
            404,
            "Firmware unavailable",
            "The verified firmware artifact is unavailable",
            "firmware_unavailable",
        )
    capability = await session.get(DeviceCapability, agent.device.id)
    compatibility = release_compatibility(agent.device, capability, release)
    reasons = set(compatibility["reasons"])
    reasons.discard("already_current")
    if command.payload.get("allow_downgrade") is True:
        reasons.discard("downgrade_requires_confirmation")
    if reasons:
        raise ProblemError(
            409,
            "Firmware incompatible",
            "The assigned release is no longer compatible with this agent",
            "firmware_incompatible",
        )
    match = RANGE_PATTERN.fullmatch(range_header)
    if match is None:
        raise ProblemError(
            416,
            "Firmware range invalid",
            "Use one explicit inclusive byte range",
            "firmware_range_invalid",
        )
    start, end = (int(value) for value in match.groups())
    if start > end or end >= release.size_bytes or end - start + 1 > MAX_FIRMWARE_RANGE_BYTES:
        raise ProblemError(
            416,
            "Firmware range invalid",
            "The requested range is outside the artifact or exceeds 64 KiB",
            "firmware_range_invalid",
        )
    path = await _verified_artifact(release, session, settings)
    body = await run_in_threadpool(_read_file_range, path, start, end - start + 1)
    if len(body) != end - start + 1:
        raise ProblemError(
            503,
            "Firmware integrity failure",
            "The verified artifact could not provide the complete range",
            "firmware_integrity_failure",
        )
    command.state = "running"
    command.accepted_at = command.accepted_at or now
    command.started_at = command.started_at or now
    await session.commit()
    return _signed_bytes_response(
        agent,
        body,
        status=206,
        media_type="application/octet-stream",
        extra_headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{release.size_bytes}",
            "Content-Length": str(len(body)),
            "X-PM-Firmware-SHA256": release.sha256,
        },
    )
