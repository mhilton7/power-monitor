from __future__ import annotations

import hashlib
import ipaddress
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.deps import AppSettings, DbSession, Verified, audit_event
from app.data_reset.service import (
    ensure_device_reset_mutations_allowed,
    ensure_site_reset_mutations_allowed,
    redact_history_values,
)
from app.db.models import (
    AlertInstance,
    AlertRule,
    DataResetOperation,
    DataResetParticipant,
    Device,
    DeviceAddress,
    DeviceCapability,
    DeviceConfigVersion,
    DeviceCredential,
    DeviceDataState,
    DeviceEvent,
    DeviceEventSyncCursor,
    DeviceHeartbeat,
    DeviceLifecycleEvent,
    DeviceSiteAssignment,
    DeviceStatusSnapshot,
    EnrollmentToken,
    FirmwareDeployment,
    FirmwareRelease,
    SequenceGap,
    Site,
    SiteDataState,
    SyncCursor,
)
from app.firmware_lifecycle import transition_firmware_deployment
from app.ingestion.service import ingest_readings
from app.network_policy import effective_client_ip, evaluate_site_address
from app.ota import (
    OTA_MANIFEST_HKDF_INFO,
    OTA_MANIFEST_HMAC_ALGORITHM,
    OTA_MANIFEST_SCHEMA,
    OTA_TRUST_MODE,
    canonical_utc,
    release_compatibility,
    sign_ota_manifest,
)
from app.problem import ProblemError
from app.schemas import (
    SIGNED_BIGINT_MAX,
    ConfigReport,
    DeviceEventBatch,
    EnrollmentClaim,
    EnrollmentClaimResponse,
    Heartbeat,
    HeartbeatResponse,
    OtaManifestUnavailable,
    OtaManifestV2,
    Reading,
    ReadingBatch,
    ReadingBatchResponse,
    SequenceCursorResponse,
)
from app.security.agent_protocol import AGENT_PROTOCOL
from app.security.protocol import PROTOCOL, SecretCipher

router = APIRouter(prefix="/api/v1", tags=["device protocol"])
logger = structlog.get_logger(__name__)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _manifest_artifact_available(release: FirmwareRelease, settings: Any) -> bool:
    root = Path(settings.firmware_path).resolve()
    if release.artifact_path:
        path = (root / release.artifact_path).resolve()
    elif release.file_path:
        path = Path(release.file_path).resolve()
    else:
        return False
    if path != root and root not in path.parents:
        return False
    try:
        return path.is_file() and path.stat().st_size == release.size_bytes
    except OSError:
        return False


async def _complete_firmware_if_stable(
    session: DbSession, deployment: FirmwareDeployment, settings: Any, now: datetime
) -> None:
    if (
        deployment.state != "awaiting_heartbeat"
        or deployment.verification_heartbeats < settings.firmware_verification_heartbeat_count
        or deployment.reading_confirmed_at is None
        or deployment.stabilization_started_at is None
        or _aware(deployment.stabilization_started_at)
        > now
        - timedelta(
            seconds=(settings.firmware_verification_heartbeat_count - 1)
            * settings.heartbeat_expectation_seconds
        )
    ):
        return
    critical_alert = await session.scalar(
        select(AlertInstance.id).where(
            AlertInstance.device_id == deployment.device_id,
            AlertInstance.status.in_(["active", "acknowledged"]),
            AlertInstance.severity == "critical",
        )
    )
    if critical_alert is not None:
        return
    transition_firmware_deployment(deployment, "completed", now)
    deployment.progress = 100
    deployment.validated_at = deployment.validated_at or now


