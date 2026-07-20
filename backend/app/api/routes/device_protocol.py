from __future__ import annotations

import hashlib
import ipaddress
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import AppSettings, DbSession, Verified, audit_event
from app.db.models import (
    Device,
    DeviceAddress,
    DeviceCapability,
    DeviceConfigVersion,
    DeviceCredential,
    DeviceEvent,
    DeviceHeartbeat,
    DeviceStatusSnapshot,
    EnrollmentToken,
    FirmwareDeployment,
    FirmwareRelease,
    SequenceGap,
    Site,
    SyncCursor,
)
from app.ingestion.service import ingest_readings
from app.problem import ProblemError
from app.schemas import (
    ConfigReport,
    DeviceEventBatch,
    EnrollmentClaim,
    EnrollmentClaimResponse,
    Heartbeat,
    HeartbeatResponse,
    ReadingBatch,
    ReadingBatchResponse,
)
from app.security.protocol import PROTOCOL, SecretCipher

router = APIRouter(prefix="/api/v1", tags=["device protocol"])


def _assert_device_id(payload_device_id: str, verified_device_id: str) -> None:
    if payload_device_id != verified_device_id:
        raise ProblemError(
            403,
            "Device identity mismatch",
            "Signed header and payload device IDs differ",
            "device_id_mismatch",
        )


@router.post("/device-enrollment/claim", response_model=EnrollmentClaimResponse, status_code=201)
async def claim_enrollment(
    payload: EnrollmentClaim,
    request: Request,
    session: DbSession,
    settings: AppSettings,
) -> EnrollmentClaimResponse:
    now = datetime.now(UTC)
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    token = await session.scalar(
        select(EnrollmentToken).where(EnrollmentToken.token_hash == token_hash).with_for_update()
    )
    if token is None or token.revoked_at is not None or token.consumed_at is not None:
        raise ProblemError(
            401, "Enrollment denied", "Token is invalid or already used", "invalid_enrollment_token"
        )
    expires_at = (
        token.expires_at if token.expires_at.tzinfo else token.expires_at.replace(tzinfo=UTC)
    )
    if expires_at <= now:
        raise ProblemError(
            401, "Enrollment denied", "Token has expired", "enrollment_token_expired"
        )
    if payload.protocol_version != PROTOCOL:
        raise ProblemError(
            426,
            "Protocol upgrade required",
            f"This server requires {PROTOCOL}",
            "protocol_incompatible",
        )
    if not payload.capabilities.sd_present or not payload.capabilities.sd_required:
        raise ProblemError(
            422,
            "Required storage unavailable",
            "Enrollment requires operational microSD storage",
            "sd_required",
        )
    if await session.scalar(select(Device.id).where(Device.hardware_id == payload.hardware_id)):
        raise ProblemError(
            409, "Already enrolled", "Hardware identity already exists", "hardware_exists"
        )
    preassignment = token.preassignment
    site_id = preassignment.get("site_id")
    site = (
        await session.get(Site, site_id) if site_id else await session.scalar(select(Site).limit(1))
    )
    if site is None:
        raise ProblemError(409, "No site", "Create a site before enrollment", "site_required")
    device = Device(
        site_id=site.id,
        circuit_id=preassignment.get("circuit_id"),
        hardware_id=payload.hardware_id,
        name=preassignment.get("name")
        or payload.requested_name
        or f"Sensor {payload.hardware_id[-6:]}",
        connection_mode=preassignment.get("connection_mode", "push"),
        measurement_role=preassignment.get("measurement_role", "submeter"),
        cost_scope="energy_only",
        include_in_default_site_total=False,
        ct_rating_amps=preassignment.get("ct_rating_amps", "100"),
        protocol_version=PROTOCOL,
    )
    session.add(device)
    await session.flush()
    secret_text = secrets.token_urlsafe(48)
    secret = secret_text.encode()
    fingerprint = hashlib.sha256(secret).hexdigest()
    credential = DeviceCredential(
        device_id=device.id,
        encrypted_secret=SecretCipher(settings.app_master_key).encrypt(secret),
        fingerprint=fingerprint,
        valid_from=now,
        created_at=now,
    )
    session.add(credential)
    session.add(
        DeviceCapability(
            device_id=device.id,
            hardware_target=payload.capabilities.hardware_target,
            pzem_model=payload.capabilities.pzem_model,
            sd_required=True,
            features={"supported_endpoints": payload.capabilities.supported_endpoints},
            reported_at=now,
        )
    )
    session.add(
        DeviceConfigVersion(
            device_id=device.id,
            version=1,
            desired_config={
                "heartbeat_interval_seconds": settings.heartbeat_expectation_seconds,
                "durable_log_interval_seconds": 60,
                "live_update_interval_seconds": 5,
                "ct_rating_amps": str(device.ct_rating_amps),
            },
            config_hash=hashlib.sha256(b"initial-config-v1").hexdigest(),
            status="pending",
            created_at=now,
        )
    )
    session.add(
        SyncCursor(
            device_id=device.id,
            highest_contiguous_sequence=0,
            maximum_seen_sequence=0,
            updated_at=now,
        )
    )
    token.consumed_at = now
    session.add(
        audit_event(
            action="device.enrolled",
            actor_type="device",
            actor_id=device.id,
            request=request,
            object_type="device",
            object_id=device.id,
            details={
                "credential_fingerprint": fingerprint,
                "hardware_target": payload.capabilities.hardware_target,
            },
        )
    )
    await session.commit()
    return EnrollmentClaimResponse(
        protocol_version=PROTOCOL,
        device_id=device.id,
        enrollment_secret=secret_text,
        credential_fingerprint=fingerprint,
        effective_metadata={
            "name": device.name,
            "site_id": device.site_id,
            "circuit_id": device.circuit_id,
            "measurement_role": device.measurement_role,
            "cost_scope": "energy_only",
            "ct_rating_amps": str(device.ct_rating_amps),
        },
        server_ota_signing_public_key=None,
        heartbeat_policy={"expected_seconds": settings.heartbeat_expectation_seconds},
        sync_policy={
            "maximum_batch_records": settings.max_reading_batch_records,
            "durable_interval_seconds": 60,
        },
    )


