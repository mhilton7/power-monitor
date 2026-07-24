from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import AppSettings, CsrfPrincipal, DbSession, Principal, audit_event
from app.bills.extraction import BillPdfError
from app.bills.service import (
    approve_cycle_draft,
    bill_comparison,
    create_bill_import,
    delete_original_artifact,
    import_payload,
    publish_and_assign,
    review_import,
    validate_bill_rate_draft,
)
from app.db.models import (
    RateSourceArtifact,
    UtilityAccount,
    UtilityBillCycleDraft,
    UtilityBillExtractedField,
    UtilityBillExtractionRevision,
    UtilityBillImport,
)
from app.problem import ProblemError

router = APIRouter(prefix="/api/v1", tags=["utility bill imports"])


class BillFieldReview(BaseModel):
    field_id: str = Field(min_length=1, max_length=36)
    action: Literal["confirm", "correct", "reject"]
    value: Any | None = None


class BillConflictResolution(BaseModel):
    conflict_id: str = Field(min_length=1, max_length=36)
    decision: Literal["accepted_bill", "accepted_configured", "dismissed"]
    note: str = Field(default="", max_length=1000)


class BillReviewWrite(BaseModel):
    revision: int = Field(gt=0)
    field_reviews: list[BillFieldReview] = Field(default_factory=list, max_length=250)
    conflict_resolutions: list[BillConflictResolution] = Field(default_factory=list, max_length=250)
    threshold_interpretation: Literal[
        "fixed_cycle_threshold",
        "daily_baseline",
        "baseline_multiplier",
        "unknown",
    ]
    source_role: Literal[
        "supporting",
        "authoritative_account_specific",
        "reference_only",
    ] = "supporting"