async def _reconcile_firmware_heartbeat(
    session: DbSession, payload: Heartbeat, settings: Any, now: datetime
) -> None:
    deployment = await session.scalar(
        select(FirmwareDeployment)
        .where(
            FirmwareDeployment.device_id == payload.device_id,
            FirmwareDeployment.state.in_(
                [
                    "manifest_authenticated",
                    "waiting_for_schedule",
                    "download_started",
                    "downloading",
                    "binary_verified",
                    "partition_written",
                    "rebooting",
                    "post_boot_validation",
                    "validated",
                    "awaiting_heartbeat",
                    # A target heartbeat carrying strictly newer persisted
                    # evidence may correct a delayed terminal report from the
                    # source boot. The reconciliation below remains fail-closed
                    # unless deployment, attempt, boot, and sequence all prove
                    # that the heartbeat supersedes that report.
                    "failed",
                    "rolled_back",
                ]
            ),
        )
        .order_by(FirmwareDeployment.scheduled_at.desc())
        .limit(1)
        .with_for_update()
    )
    if deployment is None:
        return
    release = await session.get(FirmwareRelease, deployment.firmware_release_id)
    if release is None:
        return
    target_identity = (
        payload.firmware_version == release.version
        and payload.firmware_build_hash == release.build_hash
    )
    source_identity = (
        payload.firmware_version == deployment.source_version
        and payload.firmware_build_hash == deployment.source_build_hash
    )
    resources = payload.resources if isinstance(payload.resources, dict) else {}
    recovery = resources.get("ota_recovery", {})
    recovery = recovery if isinstance(recovery, dict) else {}
    recovery_deployment_id = recovery.get("deployment_id")
    recovery_attempt = recovery.get("attempt")
    recovery_evidence_sequence = recovery.get("evidence_sequence")
    recovery_identity_matches = (
        recovery_deployment_id == deployment.id
        and isinstance(recovery_attempt, int)
        and not isinstance(recovery_attempt, bool)
        and recovery_attempt == deployment.attempt
    )
    if (
        not recovery_identity_matches
        or not isinstance(recovery_evidence_sequence, int)
        or isinstance(recovery_evidence_sequence, bool)
        or not 0 <= recovery_evidence_sequence <= (2**64 - 1)
    ):
        # Evidence ordering is meaningful only within one persisted deployment
        # attempt. Ignore legacy or mismatched identities and fail closed later
        # if a delayed report cannot otherwise be ordered safely.
        recovery_evidence_sequence = None

    terminal_report = (
        deployment.last_report_payload if isinstance(deployment.last_report_payload, dict) else {}
    )
    terminal_report_sequence = terminal_report.get("evidence_sequence")
    terminal_report_boot_id = terminal_report.get("boot_id")
    target_supersedes_terminal_report = bool(
        deployment.state in {"failed", "rolled_back"}
        and target_identity
        and recovery_identity_matches
        and recovery_evidence_sequence is not None
        and isinstance(terminal_report_sequence, int)
        and not isinstance(terminal_report_sequence, bool)
        and recovery_evidence_sequence > terminal_report_sequence
        and isinstance(terminal_report_boot_id, str)
        and payload.boot_id != terminal_report_boot_id
    )

    if deployment.state in {"failed", "rolled_back"} and not target_supersedes_terminal_report:
        return

    if not target_identity:
        retained_evidence = (
            deployment.interruption_evidence
            if isinstance(deployment.interruption_evidence, dict)
            else {}
        )
        target_heartbeat_boot_id = retained_evidence.get("target_heartbeat_boot_id")
        target_heartbeat_sequence = retained_evidence.get("target_heartbeat_evidence_sequence")
        target_was_observed = bool(
            deployment.installed_at
            or deployment.validated_version
            or deployment.state in {"post_boot_validation", "validated", "awaiting_heartbeat"}
        )
        rollback_boot_is_new = bool(
            payload.boot_id
            and payload.boot_id != deployment.source_boot_id
            and payload.boot_id != (target_heartbeat_boot_id or deployment.last_boot_id)
        )
        rollback_evidence_is_new = bool(
            recovery_identity_matches
            and recovery_evidence_sequence is not None
            and isinstance(target_heartbeat_sequence, int)
            and recovery_evidence_sequence > target_heartbeat_sequence
        )
        if (
            target_was_observed
            and source_identity
            and rollback_boot_is_new
            and rollback_evidence_is_new
        ):
            transition_firmware_deployment(
                deployment, "rolled_back", now, evidence_reconciliation=True
            )
            deployment.rollback_at = now
            deployment.rollback_version = payload.firmware_version
            deployment.rollback_build_hash = payload.firmware_build_hash
            deployment.failure_code = "ota_rollback_detected"
            deployment.failure_summary = (
                "The sensor returned to its previous firmware after the target image was observed."
            )
            deployment.interruption_evidence = {
                "reason": "target_then_source_identity",
                "boot_id_changed": True,
                "target_heartbeat_boot_id": target_heartbeat_boot_id,
                "target_heartbeat_evidence_sequence": target_heartbeat_sequence,
                "ota_recovery": recovery,
            }
            session.add(
                audit_event(
                    action="firmware.deployment_rollback_reconciled",
                    actor_type="device",
                    actor_id=payload.device_id,
                    object_type="firmware_deployment",
                    object_id=deployment.id,
                    details=deployment.interruption_evidence,
                )
            )
            return

        preinstall_states = {
            "manifest_authenticated",
            "download_started",
            "downloading",
            "binary_verified",
        }
        boot_changed = bool(
            deployment.source_boot_id and payload.boot_id != deployment.source_boot_id
        )
        previous_stage = str(recovery.get("previous_boot_stage") or "")
        interrupted_stage = previous_stage in {
            # 1.0.12+ retained ledger stage names.
            "firmware_request",
            "firmware_response_headers",
            "image_metadata_received",
            "image_metadata_validated",
            "update_begin_completed",
            "first_bytes_written",
            "sha_finalized",
            "protocol_marker_verified",
            "http_transport_destroyed",
            "update_end_beginning",
            # Backward-compatible names emitted by early repair builds.
            "download_request",
            "image_metadata",
            "update_begin",
            "streaming",
            "stream_complete",
            "hash_verified",
            "partition_finalizing",
        }
        last_activity = (
            deployment.last_report_at or deployment.downloaded_at or deployment.scheduled_at
        )
        grace_elapsed = _aware(last_activity) <= now - timedelta(
            seconds=settings.firmware_interruption_grace_seconds
        )
        if (
            deployment.state in preinstall_states
            and source_identity
            and grace_elapsed
            and (boot_changed or interrupted_stage or recovery.get("previous_boot_update_open"))
        ):
            interrupted_state = deployment.state
            transition_firmware_deployment(deployment, "failed", now)
            deployment.failure_code = "ota_interrupted_before_install"
            deployment.failure_summary = (
                "The sensor rebooted during the download workflow and returned "
                "on the previous firmware."
            )
            deployment.interruption_evidence = {
                "reason": "source_identity_after_reboot",
                "last_state": interrupted_state,
                "source_boot_id": deployment.source_boot_id,
                "observed_boot_id": payload.boot_id,
                "ota_recovery": recovery,
            }
            deployment.last_boot_id = payload.boot_id
            session.add(
                audit_event(
                    action="firmware.deployment_interrupted_reconciled",
                    actor_type="device",
                    actor_id=payload.device_id,
                    object_type="firmware_deployment",
                    object_id=deployment.id,
                    details=deployment.interruption_evidence,
                )
            )
        return

    # A target identity is authoritative evidence that installation occurred,
    # even if the final pre-reboot progress report was lost.
    was_stabilizing = deployment.state in {"validated", "awaiting_heartbeat"}
    previous_target_boot_id = deployment.last_boot_id
    if deployment.state not in {"validated", "awaiting_heartbeat"}:
        transition_firmware_deployment(
            deployment,
            "awaiting_heartbeat",
            now,
            evidence_reconciliation=True,
            supersede_terminal_evidence=target_supersedes_terminal_report,
        )
        deployment.progress = 100
        deployment.bytes_received = max(deployment.bytes_received, release.size_bytes)
        deployment.installed_at = deployment.installed_at or now
        deployment.validated_version = payload.firmware_version
        deployment.validated_build_hash = payload.firmware_build_hash
        deployment.stabilization_started_at = deployment.stabilization_started_at or now
        deployment.last_boot_id = payload.boot_id
        if target_supersedes_terminal_report:
            deployment.failure_code = None
            deployment.failure_summary = None
            deployment.failure_reason = None
            deployment.rollback_at = None
            deployment.rollback_version = None
            deployment.rollback_build_hash = None
        session.add(
            audit_event(
                action="firmware.deployment_target_reconciled",
                actor_type="device",
                actor_id=payload.device_id,
                object_type="firmware_deployment",
                object_id=deployment.id,
                details={"state": "awaiting_heartbeat", "ota_recovery": recovery},
            )
        )
    elif was_stabilizing and previous_target_boot_id and previous_target_boot_id != payload.boot_id:
        # Stabilization evidence is continuous-boot evidence. A reboot of the
        # target image starts a new proof window; heartbeats and a durable
        # reading from the previous target boot cannot complete this attempt.
        deployment.verification_heartbeats = 0
        deployment.stabilization_started_at = now
        deployment.reading_confirmed_at = None
        # This is a material same-state lifecycle revision: it invalidates all
        # continuous-boot proof gathered so far and restarts the post-boot
        # timeout window. Clients must not mistake it for unchanged evidence.
        deployment.revision += 1
        deployment.state_changed_at = now
        session.add(
            audit_event(
                action="firmware.deployment_target_restarted",
                actor_type="device",
                actor_id=payload.device_id,
                object_type="firmware_deployment",
                object_id=deployment.id,
                details={
                    "previous_boot_id": previous_target_boot_id,
                    "observed_boot_id": payload.boot_id,
                    "state": deployment.state,
                },
            )
        )
    # Persist the exact authenticated target boot even when one of its health
    # checks fails. Keep the latest *healthy* target evidence separately so a
    # delayed same-boot failure report cannot erase newer proof merely because
    # an unhealthy heartbeat was observed afterwards.
    deployment.last_boot_id = payload.boot_id
    target_heartbeat_healthy = payload.pzem.ok and payload.sd.ok and payload.time.trusted
    retained_evidence = dict(deployment.interruption_evidence or {})
    retained_evidence.update(
        {
            "target_heartbeat_boot_id": payload.boot_id,
            "target_heartbeat_received_at": now.isoformat(),
            "target_heartbeat_healthy": target_heartbeat_healthy,
        }
    )
    if recovery_evidence_sequence is not None:
        retained_evidence["target_heartbeat_deployment_id"] = deployment.id
        retained_evidence["target_heartbeat_attempt"] = deployment.attempt
        previous_sequence = retained_evidence.get("target_heartbeat_evidence_sequence")
        if not isinstance(previous_sequence, int) or recovery_evidence_sequence > previous_sequence:
            retained_evidence["target_heartbeat_evidence_sequence"] = recovery_evidence_sequence
    if target_heartbeat_healthy:
        retained_evidence.update(
            {
                "target_healthy_heartbeat_boot_id": payload.boot_id,
                "target_healthy_heartbeat_received_at": now.isoformat(),
            }
        )
        if recovery_evidence_sequence is not None:
            previous_healthy_sequence = retained_evidence.get(
                "target_healthy_heartbeat_evidence_sequence"
            )
            if (
                not isinstance(previous_healthy_sequence, int)
                or recovery_evidence_sequence > previous_healthy_sequence
            ):
                retained_evidence["target_healthy_heartbeat_evidence_sequence"] = (
                    recovery_evidence_sequence
                )
    deployment.interruption_evidence = retained_evidence
    if not target_heartbeat_healthy:
        return
    if deployment.state == "validated":
        transition_firmware_deployment(deployment, "awaiting_heartbeat", now)
        deployment.stabilization_started_at = deployment.stabilization_started_at or now
    deployment.verification_heartbeats += 1
    await _complete_firmware_if_stable(session, deployment, settings, now)


