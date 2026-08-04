from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AlertInstance,
    AuditEvent,
    Device,
    DeviceHeartbeat,
    FirmwareDeployment,
    FirmwareRelease,
    new_uuid,
)
from app.ota import ACTIVE_DEPLOYMENT_STATES, DEPLOYMENT_TRANSITIONS, TERMINAL_DEPLOYMENT_STATES

VerificationState = Literal["pending", "passed", "failed", "unavailable"]
_POST_BOOT_REPORT_STATES = {
    "partition_written",
    "rebooting",
    "post_boot_validation",
    "validated",
}


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def transition_firmware_deployment(
    deployment: FirmwareDeployment,
    target_state: str,
    now: datetime,
    *,
    evidence_reconciliation: bool = False,
    supersede_terminal_evidence: bool = False,
    retry: bool = False,
) -> bool:
    """Apply the single persisted OTA state transition rule.

    Authenticated milestones use the declared state graph. Server reconciliation may
    skip lost milestones only when backed by retained sensor evidence. Retry is the
    ordinary terminal-to-active transition; ``supersede_terminal_evidence`` is the
    narrowly scoped exception for a target heartbeat whose persisted attempt, boot,
    and evidence sequence prove that an earlier terminal report arrived out of order.
    """

    if deployment.state == target_state:
        return False
    allowed = target_state in DEPLOYMENT_TRANSITIONS.get(deployment.state, frozenset())
    if retry:
        allowed = (
            deployment.state in {"failed", "cancelled", "rolled_back"}
            and target_state == "scheduled"
        )
    elif evidence_reconciliation:
        allowed = deployment.state in ACTIVE_DEPLOYMENT_STATES and (
            target_state in TERMINAL_DEPLOYMENT_STATES or target_state == "awaiting_heartbeat"
        )
    if supersede_terminal_evidence:
        allowed = (
            deployment.state in {"failed", "rolled_back"} and target_state == "awaiting_heartbeat"
        )
    if not allowed:
        raise ValueError(f"invalid firmware transition {deployment.state!r} -> {target_state!r}")

    deployment.state = deployment.status = target_state
    deployment.state_changed_at = now
    deployment.terminal_at = now if target_state in TERMINAL_DEPLOYMENT_STATES else None
    deployment.revision += 1
    return True


def is_superseded_target_report(
    deployment: FirmwareDeployment,
    release: FirmwareRelease,
    *,
    state: str,
    current_firmware_version: str,
    current_build_hash: str,
) -> bool:
    """Recognize a milestone made obsolete by newer signed target heartbeat evidence."""

    return bool(
        deployment.state in {"awaiting_heartbeat", "completed"}
        and state in _POST_BOOT_REPORT_STATES
        and current_firmware_version == release.version
        and current_build_hash == release.build_hash
    )


@dataclass(frozen=True)
class ReconciliationDecision:
    state: Literal["failed", "rolled_back"]
    code: str
    summary: str
    evidence: dict[str, Any]


def _last_activity(deployment: FirmwareDeployment) -> datetime:
    value: datetime | None
    if deployment.state in {
        "manifest_authenticated",
        "waiting_for_schedule",
        "download_started",
        "downloading",
        "binary_verified",
    }:
        value = deployment.last_report_at or deployment.state_changed_at
    else:
        value = deployment.state_changed_at or deployment.last_report_at
    return aware(
        value
        or deployment.validated_at
        or deployment.installed_at
        or deployment.downloaded_at
        or deployment.scheduled_at
        or deployment.created_at
    )


