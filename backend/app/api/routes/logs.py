from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.api.deps import AppSettings, CsrfPrincipal, DbSession, Principal, audit_event
from app.db.models import LogExportJob
from app.logging import LOG_CATEGORIES, retention_boundary
from app.problem import ProblemError
from app.schemas import LogExportCreate
from app.services.log_exports import (
    LogExportTooLargeError,
    NoLogsAvailableError,
    build_log_export,
    log_availability,
)

router = APIRouter(prefix="/api/v1/admin/logs", tags=["administration logs"])


def _require_admin(principal: Principal) -> None:
    if "logs.export" not in principal.permissions:
        raise ProblemError(
            403, "Permission denied", "Log export permission is required", "forbidden"
        )


def _job_view(job: LogExportJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "requested_at": job.requested_at,
        "start_date": job.start_date,
        "end_date": job.end_date,
        "services": job.services,
        "size_bytes": job.size_bytes,
        "error_code": job.error_code,
        "completed_at": job.completed_at,
        "downloaded_at": job.downloaded_at,
        "expires_at": job.expires_at,
        "correlation_id": job.correlation_id,
        "download_url": (
            f"/api/v1/admin/logs/exports/{job.id}/download" if job.status == "ready" else None
        ),
    }


async def _reject_export(
    *,
    session: DbSession,
    request: Request,
    principal: Principal,
    status: int,
    detail: str,
    code: str,
    start_date: str | None,
    end_date: str | None,
    services: list[str] | None,
) -> NoReturn:
    session.add(
        audit_event(
            action="logs.export_failed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="log_export",
            outcome="failure",
            details={
                "start_date": start_date,
                "end_date": end_date,
                "services": services or [],
                "error_code": code,
                "correlation_id": request.state.request_id,
            },
        )
    )
    await session.commit()
    raise ProblemError(status, "Log export failed", detail, code)


@router.get("/availability")
async def availability(principal: Principal, settings: AppSettings) -> dict[str, object]:
    _require_admin(principal)
    return await run_in_threadpool(
        log_availability,
        settings.log_path,
        now=datetime.now(UTC),
        retention_days=settings.log_retention_days,
    )


@router.post("/exports", status_code=201)
async def create_export(
    payload: LogExportCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, Any]:
    _require_admin(principal)
    today = datetime.now(UTC).date()
    start_date = payload.start_date or today - timedelta(days=6)
    end_date = payload.end_date or today
    services = list(dict.fromkeys(payload.services or list(LOG_CATEGORIES)))
    invalid_services = [service for service in services if service not in LOG_CATEGORIES]
    if invalid_services:
        await _reject_export(
            session=session,
            request=request,
            principal=principal,
            status=422,
            detail="Select only supported application-log services",
            code="invalid_log_service",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            services=services,
        )
    if end_date < start_date:
        await _reject_export(
            session=session,
            request=request,
            principal=principal,
            status=422,
            detail="The end date must be on or after the start date",
            code="reversed_log_range",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            services=services,
        )
    boundary = retention_boundary(today, settings.log_retention_days)
    if start_date < boundary:
        await _reject_export(
            session=session,
            request=request,
            principal=principal,
            status=422,
            detail=f"Logs are retained from {boundary.isoformat()}; choose a newer start date",
            code="log_range_before_retention",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            services=services,
        )
    if end_date > today:
        await _reject_export(
            session=session,
            request=request,
            principal=principal,
            status=422,
            detail="The end date cannot be in the future",
            code="future_log_range",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            services=services,
        )

    now = datetime.now(UTC)
    job = LogExportJob(
        requested_by=principal.user.id,
        requested_at=now,
        start_date=start_date,
        end_date=end_date,
        services=services,
        status="preparing",
        expires_at=now + timedelta(minutes=15),
        correlation_id=request.state.request_id,
    )
    session.add(job)
    await session.commit()
    try:
        built = await run_in_threadpool(
            build_log_export,
            log_path=settings.log_path,
            job_id=job.id,
            start_date=start_date,
            end_date=end_date,
            services=services,
            requesting_user_id=principal.user.id,
            application_version=settings.power_monitor_version,
            max_export_bytes=settings.max_log_export_bytes,
            created_at=now,
        )
    except NoLogsAvailableError:
        job.status = "empty"
        job.error_code = "logs_not_available"
        job.completed_at = datetime.now(UTC)
        session.add(
            audit_event(
                action="logs.export_failed",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="log_export",
                object_id=job.id,
                outcome="failure",
                details={
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "services": services,
                    "error_code": job.error_code,
                    "correlation_id": job.correlation_id,
                },
            )
        )
        await session.commit()
        raise ProblemError(
            404,
            "No logs available",
            "No application logs exist for the selected range and services",
            "logs_not_available",
        ) from None
    except LogExportTooLargeError as exc:
        job.status = "failed"
        job.error_code = "log_export_too_large"
        job.completed_at = datetime.now(UTC)
        session.add(
            audit_event(
                action="logs.export_failed",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="log_export",
                object_id=job.id,
                outcome="failure",
                details={
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "services": services,
                    "error_code": job.error_code,
                    "correlation_id": job.correlation_id,
                },
            )
        )
        await session.commit()
        raise ProblemError(413, "Log export too large", str(exc), job.error_code) from None
    except OSError as exc:
        job.status = "failed"
        job.error_code = "log_export_io_error"
        job.completed_at = datetime.now(UTC)
        session.add(
            audit_event(
                action="logs.export_failed",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="log_export",
                object_id=job.id,
                outcome="failure",
                details={
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "services": services,
                    "error_code": job.error_code,
                    "error_type": type(exc).__name__,
                    "correlation_id": job.correlation_id,
                },
            )
        )
        await session.commit()
        raise ProblemError(
            500,
            "Log export failed",
            "The server could not prepare the archive; verify the logs dataset permissions",
            job.error_code,
        ) from None

    job.status = "ready"
    job.file_path = str(built.path)
    job.size_bytes = built.size_bytes
    job.completed_at = datetime.now(UTC)
    session.add(
        audit_event(
            action="logs.export_completed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="log_export",
            object_id=job.id,
            details={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "services": services,
                "export_size_bytes": built.size_bytes,
                "file_count": len(built.files),
                "correlation_id": job.correlation_id,
            },
        )
    )
    await session.commit()
    return _job_view(job)