async def _confirm_firmware_reading(
    session: DbSession,
    device_id: str,
    readings: list[Reading],
    durably_confirmed_sequences: set[int],
    settings: Any,
    now: datetime,
) -> None:
    deployment = await session.scalar(
        select(FirmwareDeployment)
        .where(
            FirmwareDeployment.device_id == device_id,
            FirmwareDeployment.state == "awaiting_heartbeat",
        )
        .order_by(FirmwareDeployment.scheduled_at.desc())
        .limit(1)
        .with_for_update()
    )
    if deployment is None:
        return
    # A previous boot's backlog may be durably accepted immediately after an
    # OTA. It remains valid History data, but is not proof that the target
    # image can acquire and synchronize a reading.
    if not any(
        reading.sequence in durably_confirmed_sequences
        and reading.boot_id == deployment.last_boot_id
        and reading.firmware_version == deployment.validated_version
        for reading in readings
    ):
        return
    deployment.reading_confirmed_at = deployment.reading_confirmed_at or now
    await _complete_firmware_if_stable(session, deployment, settings, now)


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
    if payload.protocol_version not in {PROTOCOL, AGENT_PROTOCOL}:
        raise ProblemError(
            426,
            "Protocol upgrade required",
            f"This server requires {PROTOCOL} or pm-agent/2.0.0",
            "protocol_incompatible",
        )
    if payload.protocol_version != AGENT_PROTOCOL and (
        not payload.capabilities.sd_present or not payload.capabilities.sd_required
    ):
        raise ProblemError(
            422,
            "Required storage unavailable",
            "Enrollment requires operational microSD storage",
            "sd_required",
        )
    advertised_reset_protocol = payload.capabilities.data_reset_protocol
    if (
        advertised_reset_protocol is None
        and "data-reset/1.0.0" in payload.capabilities.supported_endpoints
    ):
        advertised_reset_protocol = "data-reset/1.0.0"
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
    await ensure_site_reset_mutations_allowed(session, [site.id])
    site_data_state = await session.get(SiteDataState, site.id, with_for_update=True)
    historical_site_generation = int(
        await session.scalar(
            select(func.max(DataResetOperation.reset_generation)).where(
                DataResetOperation.site_id == site.id,
                DataResetOperation.central_commit_at.is_not(None),
            )
        )
        or 0
    )
    required_site_generation = max(
        historical_site_generation,
        int(site_data_state.data_generation) if site_data_state is not None else 0,
    )
    required_claim_generation = required_site_generation
    existing_data_state: DeviceDataState | None = None
    existing_sync_cursor: SyncCursor | None = None
    historical_reset_generation = 0
    historical_reset_boundary = 0
    if existing_device is not None:
        await ensure_device_reset_mutations_allowed(session, [existing_device.id])
        existing_data_state = await session.get(
            DeviceDataState,
            existing_device.id,
            with_for_update=True,
        )
        existing_sync_cursor = await session.get(
            SyncCursor,
            existing_device.id,
            with_for_update=True,
        )
        historical_reset_generation = int(
            await session.scalar(
                select(func.max(DataResetParticipant.reset_generation)).where(
                    DataResetParticipant.device_id == existing_device.id
                )
            )
            or 0
        )
        historical_reset_boundary = int(
            await session.scalar(
                select(func.max(DataResetParticipant.reset_boundary)).where(
                    DataResetParticipant.device_id == existing_device.id
                )
            )
            or 0
        )
        reset_handoff_required = existing_data_state is not None and (
            existing_data_state.ingestion_gate != "open"
            or existing_data_state.reset_required_on_reconnect
        )
        reset_generation = max(
            historical_reset_generation,
            required_site_generation,
            int(existing_data_state.data_generation) if existing_data_state is not None else 0,
            int(existing_sync_cursor.data_generation) if existing_sync_cursor is not None else 0,
        )
        required_claim_generation = reset_generation
        reset_boundary = max(
            historical_reset_boundary,
            int(existing_data_state.reset_boundary) if existing_data_state is not None else 0,
            int(existing_sync_cursor.reset_boundary) if existing_sync_cursor is not None else 0,
            (
                int(existing_sync_cursor.highest_contiguous_sequence)
                if existing_sync_cursor is not None
                else 0
            ),
            (
                int(existing_sync_cursor.maximum_seen_sequence)
                if existing_sync_cursor is not None
                else 0
            ),
        )
        verified_reset = None
        if reset_generation > 0 or reset_boundary > 0:
            verified_reset = await session.scalar(
                select(DataResetParticipant.device_id)
                .join(
                    DataResetOperation,
                    DataResetOperation.id == DataResetParticipant.operation_id,
                )
                .where(
                    DataResetParticipant.device_id == existing_device.id,
                    DataResetParticipant.reset_generation == reset_generation,
                    DataResetParticipant.state == "verified",
                    DataResetOperation.central_commit_at.is_not(None),
                )
                .limit(1)
            )
        cross_site_reenrollment = existing_device.site_id != site.id
        if (
            cross_site_reenrollment
            or reset_handoff_required
            or ((reset_generation > 0 or reset_boundary > 0) and verified_reset is None)
        ):
            raise ProblemError(
                409,
                "Local data reset recovery required",
                "This decommissioned sensor did not complete the site's latest "
                "data reset. Reenrollment cannot issue new credentials until a "
                "future authenticated catch-up workflow clears its device-wide "
                "local readings and installs the committed generation.",
                "reenrollment_requires_data_reset_recovery",
                extra={
                    "device_id": existing_device.id,
                    "required_data_generation": reset_generation,
                    "reset_boundary": reset_boundary,
                    "cross_site_reenrollment": cross_site_reenrollment,
                },
            )
    if required_claim_generation > 0 and advertised_reset_protocol is None:
        raise ProblemError(
            409,
            "Data-generation enrollment unsupported",
            "This site requires a sensor enrollment client that can durably install "
            "the server's reset generation before its first heartbeat or reading upload.",
            "enrollment_data_generation_unsupported",
            extra={"required_data_generation": required_claim_generation},
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
        device.protocol_version = payload.protocol_version
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
            protocol_version=payload.protocol_version,
        )
        session.add(device)
        config_version = 1
    await session.flush()
    if site_data_state is None:
        site_data_state = SiteDataState(
            site_id=site.id,
            data_generation=required_site_generation,
            history_revision=0,
            updated_at=now,
        )
        session.add(site_data_state)
        await session.flush()
    elif int(site_data_state.data_generation) < required_site_generation:
        site_data_state.data_generation = required_site_generation
        site_data_state.updated_at = now
    device_data_state = existing_data_state or await session.get(
        DeviceDataState, device.id, with_for_update=True
    )
    sync_cursor = existing_sync_cursor or await session.get(
        SyncCursor, device.id, with_for_update=True
    )
    reenrollment_boundary = max(
        historical_reset_boundary,
        int(device_data_state.reset_boundary) if device_data_state is not None else 0,
        int(sync_cursor.reset_boundary) if sync_cursor is not None else 0,
        int(sync_cursor.highest_contiguous_sequence) if sync_cursor is not None else 0,
        int(sync_cursor.maximum_seen_sequence) if sync_cursor is not None else 0,
    )
    reenrollment_generation = max(
        historical_reset_generation,
        required_site_generation,
        int(device_data_state.data_generation) if device_data_state is not None else 0,
        int(sync_cursor.data_generation) if sync_cursor is not None else 0,
    )
    if device_data_state is None:
        device_data_state = DeviceDataState(
            device_id=device.id,
            site_id=site.id,
            data_generation=reenrollment_generation,
            reset_boundary=0,
            ingestion_gate="open",
            reset_required_on_reconnect=False,
            generation_updated_at=now,
            updated_at=now,
        )
        session.add(device_data_state)
    elif reenrollment:
        device_data_state.site_id = site.id
        device_data_state.data_generation = reenrollment_generation
        device_data_state.reset_boundary = reenrollment_boundary
        device_data_state.ingestion_gate = "open"
        device_data_state.reset_required_on_reconnect = False
        device_data_state.active_operation_id = None
        device_data_state.generation_updated_at = now
        device_data_state.updated_at = now
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
    capability_features: dict[str, Any] = {
        "supported_endpoints": payload.capabilities.supported_endpoints
    }
    if payload.protocol_version == AGENT_PROTOCOL:
        capability_features.update(
            {
                "agent_protocol": "pm-agent/2.0.0",
                "outbound_commands": True,
                "runtime_http_listener": False,
            }
        )
    if advertised_reset_protocol is not None:
        capability_features["data_reset"] = advertised_reset_protocol
    if payload.capabilities.ota is not None:
        capability_features["ota"] = payload.capabilities.ota.model_dump(mode="json")
    capability = await session.get(DeviceCapability, device.id)
    if capability is None:
        capability = DeviceCapability(
            device_id=device.id,
            hardware_target=payload.capabilities.hardware_target,
            pzem_model=payload.capabilities.pzem_model,
            sd_required=payload.capabilities.sd_required,
            features=capability_features,
            reported_at=now,
        )
        session.add(capability)
    else:
        capability.hardware_target = payload.capabilities.hardware_target
        capability.pzem_model = payload.capabilities.pzem_model
        capability.sd_required = payload.capabilities.sd_required
        capability.features = capability_features
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
    if sync_cursor is None:
        session.add(
            SyncCursor(
                device_id=device.id,
                highest_contiguous_sequence=reenrollment_boundary,
                maximum_seen_sequence=reenrollment_boundary,
                data_generation=reenrollment_generation,
                reset_boundary=reenrollment_boundary,
                updated_at=now,
            )
        )
    elif reenrollment:
        sync_cursor.highest_contiguous_sequence = reenrollment_boundary
        sync_cursor.maximum_seen_sequence = reenrollment_boundary
        sync_cursor.data_generation = reenrollment_generation
        sync_cursor.reset_boundary = reenrollment_boundary
        sync_cursor.updated_at = now
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
        protocol_version=payload.protocol_version,
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
            "data_generation": reenrollment_generation,
            "reset_boundary": reenrollment_boundary,
        },
    )