def classify_stale_firmware_deployment(
    deployment: FirmwareDeployment,
    device: Device,
    settings: Any,
    now: datetime,
) -> ReconciliationDecision | None:
    """Return a deterministic terminal outcome for an overdue OTA attempt."""

    if deployment.state in TERMINAL_DEPLOYMENT_STATES:
        return None
    now = aware(now)
    activity = _last_activity(deployment)
    age_seconds = max(0, int((now - activity).total_seconds()))
    evidence = {
        "last_state": deployment.state,
        "last_activity_at": activity.isoformat(),
        "age_seconds": age_seconds,
        "attempt": deployment.attempt,
    }

    if (
        not deployment.source_version or not deployment.source_build_hash
    ) and age_seconds >= settings.firmware_legacy_evidence_timeout_seconds:
        return ReconciliationDecision(
            "failed",
            "ota_legacy_evidence_incomplete",
            "This legacy deployment has insufficient retained identity evidence "
            "to prove a safe outcome. Retry creates a new, fully evidenced attempt.",
            {**evidence, "reason": "missing_source_identity"},
        )

    if deployment.state == "rollback_detected":
        if deployment.rollback_version or deployment.rollback_build_hash:
            return ReconciliationDecision(
                "rolled_back",
                "ota_rollback_detected",
                "The sensor returned to its previous firmware after the target image was observed.",
                {**evidence, "reason": "authenticated_rollback_identity"},
            )
        if age_seconds >= settings.firmware_sensor_return_timeout_seconds:
            return ReconciliationDecision(
                "failed",
                "ota_rollback_detected",
                "Rollback was reported, but the sensor did not provide enough "
                "identity evidence to confirm the recovered image.",
                {**evidence, "reason": "rollback_identity_incomplete"},
            )
        return None

    if deployment.state == "waiting_canary":
        if deployment.expires_at is not None and aware(deployment.expires_at) <= now:
            return ReconciliationDecision(
                "failed",
                "ota_deployment_expired",
                "The rollout expired before this sensor was promoted from the canary queue.",
                {**evidence, "reason": "canary_wait_expired"},
            )
        if (
            deployment.expires_at is None
            and age_seconds >= settings.firmware_legacy_evidence_timeout_seconds
        ):
            return ReconciliationDecision(
                "failed",
                "ota_deployment_expired",
                "This legacy canary deployment has no retained expiry and did not advance "
                "within the conservative reconciliation window.",
                {**evidence, "reason": "legacy_canary_missing_expiry"},
            )
        return None

    if deployment.state == "scheduled":
        if deployment.expires_at is not None and aware(deployment.expires_at) <= now:
            return ReconciliationDecision(
                "failed",
                "ota_manifest_expired",
                "The authenticated firmware offer expired before the sensor requested it.",
                {**evidence, "reason": "manifest_not_requested"},
            )
        if (
            deployment.expires_at is None
            and age_seconds >= settings.firmware_legacy_evidence_timeout_seconds
        ):
            return ReconciliationDecision(
                "failed",
                "ota_manifest_expired",
                "This legacy scheduled deployment has no retained manifest expiry and did "
                "not advance within the conservative reconciliation window.",
                {**evidence, "reason": "legacy_manifest_missing_expiry"},
            )
        return None

    if deployment.state == "offered":
        if age_seconds >= settings.firmware_sensor_return_timeout_seconds:
            return ReconciliationDecision(
                "failed",
                "ota_sensor_did_not_return",
                "The sensor requested the update but did not return an "
                "authenticated installation report.",
                {
                    **evidence,
                    "reason": "offer_not_acknowledged",
                    "device_last_seen_at": (
                        aware(device.last_seen_at).isoformat() if device.last_seen_at else None
                    ),
                },
            )
        return None

    if deployment.state == "waiting_for_schedule":
        # The manifest is authenticated, but the device's configured local
        # maintenance window has not opened. This is not download activity and
        # must never inherit the short in-flight download timeout. The signed
        # manifest expiry remains the authoritative upper bound.
        if deployment.expires_at is not None and aware(deployment.expires_at) <= now:
            return ReconciliationDecision(
                "failed",
                "ota_manifest_expired",
                "The authenticated firmware offer expired before the configured "
                "sensor update window opened.",
                {**evidence, "reason": "update_window_not_reached_before_expiry"},
            )
        if (
            deployment.expires_at is None
            and age_seconds >= settings.firmware_legacy_evidence_timeout_seconds
        ):
            return ReconciliationDecision(
                "failed",
                "ota_manifest_expired",
                "This legacy scheduled update has no retained manifest expiry and "
                "did not reach its update window within the conservative "
                "reconciliation window.",
                {**evidence, "reason": "legacy_update_window_missing_expiry"},
            )
        return None

    if deployment.state in {
        "manifest_authenticated",
        "download_started",
        "downloading",
        "binary_verified",
    }:
        if age_seconds >= settings.firmware_download_stale_seconds:
            return ReconciliationDecision(
                "failed",
                "ota_update_timed_out",
                "The sensor stopped reporting during the authenticated download "
                "workflow; the previous firmware remains authoritative until Retry "
                "is requested.",
                {**evidence, "reason": "authenticated_report_timeout"},
            )
        return None

    if deployment.state in {"partition_written", "rebooting"}:
        if age_seconds >= settings.firmware_sensor_return_timeout_seconds:
            return ReconciliationDecision(
                "failed",
                "ota_sensor_did_not_return",
                "The image was written, but the sensor did not return with "
                "authenticated boot evidence within the recovery window.",
                {**evidence, "reason": "post_write_sensor_absent"},
            )
        return None

    if (
        deployment.state in {"post_boot_validation", "validated", "awaiting_heartbeat"}
        and age_seconds >= settings.firmware_post_boot_timeout_seconds
    ):
        missing = []
        if deployment.validated_version is None:
            missing.append("target_identity")
        if deployment.reading_confirmed_at is None:
            missing.append("post_update_reading")
        if deployment.verification_heartbeats < settings.firmware_verification_heartbeat_count:
            missing.append("verification_heartbeats")
        return ReconciliationDecision(
            "failed",
            "ota_post_boot_timeout",
            "The target image did not complete server-side health stabilization "
            "within the allowed window.",
            {**evidence, "reason": "post_boot_validation_timeout", "missing": missing},
        )
    return None