@router.get("/exports/{job_id}")
async def export_status(job_id: str, principal: Principal, session: DbSession) -> dict[str, Any]:
    _require_admin(principal)
    job = await session.get(LogExportJob, job_id)
    if job is None:
        raise ProblemError(
            404, "Export not found", "Log export does not exist", "log_export_missing"
        )
    return _job_view(job)


def _remove_export(path: Path) -> None:
    with suppress(FileNotFoundError):
        path.unlink()


@router.get("/exports/{job_id}/download", response_class=FileResponse)
async def download_export(
    job_id: str,
    request: Request,
    principal: Principal,
    session: DbSession,
    settings: AppSettings,
) -> FileResponse:
    _require_admin(principal)
    job = await session.get(LogExportJob, job_id)
    if job is None:
        raise ProblemError(
            404, "Export not found", "Log export does not exist", "log_export_missing"
        )
    if job.status != "ready" or not job.file_path:
        raise ProblemError(
            409, "Export is not ready", "Prepare a new log export", "log_export_not_ready"
        )
    path = Path(job.file_path).resolve()
    export_root = (settings.log_path / ".exports").resolve()
    if not path.is_relative_to(export_root) or not path.is_file():
        job.status = "expired"
        job.file_path = None
        await session.commit()
        raise ProblemError(
            410, "Export expired", "Prepare the log export again", "log_export_expired"
        )
    if job.expires_at.replace(tzinfo=job.expires_at.tzinfo or UTC) <= datetime.now(UTC):
        _remove_export(path)
        job.status = "expired"
        job.file_path = None
        await session.commit()
        raise ProblemError(
            410, "Export expired", "Prepare the log export again", "log_export_expired"
        )
    job.downloaded_at = datetime.now(UTC)
    job.status = "downloaded"
    session.add(
        audit_event(
            action="logs.export_downloaded",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="log_export",
            object_id=job.id,
            details={
                "export_size_bytes": job.size_bytes,
                "correlation_id": request.state.request_id,
            },
        )
    )
    await session.commit()
    filename = f"power-monitor-logs_{job.start_date.isoformat()}_to_{job.end_date.isoformat()}.zip"
    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(_remove_export, path),
    )