def _status_from_heartbeat(payload: Heartbeat) -> str:
    if not payload.time.trusted:
        return "time_unsynchronized"
    if not payload.pzem.ok:
        return "api_healthy_meter_failed"
    if payload.sd.status == "sequence_reconciling" or payload.sd.details.get(
        "sequence_reconciliation_in_progress"
    ):
        return "online_storage_reconciling"
    # Current firmware deliberately keeps ``sd.ok`` true when the card remains
    # mounted and writable but either the reading index or the independent
    # event log fails an integrity check. Preserve Online semantics while still
    # surfacing the signed degradation instead of flattening it to synchronized.
    if not payload.sd.ok or payload.sd.status in {
        "reading_index_integrity_degraded",
        "event_log_integrity_degraded",
    }:
        return "online_storage_degraded"
    if payload.backlog_estimate > 0:
        return "online_with_backlog"
    if payload.connection_mode == "push":
        return "online_push_only"
    return "online_synchronized"


def _heartbeat_generation(payload: Heartbeat) -> int | None:
    value = getattr(payload, "data_generation", None)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _safe_reset_heartbeat_payload(payload: Heartbeat) -> dict[str, Any]:
    """Retain only coordination/liveness evidence while reset ingestion is gated."""

    storage_details = payload.sd.details.model_dump(mode="json")
    reset_evidence = (
        payload.data_reset.model_dump(mode="json") if payload.data_reset is not None else None
    )
    return {
        "protocol_version": payload.protocol_version,
        "schema_version": payload.schema_version,
        "device_id": payload.device_id,
        "boot_id": payload.boot_id,
        "firmware_version": payload.firmware_version,
        "firmware_build_hash": payload.firmware_build_hash,
        "uptime_seconds": payload.uptime_seconds,
        "reboot_reason": payload.reboot_reason,
        "connection_mode": payload.connection_mode,
        "configuration_version": payload.configuration_version,
        "data_generation": _heartbeat_generation(payload),
        "pzem": {"ok": payload.pzem.ok, "status": payload.pzem.status},
        "sd": {
            "ok": payload.sd.ok,
            "status": payload.sd.status,
            "card_generation": storage_details.get("card_generation"),
        },
        "time": {"trusted": payload.time.trusted, "source": payload.time.source},
        "data_reset": reset_evidence,
    }


