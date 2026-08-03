from __future__ import annotations

import base64
import hashlib
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.access import require_recent_reauthentication
from app.api.deps import (
    AppSettings,
    CsrfPrincipal,
    DbSession,
    Principal,
    Verified,
    Viewer,
    audit_event,
)
from app.db.models import (
    AlertInstance,
    Device,
    DeviceCapability,
    FirmwareDeployment,
    FirmwareRelease,
    new_uuid,
)
from app.firmware_images import FirmwareImageError, parse_esp32s3_application_image
from app.ota import (
    ACTIVE_DEPLOYMENT_STATES,
    DEPLOYMENT_TRANSITIONS,
    OTA_TRUST_MODE,
    TERMINAL_DEPLOYMENT_STATES,
    compare_semver,
    firmware_readiness_payload,
    release_compatibility,
)
from app.problem import ProblemError
from app.schemas import (
    FirmwareCanaryPromotionResponse,
    FirmwareDeploymentCreate,
    FirmwareDeploymentCreateResponse,
    FirmwareDeploymentReport,
    FirmwareDeploymentReportResponse,
    FirmwareDeploymentView,
    FirmwareReadinessView,
    FirmwareReleaseView,
)

router = APIRouter(prefix="/api/v1", tags=["firmware"])
UPLOAD_CHUNK_BYTES = 64 * 1024
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _operator(principal: Principal, permission: str = "firmware.manage") -> None:
    if permission not in principal.permissions:
        raise ProblemError(403, "Permission denied", "Firmware permission is required", "forbidden")


def _site_allowed(principal: Principal, device: Device) -> None:
    if not principal.can_access_site(device.site_id):
        raise ProblemError(
            403,
            "Permission denied",
            "Sensor is outside your assigned sites",
            "forbidden",
        )


