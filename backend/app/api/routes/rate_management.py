from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import CsrfPrincipal, DbSession, Principal, Viewer, audit_event
from app.config import get_settings
from app.db.models import (
    BackgroundJob,
    RateApprovalDecision,
    RateAssignment,
    RateCandidateDifference,
    RateChangeCandidate,
    RateExtractionResult,
    RatePlan,
    RateSource,
    RateSourceArtifact,
    RateSourceCheckRun,
    RateSyncConfiguration,
    RateVersion,
    RateVersionSource,
    UtilityAccount,
)
from app.problem import ProblemError
from app.rates.candidates import create_candidate_from_document
from app.rates.documents import RatePlanDocument, engine_plan, validate_document
from app.rates.engine import RateEngine
from app.rates.notifications import emit_rate_alert
from app.rates.schedule import next_scheduled_time, schedule_parts
from app.rates.service import (
    activate_version,
    clone_plan_version,
    create_custom_plan,
    update_draft_version,
    validate_version,
    version_document,
    version_usage_count,
)
from app.rates.sources import (
    ADAPTERS,
    PARSER_VERSION,
    REMOTE_SOURCE_PARSER_IDS,
    SourceSecurityError,
    validate_source_url,
)

router = APIRouter(prefix="/api/v1", tags=["rate management"])


def _rate_manager(principal: Principal, permission: str = "rates.manage_custom") -> None:
    if permission not in principal.permissions:
        raise ProblemError(
            403,
            "Permission denied",
            "Your account does not have the required rate permission",
            "forbidden",
            extra={"required_permission": permission},
        )


def _version_summary(version: RateVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "version": version.version,
        "effective_from": version.effective_from,
        "effective_through": version.effective_to,
        "status": version.status,
        "source_kind": version.source_kind,
        "source_checked_at": version.source_checked_at,
        "source_label": version.source_label,
        "integrity_sha256": version.content_hash,
        "is_active": version.is_active,
        "immutable": version.immutable_after_use,
        "created_at": version.created_at,
        "approved_at": version.approved_at,
        "activated_at": version.activated_at,
    }


async def _plan_payload(session: DbSession, plan: RatePlan) -> dict[str, Any]:
    versions = list(
        await session.scalars(
            select(RateVersion)
            .where(RateVersion.rate_plan_id == plan.id)
            .order_by(RateVersion.version.desc())
        )
    )
    return {
        "id": plan.id,
        "code": plan.code,
        "name": plan.name,
        "description": plan.description,
        "plan_kind": plan.plan_kind,
        "ownership_scope": plan.ownership_scope,
        "owner_site_id": plan.owner_site_id,
        "owner_utility_account_id": plan.owner_utility_account_id,
        "currency": plan.currency,
        "timezone": plan.timezone,
        "status": plan.status,
        "cloned_from_rate_version_id": plan.cloned_from_rate_version_id,
        "versions": [_version_summary(version) for version in versions],
    }


@router.get("/rates/plans")
async def list_managed_rate_plans(principal: Viewer, session: DbSession) -> list[dict[str, Any]]:
    _rate_manager(principal, "rates.view")
    plans = list(await session.scalars(select(RatePlan).order_by(RatePlan.code)))
    return [await _plan_payload(session, plan) for plan in plans]