async def _refresh_signed_heartbeat_capabilities(
    session: DbSession,
    *,
    device: Device,
    payload: Heartbeat,
    now: datetime,
) -> None:
    """Refresh only schema-validated capability claims from an authenticated heartbeat."""

    if payload.ota is None and payload.data_reset is None:
        return
    capability = await session.get(DeviceCapability, device.id)
    if capability is None:
        return
    features = dict(capability.features or {})
    if payload.ota is not None:
        features["ota"] = payload.ota.model_dump(mode="json")
    if payload.data_reset is not None:
        features["data_reset"] = payload.data_reset.protocol
    capability.features = features
    capability.reported_at = now


async def _reject_gated_heartbeat(
    *,
    session: DbSession,
    request: Request,
    device: Device,
    payload: Heartbeat,
    state: DeviceDataState,
    now: datetime,
    code: str,
    detail: str,
) -> None:
    """Persist safe signed liveness, never measurement history, then reject."""

    device.last_seen_at = now
    device.firmware_version = payload.firmware_version
    device.firmware_build_hash = payload.firmware_build_hash
    device.connection_mode = payload.connection_mode
    device.status = "reset_pending"
    await _refresh_signed_heartbeat_capabilities(
        session,
        device=device,
        payload=payload,
        now=now,
    )
    session.add(
        DeviceHeartbeat(
            device_id=device.id,
            boot_id=payload.boot_id,
            received_at=now,
            device_time=None,
            source_ip=getattr(request.state, "device_source_ip", None),
            current_watts=None,
            rssi_dbm=payload.rssi_dbm,
            pzem_ok=payload.pzem.ok,
            sd_ok=payload.sd.ok,
            time_trusted=payload.time.trusted,
            newest_sequence=state.reset_boundary,
            backlog_estimate=0,
            data_generation=(
                _heartbeat_generation(payload) if _heartbeat_generation(payload) is not None else 0
            ),
            payload=_safe_reset_heartbeat_payload(payload),
        )
    )
    session.add(
        DeviceStatusSnapshot(
            device_id=device.id,
            captured_at=now,
            status="reset_pending",
            evidence={
                "heartbeat": True,
                "data_reset_pending": True,
                "required_data_generation": state.data_generation,
                "reset_boundary": state.reset_boundary,
            },
        )
    )
    await session.commit()
    raise ProblemError(
        422 if code == "data_generation_required" else 409,
        "Sensor reset required" if code == "sensor_reset_required" else "Data generation rejected",
        detail,
        code,
        extra={
            "required_data_generation": state.data_generation,
            "reset_boundary": state.reset_boundary,
            "operation_id": state.active_operation_id,
        },
    )


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
    reset_state = await session.get(DeviceDataState, device.id, with_for_update=True)
    received_generation = _heartbeat_generation(payload)
    if reset_state is not None:
        required_generation = int(reset_state.data_generation)
        if reset_state.ingestion_gate != "open" or reset_state.reset_required_on_reconnect:
            await _reject_gated_heartbeat(
                session=session,
                request=request,
                device=device,
                payload=payload,
                state=reset_state,
                now=now,
                code="sensor_reset_required",
                detail=(
                    "This sensor must complete its coordinated local data reset before "
                    "measurement synchronization resumes"
                ),
            )
        if received_generation is None and required_generation > 0:
            await _reject_gated_heartbeat(
                session=session,
                request=request,
                device=device,
                payload=payload,
                state=reset_state,
                now=now,
                code="data_generation_required",
                detail="This sensor must report its coordinated reset data generation",
            )
        effective_generation = received_generation if received_generation is not None else 0
        if effective_generation < required_generation:
            await _reject_gated_heartbeat(
                session=session,
                request=request,
                device=device,
                payload=payload,
                state=reset_state,
                now=now,
                code="data_generation_obsolete",
                detail="A pre-reset heartbeat cannot update measurement state",
            )
        if effective_generation > required_generation:
            await _reject_gated_heartbeat(
                session=session,
                request=request,
                device=device,
                payload=payload,
                state=reset_state,
                now=now,
                code="data_generation_ahead",
                detail="The sensor generation is ahead of the server's committed generation",
            )
    else:
        effective_generation = received_generation if received_generation is not None else 0
    device.last_seen_at = now
    device.firmware_version = payload.firmware_version
    device.firmware_build_hash = payload.firmware_build_hash
    device.connection_mode = payload.connection_mode
    device.effective_config_version = payload.configuration_version
    device.status = _status_from_heartbeat(payload)
    await _refresh_signed_heartbeat_capabilities(
        session,
        device=device,
        payload=payload,
        now=now,
    )
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
        data_generation=effective_generation,
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
                "reading_index_integrity_verified": (
                    payload.sd.details.effective_reading_index_integrity_verified
                ),
                "event_log_integrity_verified": (payload.sd.details.event_log_integrity_verified),
                "event_log_integrity_status": payload.sd.details.event_log_integrity_status,
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
            data_generation=effective_generation,
            reset_boundary=0,
            updated_at=now,
        )
        session.add(cursor)
    if cursor.data_generation != effective_generation:
        raise ProblemError(
            409,
            "Sequence cursor generation mismatch",
            "The heartbeat generation is not aligned with the committed sequence cursor",
            "data_generation_cursor_mismatch",
        )
    server_maximum_seen = max(
        cursor.highest_contiguous_sequence,
        cursor.maximum_seen_sequence,
    )
    server_next_floor = server_maximum_seen + 1
    local_floor_raw = payload.sd.details.get("sequence_floor")
    local_floor = (
        local_floor_raw
        if isinstance(local_floor_raw, int)
        and not isinstance(local_floor_raw, bool)
        and local_floor_raw >= 0
        else None
    )
    local_record_count_raw = payload.sd.details.get("local_record_count")
    local_record_count = (
        local_record_count_raw
        if isinstance(local_record_count_raw, int)
        and not isinstance(local_record_count_raw, bool)
        and local_record_count_raw >= 0
        else None
    )
    card_empty = payload.newest_stored_sequence == 0 and local_record_count in {None, 0}
    card_generation_raw = payload.sd.details.get("card_generation")
    card_generation = card_generation_raw if isinstance(card_generation_raw, int | str) else None
    logger.info(
        "device.sequence_cursor_reported",
        device_id=device.id,
        highest_contiguous=cursor.highest_contiguous_sequence,
        maximum_seen=server_maximum_seen,
        next_floor=server_next_floor,
        sensor_local_newest=payload.newest_stored_sequence,
        sensor_local_floor=local_floor,
        card_empty=card_empty,
        card_generation=card_generation,
    )
    if card_empty and payload.sd.details.get("card_replaced_or_initialized"):
        logger.info(
            "device.card_replacement_detected",
            device_id=device.id,
            card_generation=card_generation,
            server_maximum_seen=server_maximum_seen,
        )
    if payload.server_ack_sequence > server_maximum_seen:
        logger.warning(
            "device.sequence_cursor_regression",
            device_id=device.id,
            sensor_persisted_ack=payload.server_ack_sequence,
            server_highest_contiguous=cursor.highest_contiguous_sequence,
            server_maximum_seen=server_maximum_seen,
            action="sensor_must_preserve_higher_local_floor",
        )
    if local_floor is None or local_floor < server_maximum_seen:
        logger.info(
            "device.sequence_floor_requested",
            device_id=device.id,
            current_floor=local_floor,
            required_floor=server_maximum_seen,
            next_sequence=server_next_floor,
        )
    if (
        local_floor is not None
        and local_floor >= server_maximum_seen
        and payload.sd.details.get("sequence_floor_ready") is True
    ):
        logger.info(
            "device.sequence_continuity_restored",
            device_id=device.id,
            local_floor=local_floor,
            next_sequence=payload.sd.details.get("next_sequence"),
            card_empty=card_empty,
        )
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
            data_generation=effective_generation,
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
    await _reconcile_firmware_heartbeat(session, payload, settings, now)
    release_available = bool(
        await session.scalar(
            select(FirmwareDeployment.id).where(
                FirmwareDeployment.device_id == device.id,
                FirmwareDeployment.state.in_(["scheduled", "offered"]),
                FirmwareDeployment.scheduled_at <= now,
                ((FirmwareDeployment.expires_at.is_(None)) | (FirmwareDeployment.expires_at > now)),
            )
        )
    )
    await session.commit()
    return HeartbeatResponse(
        server_receive_time=now,
        highest_contiguous_accepted_sequence=cursor.highest_contiguous_sequence,
        sequence_cursor=SequenceCursorResponse(
            highest_contiguous_accepted_sequence=cursor.highest_contiguous_sequence,
            maximum_seen_sequence=server_maximum_seen,
            next_sequence_floor=server_next_floor,
            data_generation=cursor.data_generation,
            reset_boundary=cursor.reset_boundary,
        ),
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
        data_generation=getattr(payload, "data_generation", None),
        first_available_sequence=first_available,
        unavailable_sequence_ranges=payload.unavailable_sequence_ranges,
        maximum_clock_skew_seconds=settings.max_device_clock_skew_seconds,
    )
    # An identical duplicate is cryptographic proof that the exact record is
    # already durable. It is valid post-update evidence when its boot and
    # firmware identity match the authenticated target; a lost HTTP response
    # must not strand an otherwise healthy deployment forever.
    durably_confirmed = set(result.accepted) | set(result.duplicates)
    if durably_confirmed:
        await _confirm_firmware_reading(
            session,
            verified.device.id,
            payload.readings,
            durably_confirmed,
            settings,
            datetime.now(UTC),
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
    event_sequences: list[int] = []
    now = datetime.now(UTC)
    data_state = await session.get(
        DeviceDataState,
        verified.device.id,
        with_for_update=True,
    )
    redact_measurement_evidence = bool(
        data_state is not None
        and (
            payload.data_generation != data_state.data_generation
            or data_state.ingestion_gate != "open"
        )
    )
    existing_events = {
        item.event_id: item
        for item in await session.scalars(
            select(DeviceEvent).where(
                DeviceEvent.device_id == verified.device.id,
                DeviceEvent.event_id.in_([event.event_id for event in payload.events]),
            )
        )
    }
    sequence_owners: dict[int, str] = {}
    for event in payload.events:
        event_sequence = event.evidence.get("event_sequence")
        if event_sequence is not None and (
            isinstance(event_sequence, bool)
            or not isinstance(event_sequence, int)
            or not 1 <= event_sequence <= SIGNED_BIGINT_MAX
        ):
            raise ProblemError(
                422,
                "Event sequence invalid",
                "Event sequence evidence must fit signed BIGINT storage",
                "event_sequence_invalid",
            )
        if isinstance(event_sequence, int) and event_sequence > 0:
            prior_owner = sequence_owners.get(event_sequence)
            if prior_owner is not None and prior_owner != event.event_id:
                raise ProblemError(
                    422,
                    "Event sequence conflict",
                    "One event sequence cannot identify multiple events",
                    "event_sequence_conflict",
                )
            sequence_owners[event_sequence] = event.event_id
            event_sequences.append(event_sequence)
        existing = existing_events.get(event.event_id)
        if existing:
            if (
                existing.event_sequence is None
                and isinstance(event_sequence, int)
                and event_sequence > 0
            ):
                existing.event_sequence = event_sequence
            duplicates.append(event.event_id)
            continue
        event_predates_reset = bool(
            data_state is not None
            and data_state.last_reset_at is not None
            and _aware(event.occurred_at) <= _aware(data_state.last_reset_at)
        )
        evidence = (
            redact_history_values(event.evidence)
            if redact_measurement_evidence or event_predates_reset
            else event.evidence
        )
        session.add(
            DeviceEvent(
                device_id=verified.device.id,
                event_id=event.event_id,
                event_sequence=(
                    event_sequence
                    if isinstance(event_sequence, int) and event_sequence > 0
                    else None
                ),
                occurred_at=event.occurred_at,
                received_at=now,
                category=event.category,
                severity=event.severity,
                evidence=evidence,
            )
        )
        accepted.append(event.event_id)
    ordered_sequences = sorted(set(event_sequences))
    complete_sequence_evidence = bool(ordered_sequences) and len(ordered_sequences) == len(
        payload.events
    )
    explicit_retained_boundary = (
        complete_sequence_evidence
        and payload.first_stored_event_sequence is not None
        and payload.first_stored_event_sequence == ordered_sequences[0]
    )
    await session.flush()
    cursor = await session.get(
        DeviceEventSyncCursor,
        verified.device.id,
        with_for_update=True,
    )
    if cursor is None:
        cursor = DeviceEventSyncCursor(
            device_id=verified.device.id,
            # Never infer a deletion boundary from arrival order. The signed
            # device payload must explicitly identify its oldest retained event.
            highest_contiguous_sequence=(
                ordered_sequences[0] - 1 if explicit_retained_boundary else 0
            ),
            maximum_seen_sequence=0,
            updated_at=now,
        )
        session.add(cursor)
    if complete_sequence_evidence:
        cursor.maximum_seen_sequence = max(cursor.maximum_seen_sequence, ordered_sequences[-1])
        persisted_sequences = set(
            await session.scalars(
                select(DeviceEvent.event_sequence).where(
                    DeviceEvent.device_id == verified.device.id,
                    DeviceEvent.event_sequence.is_not(None),
                    DeviceEvent.event_sequence > cursor.highest_contiguous_sequence,
                    DeviceEvent.event_sequence <= cursor.maximum_seen_sequence,
                )
            )
        )
        while cursor.highest_contiguous_sequence + 1 in persisted_sequences:
            cursor.highest_contiguous_sequence += 1
        cursor.updated_at = now
    await session.commit()
    highest_contiguous_event_sequence = (
        cursor.highest_contiguous_sequence if complete_sequence_evidence else 0
    )
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        # The firmware persists this cursor before local event evidence is
        # eligible for retention. Existing clients can ignore the additive
        # field without changing pm-protocol/1.0.0.
        "highest_contiguous_event_sequence": highest_contiguous_event_sequence,
    }