def _safe_filename(value: str | None) -> str:
    basename = (value or "firmware.bin").replace("\\", "/").rsplit("/", 1)[-1]
    sanitized = _SAFE_FILENAME.sub("-", basename).strip(".-")[:255]
    return sanitized or "firmware.bin"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(UPLOAD_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def _install_content_addressed(temp_path: Path, target: Path, expected_sha256: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or _hash_file(target) != expected_sha256:
            raise FirmwareImageError(
                "firmware_integrity_failure",
                "Content-addressed firmware artifact already exists with different bytes",
            )
        return
    os.replace(temp_path, target)
    if os.name != "nt":
        try:
            directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _resolve_artifact(release: FirmwareRelease, settings: Any) -> Path:
    root = settings.firmware_path.resolve()
    if release.artifact_path:
        candidate = (root / release.artifact_path).resolve()
    elif release.file_path:
        candidate = Path(release.file_path).resolve()
    else:
        raise ProblemError(
            503,
            "Firmware integrity failure",
            "Verified firmware artifact path is missing",
            "firmware_integrity_failure",
        )
    if candidate != root and root not in candidate.parents:
        raise ProblemError(
            503,
            "Firmware integrity failure",
            "Firmware artifact path is outside configured storage",
            "firmware_integrity_failure",
        )
    return candidate


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _verified_artifact(release: FirmwareRelease, session: DbSession, settings: Any) -> Path:
    path = _resolve_artifact(release, settings)
    try:
        stat = path.stat()
    except OSError:
        stat = None
    if stat is None or not path.is_file() or stat.st_size != release.size_bytes:
        release.verification_status = "quarantined"
        await session.commit()
        raise ProblemError(
            503,
            "Firmware integrity failure",
            "Stored firmware artifact is missing or has the wrong size",
            "firmware_integrity_failure",
        )
    now = datetime.now(UTC)
    last_verified = _aware(release.artifact_verified_at) if release.artifact_verified_at else None
    verification_due = last_verified is None or last_verified <= now - timedelta(
        seconds=settings.firmware_artifact_verify_interval_seconds
    )
    if verification_due:
        digest = await run_in_threadpool(_hash_file, path)
        if digest != release.sha256:
            release.verification_status = "quarantined"
            await session.commit()
            raise ProblemError(
                503,
                "Firmware integrity failure",
                "Stored firmware artifact failed SHA-256 verification",
                "firmware_integrity_failure",
            )
        release.artifact_verified_at = now
        await session.flush()
    return path


def _release_payload(release: FirmwareRelease, *, duplicate: bool = False) -> dict[str, Any]:
    return {
        "id": release.id,
        "version": release.version,
        "project_name": release.project_name,
        "hardware_target": release.hardware_target,
        "protocol_min": release.protocol_min,
        "protocol_max": release.protocol_max,
        "size_bytes": release.size_bytes,
        "sha256": release.sha256,
        "build_hash": release.build_hash,
        "build_timestamp": release.build_timestamp,
        "trust_mode": release.trust_mode,
        "verification_status": release.verification_status,
        "verification_evidence": release.verification_evidence,
        "verified_at": release.verified_at,
        "active": release.active,
        "artifact_download_path": f"/api/v1/firmware-releases/{release.id}/artifact",
        "compatibility": {
            "verified_image": release.verification_status == "verified",
            "device_selection_required": True,
        },
        "duplicate": duplicate,
    }


def _deployment_payload(
    deployment: FirmwareDeployment, release: FirmwareRelease | None = None
) -> dict[str, Any]:
    return {
        "id": deployment.id,
        "firmware_release_id": deployment.firmware_release_id,
        "device_id": deployment.device_id,
        "state": deployment.state,
        "status": deployment.state,
        "revision": deployment.revision,
        "attempt": deployment.attempt,
        "progress": deployment.progress,
        "bytes_received": deployment.bytes_received,
        "scheduled_at": deployment.scheduled_at,
        "expires_at": deployment.expires_at,
        "allow_downgrade": deployment.allow_downgrade,
        "rollout_group_id": deployment.rollout_group_id,
        "rollout_order": deployment.rollout_order,
        "promoted_at": deployment.promoted_at,
        "downloaded_at": deployment.downloaded_at,
        "installed_at": deployment.installed_at,
        "validated_at": deployment.validated_at,
        "last_report_at": deployment.last_report_at,
        "failure_code": deployment.failure_code,
        "failure_summary": deployment.failure_summary or deployment.failure_reason,
        "validated_version": deployment.validated_version,
        "validated_build_hash": deployment.validated_build_hash,
        "rollback_at": deployment.rollback_at,
        "rollback_version": deployment.rollback_version,
        "rollback_build_hash": deployment.rollback_build_hash,
        "verification_heartbeats": deployment.verification_heartbeats,
        "reading_confirmed_at": deployment.reading_confirmed_at,
        "created_at": deployment.created_at,
        "target_version": release.version if release else None,
        "target_sha256": release.sha256 if release else None,
        "target_build_hash": release.build_hash if release else None,
    }


@router.get("/firmware-releases", response_model=list[FirmwareReleaseView])
async def list_releases(principal: Viewer, session: DbSession) -> list[dict[str, Any]]:
    _operator(principal, "firmware.view")
    releases = list(
        await session.scalars(select(FirmwareRelease).order_by(FirmwareRelease.created_at.desc()))
    )
    return [_release_payload(release) for release in releases]


@router.post("/firmware-releases", status_code=201, response_model=FirmwareReleaseView)
async def upload_release(
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
    binary: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    _operator(principal, "firmware.manage")
    form_items = list((await request.form()).multi_items())
    if len(form_items) != 1 or form_items[0][0] != "binary":
        raise ProblemError(
            422,
            "Invalid firmware upload",
            "Upload exactly one multipart field named binary",
            "firmware_upload_fields_invalid",
        )
    root = settings.firmware_path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".ota-upload-", suffix=".tmp", dir=root)
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with os.fdopen(descriptor, "wb") as target:
            while chunk := await binary.read(UPLOAD_CHUNK_BYTES):
                size_bytes += len(chunk)
                if size_bytes > settings.firmware_max_bytes:
                    raise ProblemError(
                        413,
                        "Firmware too large",
                        "Firmware exceeds the configured upload limit",
                        "firmware_too_large",
                    )
                target.write(chunk)
                digest.update(chunk)
            target.flush()
            os.fsync(target.fileno())
        if size_bytes == 0:
            raise ProblemError(
                422, "Invalid firmware", "Firmware image is empty", "firmware_image_invalid"
            )
        try:
            parsed = await run_in_threadpool(
                partial(
                    parse_esp32s3_application_image,
                    temporary_path,
                    maximum_bytes=settings.firmware_max_bytes,
                    ota_partition_size_bytes=settings.firmware_ota_partition_size_bytes,
                    expected_project_name=settings.firmware_project_name,
                )
            )
        except FirmwareImageError as exc:
            status = 413 if exc.code == "firmware_too_large" else 422
            raise ProblemError(status, "Invalid firmware", exc.detail, exc.code) from exc

        sha256 = digest.hexdigest()
        existing_version = await session.scalar(
            select(FirmwareRelease).where(
                FirmwareRelease.trust_mode == OTA_TRUST_MODE,
                FirmwareRelease.version == parsed.version,
                FirmwareRelease.hardware_target == parsed.hardware_target,
            )
        )
        if existing_version is not None:
            if existing_version.sha256 != sha256:
                raise ProblemError(
                    409,
                    "Firmware version conflict",
                    "This target/version already exists with different firmware bytes",
                    "firmware_version_conflict",
                )
            return _release_payload(existing_version, duplicate=True)
        existing_hash = await session.scalar(
            select(FirmwareRelease).where(FirmwareRelease.sha256 == sha256)
        )
        if existing_hash is not None:
            if existing_hash.trust_mode != OTA_TRUST_MODE:
                raise ProblemError(
                    409,
                    "Legacy firmware hash conflict",
                    (
                        "These bytes already belong to a legacy signed release; "
                        "build a newly versioned image for device-authenticated HMAC OTA"
                    ),
                    "firmware_legacy_hash_conflict",
                )
            return _release_payload(existing_hash, duplicate=True)

        relative_artifact = Path(sha256) / "firmware.bin"
        final_path = (root / relative_artifact).resolve()
        if root not in final_path.parents:
            raise ProblemError(500, "Unsafe path", "Firmware path validation failed", "unsafe_path")
        try:
            await run_in_threadpool(_install_content_addressed, temporary_path, final_path, sha256)
        except FirmwareImageError as exc:
            raise ProblemError(503, "Firmware integrity failure", exc.detail, exc.code) from exc

        now = datetime.now(UTC)
        release = FirmwareRelease(
            version=parsed.version,
            channel="stable",
            trust_mode=OTA_TRUST_MODE,
            project_name=parsed.project_name,
            hardware_target=parsed.hardware_target,
            protocol_min=parsed.protocol_min,
            protocol_max=parsed.protocol_max,
            file_path=None,
            artifact_path=relative_artifact.as_posix(),
            size_bytes=parsed.size_bytes,
            sha256=sha256,
            build_hash=parsed.build_hash,
            build_timestamp=parsed.build_timestamp,
            original_filename=_safe_filename(binary.filename),
            uploaded_by=principal.user.id,
            verification_status="verified",
            verification_evidence={
                "esp_image_magic": True,
                "application_image": True,
                "chip_id": parsed.chip_id,
                "segment_count": parsed.segment_count,
                "image_checksum": True,
                "appended_image_sha256": parsed.image_hash,
                "project_name": parsed.project_name,
                "semantic_version": parsed.version,
                "protocol_version": parsed.protocol_min,
                "ota_partition_size_bytes": settings.firmware_ota_partition_size_bytes,
                "existing_device_trust": True,
            },
            artifact_verified_at=now,
            signature=None,
            signing_key_id=None,
            release_notes="",
            verified_at=now,
            active=False,
        )
        session.add(release)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            conflict = await session.scalar(
                select(FirmwareRelease).where(
                    FirmwareRelease.version == parsed.version,
                    FirmwareRelease.hardware_target == parsed.hardware_target,
                )
            )
            if conflict is not None and conflict.sha256 == sha256:
                return _release_payload(conflict, duplicate=True)
            raise ProblemError(
                409,
                "Firmware version conflict",
                "This target/version was uploaded concurrently with different bytes",
                "firmware_version_conflict",
            ) from exc
        session.add(
            audit_event(
                action="firmware.uploaded_verified",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="firmware_release",
                object_id=release.id,
                details={
                    "sha256": sha256,
                    "trust_mode": OTA_TRUST_MODE,
                    "version": release.version,
                    "hardware_target": release.hardware_target,
                },
            )
        )
        await session.commit()
        return _release_payload(release)
    finally:
        await binary.close()
        await run_in_threadpool(_remove_file, temporary_path)


@router.get("/firmware-releases/{release_id}/artifact", response_class=FileResponse)
async def admin_download_artifact(
    release_id: str,
    principal: Viewer,
    session: DbSession,
    settings: AppSettings,
) -> FileResponse:
    _operator(principal, "firmware.manage")
    release = await session.get(FirmwareRelease, release_id)
    if release is None or release.verification_status != "verified":
        raise ProblemError(
            404, "Release not found", "Verified firmware release does not exist", "firmware_missing"
        )
    path = await _verified_artifact(release, session, settings)
    await session.commit()
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=f"power-monitor-sensor-{release.version}.bin",
        headers={
            "Cache-Control": "no-store",
            "Content-Length": str(release.size_bytes),
            "Digest": f"sha-256={base64.b64encode(bytes.fromhex(release.sha256)).decode()}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/devices/{device_id}/firmware-readiness", response_model=FirmwareReadinessView)
async def firmware_readiness(
    device_id: str,
    principal: Viewer,
    session: DbSession,
    settings: AppSettings,
    release_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    _operator(principal, "firmware.view")
    device = await session.get(Device, device_id)
    if device is None:
        raise ProblemError(404, "Sensor not found", "Sensor does not exist", "device_missing")
    _site_allowed(principal, device)
    capability = await session.get(DeviceCapability, device.id)
    release = await session.get(FirmwareRelease, release_id) if release_id else None
    if release_id and release is None:
        raise ProblemError(
            404,
            "Release not found",
            "Firmware release does not exist",
            "firmware_missing",
        )
    return firmware_readiness_payload(
        device,
        capability,
        release,
        bootstrap_offset=settings.firmware_bootstrap_offset,
    )


@router.post(
    "/firmware-deployments",
    status_code=201,
    response_model=FirmwareDeploymentCreateResponse,
)
async def create_deployments(
    payload: FirmwareDeploymentCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, Any]:
    _operator(principal, "firmware.deploy")
    release = await session.get(FirmwareRelease, payload.firmware_release_id)
    if release is None or release.verification_status != "verified":
        raise ProblemError(
            404, "Release not found", "Verified firmware release does not exist", "firmware_missing"
        )
    if release.trust_mode != OTA_TRUST_MODE:
        raise ProblemError(
            422,
            "Unsupported firmware trust",
            "Only device-authenticated HMAC releases can be deployed by this workflow",
            "firmware_trust",
        )
    if payload.allow_downgrade:
        require_recent_reauthentication(principal.session.reauthenticated_at)

    now = datetime.now(UTC)
    expires_at = payload.expires_at or max(payload.scheduled_at, now) + timedelta(
        seconds=settings.firmware_manifest_ttl_seconds
    )
    deployments: list[FirmwareDeployment] = []
    rollout_group_id = new_uuid() if len(payload.device_ids) > 1 else None
    for rollout_order, device_id in enumerate(payload.device_ids):
        device = await session.get(Device, device_id)
        capability = await session.get(DeviceCapability, device_id)
        if device is None or device.revoked_at or device.lifecycle_status != "active":
            raise ProblemError(
                422, "Invalid target", f"Sensor {device_id} is unavailable", "device_missing"
            )
        _site_allowed(principal, device)
        compatibility = release_compatibility(device, capability, release)
        blocking_reasons = set(compatibility["reasons"])
        blocking_reasons.discard("already_current")
        blocking_reasons.discard("downgrade_requires_confirmation")
        if blocking_reasons:
            code = (
                "firmware_bootstrap_required"
                if "bootstrap_required" in blocking_reasons
                else "firmware_incompatible"
            )
            raise ProblemError(
                422,
                "Incompatible target",
                f"Sensor {device_id} is not ready: {', '.join(sorted(blocking_reasons))}",
                code,
                extra={"compatibility": compatibility},
            )
        version_order = (
            compare_semver(release.version, device.firmware_version)
            if device.firmware_version
            else None
        )
        if version_order == 0:
            raise ProblemError(
                409,
                "Already current",
                f"Sensor {device_id} already runs firmware {release.version}",
                "firmware_already_current",
            )
        if version_order is not None and version_order < 0 and not payload.allow_downgrade:
            raise ProblemError(
                409,
                "Downgrade blocked",
                "Older firmware requires explicit downgrade confirmation",
                "firmware_downgrade_blocked",
            )
        if payload.idempotency_key:
            prior = await session.scalar(
                select(FirmwareDeployment).where(
                    FirmwareDeployment.device_id == device.id,
                    FirmwareDeployment.idempotency_key == payload.idempotency_key,
                )
            )
            if prior is not None:
                if prior.firmware_release_id != release.id:
                    raise ProblemError(
                        409,
                        "Idempotency conflict",
                        "Idempotency key was already used for another release",
                        "idempotency_conflict",
                    )
                deployments.append(prior)
                continue
        active = await session.scalar(
            select(FirmwareDeployment).where(
                FirmwareDeployment.device_id == device.id,
                FirmwareDeployment.state.in_(ACTIVE_DEPLOYMENT_STATES),
            )
        )
        if active is not None:
            if active.firmware_release_id == release.id:
                deployments.append(active)
                continue
            raise ProblemError(
                409,
                "Firmware deployment active",
                f"Sensor {device_id} already has an active firmware deployment",
                "firmware_deployment_active",
            )
        deployment = FirmwareDeployment(
            firmware_release_id=release.id,
            device_id=device.id,
            status="scheduled" if rollout_order == 0 else "waiting_canary",
            state="scheduled" if rollout_order == 0 else "waiting_canary",
            idempotency_key=payload.idempotency_key,
            rollout_group_id=rollout_group_id,
            rollout_order=rollout_order,
            revision=1,
            attempt=1,
            progress=0,
            bytes_received=0,
            scheduled_at=payload.scheduled_at,
            expires_at=expires_at,
            allow_downgrade=payload.allow_downgrade,
            created_by=principal.user.id,
            created_at=now,
        )
        session.add(deployment)
        await session.flush()
        deployments.append(deployment)
    release.active = True
    session.add(
        audit_event(
            action="firmware.deployment_created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="firmware_release",
            object_id=release.id,
            details={
                "devices": payload.device_ids,
                "scheduled_at": payload.scheduled_at.isoformat(),
                "allow_downgrade": payload.allow_downgrade,
                "trust_mode": release.trust_mode,
                "rollout_group_id": rollout_group_id,
                "maximum_concurrency": payload.maximum_concurrency,
            },
        )
    )
    await session.commit()
    return {
        "deployment_ids": [item.id for item in deployments],
        "scheduled": len(deployments),
        "deployments": [_deployment_payload(item, release) for item in deployments],
    }


@router.get("/firmware-deployments", response_model=list[FirmwareDeploymentView])
async def list_deployments(
    principal: Viewer,
    session: DbSession,
    device_id: Annotated[str | None, Query()] = None,
) -> list[dict[str, Any]]:
    _operator(principal, "firmware.view")
    query = select(FirmwareDeployment)
    if device_id is not None:
        query = query.where(FirmwareDeployment.device_id == device_id)
    deployments = list(
        await session.scalars(query.order_by(FirmwareDeployment.scheduled_at.desc()).limit(1000))
    )
    releases = {
        release.id: release
        for release in await session.scalars(
            select(FirmwareRelease).where(
                FirmwareRelease.id.in_({item.firmware_release_id for item in deployments})
            )
        )
    }
    devices = {
        device.id: device
        for device in await session.scalars(
            select(Device).where(Device.id.in_({item.device_id for item in deployments}))
        )
    }
    return [
        _deployment_payload(item, releases.get(item.firmware_release_id))
        for item in deployments
        if (device := devices.get(item.device_id)) is not None
        and principal.can_access_site(device.site_id)
    ]


@router.post(
    "/firmware-deployments/{deployment_id}/cancel",
    response_model=FirmwareDeploymentView,
)
async def cancel_deployment(
    deployment_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _operator(principal, "firmware.deploy")
    deployment = await session.get(FirmwareDeployment, deployment_id)
    if deployment is None:
        raise ProblemError(
            404,
            "Deployment not found",
            "Deployment does not exist",
            "firmware_missing",
        )
    device = await session.get(Device, deployment.device_id)
    if device is None:
        raise ProblemError(404, "Sensor not found", "Sensor does not exist", "device_missing")
    _site_allowed(principal, device)
    if deployment.state in TERMINAL_DEPLOYMENT_STATES:
        return _deployment_payload(deployment)
    if "cancelled" not in DEPLOYMENT_TRANSITIONS.get(deployment.state, frozenset()):
        raise ProblemError(
            409,
            "Cancellation unsafe",
            "Deployment can no longer be safely cancelled",
            "firmware_transition_invalid",
        )
    deployment.state = deployment.status = "cancelled"
    deployment.revision += 1
    session.add(
        audit_event(
            action="firmware.deployment_cancelled",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="firmware_deployment",
            object_id=deployment.id,
        )
    )
    await session.commit()
    return _deployment_payload(deployment)


@router.post(
    "/firmware-deployments/{deployment_id}/retry",
    response_model=FirmwareDeploymentView,
)
async def retry_deployment(
    deployment_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, Any]:
    _operator(principal, "firmware.deploy")
    deployment = await session.get(FirmwareDeployment, deployment_id)
    if deployment is None:
        raise ProblemError(
            404,
            "Deployment not found",
            "Deployment does not exist",
            "firmware_missing",
        )
    device = await session.get(Device, deployment.device_id)
    if device is None:
        raise ProblemError(404, "Sensor not found", "Sensor does not exist", "device_missing")
    _site_allowed(principal, device)
    if deployment.state not in {"failed", "cancelled", "rolled_back"}:
        raise ProblemError(
            409,
            "Retry unavailable",
            "Only a terminal unsuccessful deployment can be retried",
            "firmware_transition_invalid",
        )
    other_active = await session.scalar(
        select(FirmwareDeployment.id).where(
            FirmwareDeployment.device_id == deployment.device_id,
            FirmwareDeployment.id != deployment.id,
            FirmwareDeployment.state.in_(ACTIVE_DEPLOYMENT_STATES),
        )
    )
    if other_active is not None:
        raise ProblemError(
            409,
            "Retry unavailable",
            "Another firmware deployment is active for this sensor",
            "firmware_deployment_active",
        )
    if deployment.rollout_group_id is not None:
        active_sibling = await session.scalar(
            select(FirmwareDeployment.id).where(
                FirmwareDeployment.rollout_group_id == deployment.rollout_group_id,
                FirmwareDeployment.id != deployment.id,
                FirmwareDeployment.state.in_(
                    [state for state in ACTIVE_DEPLOYMENT_STATES if state != "waiting_canary"]
                ),
            )
        )
        if active_sibling is not None:
            raise ProblemError(
                409,
                "Retry unavailable",
                "Another sensor in this rollout is already active",
                "firmware_rollout_busy",
            )
    now = datetime.now(UTC)
    deployment.state = deployment.status = "scheduled"
    deployment.attempt += 1
    deployment.revision += 1
    deployment.progress = 0
    deployment.bytes_received = 0
    deployment.scheduled_at = now
    deployment.expires_at = now + timedelta(seconds=settings.firmware_manifest_ttl_seconds)
    deployment.failure_reason = None
    deployment.failure_code = None
    deployment.failure_summary = None
    deployment.last_report_at = None
    deployment.last_report_payload = {}
    deployment.validated_at = None
    deployment.validated_version = None
    deployment.validated_build_hash = None
    deployment.rollback_at = None
    deployment.rollback_version = None
    deployment.rollback_build_hash = None
    deployment.verification_heartbeats = 0
    deployment.reading_confirmed_at = None
    session.add(
        audit_event(
            action="firmware.deployment_retried",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="firmware_deployment",
            object_id=deployment.id,
            details={"attempt": deployment.attempt},
        )
    )
    await session.commit()
    return _deployment_payload(deployment)


@router.post("/device-firmware/report", response_model=FirmwareDeploymentReportResponse)
async def report_deployment(
    payload: FirmwareDeploymentReport,
    verified: Verified,
    session: DbSession,
) -> dict[str, Any]:
    if payload.device_id != verified.device.id:
        raise ProblemError(
            403,
            "Device identity mismatch",
            "Signed header and report device IDs differ",
            "device_id_mismatch",
        )
    deployment = await session.scalar(
        select(FirmwareDeployment)
        .where(FirmwareDeployment.id == payload.deployment_id)
        .with_for_update()
    )
    if deployment is None or deployment.device_id != verified.device.id:
        raise ProblemError(
            404,
            "Deployment unavailable",
            "Deployment does not belong to this sensor",
            "firmware_unavailable",
        )
    release = await session.get(FirmwareRelease, deployment.firmware_release_id)
    if release is None or release.id != payload.release_id:
        raise ProblemError(
            409,
            "Release mismatch",
            "Report release does not match deployment",
            "firmware_report_mismatch",
        )
    if payload.attempt != deployment.attempt:
        code = (
            "firmware_report_stale"
            if payload.attempt < deployment.attempt
            else "firmware_report_mismatch"
        )
        raise ProblemError(
            409,
            "Attempt mismatch",
            "Report attempt does not match the active deployment attempt",
            code,
        )
    if (
        payload.target_version != release.version
        or payload.target_sha256 != release.sha256
        or payload.image_size != release.size_bytes
    ):
        raise ProblemError(
            409,
            "Target mismatch",
            "Report firmware target does not match the authenticated deployment",
            "firmware_report_mismatch",
        )
    report_data = payload.model_dump(mode="json")
    if report_data == deployment.last_report_payload:
        # Device authentication persists replay protection in this transaction even
        # when the milestone itself is an idempotent duplicate.
        await session.commit()
        return {
            "recorded": True,
            "duplicate": True,
            "state": deployment.state,
            "revision": deployment.revision,
            "attempt": deployment.attempt,
        }
    if payload.state == deployment.state:
        if payload.state != "downloading":
            raise ProblemError(
                409,
                "Stale report",
                "Repeated state report differs from the recorded milestone",
                "firmware_report_stale",
            )
        if (
            payload.progress < deployment.progress
            or payload.bytes_received < deployment.bytes_received
        ):
            raise ProblemError(
                409,
                "Stale report",
                "Download progress cannot move backwards",
                "firmware_report_stale",
            )
    elif payload.state not in DEPLOYMENT_TRANSITIONS.get(deployment.state, frozenset()):
        raise ProblemError(
            409,
            "Invalid firmware transition",
            f"Deployment cannot move from {deployment.state} to {payload.state}",
            "firmware_transition_invalid",
        )

    now = datetime.now(UTC)
    deployment.state = deployment.status = payload.state
    deployment.revision += 1
    deployment.progress = max(deployment.progress, payload.progress)
    deployment.bytes_received = max(deployment.bytes_received, payload.bytes_received)
    deployment.last_report_at = now
    deployment.last_report_payload = report_data
    deployment.last_boot_id = payload.boot_id
    if payload.state == "download_started":
        deployment.downloaded_at = now
    if payload.state == "partition_written":
        deployment.installed_at = now
    if payload.state == "validated":
        if (
            payload.current_firmware_version != release.version
            or payload.current_build_hash != release.build_hash
        ):
            raise ProblemError(
                409,
                "Validation mismatch",
                "Validated firmware identity does not match the release",
                "firmware_report_mismatch",
            )
        deployment.validated_at = now
        deployment.validated_version = payload.current_firmware_version
        deployment.validated_build_hash = payload.current_build_hash
        deployment.stabilization_started_at = now
    if payload.state == "failed":
        deployment.failure_code = payload.failure_code
        deployment.failure_summary = payload.failure_summary
        deployment.failure_reason = payload.failure_summary
    if payload.state in {"rollback_detected", "rolled_back"}:
        deployment.rollback_at = now
        deployment.rollback_version = payload.current_firmware_version
        deployment.rollback_build_hash = payload.current_build_hash
        deployment.failure_code = payload.failure_code or deployment.failure_code
        deployment.failure_summary = payload.failure_summary or deployment.failure_summary
    await session.commit()
    return {
        "recorded": True,
        "duplicate": False,
        "state": deployment.state,
        "revision": deployment.revision,
        "attempt": deployment.attempt,
    }


@router.post(
    "/firmware-deployments/{deployment_id}/promote",
    response_model=FirmwareCanaryPromotionResponse,
)
async def promote_canary(
    deployment_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, Any]:
    _operator(principal, "firmware.deploy")
    canary = await session.get(FirmwareDeployment, deployment_id)
    if canary is None:
        raise ProblemError(
            404,
            "Deployment not found",
            "Deployment does not exist",
            "firmware_missing",
        )
    device = await session.get(Device, canary.device_id)
    if device is None:
        raise ProblemError(404, "Sensor not found", "Sensor does not exist", "device_missing")
    _site_allowed(principal, device)
    if canary.rollout_group_id is None:
        raise ProblemError(
            409,
            "Canary unavailable",
            "Deployment is not part of a multi-sensor rollout",
            "firmware_canary_unavailable",
        )
    critical_alert = await session.scalar(
        select(AlertInstance.id).where(
            AlertInstance.device_id == canary.device_id,
            AlertInstance.status.in_(["active", "acknowledged"]),
            AlertInstance.severity == "critical",
        )
    )
    if (
        canary.state != "completed"
        or canary.verification_heartbeats < settings.firmware_verification_heartbeat_count
        or canary.reading_confirmed_at is None
        or canary.rollback_at is not None
        or critical_alert is not None
    ):
        raise ProblemError(
            409,
            "Canary not verified",
            "Canary promotion requires completed stabilization, readings, and no rollback or alert",
            "firmware_canary_not_verified",
        )
    operational_sibling = await session.scalar(
        select(FirmwareDeployment.id).where(
            FirmwareDeployment.rollout_group_id == canary.rollout_group_id,
            FirmwareDeployment.state.in_(
                [state for state in ACTIVE_DEPLOYMENT_STATES if state != "waiting_canary"]
            ),
        )
    )
    if operational_sibling is not None:
        raise ProblemError(
            409,
            "Rollout busy",
            "Another sensor in this rollout is already active",
            "firmware_rollout_busy",
        )
    next_deployment = await session.scalar(
        select(FirmwareDeployment)
        .where(
            FirmwareDeployment.rollout_group_id == canary.rollout_group_id,
            FirmwareDeployment.state == "waiting_canary",
        )
        .order_by(FirmwareDeployment.rollout_order)
        .limit(1)
        .with_for_update()
    )
    if next_deployment is None:
        return {"promoted": False, "rollout_complete": True}
    next_device = await session.get(Device, next_deployment.device_id)
    if next_device is None or not principal.can_access_site(next_device.site_id):
        raise ProblemError(
            403,
            "Permission denied",
            "Next rollout sensor is outside your assigned sites",
            "forbidden",
        )
    now = datetime.now(UTC)
    next_deployment.state = next_deployment.status = "scheduled"
    next_deployment.scheduled_at = now
    next_deployment.expires_at = now + timedelta(seconds=settings.firmware_manifest_ttl_seconds)
    next_deployment.promoted_at = now
    next_deployment.revision += 1
    session.add(
        audit_event(
            action="firmware.canary_promoted",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="firmware_deployment",
            object_id=next_deployment.id,
            details={
                "rollout_group_id": canary.rollout_group_id,
                "verified_canary_id": canary.id,
                "rollout_order": next_deployment.rollout_order,
                "maximum_concurrency": 1,
            },
        )
    )
    await session.commit()
    release = await session.get(FirmwareRelease, next_deployment.firmware_release_id)
    return {
        "promoted": True,
        "rollout_complete": False,
        "deployment": _deployment_payload(next_deployment, release),
    }


@router.get("/device-firmware/{release_id}/download", response_class=FileResponse)
async def device_download_firmware(
    release_id: str,
    verified: Verified,
    session: DbSession,
    settings: AppSettings,
    deployment_id: Annotated[str, Query(min_length=1)],
) -> FileResponse:
    deployment = await session.get(FirmwareDeployment, deployment_id)
    release = await session.get(FirmwareRelease, release_id)
    now = datetime.now(UTC)
    eligible_states = {
        "offered",
        "manifest_authenticated",
        "download_started",
        "downloading",
        "binary_verified",
    }
    if (
        deployment is None
        or release is None
        or deployment.device_id != verified.device.id
        or deployment.firmware_release_id != release.id
        or deployment.state not in eligible_states
        or release.trust_mode != OTA_TRUST_MODE
        or release.verification_status != "verified"
        or _aware(deployment.scheduled_at) > now
        or (deployment.expires_at is not None and _aware(deployment.expires_at) <= now)
    ):
        raise ProblemError(
            404, "Firmware unavailable", "No authorized deployment exists", "firmware_unavailable"
        )
    capability = await session.get(DeviceCapability, verified.device.id)
    compatibility = release_compatibility(verified.device, capability, release)
    reasons = set(compatibility["reasons"])
    reasons.discard("already_current")
    if deployment.allow_downgrade:
        reasons.discard("downgrade_requires_confirmation")
    if reasons:
        raise ProblemError(
            409,
            "Firmware incompatible",
            "Sensor is no longer compatible with this deployment",
            "firmware_incompatible",
        )
    path = await _verified_artifact(release, session, settings)
    await session.commit()
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=f"power-monitor-sensor-{release.version}.bin",
        headers={
            "Cache-Control": "no-store",
            "Content-Length": str(release.size_bytes),
            "Digest": f"sha-256={base64.b64encode(bytes.fromhex(release.sha256)).decode()}",
            "X-Content-Type-Options": "nosniff",
        },
    )