@router.post("/rates/plans", status_code=201)
async def create_rate_plan(
    payload: RatePlanDocument,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _rate_manager(principal)
    plan, version = await create_custom_plan(session, payload, principal.user.id)
    session.add(
        audit_event(
            action="rate_plan.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_plan",
            object_id=plan.id,
            details={"version_id": version.id, "integrity_sha256": version.content_hash},
        )
    )
    await session.commit()
    return {"plan": await _plan_payload(session, plan), "document": payload}


@router.get("/rates/plans/{plan_id}")
async def get_managed_rate_plan(
    plan_id: str, _viewer: Viewer, session: DbSession
) -> dict[str, Any]:
    plan = await session.get(RatePlan, plan_id)
    if plan is None:
        raise ProblemError(
            404, "Rate plan not found", "Rate plan does not exist", "rate_plan_missing"
        )
    return await _plan_payload(session, plan)


@router.patch("/rates/plans/{plan_id}")
async def update_rate_plan(
    plan_id: str,
    payload: RatePlanDocument,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _rate_manager(principal)
    version = await session.scalar(
        select(RateVersion)
        .where(RateVersion.rate_plan_id == plan_id)
        .order_by(RateVersion.version.desc())
        .limit(1)
    )
    if version is None:
        raise ProblemError(
            404, "Rate plan not found", "Rate plan has no version", "rate_plan_missing"
        )
    report = await update_draft_version(session, version, payload)
    session.add(
        audit_event(
            action="rate_plan.edited",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_version",
            object_id=version.id,
            details={"integrity_sha256": report.integrity_sha256},
        )
    )
    await session.commit()
    return {"version": _version_summary(version), "validation": report}


@router.post("/rates/plans/{plan_id}/clone", status_code=201)
async def clone_rate_plan(
    plan_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _rate_manager(principal)
    version = await session.scalar(
        select(RateVersion)
        .where(RateVersion.rate_plan_id == plan_id)
        .order_by(RateVersion.version.desc())
        .limit(1)
    )
    if version is None:
        raise ProblemError(
            404, "Rate version not found", "Rate plan has no versions", "rate_version_missing"
        )
    plan, clone = await clone_plan_version(session, version, principal.user.id)
    session.add(
        audit_event(
            action="rate_plan.cloned",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_plan",
            object_id=plan.id,
            details={"cloned_from_rate_version_id": version.id, "new_version_id": clone.id},
        )
    )
    await session.commit()
    return {
        "plan_id": plan.id,
        "version_id": clone.id,
        "editor_url": f"/rates/{plan.id}/versions/{clone.id}",
    }


@router.post("/rates/plans/{plan_id}/versions", status_code=201)
async def create_plan_version(
    plan_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _rate_manager(principal)
    source = await session.scalar(
        select(RateVersion)
        .where(RateVersion.rate_plan_id == plan_id)
        .order_by(RateVersion.version.desc())
        .limit(1)
    )
    if source is None:
        raise ProblemError(
            404, "Rate version not found", "Rate plan has no versions", "rate_version_missing"
        )
    document = await version_document(session, source)
    latest = source.version
    version = RateVersion(
        rate_plan_id=source.rate_plan_id,
        version=latest + 1,
        effective_from=document.effective_from,
        effective_to=document.effective_through,
        timezone=document.timezone,
        currency=document.currency,
        source_url="urn:power-monitor:custom-rate-version",
        source_checked_on=date.today(),
        source_checked_at=datetime.now(UTC),
        source_notes=f"Draft derived from v{source.version}",
        source_label=f"Custom version derived from v{source.version}",
        source_kind="custom",
        content_hash=source.content_hash,
        status="draft",
        normalized_payload=document.model_dump(mode="json"),
        immutable_after_use=False,
        is_active=False,
        created_at=datetime.now(UTC),
        created_by=principal.user.id,
    )
    session.add(version)
    await session.flush()
    await update_draft_version(session, version, document)
    session.add(
        audit_event(
            action="rate_version.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_version",
            object_id=version.id,
            details={"derived_from": source.id},
        )
    )
    await session.commit()
    return _version_summary(version)


@router.get("/rates/plans/{plan_id}/versions")
async def list_plan_versions(
    plan_id: str, _viewer: Viewer, session: DbSession
) -> list[dict[str, Any]]:
    versions = await session.scalars(
        select(RateVersion)
        .where(RateVersion.rate_plan_id == plan_id)
        .order_by(RateVersion.version.desc())
    )
    return [_version_summary(item) for item in versions]


@router.get("/rates/versions/{version_id}")
async def get_rate_version(version_id: str, _viewer: Viewer, session: DbSession) -> dict[str, Any]:
    version = await session.get(RateVersion, version_id)
    if version is None:
        raise ProblemError(
            404, "Rate version not found", "Version does not exist", "rate_version_missing"
        )
    evidence_links = list(
        await session.scalars(
            select(RateVersionSource).where(RateVersionSource.rate_version_id == version.id)
        )
    )
    evidence = []
    for link in evidence_links:
        artifact = await session.get(RateSourceArtifact, link.artifact_id)
        extraction = (
            await session.get(RateExtractionResult, link.extraction_result_id)
            if link.extraction_result_id
            else None
        )
        if artifact:
            evidence.append(
                {
                    "artifact_id": artifact.id,
                    "sha256": artifact.sha256,
                    "captured_at": artifact.captured_at,
                    "parser_id": extraction.parser_id if extraction else None,
                    "parser_version": extraction.parser_version if extraction else None,
                    "relationship": link.relationship,
                }
            )
    return {
        "version": _version_summary(version),
        "document": await version_document(session, version),
        "source_evidence": evidence,
    }


@router.patch("/rates/versions/{version_id}")
async def patch_rate_version(
    version_id: str,
    payload: RatePlanDocument,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _rate_manager(principal)
    version = await session.get(RateVersion, version_id)
    if version is None:
        raise ProblemError(
            404, "Rate version not found", "Version does not exist", "rate_version_missing"
        )
    report = await update_draft_version(session, version, payload)
    session.add(
        audit_event(
            action="rate_version.edited",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_version",
            object_id=version.id,
            details={"integrity_sha256": report.integrity_sha256},
        )
    )
    await session.commit()
    return {"version": _version_summary(version), "validation": report}


@router.post("/rates/versions/{version_id}/validate")
async def validate_managed_version(
    version_id: str, principal: CsrfPrincipal, session: DbSession
) -> dict[str, Any]:
    _rate_manager(principal)
    version = await session.get(RateVersion, version_id)
    if version is None:
        raise ProblemError(
            404, "Rate version not found", "Version does not exist", "rate_version_missing"
        )
    return (await validate_version(session, version)).model_dump(mode="json")


@router.post("/rates/versions/{version_id}/activate")
async def activate_managed_version(
    version_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _rate_manager(principal)
    version = await session.get(RateVersion, version_id)
    if version is None:
        raise ProblemError(
            404, "Rate version not found", "Version does not exist", "rate_version_missing"
        )
    status, report = await activate_version(session, version, principal.user.id)
    session.add(
        audit_event(
            action="rate_version.activated" if status == "active" else "rate_version.scheduled",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_version",
            object_id=version.id,
            details={
                "effective_from": version.effective_from.isoformat(),
                "integrity_sha256": report.integrity_sha256,
            },
        )
    )
    await session.commit()
    return {"status": status, "version": _version_summary(version), "validation": report}


@router.post("/rates/versions/{version_id}/retire")
async def retire_managed_version(
    version_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> dict[str, str]:
    _rate_manager(principal)
    version = await session.get(RateVersion, version_id)
    if version is None:
        raise ProblemError(
            404, "Rate version not found", "Version does not exist", "rate_version_missing"
        )
    version.status = "retired"
    version.is_active = False
    plan = await session.get(RatePlan, version.rate_plan_id)
    if plan and not await session.scalar(
        select(RateVersion.id).where(
            RateVersion.rate_plan_id == plan.id,
            RateVersion.is_active.is_(True),
            RateVersion.id != version.id,
        )
    ):
        plan.status = "retired"
    session.add(
        audit_event(
            action="rate_version.retired",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_version",
            object_id=version.id,
        )
    )
    await session.commit()
    return {"status": "retired"}


@router.delete("/rates/versions/{version_id}", status_code=204)
async def delete_draft_version(
    version_id: str, principal: CsrfPrincipal, session: DbSession
) -> None:
    _rate_manager(principal)
    version = await session.get(RateVersion, version_id)
    if version is None:
        return
    if version.status != "draft" or await version_usage_count(session, version.id):
        raise ProblemError(
            409,
            "Rate version cannot be deleted",
            "Only unused drafts can be deleted",
            "rate_version_in_use",
        )
    await session.delete(version)
    await session.commit()


@router.get("/rates/versions/{version_id}/export")
async def export_managed_version(
    version_id: str, _viewer: Viewer, session: DbSession
) -> dict[str, Any]:
    version = await session.get(RateVersion, version_id)
    if version is None:
        raise ProblemError(
            404, "Rate version not found", "Version does not exist", "rate_version_missing"
        )
    document = await version_document(session, version)
    return {"document": document.model_dump(mode="json"), "integrity_sha256": version.content_hash}


@router.post("/rates/import", status_code=201)
async def import_rate_plan(
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    upload: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    _rate_manager(principal)
    if upload.content_type not in {"application/json", "text/json"} or not (
        upload.filename or ""
    ).lower().endswith(".json"):
        raise ProblemError(
            415, "Unsupported rate file", "Import a .json rate-plan document", "rate_import_type"
        )
    content = await upload.read(1_048_577)
    if len(content) > 1_048_576:
        raise ProblemError(
            413, "Rate import too large", "Rate imports are limited to 1 MiB", "rate_import_size"
        )
    try:
        document = RatePlanDocument.model_validate_json(content)
    except ValueError as exc:
        raise ProblemError(
            422,
            "Invalid rate import",
            "The document does not match power-monitor-rate-plan/1.0",
            "rate_import_invalid",
        ) from exc
    report = validate_document(document)
    plan, version = await create_custom_plan(
        session, document, principal.user.id, duplicate_suffix=True
    )
    session.add(
        audit_event(
            action="rate_plan.imported",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_plan",
            object_id=plan.id,
            details={
                "filename": Path(upload.filename or "import.json").name,
                "integrity_sha256": report.integrity_sha256,
            },
        )
    )
    await session.commit()
    return {"plan_id": plan.id, "version_id": version.id, "validation": report}


@router.post("/rates/preview-cost")
async def preview_managed_cost(
    payload: dict[str, Any], _viewer: Viewer, session: DbSession
) -> dict[str, Any]:
    if payload.get("version_id"):
        version = await session.get(RateVersion, str(payload["version_id"]))
        if version is None:
            raise ProblemError(
                404, "Rate version not found", "Version does not exist", "rate_version_missing"
            )
        document = await version_document(session, version)
    else:
        document = RatePlanDocument.model_validate(payload["document"])
    report = validate_document(document)
    if not report.valid:
        raise ProblemError(
            422,
            "Rate plan failed validation",
            "Preview requires a complete valid schedule",
            "rate_validation_failed",
            extra={"validation": report.model_dump(mode="json")},
        )
    engine = RateEngine(engine_plan(document))
    result = engine.calculate(
        start=datetime.fromisoformat(str(payload["interval_start"])),
        end=datetime.fromisoformat(str(payload["interval_end"])),
        energy_kwh=Decimal(str(payload["energy_kwh"])),
        cost_scope=payload.get("cost_scope", document.cost_scope_default),
        baseline_allocation_kwh=(
            Decimal(str(payload["baseline_allocation_kwh"]))
            if payload.get("baseline_allocation_kwh") is not None
            else None
        ),
    )
    return {
        "energy_by_bucket_kwh": {key: str(value) for key, value in result.energy_by_bucket.items()},
        "energy_charge": str(result.energy_charge),
        "fixed_charge": str(result.fixed_charge),
        "baseline_credit": str(result.baseline_credit),
        "unrounded_total": str(result.total),
        "display_total": str(engine.display_currency(result.total)),
        "integrity_sha256": report.integrity_sha256,
    }


@router.post("/rates/validate-document")
async def validate_rate_document(
    payload: RatePlanDocument, principal: CsrfPrincipal
) -> dict[str, Any]:
    _rate_manager(principal)
    return validate_document(payload).model_dump(mode="json")


@router.get("/rates/assignments")
async def list_rate_assignments(_viewer: Viewer, session: DbSession) -> list[dict[str, Any]]:
    _rate_manager(_viewer, "rates.view")
    assignments = await session.scalars(
        select(RateAssignment).order_by(RateAssignment.effective_from.desc())
    )
    return [
        {
            "id": item.id,
            "utility_account_id": item.utility_account_id,
            "rate_version_id": item.rate_version_id,
            "effective_from": item.effective_from,
            "effective_to": item.effective_to,
        }
        for item in assignments
    ]


@router.post("/rates/assignments", status_code=201)
async def create_rate_assignment(
    payload: dict[str, Any], request: Request, principal: CsrfPrincipal, session: DbSession
) -> dict[str, Any]:
    _rate_manager(principal, "rates.assign")
    account_id = payload.get("utility_account_id")
    account = await session.get(UtilityAccount, str(account_id)) if account_id else None
    if account is None and payload.get("site_id"):
        account = await session.scalar(
            select(UtilityAccount)
            .where(UtilityAccount.site_id == str(payload["site_id"]))
            .order_by(UtilityAccount.created_at)
            .limit(1)
        )
    version = await session.get(RateVersion, str(payload["rate_version_id"]))
    if account is None or version is None or version.status not in {"active", "approved"}:
        raise ProblemError(
            422,
            "Invalid rate assignment",
            "Account and active or scheduled version are required",
            "rate_assignment_invalid",
        )
    effective_from = datetime.fromisoformat(
        str(payload.get("effective_from") or datetime.now(UTC).isoformat())
    )
    current = list(
        await session.scalars(
            select(RateAssignment).where(
                RateAssignment.utility_account_id == account.id,
                RateAssignment.effective_to.is_(None),
            )
        )
    )
    for item in current:
        item.effective_to = effective_from
    assignment = RateAssignment(
        utility_account_id=account.id,
        rate_version_id=version.id,
        effective_from=effective_from,
        effective_to=None,
        assigned_by=principal.user.id,
        created_at=datetime.now(UTC),
    )
    account.active_rate_version_id = version.id
    provider_mode = str(payload.get("provider_mode", account.provider_mode))
    cost_scope = str(payload.get("cost_scope", account.cost_scope_default))
    if provider_mode not in {
        "sce_bundled",
        "sce_delivery_generation",
        "sce_delivery_cca",
        "sce_delivery_direct_access",
        "custom_combined",
    } or cost_scope not in {
        "energy_only",
        "allocated_account_estimate",
        "full_account_estimate",
    }:
        raise ProblemError(
            422,
            "Invalid rate assignment",
            "Provider mode or cost scope is not supported",
            "rate_assignment_invalid",
        )
    account.provider_mode = provider_mode
    account.cost_scope_default = cost_scope
    if "baseline_allocation_kwh" in payload:
        account.baseline_allocation_kwh = (
            Decimal(str(payload["baseline_allocation_kwh"]))
            if payload["baseline_allocation_kwh"] is not None
            else None
        )
    if payload.get("generation_provider"):
        account.generation_provider = str(payload["generation_provider"])[:32]
    session.add(assignment)
    await session.flush()
    session.add(
        audit_event(
            action="rate_assignment.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_assignment",
            object_id=assignment.id,
            details={
                "rate_version_id": version.id,
                "utility_account_id": account.id,
                "provider_mode": account.provider_mode,
                "cost_scope": account.cost_scope_default,
            },
        )
    )
    await session.commit()
    return {"id": assignment.id, "effective_from": assignment.effective_from}


@router.patch("/rates/assignments/{assignment_id}")
async def patch_rate_assignment(
    assignment_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _rate_manager(principal, "rates.assign")
    assignment = await session.get(RateAssignment, assignment_id)
    if assignment is None:
        raise ProblemError(
            404, "Rate assignment not found", "Assignment does not exist", "rate_assignment_missing"
        )
    if payload.get("effective_to") is not None:
        assignment.effective_to = datetime.fromisoformat(str(payload["effective_to"]))
        if assignment.effective_to <= assignment.effective_from:
            raise ProblemError(
                422,
                "Invalid assignment dates",
                "Assignment end must follow its start",
                "rate_assignment_invalid",
            )
    session.add(
        audit_event(
            action="rate_assignment.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_assignment",
            object_id=assignment.id,
            details={
                "effective_to": assignment.effective_to.isoformat()
                if assignment.effective_to
                else None
            },
        )
    )
    await session.commit()
    return {"id": assignment.id, "effective_to": assignment.effective_to}


@router.delete("/rates/assignments/{assignment_id}", status_code=204)
async def delete_rate_assignment(
    assignment_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> None:
    _rate_manager(principal, "rates.assign")
    assignment = await session.get(RateAssignment, assignment_id)
    if assignment:
        session.add(
            audit_event(
                action="rate_assignment.deleted",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="rate_assignment",
                object_id=assignment.id,
                details={"rate_version_id": assignment.rate_version_id},
            )
        )
        await session.delete(assignment)
        await session.commit()


def _rate_sync_configuration(config: RateSyncConfiguration) -> dict[str, Any]:
    return {
        "enabled": config.enabled,
        "schedule_cron": config.schedule_cron,
        "timezone": config.timezone,
        "jitter_minutes": config.jitter_minutes,
        "approval_mode": config.approval_mode,
        "auto_activate_verified": config.auto_activate_verified,
        "next_scheduled_run": config.next_scheduled_run,
        "last_attempted_run": config.last_attempted_run,
        "last_successful_run": config.last_successful_run,
        "last_source_change": config.last_source_change,
        "last_candidate_created": config.last_candidate_created,
        "last_approved_version": config.last_approved_version,
        "last_error": config.last_error,
    }


def _rate_source_payload(source: RateSource) -> dict[str, Any]:
    return {
        "id": source.id,
        "name": source.name,
        "url": source.url,
        "parser_id": source.parser_id,
        "effective_from": source.effective_from_hint,
        "enabled": source.enabled,
        "last_checked_at": source.last_checked_at,
        "last_success_at": source.last_success_at,
        "consecutive_failures": source.consecutive_failures,
        "created_at": source.created_at,
    }


@router.get("/admin/rate-sources")
async def list_rate_sources(principal: Principal, session: DbSession) -> dict[str, Any]:
    _rate_manager(principal, "rates.manage_sources")
    config = await session.get(RateSyncConfiguration, "default")
    sources = list(await session.scalars(select(RateSource).order_by(RateSource.url)))
    last_success = await session.scalar(
        select(RateSourceCheckRun.checked_at)
        .where(RateSourceCheckRun.outcome.in_(["succeeded", "manual_review", "not_modified"]))
        .order_by(RateSourceCheckRun.checked_at.desc())
        .limit(1)
    )
    return {
        "configuration": _rate_sync_configuration(config) if config else None,
        "last_successful_check": last_success,
        "sources": [_rate_source_payload(item) for item in sources],
    }


@router.post("/admin/rate-sources", status_code=201)
async def create_rate_source(
    payload: dict[str, Any],
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _rate_manager(principal, "rates.manage_sources")
    allowed = {"name", "url", "parser_id", "effective_from"}
    if set(payload) - allowed or not {"name", "url", "parser_id"}.issubset(payload):
        raise ProblemError(
            422,
            "Invalid rate source",
            "Name, URL, and parser type are required; unsupported fields are not accepted",
            "rate_source_invalid",
        )
    if not all(isinstance(payload.get(field), str) for field in ("name", "url", "parser_id")):
        raise ProblemError(
            422,
            "Invalid rate source",
            "Name, URL, and parser type must be text values",
            "rate_source_invalid",
        )
    name = str(payload["name"]).strip()
    parser_id = str(payload["parser_id"]).strip()
    if not 3 <= len(name) <= 160:
        raise ProblemError(
            422,
            "Invalid rate source",
            "Source name must contain between 3 and 160 characters",
            "rate_source_invalid",
        )
    if parser_id not in REMOTE_SOURCE_PARSER_IDS:
        raise ProblemError(
            422,
            "Invalid rate source",
            "Select a supported remote SCE source type",
            "rate_source_parser_invalid",
        )
    try:
        normalized_url = validate_source_url(
            str(payload["url"]).strip(),
            document_link=parser_id == "sce_tariff_pdf_v1",
        )
    except (SourceSecurityError, ValueError) as exc:
        raise ProblemError(
            422,
            "Rate source is not permitted",
            str(exc),
            "rate_source_url_invalid",
        ) from exc
    path_is_pdf = normalized_url.split("?", 1)[0].lower().endswith(".pdf")
    if path_is_pdf != (parser_id == "sce_tariff_pdf_v1"):
        raise ProblemError(
            422,
            "Source type does not match URL",
            "PDF URLs require the tariff PDF parser; page parsers require an HTML URL",
            "rate_source_parser_mismatch",
        )
    effective_from_hint: date | None = None
    if payload.get("effective_from"):
        try:
            effective_from_hint = date.fromisoformat(str(payload["effective_from"]))
        except ValueError as exc:
            raise ProblemError(
                422,
                "Invalid effective date",
                "Effective date must use YYYY-MM-DD",
                "rate_source_effective_date_invalid",
            ) from exc
    if parser_id == "sce_public_tou_html_v1" and effective_from_hint is None:
        raise ProblemError(
            422,
            "Effective date required",
            "Rate pages need the effective date shown by the supporting SCE advisory or tariff",
            "rate_source_effective_date_required",
        )
    if await session.scalar(select(RateSource.id).where(RateSource.url == normalized_url)):
        raise ProblemError(
            409,
            "Rate source already exists",
            "Enable or check the existing source instead of adding a duplicate",
            "rate_source_exists",
        )
    source = RateSource(
        name=name,
        url=normalized_url,
        parser_id=parser_id,
        effective_from_hint=effective_from_hint,
        enabled=True,
        consecutive_failures=0,
        created_by=principal.user.id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(source)
    await session.flush()
    session.add(
        audit_event(
            action="rate_source.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_source",
            object_id=source.id,
            details={
                "url": source.url,
                "parser_id": source.parser_id,
                "effective_from": (
                    source.effective_from_hint.isoformat() if source.effective_from_hint else None
                ),
            },
        )
    )
    await session.commit()
    return _rate_source_payload(source)


@router.get("/admin/rate-sources/{source_id}")
async def get_rate_source(
    source_id: str, principal: Principal, session: DbSession
) -> dict[str, Any]:
    _rate_manager(principal, "rates.manage_sources")
    source = await session.get(RateSource, source_id)
    if source is None:
        raise ProblemError(
            404, "Rate source not found", "Source does not exist", "rate_source_missing"
        )
    checks = list(
        await session.scalars(
            select(RateSourceCheckRun)
            .where(RateSourceCheckRun.rate_source_id == source.id)
            .order_by(RateSourceCheckRun.checked_at.desc())
            .limit(20)
        )
    )
    return {
        "id": source.id,
        "name": source.name,
        "url": source.url,
        "parser_id": source.parser_id,
        "effective_from": source.effective_from_hint,
        "enabled": source.enabled,
        "checks": [
            {
                "id": item.id,
                "checked_at": item.checked_at,
                "outcome": item.outcome,
                "http_status": item.http_status,
                "error_code": item.error_code,
            }
            for item in checks
        ],
    }


@router.patch("/admin/rate-sources/{source_id}")
async def patch_rate_source(
    source_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _rate_manager(principal, "rates.manage_sources")
    if set(payload) - {"enabled"}:
        raise ProblemError(
            422,
            "Source URL is immutable",
            "Automated URLs require a server-side allowlist change",
            "rate_source_immutable",
        )
    source = await session.get(RateSource, source_id)
    if source is None:
        raise ProblemError(
            404, "Rate source not found", "Source does not exist", "rate_source_missing"
        )
    source.enabled = bool(payload["enabled"])
    source.updated_at = datetime.now(UTC)
    session.add(
        audit_event(
            action="rate_source.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_source",
            object_id=source.id,
            details={"enabled": source.enabled},
        )
    )
    await session.commit()
    return {"id": source.id, "enabled": source.enabled}


@router.patch("/admin/rate-source-settings")
async def patch_rate_settings(
    payload: dict[str, Any], request: Request, principal: CsrfPrincipal, session: DbSession
) -> dict[str, Any]:
    _rate_manager(principal, "rates.manage_sources")
    allowed = {
        "enabled",
        "schedule_cron",
        "timezone",
        "jitter_minutes",
        "approval_mode",
        "auto_activate_verified",
    }
    if set(payload) - allowed:
        raise ProblemError(
            422,
            "Invalid source setting",
            "One or more settings are not editable",
            "rate_setting_invalid",
        )
    config = await session.get(RateSyncConfiguration, "default")
    if config is None:
        raise ProblemError(
            409, "Rate settings unavailable", "Run database initialization", "rate_settings_missing"
        )
    for key, value in payload.items():
        setattr(config, key, value)
    try:
        schedule_parts(config.schedule_cron)
        config.next_scheduled_run = next_scheduled_time(
            datetime.now(UTC), config.schedule_cron, config.timezone
        )
    except ValueError as exc:
        raise ProblemError(
            422,
            "Invalid source setting",
            str(exc),
            "rate_setting_invalid",
        ) from exc
    if (
        config.approval_mode not in {"manual_review", "notify_only", "auto_activate_verified"}
        or not 0 <= config.jitter_minutes <= 20
    ):
        raise ProblemError(
            422,
            "Invalid source setting",
            "Policy or jitter is outside the supported range",
            "rate_setting_invalid",
        )
    config.updated_at = datetime.now(UTC)
    config.updated_by = principal.user.id
    session.add(
        audit_event(
            action="rate_source.settings_updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_sync_configuration",
            object_id=config.id,
            details={key: value for key, value in payload.items()},
        )
    )
    await session.commit()
    return {"updated": True, "configuration": _rate_sync_configuration(config)}


async def _queue_check(session: DbSession, user_id: str, source_ids: list[str]) -> BackgroundJob:
    job = BackgroundJob(
        job_type="rate_source_sync",
        status="queued",
        requested_by=user_id,
        requested_at=datetime.now(UTC),
        scheduled_for=None,
        correlation_id=f"rate-sync-{secrets.token_hex(12)}",
        progress={"source_ids": source_ids, "completed": 0},
        result={},
    )
    session.add(job)
    await session.flush()
    return job


@router.post("/admin/rate-sources/check-now", status_code=202)
async def check_all_sources(
    request: Request, principal: CsrfPrincipal, session: DbSession
) -> dict[str, str]:
    _rate_manager(principal, "rates.check_sources")
    job = await _queue_check(session, principal.user.id, [])
    session.add(
        audit_event(
            action="rate_source.check_requested",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="background_job",
            object_id=job.id,
        )
    )
    await session.commit()
    return {"job_id": job.id, "status": "queued"}


@router.post("/admin/rate-sources/{source_id}/check", status_code=202)
async def check_one_source(
    source_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> dict[str, str]:
    _rate_manager(principal, "rates.check_sources")
    if await session.get(RateSource, source_id) is None:
        raise ProblemError(
            404, "Rate source not found", "Source does not exist", "rate_source_missing"
        )
    job = await _queue_check(session, principal.user.id, [source_id])
    session.add(
        audit_event(
            action="rate_source.check_requested",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_source",
            object_id=source_id,
            details={"job_id": job.id},
        )
    )
    await session.commit()
    return {"job_id": job.id, "status": "queued"}


@router.get("/admin/rate-checks")
async def list_rate_checks(principal: Principal, session: DbSession) -> list[dict[str, Any]]:
    _rate_manager(principal, "rates.review_candidates")
    checks = await session.scalars(
        select(RateSourceCheckRun).order_by(RateSourceCheckRun.checked_at.desc()).limit(100)
    )
    return [
        {
            "id": item.id,
            "job_id": item.job_id,
            "rate_source_id": item.rate_source_id,
            "checked_at": item.checked_at,
            "outcome": item.outcome,
            "http_status": item.http_status,
            "error_code": item.error_code,
        }
        for item in checks
    ]


@router.get("/admin/rate-checks/{check_id}")
async def get_rate_check(check_id: str, principal: Principal, session: DbSession) -> dict[str, Any]:
    _rate_manager(principal, "rates.review_candidates")
    check = await session.get(RateSourceCheckRun, check_id)
    if check is None:
        raise ProblemError(
            404, "Rate check not found", "Check does not exist", "rate_check_missing"
        )
    artifacts = list(
        await session.scalars(
            select(RateSourceArtifact).where(RateSourceArtifact.source_check_id == check.id)
        )
    )
    return {
        "id": check.id,
        "job_id": check.job_id,
        "rate_source_id": check.rate_source_id,
        "checked_at": check.checked_at,
        "outcome": check.outcome,
        "http_status": check.http_status,
        "final_url": check.final_url,
        "duration_ms": check.duration_ms,
        "response_bytes": check.response_bytes,
        "error_code": check.error_code,
        "error_detail": check.error_detail,
        "artifacts": [
            {
                "id": item.id,
                "sha256": item.sha256,
                "content_type": item.content_type,
                "byte_size": item.byte_size,
            }
            for item in artifacts
        ],
    }


@router.get("/admin/rate-candidates")
async def list_rate_candidates(principal: Principal, session: DbSession) -> list[dict[str, Any]]:
    _rate_manager(principal, "rates.review_candidates")
    candidates = await session.scalars(
        select(RateChangeCandidate).order_by(RateChangeCandidate.created_at.desc()).limit(100)
    )
    return [
        {
            "id": item.id,
            "rate_plan_id": item.rate_plan_id,
            "candidate_rate_version_id": item.candidate_rate_version_id,
            "status": item.status,
            "risk_level": item.risk_level,
            "summary": item.summary,
            "created_at": item.created_at,
        }
        for item in candidates
    ]


@router.get("/admin/rate-candidates/{candidate_id}")
async def get_rate_candidate(
    candidate_id: str, principal: Principal, session: DbSession
) -> dict[str, Any]:
    _rate_manager(principal, "rates.review_candidates")
    candidate = await session.get(RateChangeCandidate, candidate_id)
    if candidate is None:
        raise ProblemError(
            404, "Rate candidate not found", "Candidate does not exist", "rate_candidate_missing"
        )
    differences = list(
        await session.scalars(
            select(RateCandidateDifference)
            .where(RateCandidateDifference.candidate_id == candidate.id)
            .order_by(RateCandidateDifference.path)
        )
    )
    extraction = await session.get(RateExtractionResult, candidate.extraction_result_id)
    artifact = await session.get(RateSourceArtifact, extraction.artifact_id) if extraction else None
    return {
        "id": candidate.id,
        "status": candidate.status,
        "risk_level": candidate.risk_level,
        "summary": candidate.summary,
        "candidate_rate_version_id": candidate.candidate_rate_version_id,
        "source_evidence": (
            {
                "artifact_id": artifact.id,
                "sha256": artifact.sha256,
                "content_type": artifact.content_type,
                "captured_at": artifact.captured_at,
                "parser_id": extraction.parser_id,
                "parser_version": extraction.parser_version,
                "warnings": extraction.warnings,
                "errors": extraction.errors,
            }
            if extraction and artifact
            else None
        ),
        "differences": [
            {
                "path": item.path,
                "change_type": item.change_type,
                "before": item.before_value,
                "after": item.after_value,
                "material": item.material,
            }
            for item in differences
        ],
    }


async def _decide_candidate(
    candidate_id: str,
    decision: str,
    comment: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> RateChangeCandidate:
    _rate_manager(principal, "rates.approve_candidates")
    candidate = await session.get(RateChangeCandidate, candidate_id)
    if candidate is None:
        raise ProblemError(
            404, "Rate candidate not found", "Candidate does not exist", "rate_candidate_missing"
        )
    if candidate.status not in {"pending_review", "approved"}:
        raise ProblemError(
            409,
            "Candidate is closed",
            "Only pending candidates can be reviewed",
            "rate_candidate_closed",
        )
    if decision == "approve" and candidate.risk_level == "blocking":
        conflict_ids = candidate.summary.get("conflicting_candidate_ids", [])
        unresolved = []
        for conflict_id in conflict_ids:
            conflict = await session.get(RateChangeCandidate, str(conflict_id))
            if conflict and conflict.status not in {"rejected", "validation_failed"}:
                unresolved.append(conflict.id)
        if unresolved:
            raise ProblemError(
                409,
                "Source conflict is unresolved",
                "Reject the conflicting candidate before approving this evidence",
                "rate_source_conflict",
                extra={"conflicting_candidate_ids": unresolved},
            )
    candidate.status = "approved" if decision == "approve" else "rejected"
    candidate.reviewed_at = datetime.now(UTC)
    candidate.reviewed_by = principal.user.id
    session.add(
        RateApprovalDecision(
            candidate_id=candidate.id,
            decision=decision,
            comment=comment[:4000],
            decided_by=principal.user.id,
            decided_at=datetime.now(UTC),
        )
    )
    await emit_rate_alert(
        session,
        "rate_candidate_approved" if decision == "approve" else "rate_candidate_rejected",
        {"candidate_id": candidate.id, "comment_present": bool(comment)},
    )
    if candidate.candidate_rate_version_id:
        version = await session.get(RateVersion, candidate.candidate_rate_version_id)
        if version:
            version.status = "approved" if decision == "approve" else "rejected"
            version.approved_by = principal.user.id if decision == "approve" else None
            version.approved_at = datetime.now(UTC) if decision == "approve" else None
    config = await session.get(RateSyncConfiguration, "default")
    if config and decision == "approve":
        config.last_approved_version = datetime.now(UTC)
    session.add(
        audit_event(
            action=(
                "rate_candidate.approved" if decision == "approve" else "rate_candidate.rejected"
            ),
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_candidate",
            object_id=candidate.id,
            details={"comment_present": bool(comment)},
        )
    )
    await session.commit()
    return candidate


@router.post("/admin/rate-candidates/{candidate_id}/approve")
async def approve_candidate(
    candidate_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, str]:
    candidate = await _decide_candidate(
        candidate_id, "approve", str(payload.get("comment", "")), request, principal, session
    )
    return {"status": candidate.status}


@router.post("/admin/rate-candidates/{candidate_id}/reject")
async def reject_candidate(
    candidate_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, str]:
    if not str(payload.get("comment", "")).strip():
        raise ProblemError(
            422,
            "Rejection reason required",
            "Explain why the candidate was rejected",
            "rate_rejection_reason",
        )
    candidate = await _decide_candidate(
        candidate_id, "reject", str(payload["comment"]), request, principal, session
    )
    return {"status": candidate.status}


@router.post("/admin/rate-candidates/{candidate_id}/activate")
async def activate_candidate(
    candidate_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> dict[str, Any]:
    _rate_manager(principal, "rates.approve_candidates")
    candidate = await session.get(RateChangeCandidate, candidate_id)
    if (
        candidate is None
        or candidate.status != "approved"
        or not candidate.candidate_rate_version_id
    ):
        raise ProblemError(
            409,
            "Candidate is not approved",
            "Approve a valid candidate before activation",
            "rate_candidate_not_approved",
        )
    version = await session.get(RateVersion, candidate.candidate_rate_version_id)
    if version is None:
        raise ProblemError(
            404,
            "Rate version not found",
            "Candidate version does not exist",
            "rate_version_missing",
        )
    status, report = await activate_version(session, version, principal.user.id)
    candidate.status = "activated" if status == "active" else "scheduled"
    await emit_rate_alert(
        session,
        "rate_version_activated",
        {
            "candidate_id": candidate.id,
            "rate_version_id": version.id,
            "status": status,
        },
    )
    session.add(
        audit_event(
            action="rate_candidate.activated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_candidate",
            object_id=candidate.id,
            details={"version_id": version.id, "status": status},
        )
    )
    await session.commit()
    return {"status": status, "validation": report}


def _verified_artifact_path(artifact: RateSourceArtifact) -> Path:
    root = get_settings().rate_sync_artifact_path.resolve()
    path = Path(artifact.storage_path).resolve()
    if root not in path.parents or not path.is_file():
        raise ProblemError(
            404, "Rate artifact unavailable", "Archived file is missing", "rate_artifact_missing"
        )
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
        raise ProblemError(
            409,
            "Rate artifact integrity failure",
            "Archived file hash does not match",
            "rate_artifact_integrity",
        )
    return path


@router.get("/admin/rate-artifacts/{artifact_id}")
async def get_rate_artifact(
    artifact_id: str, principal: Principal, session: DbSession
) -> dict[str, Any]:
    _rate_manager(principal, "rates.review_candidates")
    artifact = await session.get(RateSourceArtifact, artifact_id)
    if artifact is None:
        raise ProblemError(
            404, "Rate artifact not found", "Artifact does not exist", "rate_artifact_missing"
        )
    return {
        "id": artifact.id,
        "source_check_id": artifact.source_check_id,
        "sha256": artifact.sha256,
        "content_type": artifact.content_type,
        "byte_size": artifact.byte_size,
        "captured_at": artifact.captured_at,
    }


@router.get("/admin/rate-artifacts/{artifact_id}/download")
async def download_rate_artifact(
    artifact_id: str, principal: Principal, session: DbSession
) -> FileResponse:
    _rate_manager(principal, "rates.review_candidates")
    artifact = await session.get(RateSourceArtifact, artifact_id)
    if artifact is None:
        raise ProblemError(
            404, "Rate artifact not found", "Artifact does not exist", "rate_artifact_missing"
        )
    return FileResponse(
        _verified_artifact_path(artifact),
        media_type=artifact.content_type,
        filename=artifact.original_filename or f"{artifact.sha256}.bin",
    )


@router.post("/admin/rate-artifacts/upload", status_code=201)
async def upload_rate_artifact(
    source_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    upload: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    _rate_manager(principal, "rates.manage_sources")
    source = await session.get(RateSource, source_id)
    if source is None:
        raise ProblemError(
            404, "Rate source not found", "Choose an approved source", "rate_source_missing"
        )
    allowed_types = {"application/json", "text/json", "application/pdf", "text/html"}
    if upload.content_type not in allowed_types:
        raise ProblemError(
            415,
            "Unsupported source file",
            "Upload JSON, HTML, or PDF evidence",
            "rate_artifact_type",
        )
    settings = get_settings()
    content = await upload.read(settings.rate_sync_max_source_bytes + 1)
    if len(content) > settings.rate_sync_max_source_bytes:
        raise ProblemError(
            413,
            "Source file too large",
            "Uploaded evidence exceeds the configured limit",
            "rate_artifact_size",
        )
    job = BackgroundJob(
        job_type="rate_artifact_upload",
        status="succeeded",
        requested_by=principal.user.id,
        requested_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        correlation_id=f"rate-upload-{secrets.token_hex(12)}",
        progress={},
        result={},
    )
    session.add(job)
    await session.flush()
    check = RateSourceCheckRun(
        job_id=job.id,
        rate_source_id=source.id,
        checked_at=datetime.now(UTC),
        http_status=None,
        outcome="uploaded",
        final_url=source.url,
        response_bytes=len(content),
    )
    session.add(check)
    await session.flush()
    digest = hashlib.sha256(content).hexdigest()
    suffix = (
        ".json"
        if "json" in (upload.content_type or "")
        else ".pdf"
        if upload.content_type == "application/pdf"
        else ".html"
    )
    root = settings.rate_sync_artifact_path.resolve()
    path = (root / digest[:2] / f"{digest}{suffix}").resolve()
    if root not in path.parents:
        raise ProblemError(
            400, "Invalid artifact path", "Artifact path was rejected", "rate_artifact_path"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(content)
    metadata_path = (root / digest[:2] / f"{digest}.metadata.json").resolve()
    metadata_path.write_text(
        json.dumps(
            {
                "source_url": source.url,
                "captured_at": datetime.now(UTC).isoformat(),
                "sha256": digest,
                "content_type": upload.content_type,
                "byte_size": len(content),
                "uploaded_by": principal.user.id,
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    artifact = RateSourceArtifact(
        source_check_id=check.id,
        sha256=digest,
        content_type=upload.content_type or "application/octet-stream",
        byte_size=len(content),
        storage_path=str(path),
        original_filename=Path(upload.filename or "upload").name[:255],
        captured_at=datetime.now(UTC),
    )
    session.add(artifact)
    await session.flush()
    parser_id = (
        "admin_uploaded_structured_v1"
        if "json" in (upload.content_type or "")
        else "sce_tariff_pdf_v1"
        if upload.content_type == "application/pdf"
        else source.parser_id
    )
    parsed = ADAPTERS[parser_id].parse(
        content,
        source.url,
        upload.content_type or "",
        effective_from=source.effective_from_hint,
    )
    extraction = RateExtractionResult(
        artifact_id=artifact.id,
        parser_id=parser_id,
        parser_version=PARSER_VERSION,
        status=parsed.status,
        normalized_payload={
            "documents": [item.model_dump(mode="json") for item in parsed.documents]
        },
        warnings=parsed.warnings,
        errors=parsed.errors,
        extracted_at=datetime.now(UTC),
    )
    session.add(extraction)
    await session.flush()
    extraction_path = (root / digest[:2] / f"{digest}.extraction.json").resolve()
    extraction_path.write_text(
        json.dumps(
            {
                "parser_id": parser_id,
                "parser_version": PARSER_VERSION,
                "status": parsed.status,
                "payload": extraction.normalized_payload,
                "warnings": parsed.warnings,
                "errors": parsed.errors,
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    config = await session.get(RateSyncConfiguration, "default")
    candidate_ids: list[str] = []
    for document in parsed.documents:
        candidate = await create_candidate_from_document(
            session,
            document,
            extraction,
            artifact,
            approval_mode=(config.approval_mode if config else "manual_review"),
            auto_activate_verified=False,
            maximum_percent_change=Decimal(str(settings.rate_sync_auto_max_percent_change)),
            retroactive_days=settings.rate_sync_retroactive_auto_days,
        )
        if candidate:
            candidate_ids.append(candidate.id)
    if config and candidate_ids:
        config.last_source_change = datetime.now(UTC)
        config.last_candidate_created = datetime.now(UTC)
    job.result = {
        "artifact_id": artifact.id,
        "extraction_id": extraction.id,
        "status": parsed.status,
        "candidate_ids": candidate_ids,
    }
    session.add(
        audit_event(
            action="rate_artifact.uploaded",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_source_artifact",
            object_id=artifact.id,
            details={"sha256": digest, "byte_size": len(content), "parser_id": parser_id},
        )
    )
    await session.commit()
    return job.result


@router.get("/jobs/{job_id}")
async def get_background_job(job_id: str, _viewer: Viewer, session: DbSession) -> dict[str, Any]:
    job = await session.get(BackgroundJob, job_id)
    if job is None:
        raise ProblemError(404, "Job not found", "Job does not exist", "job_missing")
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "error": ({"code": job.error_code, "detail": job.error_detail} if job.error_code else None),
        "result": job.result,
    }