def _status_from_heartbeat(payload: Heartbeat) -> str:
    if not payload.time.trusted:
        return "time_unsynchronized"
    if not payload.pzem.ok:
        return "api_healthy_meter_failed"
    if not payload.sd.ok:
        return "api_healthy_storage_failed"
    if payload.backlog_estimate > 0:
        return "online_with_backlog"
    if payload.connection_mode == "push":
        return "online_push_only"
    return "online_synchronized"


@router.post("/device-heartbeats", response_model=HeartbeatResponse)
async def heartbeat(
    payload: Heartbeat,
    request: Request,
    verified: Verified,
    session: DbSession,
    settings: AppSettings,
) -> HeartbeatResponse:
    _assert_device_id(payload.device_id, verified.device.id)
    if payload.protocol_version != PROTOCOL:
        raise ProblemError(
            426, "Protocol upgrade required", f"Use {PROTOCOL}", "protocol_incompatible"
        )
    now = datetime.now(UTC)
    device = verified.device
    device.last_seen_at = now
    device.firmware_version = payload.firmware_version
    device.firmware_build_hash = payload.firmware_build_hash
    device.connection_mode = payload.connection_mode
    device.effective_config_version = payload.configuration_version
    device.status = _status_from_heartbeat(payload)
    heartbeat_row = DeviceHeartbeat(
        device_id=device.id,
        boot_id=payload.boot_id,
        received_at=now,
        device_time=payload.latest.measured_at if payload.latest else None,
        source_ip=request.client.host if request.client else None,
        current_watts=payload.latest.power_w if payload.latest else None,
        rssi_dbm=payload.rssi_dbm,
        pzem_ok=payload.pzem.ok,
        sd_ok=payload.sd.ok,
        time_trusted=payload.time.trusted,
        newest_sequence=payload.newest_stored_sequence,
        backlog_estimate=payload.backlog_estimate,
        payload=payload.model_dump(mode="json"),
    )
    session.add(heartbeat_row)
    session.add(
        DeviceStatusSnapshot(
            device_id=device.id,
            captured_at=now,
            status=device.status,
            evidence={
                "heartbeat": True,
                "pzem": payload.pzem.status,
                "sd": payload.sd.status,
                "time_trusted": payload.time.trusted,
                "backlog": payload.backlog_estimate,
            },
        )
    )
    if payload.current_ip:
        validation_error = None
        try:
            address = ipaddress.ip_address(payload.current_ip)
            if address.is_loopback or address.is_link_local or address.is_multicast:
                validation_error = "address is never pollable"
        except ValueError:
            validation_error = "not an IP literal; hostname is tracked separately"
        existing_address = await session.scalar(
            select(DeviceAddress).where(
                DeviceAddress.device_id == device.id,
                DeviceAddress.host == payload.current_ip,
                DeviceAddress.source == "heartbeat",
            )
        )
        if existing_address:
            existing_address.last_seen_at = now
            existing_address.validation_error = validation_error
        else:
            session.add(
                DeviceAddress(
                    device_id=device.id,
                    host=payload.current_ip,
                    port=443,
                    scheme="https",
                    source="heartbeat",
                    first_seen_at=now,
                    last_seen_at=now,
                    validation_error=validation_error,
                )
            )
    cursor = await session.get(SyncCursor, device.id)
    if cursor is None:
        cursor = SyncCursor(
            device_id=device.id,
            highest_contiguous_sequence=0,
            maximum_seen_sequence=0,
            updated_at=now,
        )
        session.add(cursor)
    gaps = list(
        await session.scalars(
            select(SequenceGap)
            .where(SequenceGap.device_id == device.id, SequenceGap.resolved_at.is_(None))
            .order_by(SequenceGap.start_sequence)
        )
    )
    release_available = bool(
        await session.scalar(
            select(FirmwareDeployment.id).where(
                FirmwareDeployment.device_id == device.id,
                FirmwareDeployment.status.in_(["scheduled", "available"]),
            )
        )
    )
    await session.commit()
    return HeartbeatResponse(
        server_receive_time=now,
        highest_contiguous_accepted_sequence=cursor.highest_contiguous_sequence,
        gap_ranges=[(gap.start_sequence, gap.end_sequence) for gap in gaps],
        desired_configuration_version=device.desired_config_version,
        firmware_release_available=release_available,
        recommended_heartbeat_interval_seconds=settings.heartbeat_expectation_seconds,
        immediate_sync_requested=bool(gaps) or payload.backlog_estimate > 0,
    )