class BillPublishWrite(BaseModel):
    effective_from: datetime | None = None

    @field_validator("effective_from")
    @classmethod
    def aware_effective_from(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("effective_from must include a timezone offset")
        return value


class BillRetentionWrite(BaseModel):
    revision: int = Field(gt=0)
    retention_mode: Literal["retain", "retain_until", "delete_after_approval"]
    retain_until: datetime | None = None

    @field_validator("retain_until")
    @classmethod
    def aware_retention(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("retain_until must include a timezone offset")
        return value


class BillAccountContextWrite(BaseModel):
    revision: int = Field(gt=0)
    account_id: str = Field(min_length=1, max_length=36)


def _bill_admin(principal: Principal, permission: str) -> None:
    if "admin" not in principal.roles or permission not in principal.permissions:
        raise ProblemError(
            403,
            "Administrator permission required",
            "Private utility-bill evidence is restricted to administrators",
            "utility_bill_forbidden",
            extra={"required_permission": permission},
        )


def _site_allowed(principal: Principal, account: UtilityAccount) -> None:
    if principal.site_ids and account.site_id not in principal.site_ids:
        raise ProblemError(
            403,
            "Site access denied",
            "The utility account is outside your permitted sites",
            "site_scope_forbidden",
        )


async def _account(session: DbSession, principal: Principal, account_id: str) -> UtilityAccount:
    account = await session.get(UtilityAccount, account_id)
    if account is None or account.status != "active":
        raise ProblemError(
            404,
            "Utility account not found",
            "The selected active utility account does not exist",
            "utility_account_missing",
        )
    _site_allowed(principal, account)
    return account


async def _bill(session: DbSession, principal: Principal, bill_id: str) -> UtilityBillImport:
    bill = await session.get(UtilityBillImport, bill_id)
    if bill is None:
        raise ProblemError(
            404,
            "Utility bill import not found",
            "The requested import does not exist",
            "utility_bill_missing",
        )
    if bill.utility_account_id is not None:
        await _account(session, principal, bill.utility_account_id)
    elif bill.created_by != principal.user.id:
        raise ProblemError(
            404,
            "Utility bill import not found",
            "The requested import does not exist",
            "utility_bill_missing",
        )
    return bill


async def _store_utility_bill(
    *,
    account: UtilityAccount | None,
    request: Request,
    principal: Principal,
    session: DbSession,
    settings: AppSettings,
    upload: UploadFile,
    retention_mode: Literal["retain", "retain_until", "delete_after_approval"],
    source_role: Literal["supporting", "authoritative_account_specific", "reference_only"],
    retain_until: datetime | None,
    timezone: str,
    currency: str,
) -> dict[str, Any]:
    if upload.content_type != "application/pdf":
        raise ProblemError(
            415,
            "Unsupported utility-bill file",
            "Upload a password-free PDF",
            "bill_pdf_type",
        )
    if retention_mode == "retain_until" and (
        retain_until is None or retain_until <= datetime.now(UTC)
    ):
        raise ProblemError(
            422,
            "Retention date required",
            "A future retain-until timestamp is required",
            "bill_retention_invalid",
        )
    if account is None and source_role == "authoritative_account_specific":
        raise ProblemError(
            422,
            "Utility account required",
            "Choose a utility account before marking a bill account-authoritative",
            "bill_account_context_required",
        )
    content = await upload.read(settings.utility_bill_max_bytes + 1)
    try:
        bill, duplicate = await create_bill_import(
            session,
            account=account,
            content=content,
            user_id=principal.user.id,
            settings=settings,
            correlation_id=getattr(request.state, "request_id", ""),
            retention_mode=retention_mode,
            retain_until=retain_until,
            source_role=source_role,
            timezone=timezone,
            currency=currency,
        )
    except BillPdfError as exc:
        await session.rollback()
        status = 413 if exc.code in {"bill_pdf_too_large", "bill_pdf_page_limit"} else 415
        if exc.code in {"bill_pdf_malformed", "bill_pdf_no_text", "bill_pdf_empty"}:
            status = 422
        raise ProblemError(
            status,
            "Utility-bill PDF rejected",
            str(exc),
            exc.code,
        ) from exc
    session.add(
        audit_event(
            action="utility_bill.reused" if duplicate else "utility_bill.uploaded",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_bill_import",
            object_id=bill.id,
            details={
                "utility_account_id": account.id if account is not None else None,
                "sha256": bill.content_sha256,
                "page_count": bill.page_count,
                "extraction_method": bill.extraction_method,
                "retention_mode": bill.retention_mode,
                "source_role": bill.source_role,
                "automatic_activation": False,
                "filename_logged": False,
            },
        )
    )
    await session.commit()
    return {**await import_payload(session, bill), "duplicate": duplicate}


@router.post("/admin/utility-bill-imports", status_code=201)
async def upload_unassigned_utility_bill(
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
    upload: Annotated[UploadFile, File()],
    account_id: Annotated[str | None, Query()] = None,
    timezone: Annotated[str | None, Query(max_length=64)] = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
    retention_mode: Annotated[
        Literal["retain", "retain_until", "delete_after_approval"], Query()
    ] = "retain",
    source_role: Annotated[
        Literal["supporting", "authoritative_account_specific", "reference_only"], Query()
    ] = "supporting",
    retain_until: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    _bill_admin(principal, "utility_bills.manage")
    account = await _account(session, principal, account_id) if account_id else None
    return await _store_utility_bill(
        account=account,
        request=request,
        principal=principal,
        session=session,
        settings=settings,
        upload=upload,
        retention_mode=retention_mode,
        source_role=source_role,
        retain_until=retain_until,
        timezone=account.timezone if account is not None else timezone or settings.default_timezone,
        currency=account.currency if account is not None else currency or settings.default_currency,
    )


@router.post("/admin/utility-accounts/{account_id}/bill-imports", status_code=201)
async def upload_utility_bill(
    account_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
    upload: Annotated[UploadFile, File()],
    retention_mode: Annotated[
        Literal["retain", "retain_until", "delete_after_approval"], Query()
    ] = "retain",
    source_role: Annotated[
        Literal["supporting", "authoritative_account_specific", "reference_only"], Query()
    ] = "supporting",
    retain_until: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    _bill_admin(principal, "utility_bills.manage")
    account = await _account(session, principal, account_id)
    return await _store_utility_bill(
        account=account,
        request=request,
        principal=principal,
        session=session,
        settings=settings,
        upload=upload,
        retention_mode=retention_mode,
        source_role=source_role,
        retain_until=retain_until,
        timezone=account.timezone,
        currency=account.currency,
    )


@router.get("/admin/utility-bill-imports")
async def list_utility_bill_imports(
    principal: Principal,
    session: DbSession,
    utility_account_id: str | None = None,
) -> list[dict[str, Any]]:
    _bill_admin(principal, "utility_bills.view")
    statement = select(UtilityBillImport).order_by(UtilityBillImport.created_at.desc())
    if utility_account_id:
        await _account(session, principal, utility_account_id)
        statement = statement.where(UtilityBillImport.utility_account_id == utility_account_id)
    bills = list(await session.scalars(statement))
    visible: list[dict[str, Any]] = []
    for bill in bills:
        if bill.utility_account_id is not None:
            try:
                await _account(session, principal, bill.utility_account_id)
            except ProblemError:
                continue
        elif bill.created_by != principal.user.id:
            continue
        payload = await import_payload(session, bill)
        cycle = await session.scalar(
            select(UtilityBillCycleDraft).where(UtilityBillCycleDraft.bill_import_id == bill.id)
        )
        visible.append(
            {
                key: payload[key]
                for key in (
                    "id",
                    "job_id",
                    "utility_account_id",
                    "utility_account_name",
                    "status",
                    "source_role",
                    "extraction_method",
                    "page_count",
                    "retention_mode",
                    "original_available",
                    "rate_plan_id",
                    "rate_version_id",
                    "revision",
                    "blocking_warnings",
                    "created_at",
                    "updated_at",
                )
            }
            | {
                "billing_cycle": (
                    {
                        "starts_at": cycle.starts_at,
                        "ends_at": cycle.ends_at,
                        "total_usage_kwh": cycle.total_usage_kwh,
                        "estimated_total": cycle.full_bill_total,
                        "status": cycle.status,
                        "billing_cycle_id": cycle.billing_cycle_id,
                    }
                    if cycle
                    else None
                )
            }
        )
    return visible


@router.put("/admin/utility-bill-imports/{bill_id}/account-context")
async def attach_utility_bill_account(
    bill_id: str,
    payload: BillAccountContextWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _bill_admin(principal, "utility_bills.manage")
    bill = await _bill(session, principal, bill_id)
    if bill.revision != payload.revision:
        raise ProblemError(
            409,
            "Bill import changed",
            "Reload the import before changing its utility-account context",
            "stale_revision",
        )
    if bill.status == "published":
        raise ProblemError(
            409,
            "Published import is immutable",
            "A published bill import cannot be attached to a different account",
            "bill_account_context_immutable",
        )
    account = await _account(session, principal, payload.account_id)
    duplicate = await session.scalar(
        select(UtilityBillImport).where(
            UtilityBillImport.utility_account_id == account.id,
            UtilityBillImport.content_sha256 == bill.content_sha256,
            UtilityBillImport.id != bill.id,
        )
    )
    if duplicate is not None:
        raise ProblemError(
            409,
            "Bill already exists for this account",
            "Open the existing account import instead of attaching a duplicate",
            "bill_account_duplicate",
            extra={"existing_bill_import_id": duplicate.id},
        )
    cycle = await session.scalar(
        select(UtilityBillCycleDraft).where(UtilityBillCycleDraft.bill_import_id == bill.id)
    )
    if cycle is not None and cycle.status == "imported":
        raise ProblemError(
            409,
            "Billing cycle already imported",
            "The imported billing-cycle evidence cannot be reassigned",
            "bill_cycle_context_immutable",
        )
    previous_account_id = bill.utility_account_id
    bill.utility_account_id = account.id
    bill.updated_at = datetime.now(UTC)
    bill.revision += 1
    if cycle is not None:
        cycle.utility_account_id = account.id
        cycle.updated_at = datetime.now(UTC)
        cycle.revision += 1
    session.add(
        audit_event(
            action="utility_bill.account_context_attached",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_bill_import",
            object_id=bill.id,
            details={
                "previous_utility_account_id": previous_account_id,
                "utility_account_id": account.id,
                "automatic_assignment": False,
                "billing_cycle_imported": False,
            },
        )
    )
    await session.commit()
    return await import_payload(session, bill)


@router.get("/admin/utility-bill-imports/{bill_id}")
async def get_utility_bill_import(
    bill_id: str, principal: Principal, session: DbSession
) -> dict[str, Any]:
    _bill_admin(principal, "utility_bills.view")
    return await import_payload(session, await _bill(session, principal, bill_id))


@router.get("/admin/utility-bill-imports/{bill_id}/evidence/pages/{page_number}")
async def get_utility_bill_evidence_page(
    bill_id: str,
    page_number: int,
    principal: Principal,
    session: DbSession,
) -> dict[str, Any]:
    _bill_admin(principal, "utility_bills.view")
    bill = await _bill(session, principal, bill_id)
    if page_number < 1 or page_number > bill.page_count:
        raise ProblemError(
            404,
            "Evidence page not found",
            "The requested source page does not exist",
            "bill_evidence_page_missing",
        )
    revision = await session.scalar(
        select(UtilityBillExtractionRevision)
        .where(UtilityBillExtractionRevision.bill_import_id == bill.id)
        .order_by(UtilityBillExtractionRevision.revision.desc())
        .limit(1)
    )
    fields = (
        list(
            await session.scalars(
                select(UtilityBillExtractedField)
                .where(
                    UtilityBillExtractedField.extraction_revision_id == revision.id,
                    UtilityBillExtractedField.page_number == page_number,
                )
                .order_by(UtilityBillExtractedField.field_key)
            )
        )
        if revision
        else []
    )
    return {
        "bill_import_id": bill.id,
        "artifact_id": bill.artifact_id,
        "page_number": page_number,
        "parser_version": bill.parser_version,
        "fields": [
            {
                "field_key": item.field_key,
                "source_excerpt": item.source_excerpt,
                "text_region": item.text_region,
                "method": item.extraction_method,
                "confidence": item.confidence,
            }
            for item in fields
        ],
    }


@router.get("/admin/utility-bill-imports/{bill_id}/original")
async def download_original_utility_bill(
    bill_id: str, principal: Principal, session: DbSession
) -> FileResponse:
    _bill_admin(principal, "utility_bills.view")
    bill = await _bill(session, principal, bill_id)
    artifact = await session.get(RateSourceArtifact, bill.artifact_id)
    path = Path(artifact.storage_path) if artifact else None
    if bill.original_deleted_at is not None or path is None or not path.is_file():
        raise ProblemError(
            410,
            "Original utility bill removed",
            "Sanitized evidence and audit history remain available",
            "bill_original_removed",
        )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"utility-bill-{bill.id}.pdf",
        headers={"Cache-Control": "no-store, private"},
    )


@router.get("/admin/utility-bill-imports/{bill_id}/sanitized-evidence")
async def download_sanitized_utility_bill_evidence(
    bill_id: str, principal: Principal, session: DbSession
) -> FileResponse:
    _bill_admin(principal, "utility_bills.view")
    bill = await _bill(session, principal, bill_id)
    path = Path(bill.sanitized_evidence_path)
    if not path.is_file():
        raise ProblemError(
            404,
            "Sanitized evidence missing",
            "The retained evidence artifact is unavailable",
            "bill_evidence_missing",
        )
    return FileResponse(
        path,
        media_type="application/json",
        filename=f"utility-bill-{bill.id}-sanitized-evidence.json",
        headers={"Cache-Control": "no-store, private"},
    )


@router.put("/admin/utility-bill-imports/{bill_id}/review")
async def review_utility_bill_import(
    bill_id: str,
    payload: BillReviewWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _bill_admin(principal, "utility_bills.manage")
    bill = await _bill(session, principal, bill_id)
    result = await review_import(
        session,
        bill=bill,
        user_id=principal.user.id,
        expected_revision=payload.revision,
        field_reviews=[item.model_dump(mode="json") for item in payload.field_reviews],
        conflict_resolutions=[
            item.model_dump(mode="json") for item in payload.conflict_resolutions
        ],
        threshold_interpretation=payload.threshold_interpretation,
        source_role=payload.source_role,
    )
    session.add(
        audit_event(
            action="utility_bill.reviewed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_bill_import",
            object_id=bill.id,
            details={
                "field_decision_count": len(payload.field_reviews),
                "conflict_decision_count": len(payload.conflict_resolutions),
                "threshold_interpretation": payload.threshold_interpretation,
                "source_role": payload.source_role,
                "ready_to_publish": bill.status == "ready_to_publish",
            },
        )
    )
    await session.commit()
    return result


@router.post("/admin/utility-bill-imports/{bill_id}/validate")
async def validate_utility_bill_rate_draft(
    bill_id: str, principal: CsrfPrincipal, session: DbSession
) -> dict[str, Any]:
    _bill_admin(principal, "utility_bills.manage")
    return await validate_bill_rate_draft(
        session,
        await _bill(session, principal, bill_id),
    )


@router.get("/admin/utility-bill-imports/{bill_id}/comparison")
async def compare_utility_bill_import(
    bill_id: str, principal: Principal, session: DbSession
) -> dict[str, Any]:
    _bill_admin(principal, "utility_bills.view")
    return await bill_comparison(
        session,
        bill=await _bill(session, principal, bill_id),
    )


@router.post("/admin/utility-bill-imports/{bill_id}/publish-and-assign")
async def publish_and_assign_utility_bill_rate(
    bill_id: str,
    payload: BillPublishWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _bill_admin(principal, "utility_bills.manage")
    bill = await _bill(session, principal, bill_id)
    if bill.utility_account_id is None:
        raise ProblemError(
            409,
            "Utility account required",
            "Select a utility account before publishing or assigning the imported rate",
            "bill_account_context_required",
        )
    account = await _account(session, principal, bill.utility_account_id)
    if "rates.assign" not in principal.permissions:
        raise ProblemError(
            403,
            "Rate assignment permission required",
            "Publishing an imported bill rate requires rate assignment permission",
            "forbidden",
        )
    effective_from = payload.effective_from
    if effective_from is None:
        effective_date = datetime.fromisoformat(
            str((await import_payload(session, bill))["normalized"]["rate_plan"]["effective_from"])
        ).date()
        effective_from = datetime.combine(
            effective_date,
            time.min,
            tzinfo=ZoneInfo(account.timezone),
        ).astimezone(UTC)
    version, assignment, status = await publish_and_assign(
        session,
        bill=bill,
        account=account,
        user_id=principal.user.id,
        effective_from=effective_from,
    )
    session.add(
        audit_event(
            action="utility_bill.rate_published_and_assigned",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_bill_import",
            object_id=bill.id,
            details={
                "rate_version_id": version.id,
                "rate_assignment_id": assignment.id,
                "utility_account_id": account.id,
                "requested_effective_from": effective_from.isoformat(),
                "effective_from": assignment.effective_from.isoformat(),
                "status": status,
                "explicit_administrator_approval": True,
            },
        )
    )
    await session.commit()
    return {
        "bill_import_id": bill.id,
        "rate_version_id": version.id,
        "rate_assignment_id": assignment.id,
        "status": status,
    }


@router.post("/admin/utility-bill-imports/{bill_id}/import-billing-cycle")
async def import_utility_bill_cycle(
    bill_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _bill_admin(principal, "utility_bills.manage")
    if "usage_imports.manage" not in principal.permissions:
        raise ProblemError(
            403,
            "Usage import permission required",
            "Importing utility billing-cycle evidence requires usage import permission",
            "forbidden",
        )
    bill = await _bill(session, principal, bill_id)
    if bill.utility_account_id is None:
        raise ProblemError(
            409,
            "Utility account required",
            "Select a utility account before applying billing-cycle data",
            "bill_account_context_required",
        )
    account = await _account(session, principal, bill.utility_account_id)
    draft = await approve_cycle_draft(
        session,
        bill=bill,
        account=account,
        user_id=principal.user.id,
    )
    session.add(
        audit_event(
            action="utility_bill.billing_cycle_imported",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_bill_cycle_draft",
            object_id=draft.id,
            details={
                "bill_import_id": bill.id,
                "billing_cycle_id": draft.billing_cycle_id,
                "utility_usage_import_id": draft.utility_usage_import_id,
                "monitored_readings_overwritten": False,
            },
        )
    )
    await session.commit()
    return {
        "cycle_draft_id": draft.id,
        "status": draft.status,
        "billing_cycle_id": draft.billing_cycle_id,
        "utility_usage_import_id": draft.utility_usage_import_id,
    }


@router.put("/admin/utility-bill-imports/{bill_id}/retention")
async def update_utility_bill_retention(
    bill_id: str,
    payload: BillRetentionWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _bill_admin(principal, "utility_bills.manage")
    bill = await _bill(session, principal, bill_id)
    if bill.revision != payload.revision:
        raise ProblemError(
            409,
            "Bill import changed",
            "Reload the import before changing retention",
            "stale_revision",
        )
    if payload.retention_mode == "retain_until" and (
        payload.retain_until is None or payload.retain_until <= datetime.now(UTC)
    ):
        raise ProblemError(
            422,
            "Retention date required",
            "A future retain-until timestamp is required",
            "bill_retention_invalid",
        )
    bill.retention_mode = payload.retention_mode
    bill.retain_until = payload.retain_until
    bill.revision += 1
    bill.updated_at = datetime.now(UTC)
    removed = False
    if payload.retention_mode == "delete_after_approval" and bill.status in {
        "ready_to_publish",
        "published",
    }:
        removed = await delete_original_artifact(session, bill=bill)
    session.add(
        audit_event(
            action="utility_bill.retention_updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_bill_import",
            object_id=bill.id,
            details={
                "retention_mode": bill.retention_mode,
                "retain_until": (bill.retain_until.isoformat() if bill.retain_until else None),
                "original_removed": removed,
                "sanitized_evidence_preserved": True,
            },
        )
    )
    await session.commit()
    return await import_payload(session, bill)


@router.delete("/admin/utility-bill-imports/{bill_id}/original")
async def delete_original_utility_bill(
    bill_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _bill_admin(principal, "utility_bills.manage")
    bill = await _bill(session, principal, bill_id)
    removed = await delete_original_artifact(session, bill=bill)
    session.add(
        audit_event(
            action="utility_bill.original_deleted",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_bill_import",
            object_id=bill.id,
            details={
                "file_removed": removed,
                "sanitized_evidence_preserved": True,
                "normalized_data_preserved": True,
                "audit_history_preserved": True,
            },
        )
    )
    await session.commit()
    return {
        "original_available": False,
        "sanitized_evidence_preserved": True,
    }
