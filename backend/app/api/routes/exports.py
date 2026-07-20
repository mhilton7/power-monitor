from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import AppSettings, CsrfPrincipal, DbSession, Principal, audit_event
from app.db.models import ExportJob
from app.problem import ProblemError

router = APIRouter(prefix="/api/v1", tags=["exports and reports"])


@router.post("/exports", status_code=202)
async def create_export(
    payload: dict[str, Any],
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    if (
        "history.export" not in principal.permissions
        and "costs.export" not in principal.permissions
    ):
        raise ProblemError(403, "Permission denied", "Export permission is required", "forbidden")
    requested_site = payload.get("site_id")
    if requested_site and not principal.can_access_site(str(requested_site)):
        raise ProblemError(404, "Resource not found", "Resource does not exist", "resource_missing")
    if payload.get("format") not in {"csv", "json"}:
        raise ProblemError(422, "Invalid export", "Format must be csv or json", "invalid_export")
    now = datetime.now(UTC)
    job = ExportJob(
        requested_by=principal.user.id,
        format=payload["format"],
        query={key: value for key, value in payload.items() if key != "format"},
        status="queued",
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    session.add(job)
    session.add(
        audit_event(
            action="export.queued",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="export_job",
            object_id=job.id,
            details={"format": job.format},
        )
    )
    await session.commit()
    return {"id": job.id, "status": job.status, "expires_at": job.expires_at}


@router.get("/exports")
async def list_exports(principal: Principal, session: DbSession) -> list[dict[str, Any]]:
    query = select(ExportJob).order_by(ExportJob.created_at.desc()).limit(100)
    if "admin" not in principal.roles:
        query = query.where(ExportJob.requested_by == principal.user.id)
    jobs = list(await session.scalars(query))
    return [
        {
            "id": job.id,
            "format": job.format,
            "status": job.status,
            "created_at": job.created_at,
            "expires_at": job.expires_at,
            "content_hash": job.content_hash,
        }
        for job in jobs
    ]


@router.get("/exports/{job_id}/download", response_class=FileResponse)
async def download_export(
    job_id: str,
    principal: Principal,
    session: DbSession,
    settings: AppSettings,
) -> FileResponse:
    job = await session.get(ExportJob, job_id)
    if job is None or (job.requested_by != principal.user.id and "admin" not in principal.roles):
        raise ProblemError(404, "Export not found", "Export does not exist", "export_missing")
    if job.status != "completed" or not job.file_path:
        raise ProblemError(
            409, "Export not ready", "The background job has not completed", "export_pending"
        )
    if job.expires_at <= datetime.now(UTC):
        raise ProblemError(410, "Export expired", "Create a new export", "export_expired")
    root = settings.report_path.resolve()
    path = Path(job.file_path).resolve()
    if root not in path.parents or not path.is_file():
        raise ProblemError(
            404, "Export file missing", "Stored export is unavailable", "export_file_missing"
        )
    return FileResponse(
        path,
        media_type="text/csv" if job.format == "csv" else "application/json",
        filename=f"power-monitor-export-{job.id}.{job.format}",
    )