@router.get("/device-config/effective")
async def effective_config(verified: Verified, session: DbSession) -> dict[str, Any]:
    await ensure_device_reset_mutations_allowed(session, [verified.device.id])
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
    await ensure_device_reset_mutations_allowed(session, [verified.device.id])
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
    await ensure_device_reset_mutations_allowed(session, [verified.device.id])
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
    await ensure_device_reset_mutations_allowed(session, [verified.device.id])
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


@router.get(
    "/device-firmware/manifest",
    response_model=OtaManifestV2 | OtaManifestUnavailable,
)
async def firmware_manifest(
    verified: Verified, session: DbSession, settings: AppSettings
) -> dict[str, Any]:
    await ensure_device_reset_mutations_allowed(session, [verified.device.id])
    now = datetime.now(UTC)
    deployment = await session.scalar(
        select(FirmwareDeployment)
        .where(
            FirmwareDeployment.device_id == verified.device.id,
            FirmwareDeployment.state.in_(["scheduled", "offered"]),
            FirmwareDeployment.scheduled_at <= now,
            ((FirmwareDeployment.expires_at.is_(None)) | (FirmwareDeployment.expires_at > now)),
        )
        .order_by(FirmwareDeployment.scheduled_at)
        .limit(1)
        .with_for_update()
    )
    if deployment is None:
        await session.commit()
        return {"available": False, "protocol_version": PROTOCOL}
    release = await session.get(FirmwareRelease, deployment.firmware_release_id)
    capability = await session.get(DeviceCapability, verified.device.id)
    if (
        release is None
        or release.trust_mode != OTA_TRUST_MODE
        or release.verification_status != "verified"
        or release.project_name is None
        or release.build_hash is None
        or deployment.expires_at is None
        or not _manifest_artifact_available(release, settings)
    ):
        raise ProblemError(
            503,
            "Firmware unavailable",
            "Verified firmware metadata or artifact is unavailable",
            "firmware_integrity_failure",
        )
    compatibility = release_compatibility(verified.device, capability, release)
    reasons = set(compatibility["reasons"])
    if deployment.allow_downgrade:
        reasons.discard("downgrade_requires_confirmation")
    if reasons:
        raise ProblemError(
            409,
            "Firmware incompatible",
            "Sensor capability no longer matches the scheduled firmware",
            "firmware_incompatible",
        )
    manifest: dict[str, Any] = {
        "schema_version": OTA_MANIFEST_SCHEMA,
        "protocol_version": PROTOCOL,
        "deployment_id": deployment.id,
        "release_id": release.id,
        "device_id": verified.device.id,
        "version": release.version,
        "project_name": release.project_name,
        "hardware_target": release.hardware_target,
        "protocol_min": release.protocol_min,
        "protocol_max": release.protocol_max,
        "size_bytes": release.size_bytes,
        "sha256": release.sha256,
        "build_hash": release.build_hash,
        "not_before": canonical_utc(deployment.scheduled_at),
        "expires_at": canonical_utc(deployment.expires_at),
        "allow_downgrade": deployment.allow_downgrade,
        "attempt": deployment.attempt,
        "hmac_algorithm": OTA_MANIFEST_HMAC_ALGORITHM,
        "hmac_key_context": OTA_MANIFEST_HKDF_INFO.decode("ascii"),
        "download_path": (
            f"/api/v1/device-firmware/{release.id}/download?deployment_id={deployment.id}"
        ),
    }
    secret_buffer = bytearray(
        SecretCipher(settings.app_master_key).decrypt(verified.credential.encrypted_secret)
    )
    try:
        manifest["manifest_hmac"] = sign_ota_manifest(
            bytes(secret_buffer), verified.device.id, manifest
        )
    finally:
        for index in range(len(secret_buffer)):
            secret_buffer[index] = 0
    if deployment.state == "scheduled":
        transition_firmware_deployment(deployment, "offered", now)
    await session.commit()
    return manifest
