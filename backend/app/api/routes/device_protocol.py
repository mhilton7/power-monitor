from __future__ import annotations

import hashlib
import ipaddress
import secrets
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import AppSettings, DbSession, Verified, audit_event
from app.db.models import (
    AlertInstance,
    AlertRule,
    Device,
    DeviceAddress,
    DeviceCapability,
    DeviceConfigVersion,
    DeviceCredential,
    DeviceEvent,
    DeviceHeartbeat,
    DeviceLifecycleEvent,
    DeviceSiteAssignment,
    DeviceStatusSnapshot,
    EnrollmentToken,
    FirmwareDeployment,
    FirmwareRelease,
    SequenceGap,
    Site,
    SyncCursor,
)
from app.ingestion.service import ingest_readings
from app.network_policy import effective_client_ip, evaluate_site_address
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
logger = structlog.get_logger(__name__)


async def _set_address_policy_alert(
    session: DbSession,
    device: Device,
    *,
    active: bool,
    address: str,
    reason: str | None,
    now: datetime,
) -> None:
    rules = list(
        await session.scalars(
            select(AlertRule).where(
                AlertRule.rule_type == "device_address_outside_policy",
                AlertRule.enabled.is_(True),
            )
        )
    )
    for rule in rules:
        if rule.device_id not in {None, device.id} or rule.site_id not in {
            None,
            device.site_id,
        }:
            continue
        instance = await session.scalar(
            select(AlertInstance).where(
                AlertInstance.rule_id == rule.id,
                AlertInstance.device_id == device.id,
                AlertInstance.status.in_(["active", "acknowledged"]),
            )
        )
        evidence = {
            "address": address,
            "policy_direction": "server_pull",
            "reason": reason or "address accepted",
            "source": "signed_heartbeat",
        }
        if active and instance is None:
            session.add(
                AlertInstance(
                    rule_id=rule.id,
                    device_id=device.id,
                    site_id=device.site_id,
                    status="active",
                    severity=rule.severity,
                    opened_at=now,
                    evidence=evidence,
                )
            )
        elif active and instance is not None:
            instance.evidence = evidence
        elif not active and instance is not None:
            instance.status = "resolved"
            instance.resolved_at = now
            instance.evidence = evidence


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
    existing_device = await session.scalar(
        select(Device).where(Device.hardware_id == payload.hardware_id).with_for_update()
    )
    if existing_device and existing_device.lifecycle_status != "decommissioned":
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
    if site.lifecycle_state != "active":
        raise ProblemError(
            409,
            "Active site required",
            "Enable the site before enrolling or reassigning sensors",
            "site_not_assignable",
        )
    direct_address = request.client.host if request.client else ""
    try:
        source_ip = effective_client_ip(
            direct_address,
            request.headers.get("x-forwarded-for"),
            settings.trusted_proxy_cidrs,
        )
        ingress = await evaluate_site_address(session, site, "device_ingress", source_ip)
    except ProblemError:
        ingress = None
    if ingress is None or not ingress.allowed:
        session.add(
            audit_event(
                action="network_policy.enrollment_blocked",
                actor_type="enrollment_token",
                actor_id=token.id,
                request=request,
                object_type="site",
                object_id=site.id,
                outcome="blocked",
                details={"direction": "device_ingress"},
            )
        )
        await session.commit()
        raise ProblemError(
            403,
            "Enrollment denied",
            "This sensor network is not accepted for enrollment",
            "enrollment_network_blocked",
        )
    assigned_name = (
        preassignment.get("name") or payload.requested_name or f"Sensor {payload.hardware_id[-6:]}"
    )
    reenrollment = existing_device is not None
    if existing_device:
        device = existing_device
        device.site_id = site.id
        device.circuit_id = preassignment.get("circuit_id")
        device.name = assigned_name
        device.connection_mode = preassignment.get("connection_mode", "push")
        device.measurement_role = preassignment.get("measurement_role", "submeter")
        device.ct_rating_amps = preassignment.get("ct_rating_amps", "100")
        device.protocol_version = PROTOCOL
        device.status = "offline_last_known"
        device.last_seen_at = None
        device.revoked_at = None
        device.lifecycle_status = "active"
        device.decommissioned_at = None
        device.decommissioned_by = None
        device.decommission_reason = None
        device.maintenance_until = None
        device.desired_config_version += 1
        config_version = device.desired_config_version
        session.add(
            DeviceLifecycleEvent(
                device_id=device.id,
                generation=device.lifecycle_generation,
                event_type="reenrolled",
                occurred_at=now,
                actor_id=token.created_by,
                site_id=site.id,
                circuit_id=device.circuit_id,
                details={"new_credential": True},
            )
        )
    else:
        existing_site_device = await session.scalar(
            select(Device.id)
            .where(
                Device.site_id == site.id,
                Device.lifecycle_status == "active",
            )
            .limit(1)
        )
        device = Device(
            site_id=site.id,
            circuit_id=preassignment.get("circuit_id"),
            hardware_id=payload.hardware_id,
            name=assigned_name,
            connection_mode=preassignment.get("connection_mode", "push"),
            measurement_role=preassignment.get("measurement_role", "submeter"),
            cost_scope="energy_only",
            # The first sensor in Single Home is the only unambiguous live
            # aggregate. Additional sensors require explicit topology so a
            # whole-home CT is never blindly summed with a submeter.
            include_in_default_site_total=existing_site_device is None,
            ct_rating_amps=preassignment.get("ct_rating_amps", "100"),
            protocol_version=PROTOCOL,
        )
        session.add(device)
        config_version = 1
    await session.flush()
    current_assignment = await session.scalar(
        select(DeviceSiteAssignment).where(
            DeviceSiteAssignment.device_id == device.id,
            DeviceSiteAssignment.effective_to.is_(None),
        )
    )
    if current_assignment is None or current_assignment.site_id != site.id:
        if current_assignment is not None:
            current_assignment.effective_to = now
        session.add(
            DeviceSiteAssignment(
                device_id=device.id,
                site_id=site.id,
                effective_from=now,
                assigned_by=token.created_by,
                reason="Device enrollment",
                created_at=now,
            )
        )
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
    capability = await session.get(DeviceCapability, device.id)
    if capability is None:
        capability = DeviceCapability(
            device_id=device.id,
            hardware_target=payload.capabilities.hardware_target,
            pzem_model=payload.capabilities.pzem_model,
            sd_required=True,
            features={"supported_endpoints": payload.capabilities.supported_endpoints},
            reported_at=now,
        )
        session.add(capability)
    else:
        capability.hardware_target = payload.capabilities.hardware_target
        capability.pzem_model = payload.capabilities.pzem_model
        capability.sd_required = True
        capability.features = {"supported_endpoints": payload.capabilities.supported_endpoints}
        capability.reported_at = now
    session.add(
        DeviceConfigVersion(
            device_id=device.id,
            version=config_version,
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
    if await session.get(SyncCursor, device.id) is None:
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
            action="device.reenrolled" if reenrollment else "device.enrolled",
            actor_type="device",
            actor_id=device.id,
            request=request,
            object_type="device",
            object_id=device.id,
            details={
                "credential_fingerprint": fingerprint,
                "hardware_target": payload.capabilities.hardware_target,
                "new_credential": True,
                "lifecycle_generation": device.lifecycle_generation,
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
        source_ip=getattr(request.state, "device_source_ip", None),
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
            device_site = await session.get(Site, device.site_id)
            if device_site is None:
                raise ProblemError(
                    409, "Device site unavailable", "The device site does not exist", "site_missing"
                )
            pull_decision = await evaluate_site_address(
                session,
                device_site,
                "server_pull",
                str(address),
            )
            if not pull_decision.allowed:
                validation_error = pull_decision.reason
        except (ValueError, ProblemError):
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
            prior_address = await session.scalar(
                select(DeviceAddress)
                .where(
                    DeviceAddress.device_id == device.id,
                    DeviceAddress.source == "heartbeat",
                )
                .order_by(DeviceAddress.last_seen_at.desc())
                .limit(1)
            )
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
            if prior_address is not None and prior_address.host != payload.current_ip:
                session.add(
                    audit_event(
                        action="device.address_changed_outside_policy"
                        if validation_error
                        else "device.address_changed",
                        actor_type="device",
                        actor_id=device.id,
                        request=request,
                        object_type="device",
                        object_id=device.id,
                        outcome="warning" if validation_error else "success",
                        details={
                            "previous_address": prior_address.host,
                            "current_address": payload.current_ip,
                            "server_pull_allowed": validation_error is None,
                        },
                    )
                )
        await _set_address_policy_alert(
            session,
            device,
            active=validation_error is not None,
            address=payload.current_ip,
            reason=validation_error,
            now=now,
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
    logger.info(
        "HEARTBEAT_ACCEPTED",
        device_id=device.id,
        site_id=device.site_id,
        received_at=now,
        newest_sequence=payload.newest_stored_sequence,
        backlog=payload.backlog_estimate,
    )
    if payload.latest is not None:
        logger.info(
            "LIVE_MEASUREMENT_ACCEPTED",
            device_id=device.id,
            site_id=device.site_id,
            measured_at=payload.latest.measured_at,
            power_watts=payload.latest.power_w,
        )
        logger.info(
            "LATEST_MEASUREMENT_UPDATED",
            device_id=device.id,
            site_id=device.site_id,
            sequence=None,
            measured_at=payload.latest.measured_at,
            source="heartbeat_live",
        )
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
    settings: AppSettings,
) -> ReadingBatchResponse:
    _assert_device_id(payload.device_id, verified.device.id)
    if payload.protocol_version != PROTOCOL:
        raise ProblemError(
            426, "Protocol upgrade required", f"Use {PROTOCOL}", "protocol_incompatible"
        )
    heartbeat_row = await session.scalar(
        select(DeviceHeartbeat)
        .where(DeviceHeartbeat.device_id == verified.device.id)
        .order_by(DeviceHeartbeat.received_at.desc())
        .limit(1)
    )
    first_available = None
    if heartbeat_row is not None and isinstance(heartbeat_row.payload, dict):
        raw_first = heartbeat_row.payload.get("oldest_syncable_sequence")
        if not isinstance(raw_first, int) or raw_first <= 0:
            raw_first = heartbeat_row.payload.get("oldest_stored_sequence")
        if isinstance(raw_first, int) and raw_first > 0:
            first_available = raw_first
    result = await ingest_readings(
        session,
        device_id=verified.device.id,
        readings=payload.readings,
        source="push",
        first_available_sequence=first_available,
        unavailable_sequence_ranges=payload.unavailable_sequence_ranges,
        maximum_clock_skew_seconds=settings.max_device_clock_skew_seconds,
    )
    await session.commit()
    logger.info(
        "history.normalization_completed",
        device_id=verified.device.id,
        accepted_count=len(result.accepted),
        duplicate_count=len(result.duplicates),
        rejected_count=len(result.rejected),
        highest_contiguous_sequence=result.highest_contiguous_accepted_sequence,
    )
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