@router.post("/device-readings/batch", response_model=ReadingBatchResponse)
async def reading_batch(
    payload: ReadingBatch,
    verified: Verified,
    session: DbSession,
) -> ReadingBatchResponse:
    _assert_device_id(payload.device_id, verified.device.id)
    if payload.protocol_version != PROTOCOL:
        raise ProblemError(
            426, "Protocol upgrade required", f"Use {PROTOCOL}", "protocol_incompatible"
        )
    result = await ingest_readings(
        session, device_id=verified.device.id, readings=payload.readings, source="push"
    )
    await session.commit()
    return result


@router.post("/device-events/batch")
async def event_batch(
    payload: DeviceEventBatch,
    verified: Verified,
    session: DbSession,
) -> dict[str, Any]:
    _assert_device_id(payload.device_id, verified.device.id)
    accepted: list[str] = []
    duplicates: list[str] = []
    now = datetime.now(UTC)
    for event in payload.events:
        existing = await session.scalar(
            select(DeviceEvent.id).where(
                DeviceEvent.device_id == verified.device.id,
                DeviceEvent.event_id == event.event_id,
            )
        )
        if existing:
            duplicates.append(event.event_id)
            continue
        session.add(
            DeviceEvent(
                device_id=verified.device.id,
                event_id=event.event_id,
                occurred_at=event.occurred_at,
                received_at=now,
                category=event.category,
                severity=event.severity,
                evidence=event.evidence,
            )
        )
        accepted.append(event.event_id)
    await session.commit()
    return {"accepted": accepted, "duplicates": duplicates}


@router.get("/device-config/effective")
async def effective_config(verified: Verified, session: DbSession) -> dict[str, Any]:
    config = await session.scalar(
        select(DeviceConfigVersion)
        .where(DeviceConfigVersion.device_id == verified.device.id)
        .order_by(DeviceConfigVersion.version.desc())
        .limit(1)
    )
    await session.commit()
    if config is None:
        raise ProblemError(
            404, "No configuration", "No desired configuration exists", "config_missing"
        )
    return {
        "protocol_version": PROTOCOL,
        "device_id": verified.device.id,
        "version": config.version,
        "settings": config.desired_config,
        "sha256": config.config_hash,
    }