def _audit_transition(
    deployment: FirmwareDeployment, decision: ReconciliationDecision, now: datetime
) -> AuditEvent:
    return AuditEvent(
        id=new_uuid(),
        occurred_at=now,
        actor_type="system",
        actor_id=None,
        action="firmware.deployment_reconciled",
        object_type="firmware_deployment",
        object_id=deployment.id,
        source_ip=None,
        outcome="success",
        correlation_id=None,
        details={
            **decision.evidence,
            "terminal_state": decision.state,
            "failure_code": decision.code,
        },
    )


async def reconcile_stale_firmware_deployments(
    session: AsyncSession,
    settings: Any,
    now: datetime | None = None,
    *,
    limit: int | None = None,
) -> dict[str, int]:
    """Lock and terminalize overdue deployments independently of UI activity."""

    current = aware(now or datetime.now(UTC))
    batch_size = limit or settings.firmware_reconcile_batch_size
    rows: list[tuple[FirmwareDeployment, Device]] = []
    # Bound each state independently. A single large, healthy scheduled/canary
    # queue must not occupy the global batch forever while a later download or
    # post-boot attempt is already overdue. Within one state, older activity is
    # always at least as urgent as newer activity; expiry-driven queues sort by
    # their explicit expiry when present.
    for state in sorted(ACTIVE_DEPLOYMENT_STATES):
        order_column = (
            func.coalesce(FirmwareDeployment.expires_at, FirmwareDeployment.state_changed_at)
            if state in {"scheduled", "waiting_canary"}
            else FirmwareDeployment.state_changed_at
        )
        state_rows = (
            await session.execute(
                select(FirmwareDeployment, Device)
                .join(Device, Device.id == FirmwareDeployment.device_id)
                .where(FirmwareDeployment.state == state)
                .order_by(order_column, FirmwareDeployment.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
        rows.extend((deployment, device) for deployment, device in state_rows)
    outcomes: dict[str, int] = {"examined": len(rows), "terminalized": 0}
    for deployment, device in rows:
        decision = classify_stale_firmware_deployment(deployment, device, settings, current)
        if decision is None:
            continue
        if not transition_firmware_deployment(
            deployment, decision.state, current, evidence_reconciliation=True
        ):
            continue
        deployment.failure_code = decision.code
        deployment.failure_summary = decision.summary
        deployment.failure_reason = decision.summary
        deployment.interruption_evidence = decision.evidence
        if decision.state == "rolled_back":
            deployment.rollback_at = deployment.rollback_at or current
        session.add(_audit_transition(deployment, decision, current))
        outcomes["terminalized"] += 1
        outcomes[decision.code] = outcomes.get(decision.code, 0) + 1
    return outcomes


@dataclass(frozen=True)
class VerificationContext:
    device: Device | None = None
    heartbeat: DeviceHeartbeat | None = None
    critical_alert_count: int = 0


async def load_verification_contexts(
    session: AsyncSession, deployments: list[FirmwareDeployment]
) -> dict[str, VerificationContext]:
    """Load checklist evidence for a deployment list without an N+1 query."""

    device_ids = {item.device_id for item in deployments}
    if not device_ids:
        return {}
    devices = {
        item.id: item
        for item in await session.scalars(select(Device).where(Device.id.in_(device_ids)))
    }
    # Resolve the latest heartbeat for every retained boot, not merely the
    # device's latest heartbeat. Deployment history can contain several target
    # boots for one sensor; one device-level row would let a later source or
    # ordinary boot rewrite the evidence shown for an older deployment.
    latest = (
        select(
            DeviceHeartbeat.device_id,
            DeviceHeartbeat.boot_id,
            func.max(DeviceHeartbeat.received_at).label("received_at"),
        )
        .where(DeviceHeartbeat.device_id.in_(device_ids))
        .group_by(DeviceHeartbeat.device_id, DeviceHeartbeat.boot_id)
        .subquery()
    )
    heartbeat_rows = (
        await session.execute(
            select(DeviceHeartbeat)
            .join(
                latest,
                (latest.c.device_id == DeviceHeartbeat.device_id)
                & (latest.c.boot_id == DeviceHeartbeat.boot_id)
                & (latest.c.received_at == DeviceHeartbeat.received_at),
            )
            .order_by(DeviceHeartbeat.id)
        )
    ).scalars()
    heartbeats: dict[tuple[str, str], DeviceHeartbeat] = {}
    for heartbeat in heartbeat_rows:
        heartbeats[(heartbeat.device_id, heartbeat.boot_id)] = heartbeat
    alert_rows = await session.execute(
        select(AlertInstance.device_id, func.count(AlertInstance.id))
        .where(
            AlertInstance.device_id.in_(device_ids),
            AlertInstance.status.in_(["active", "acknowledged"]),
            AlertInstance.severity == "critical",
        )
        .group_by(AlertInstance.device_id)
    )
    alerts = {device_id: int(count) for device_id, count in alert_rows if device_id is not None}
    return {
        deployment.id: VerificationContext(
            device=devices.get(deployment.device_id),
            heartbeat=(
                heartbeats.get((deployment.device_id, deployment.last_boot_id))
                if deployment.last_boot_id
                else None
            ),
            critical_alert_count=alerts.get(deployment.device_id, 0),
        )
        for deployment in deployments
    }


def _check(
    key: str,
    label: str,
    status: VerificationState,
    detail: str,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "observed_at": observed_at,
    }


def build_firmware_verification(
    deployment: FirmwareDeployment,
    release: FirmwareRelease | None,
    settings: Any,
    context: VerificationContext | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build one authoritative verification checklist and current blocker."""

    current = aware(now or datetime.now(UTC))
    context = context or VerificationContext()
    heartbeat = context.heartbeat
    heartbeat_requirement = settings.firmware_verification_heartbeat_count
    stabilization_required = max(
        0,
        (heartbeat_requirement - 1) * settings.heartbeat_expectation_seconds,
    )
    stabilization_elapsed = (
        max(0, int((current - aware(deployment.stabilization_started_at)).total_seconds()))
        if deployment.stabilization_started_at
        else 0
    )
    target_ok = bool(
        release
        and deployment.validated_version == release.version
        and deployment.validated_build_hash == release.build_hash
    )
    heartbeat_payload = (
        heartbeat.payload if heartbeat and isinstance(heartbeat.payload, dict) else {}
    )
    heartbeat_resources = heartbeat_payload.get("resources", {})
    heartbeat_resources = heartbeat_resources if isinstance(heartbeat_resources, dict) else {}
    heartbeat_recovery = heartbeat_resources.get("ota_recovery", {})
    heartbeat_recovery = heartbeat_recovery if isinstance(heartbeat_recovery, dict) else {}
    interruption_evidence = (
        deployment.interruption_evidence
        if isinstance(deployment.interruption_evidence, dict)
        else {}
    )
    interruption_recovery = interruption_evidence.get("ota_recovery", {})
    interruption_recovery = interruption_recovery if isinstance(interruption_recovery, dict) else {}
    retained_recovery = heartbeat_recovery or interruption_recovery
    previous_boot_stage = retained_recovery.get("previous_boot_stage")
    previous_reset_reason = heartbeat_payload.get("reboot_reason") or retained_recovery.get(
        "previous_reset_reason"
    )
    target_boot_observed = bool(target_ok and deployment.last_boot_id)
    heartbeat_is_target = bool(
        heartbeat
        and deployment.last_boot_id
        and heartbeat.boot_id == deployment.last_boot_id
        and target_ok
    )
    completed = deployment.state == "completed"

    def target_health_status(value: bool | None) -> VerificationState:
        if heartbeat_is_target:
            return "passed" if value else "failed"
        # A current-version completion could only be persisted after a healthy
        # target heartbeat. If heartbeat retention no longer contains that
        # exact boot, the terminal record remains authoritative. Older legacy
        # completions without target identity remain explicitly unavailable.
        if completed and target_boot_observed:
            return "passed"
        return "unavailable" if completed else "pending"

    health_observed_at = (
        heartbeat.received_at
        if heartbeat_is_target and heartbeat
        else deployment.terminal_at
        if completed and target_boot_observed
        else None
    )
    blocking_critical_alert_count = 0 if completed else context.critical_alert_count
    checks = [
        _check(
            "target_identity",
            "Target firmware identity",
            "passed" if target_ok else "pending",
            (
                f"Verified {deployment.validated_version}."
                if target_ok
                else "Waiting for the target version and build hash."
            ),
            deployment.validated_at,
        ),
        _check(
            "target_boot",
            "Target boot",
            "passed" if target_boot_observed else "unavailable" if completed else "pending",
            "Authenticated target boot observed."
            if target_boot_observed
            else "Waiting for an authenticated target boot.",
            heartbeat.received_at
            if heartbeat_is_target and heartbeat
            else deployment.validated_at
            if target_boot_observed
            else None,
        ),
        _check(
            "pzem",
            "PZEM measurement hardware",
            target_health_status(heartbeat.pzem_ok if heartbeat_is_target and heartbeat else None),
            "PZEM health passed."
            if (heartbeat_is_target and heartbeat and heartbeat.pzem_ok)
            or (completed and target_boot_observed)
            else "A healthy PZEM report from the authenticated target boot is required.",
            health_observed_at,
        ),
        _check(
            "storage",
            "microSD storage",
            target_health_status(heartbeat.sd_ok if heartbeat_is_target and heartbeat else None),
            "Storage health passed."
            if (heartbeat_is_target and heartbeat and heartbeat.sd_ok)
            or (completed and target_boot_observed)
            else "Writable durable storage from the authenticated target boot is required.",
            health_observed_at,
        ),
        _check(
            "trusted_time",
            "Trusted time",
            target_health_status(
                heartbeat.time_trusted if heartbeat_is_target and heartbeat else None
            ),
            "Time trust passed."
            if (heartbeat_is_target and heartbeat and heartbeat.time_trusted)
            or (completed and target_boot_observed)
            else "Trusted time from the authenticated target boot is required.",
            health_observed_at,
        ),
        _check(
            "verification_heartbeats",
            "Healthy verification heartbeats",
            "passed" if deployment.verification_heartbeats >= heartbeat_requirement else "pending",
            f"{deployment.verification_heartbeats} of {heartbeat_requirement} received.",
            heartbeat.received_at if heartbeat else None,
        ),
        _check(
            "stabilization",
            "Server stabilization window",
            "passed" if stabilization_elapsed >= stabilization_required else "pending",
            f"{stabilization_elapsed} of {stabilization_required} seconds elapsed.",
            deployment.stabilization_started_at,
        ),
        _check(
            "post_update_reading",
            "Post-update reading",
            "passed" if deployment.reading_confirmed_at else "pending",
            "A durable reading was accepted."
            if deployment.reading_confirmed_at
            else "Waiting for the first durable reading.",
            deployment.reading_confirmed_at,
        ),
        _check(
            "critical_alerts",
            "Critical sensor alerts",
            "passed" if blocking_critical_alert_count == 0 else "failed",
            "No active critical alerts."
            if blocking_critical_alert_count == 0
            else f"{blocking_critical_alert_count} critical alert(s) must be resolved.",
        ),
    ]

    blocker: dict[str, Any] | None = None
    if deployment.state in {"failed", "rolled_back"}:
        blocker = {
            "code": deployment.failure_code or "ota_update_failed",
            "state": deployment.state,
            "title": "Firmware update did not complete",
            "detail": deployment.failure_summary
            or deployment.failure_reason
            or "Review retained evidence before retrying.",
            "action": "retry",
        }
    elif deployment.state == "cancelled":
        blocker = {
            "code": "ota_update_cancelled",
            "state": deployment.state,
            "title": "Firmware update was cancelled",
            "detail": "Schedule a new deployment when the sensor is ready.",
            "action": "schedule",
        }
    elif deployment.state != "completed":
        failed_check = next((item for item in checks if item["status"] == "failed"), None)
        pending_check = next((item for item in checks if item["status"] == "pending"), None)
        selected = failed_check or pending_check
        if selected is not None:
            blocker = {
                "code": f"ota_waiting_{selected['key']}",
                "state": deployment.state,
                "title": selected["label"],
                "detail": selected["detail"],
                "action": "resolve" if failed_check else "wait",
            }
    return {
        "checks": checks,
        "blocker": blocker,
        "target_version_expected": release.version if release else None,
        "target_version_observed": deployment.validated_version,
        "target_build_hash_expected": release.build_hash if release else None,
        "target_build_hash_observed": deployment.validated_build_hash,
        "target_boot_id_observed": deployment.last_boot_id if target_boot_observed else None,
        "previous_boot_stage": str(previous_boot_stage) if previous_boot_stage else None,
        "previous_reset_reason": (str(previous_reset_reason) if previous_reset_reason else None),
        "rollback_state": (
            "rolled_back"
            if deployment.state == "rolled_back"
            else "detected"
            if deployment.rollback_at is not None
            else "not_detected"
        ),
        "exact_failure_code": deployment.failure_code,
        "blocking_critical_alert_count": blocking_critical_alert_count,
        "verification_heartbeat_count": deployment.verification_heartbeats,
        "verification_heartbeat_required": heartbeat_requirement,
        "last_sensor_activity_at": context.device.last_seen_at if context.device else None,
        "last_report_at": deployment.last_report_at,
        "stabilization_elapsed_seconds": stabilization_elapsed,
        "stabilization_required_seconds": stabilization_required,
    }
