from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import (
    AppSettings,
    CsrfPrincipal,
    DbSession,
    Principal,
    Verified,
    Viewer,
    audit_event,
)
from app.db.models import Device, DeviceCapability, FirmwareDeployment, FirmwareRelease
from app.problem import ProblemError
from app.schemas import FirmwareDeploymentCreate, FirmwareManifest

router = APIRouter(prefix="/api/v1", tags=["firmware"])
MAX_FIRMWARE_BYTES = 32 * 1024 * 1024


def _operator(principal: Principal, permission: str = "firmware.manage") -> None:
    if permission not in principal.permissions:
        raise ProblemError(403, "Permission denied", "Firmware permission is required", "forbidden")


@router.get("/firmware-releases")
async def list_releases(principal: Viewer, session: DbSession) -> list[dict[str, Any]]:
    _operator(principal, "firmware.view")
    releases = list(
        await session.scalars(select(FirmwareRelease).order_by(FirmwareRelease.created_at.desc()))
    )
    return [
        {
            "id": release.id,
            "version": release.version,
            "channel": release.channel,
            "hardware_target": release.hardware_target,
            "protocol_min": release.protocol_min,
            "protocol_max": release.protocol_max,
            "size_bytes": release.size_bytes,
            "sha256": release.sha256,
            "signing_key_id": release.signing_key_id,
            "release_notes": release.release_notes,
            "verified_at": release.verified_at,
            "active": release.active,
        }
        for release in releases
    ]


@router.post("/firmware-releases", status_code=201)
async def upload_release(
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
    manifest_json: Annotated[str, Form()],
    public_key_pem: Annotated[str, Form()],
    binary: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    _operator(principal)
    try:
        manifest = FirmwareManifest.model_validate_json(manifest_json)
    except ValueError as exc:
        raise ProblemError(422, "Invalid manifest", str(exc), "invalid_firmware_manifest") from exc
    content = await binary.read(MAX_FIRMWARE_BYTES + 1)
    if not content or len(content) > MAX_FIRMWARE_BYTES:
        raise ProblemError(
            413, "Firmware too large", "Firmware must be 1 byte to 32 MiB", "firmware_size"
        )
    digest = hashlib.sha256(content).hexdigest()
    if digest != manifest.sha256:
        raise ProblemError(
            422, "Hash mismatch", "Binary SHA-256 does not match manifest", "firmware_hash_mismatch"
        )
    signed_manifest = manifest.model_dump(exclude={"signature"}, mode="json")
    signed_bytes = json.dumps(signed_manifest, sort_keys=True, separators=(",", ":")).encode()
    try:
        key = load_pem_public_key(public_key_pem.encode())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("Only Ed25519 firmware signing keys are accepted")
        signature = base64.b64decode(manifest.signature, validate=True)
        key.verify(signature, signed_bytes)
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise ProblemError(
            422,
            "Invalid signature",
            "Firmware manifest signature failed",
            "firmware_signature_invalid",
        ) from exc
    root = settings.firmware_path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    release = FirmwareRelease(
        version=manifest.version,
        channel=manifest.channel,
        hardware_target=manifest.hardware_target,
        protocol_min=manifest.protocol_min,
        protocol_max=manifest.protocol_max,
        file_path="pending",
        size_bytes=len(content),
        sha256=digest,
        signature=manifest.signature,
        signing_key_id=manifest.signing_key_id,
        release_notes=manifest.release_notes,
        verified_at=datetime.now(UTC),
        active=False,
    )
    session.add(release)
    await session.flush()
    target = (root / f"{release.id}.bin").resolve()
    if root not in target.parents:
        raise ProblemError(500, "Unsafe path", "Firmware path validation failed", "unsafe_path")
    target.write_bytes(content)
    release.file_path = str(target)
    session.add(
        audit_event(
            action="firmware.uploaded_verified",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="firmware_release",
            object_id=release.id,
            details={"sha256": digest, "signing_key_id": manifest.signing_key_id},
        )
    )
    await session.commit()
    return {"id": release.id, "sha256": digest, "verified": True, "active": False}


@router.post("/firmware-deployments", status_code=201)
async def create_deployments(
    payload: FirmwareDeploymentCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _operator(principal)
    release = await session.get(FirmwareRelease, payload.firmware_release_id)
    if release is None:
        raise ProblemError(
            404, "Release not found", "Firmware release does not exist", "firmware_missing"
        )
    deployments: list[str] = []
    for device_id in payload.device_ids:
        device = await session.get(Device, device_id)
        capability = await session.get(DeviceCapability, device_id)
        if device is None or device.revoked_at:
            raise ProblemError(
                422, "Invalid target", f"Device {device_id} is unavailable", "device_missing"
            )
        if capability is None or capability.hardware_target != release.hardware_target:
            raise ProblemError(
                422,
                "Incompatible target",
                f"Device {device_id} hardware differs",
                "firmware_incompatible",
            )
        if not (release.protocol_min <= device.protocol_version <= release.protocol_max):
            raise ProblemError(
                422,
                "Incompatible protocol",
                f"Device {device_id} protocol differs",
                "firmware_incompatible",
            )
        deployment = FirmwareDeployment(
            firmware_release_id=release.id,
            device_id=device.id,
            status="scheduled",
            scheduled_at=payload.scheduled_at,
            created_by=principal.user.id,
        )
        session.add(deployment)
        deployments.append(deployment.id)
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
            },
        )
    )
    await session.commit()
    return {"deployment_ids": deployments, "scheduled": len(deployments)}


@router.get("/firmware-deployments")
async def list_deployments(_viewer: Viewer, session: DbSession) -> list[dict[str, Any]]:
    deployments = list(
        await session.scalars(
            select(FirmwareDeployment).order_by(FirmwareDeployment.scheduled_at.desc()).limit(1000)
        )
    )
    return [
        {
            "id": item.id,
            "firmware_release_id": item.firmware_release_id,
            "device_id": item.device_id,
            "status": item.status,
            "scheduled_at": item.scheduled_at,
            "downloaded_at": item.downloaded_at,
            "installed_at": item.installed_at,
            "validated_at": item.validated_at,
            "failure_reason": item.failure_reason,
            "rollback_at": item.rollback_at,
        }
        for item in deployments
    ]


@router.get("/device-firmware/{release_id}/download", response_class=FileResponse)
async def device_download_firmware(
    release_id: str, verified: Verified, session: DbSession
) -> FileResponse:
    deployment = await session.scalar(
        select(FirmwareDeployment).where(
            FirmwareDeployment.device_id == verified.device.id,
            FirmwareDeployment.firmware_release_id == release_id,
            FirmwareDeployment.status.in_(["scheduled", "available", "downloaded"]),
        )
    )
    release = await session.get(FirmwareRelease, release_id)
    if deployment is None or release is None:
        raise ProblemError(
            404, "Firmware unavailable", "No authorized deployment exists", "firmware_unavailable"
        )
    path = Path(release.file_path)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != release.sha256:
        raise ProblemError(
            503,
            "Firmware integrity failure",
            "Stored package failed verification",
            "firmware_integrity",
        )
    deployment.status = "downloaded"
    deployment.downloaded_at = datetime.now(UTC)
    await session.commit()
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=f"power-monitor-sensor-{release.version}.bin",
        headers={"Digest": f"sha-256={base64.b64encode(bytes.fromhex(release.sha256)).decode()}"},
    )