@router.get("/device-credentials/rotation")
async def deliver_rotated_credential(
    verified: Verified, session: DbSession, settings: AppSettings
) -> dict[str, Any]:
    pending = await session.scalar(
        select(DeviceCredential)
        .where(
            DeviceCredential.device_id == verified.device.id,
            DeviceCredential.id != verified.credential.id,
            DeviceCredential.revoked_at.is_(None),
            DeviceCredential.confirmed_at.is_(None),
        )
        .order_by(DeviceCredential.created_at.desc())
        .limit(1)
    )
    if pending is None:
        return {"rotation_available": False}
    pending.delivered_at = pending.delivered_at or datetime.now(UTC)
    secret = SecretCipher(settings.app_master_key).decrypt(pending.encrypted_secret).decode()
    await session.commit()
    return {
        "rotation_available": True,
        "credential_id": pending.id,
        "enrollment_secret": secret,
        "fingerprint": pending.fingerprint,
        "valid_from": pending.valid_from,
        "confirm_with_new_credential": True,
    }


@router.post("/device-credentials/rotation/confirm")
async def confirm_rotated_credential(
    request: Request, verified: Verified, session: DbSession
) -> dict[str, bool]:
    payload = await request.json()
    credential_id = payload.get("credential_id") if isinstance(payload, dict) else None
    if credential_id != verified.credential.id:
        raise ProblemError(
            403,
            "New credential required",
            "Confirm rotation using the newly delivered credential",
            "rotation_confirmation_key",
        )
    now = datetime.now(UTC)
    verified.credential.confirmed_at = now
    old_credentials = await session.scalars(
        select(DeviceCredential).where(
            DeviceCredential.device_id == verified.device.id,
            DeviceCredential.id != verified.credential.id,
            DeviceCredential.revoked_at.is_(None),
        )
    )
    for credential in old_credentials:
        credential.revoked_at = now
        credential.valid_until = now
    session.add(
        audit_event(
            action="device.credential_rotation_confirmed",
            actor_type="device",
            actor_id=verified.device.id,
            request=request,
            object_type="device_credential",
            object_id=verified.credential.id,
        )
    )
    await session.commit()
    return {"confirmed": True}


@router.post("/device-config/report")
async def report_config(
    payload: ConfigReport, verified: Verified, session: DbSession
) -> dict[str, bool]:
    _assert_device_id(payload.device_id, verified.device.id)
    config = await session.scalar(
        select(DeviceConfigVersion).where(
            DeviceConfigVersion.device_id == verified.device.id,
            DeviceConfigVersion.version == payload.version,
        )
    )
    if config is None:
        raise ProblemError(404, "Configuration unknown", "Version does not exist", "config_missing")
    config.status = payload.status
    config.report = payload.model_dump(mode="json")
    config.reported_at = datetime.now(UTC)
    if payload.status in {"applied", "partially_applied"}:
        verified.device.effective_config_version = payload.version
    await session.commit()
    return {"recorded": True}


@router.get("/device-firmware/manifest")
async def firmware_manifest(verified: Verified, session: DbSession) -> dict[str, Any]:
    deployment = await session.scalar(
        select(FirmwareDeployment)
        .where(
            FirmwareDeployment.device_id == verified.device.id,
            FirmwareDeployment.status.in_(["scheduled", "available"]),
        )
        .order_by(FirmwareDeployment.scheduled_at)
        .limit(1)
    )
    await session.commit()
    if deployment is None:
        return {"available": False, "protocol_version": PROTOCOL}
    release = await session.get(FirmwareRelease, deployment.firmware_release_id)
    if release is None:
        raise ProblemError(
            500, "Deployment invalid", "Firmware metadata is missing", "firmware_missing"
        )
    return {
        "available": True,
        "deployment_id": deployment.id,
        "version": release.version,
        "channel": release.channel,
        "hardware_target": release.hardware_target,
        "protocol_min": release.protocol_min,
        "protocol_max": release.protocol_max,
        "size_bytes": release.size_bytes,
        "sha256": release.sha256,
        "signature": release.signature,
        "signing_key_id": release.signing_key_id,
        "download_path": f"/api/v1/device-firmware/{release.id}/download",
    }
