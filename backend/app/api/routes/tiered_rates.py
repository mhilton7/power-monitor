from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.deps import CsrfPrincipal, DbSession, Principal, Viewer, audit_event
from app.db.models import (
    AccountReconciliationAdjustment,
    AccountUsageAuthority,
    AggregateSet,
    BillingCycle,
    Device,
    ManualAccountUsage,
    NormalizedInterval,
    UtilityAccount,
    UtilityUsageImport,
)
from app.problem import ProblemError
from app.rates.tiered import (
    authority_payload,
    calculate_cycle_tier_status,
    current_billing_cycle,
    import_digest,
    import_quality,
    normalized_import_rows,
    usage_authority,
)
from app.schemas import (
    AccountUsageAuthorityWrite,
    BillingCycleOverrideWrite,
    ManualAccountUsageWrite,
    ReconciliationAdjustmentWrite,
    UtilityUsageImportWrite,
)

router = APIRouter(prefix="/api/v1", tags=["tiered rates and billing cycles"])


def _permission(principal: Principal, permission: str) -> None:
    if permission not in principal.permissions:
        raise ProblemError(
            403,
            "Permission denied",
            "Your account does not have the required billing permission",
            "forbidden",
            extra={"required_permission": permission},
        )


async def _account(session: DbSession, principal: Principal, account_id: str) -> UtilityAccount:
    account = await session.get(UtilityAccount, account_id)
    if account is None or not principal.can_access_site(account.site_id):
        raise ProblemError(
            404, "Utility account not found", "Resource does not exist", "account_missing"
        )
    return account


async def _cycle(
    session: DbSession,
    account: UtilityAccount,
    cycle_id: str,
    *,
    lock: bool = False,
) -> BillingCycle:
    statement = select(BillingCycle).where(
        BillingCycle.id == cycle_id,
        BillingCycle.utility_account_id == account.id,
    )
    if lock:
        statement = statement.with_for_update()
    cycle = await session.scalar(statement)
    if cycle is None:
        raise ProblemError(
            404, "Billing cycle not found", "Resource does not exist", "billing_cycle_missing"
        )
    return cycle


@router.get("/utility-accounts/{account_id}/tier-status")
async def tier_status(account_id: str, principal: Viewer, session: DbSession) -> dict[str, Any]:
    _permission(principal, "rates.view")
    account = await _account(session, principal, account_id)
    cycle = await current_billing_cycle(session, account, datetime.now(UTC), create=False)
    return await calculate_cycle_tier_status(session, account, cycle, persist=False)


@router.get("/admin/utility-accounts/{account_id}/usage-authority")
async def get_usage_authority(
    account_id: str, principal: Viewer, session: DbSession
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.view")
    await _account(session, principal, account_id)
    return authority_payload(await usage_authority(session, account_id))


@router.put("/admin/utility-accounts/{account_id}/usage-authority")
async def put_usage_authority(
    account_id: str,
    payload: AccountUsageAuthorityWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.manage")
    account = await _account(session, principal, account_id)
    if payload.aggregate_set_id:
        aggregate = await session.get(AggregateSet, payload.aggregate_set_id)
        if (
            aggregate is None
            or aggregate.utility_account_id != account.id
            or aggregate.site_id != account.site_id
        ):
            raise ProblemError(
                422,
                "Aggregate is not eligible",
                "Choose a full-account aggregate assigned to this utility account",
                "usage_authority_aggregate_invalid",
            )
        if aggregate.cost_scope != "full_account":
            raise ProblemError(
                422,
                "Aggregate is partial",
                "Tier progression requires an aggregate explicitly scoped to the full account",
                "usage_authority_partial_aggregate",
            )
    if payload.device_ids:
        valid_count = int(
            await session.scalar(
                select(func.count())
                .select_from(Device)
                .where(
                    Device.id.in_(set(payload.device_ids)),
                    Device.utility_account_id == account.id,
                    Device.lifecycle_status == "active",
                )
            )
            or 0
        )
        if valid_count != len(set(payload.device_ids)):
            raise ProblemError(
                422,
                "Sensor selection is invalid",
                "Every authority sensor must be active and assigned to this account",
                "usage_authority_device_invalid",
            )
    authority = await session.scalar(
        select(AccountUsageAuthority)
        .where(AccountUsageAuthority.utility_account_id == account.id)
        .with_for_update()
    )
    if authority is None:
        authority = AccountUsageAuthority(
            utility_account_id=account.id,
            revision=1,
            updated_at=datetime.now(UTC),
        )
        session.add(authority)
    elif payload.revision is None or payload.revision != authority.revision:
        raise ProblemError(
            409,
            "Usage authority changed",
            "Reload the account usage authority before saving",
            "stale_revision",
        )
    else:
        authority.revision += 1
    authority.authority_type = payload.authority_type
    authority.aggregate_set_id = payload.aggregate_set_id
    authority.device_ids = sorted(set(payload.device_ids))
    authority.source_reference = payload.source_reference
    authority.confidence = payload.confidence
    authority.complete_account = payload.complete_account
    authority.updated_by = principal.user.id
    authority.updated_at = datetime.now(UTC)
    session.add(
        audit_event(
            action="utility_account.usage_authority_changed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="account_usage_authority",
            object_id=authority.id,
            details={
                "utility_account_id": account.id,
                "authority_type": authority.authority_type,
                "complete_account": authority.complete_account,
                "confidence": authority.confidence,
                "device_count": len(authority.device_ids),
                "aggregate_set_id": authority.aggregate_set_id,
                "revision": authority.revision,
            },
        )
    )
    await session.commit()
    return authority_payload(authority)


@router.post("/admin/utility-accounts/{account_id}/manual-usage", status_code=201)
async def enter_manual_usage(
    account_id: str,
    payload: ManualAccountUsageWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.manage")
    account = await _account(session, principal, account_id)
    existing = await session.scalar(
        select(ManualAccountUsage).where(
            ManualAccountUsage.utility_account_id == account.id,
            ManualAccountUsage.idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        return _manual_payload(existing)
    cycle = await current_billing_cycle(
        session,
        account,
        payload.effective_at,
        create=True,
        actor_id=principal.user.id,
    )
    item = ManualAccountUsage(
        utility_account_id=account.id,
        billing_cycle_id=cycle.id,
        effective_at=payload.effective_at.astimezone(UTC),
        cumulative_kwh=payload.cumulative_kwh,
        source_note=payload.source_note,
        evidence_reference=payload.evidence_reference,
        idempotency_key=payload.idempotency_key,
        verification_status=payload.verification_status,
        created_by=principal.user.id,
        created_at=datetime.now(UTC),
    )
    session.add(item)
    session.add(
        audit_event(
            action="utility_account.manual_usage_entered",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="manual_account_usage",
            object_id=item.id,
            details={
                "utility_account_id": account.id,
                "billing_cycle_id": cycle.id,
                "effective_at": item.effective_at.isoformat(),
                "verification_status": item.verification_status,
                "evidence_attached": bool(item.evidence_reference),
            },
        )
    )
    await session.commit()
    return _manual_payload(item)


def _manual_payload(item: ManualAccountUsage) -> dict[str, Any]:
    return {
        "id": item.id,
        "billing_cycle_id": item.billing_cycle_id,
        "effective_at": item.effective_at,
        "cumulative_kwh": str(item.cumulative_kwh),
        "source_note": item.source_note,
        "evidence_reference": item.evidence_reference,
        "verification_status": item.verification_status,
        "created_at": item.created_at,
    }


@router.post("/admin/utility-accounts/{account_id}/usage-imports")
async def import_usage(
    account_id: str,
    payload: UtilityUsageImportWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "usage_imports.manage")
    account = await _account(session, principal, account_id)
    try:
        mapped_rows = _mapped_import_rows(payload.rows, payload.field_mapping)
        rows = normalized_import_rows(mapped_rows, payload.import_kind, payload.timezone)
    except (KeyError, TypeError, ValueError) as exc:
        raise ProblemError(
            422,
            "Usage import is invalid",
            "Correct the field mapping, timestamps, and exact decimal values",
            "usage_import_invalid",
            extra={"reason": str(exc)},
        ) from exc
    digest = import_digest(payload.import_kind, payload.timezone, rows)
    duplicate = await session.scalar(
        select(UtilityUsageImport).where(
            UtilityUsageImport.utility_account_id == account.id,
            UtilityUsageImport.content_sha256 == digest,
        )
    )
    conflicts = await _import_conflicts(session, account, rows, payload.import_kind)
    quality = import_quality(rows, payload.import_kind)
    cycle_impact = await _import_cycle_impact(session, account, rows, payload.import_kind)
    preview = {
        "content_sha256": digest,
        "row_count": len(rows),
        "duplicate": duplicate is not None,
        "conflict_count": conflicts,
        **quality,
        **cycle_impact,
        "normalized_preview": rows[:25],
        "will_commit": payload.commit,
    }
    if not payload.commit:
        return preview
    if duplicate:
        raise ProblemError(
            409,
            "Usage import already exists",
            "This exact normalized import was already recorded",
            "usage_import_duplicate",
            extra={"import_id": duplicate.id},
        )
    if quality["duplicate_row_count"] or quality["overlap_count"]:
        raise ProblemError(
            422,
            "Usage import contains duplicate or overlapping rows",
            "Remove duplicate rows and make imported windows non-overlapping before committing",
            "usage_import_internal_overlap",
            extra=quality,
        )
    if conflicts and payload.conflict_policy == "reject":
        raise ProblemError(
            409,
            "Usage import overlaps monitored data",
            "Choose an explicit conflict policy after reviewing the preview",
            "usage_import_conflict",
            extra={"conflict_count": conflicts},
        )
    item = UtilityUsageImport(
        utility_account_id=account.id,
        import_kind=payload.import_kind,
        status="committed",
        timezone=payload.timezone,
        source_name=payload.source_name,
        content_sha256=digest,
        field_mapping={
            **payload.field_mapping,
            "conflict_policy": payload.conflict_policy,
        },
        row_count=len(rows),
        conflict_count=conflicts,
        normalized_rows=rows,
        created_by=principal.user.id,
        created_at=datetime.now(UTC),
    )
    session.add(item)
    await session.flush()
    affected_cycles: set[str] = set()
    if payload.import_kind == "cycle_dates":
        for row in rows:
            starts_at = datetime.fromisoformat(str(row["starts_at"]))
            ends_at = datetime.fromisoformat(str(row["ends_at"]))
            overlapping = list(
                await session.scalars(
                    select(BillingCycle)
                    .where(
                        BillingCycle.utility_account_id == account.id,
                        BillingCycle.starts_at < ends_at,
                        BillingCycle.ends_at > starts_at,
                    )
                    .with_for_update()
                )
            )
            if any(cycle.finalized_at is not None for cycle in overlapping):
                raise ProblemError(
                    409,
                    "Finalized cycle is protected",
                    "Imported cycle dates cannot rewrite a finalized billing cycle",
                    "billing_cycle_finalized",
                )
            if len(overlapping) > 1:
                raise ProblemError(
                    409,
                    "Imported dates overlap multiple billing cycles",
                    "Correct the existing unfinalized cycle boundaries before "
                    "committing this import",
                    "billing_cycle_multiple_overlap",
                )
            cycle = (
                overlapping[0]
                if overlapping
                else BillingCycle(
                    utility_account_id=account.id,
                    created_by=principal.user.id,
                    created_at=datetime.now(UTC),
                    override_revision=0,
                    recalculation_version=0,
                )
            )
            if not overlapping:
                session.add(cycle)
            cycle.starts_at = starts_at
            cycle.ends_at = ends_at
            cycle.explicit_meter_dates = True
            cycle.status = "recalculating"
            cycle.boundary_source = "utility_import"
            cycle.override_revision += 1
            cycle.updated_by = principal.user.id
            cycle.updated_at = datetime.now(UTC)
            await session.flush()
            affected_cycles.add(cycle.id)
    else:
        for starts_at, ends_at in _import_windows(rows, payload.import_kind):
            cycles = list(
                await session.scalars(
                    select(BillingCycle)
                    .where(
                        BillingCycle.utility_account_id == account.id,
                        BillingCycle.starts_at < ends_at,
                        BillingCycle.ends_at > starts_at,
                        BillingCycle.finalized_at.is_(None),
                    )
                    .with_for_update()
                )
            )
            for cycle in cycles:
                cycle.status = "recalculating"
                cycle.updated_by = principal.user.id
                cycle.updated_at = datetime.now(UTC)
                affected_cycles.add(cycle.id)
    session.add(
        audit_event(
            action="utility_account.usage_imported",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_usage_import",
            object_id=item.id,
            details={
                "utility_account_id": account.id,
                "kind": item.import_kind,
                "row_count": item.row_count,
                "conflict_count": item.conflict_count,
                "conflict_policy": payload.conflict_policy,
                "content_sha256": digest,
                "affected_cycle_count": len(affected_cycles),
            },
        )
    )
    await session.commit()
    return {
        **preview,
        "id": item.id,
        "status": item.status,
        "affected_cycle_count": len(affected_cycles),
    }


@router.post("/admin/utility-accounts/{account_id}/usage-imports/{import_id}/reverse")
async def reverse_usage_import(
    account_id: str,
    import_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "usage_imports.manage")
    account = await _account(session, principal, account_id)
    item = await session.scalar(
        select(UtilityUsageImport)
        .where(
            UtilityUsageImport.id == import_id,
            UtilityUsageImport.utility_account_id == account.id,
        )
        .with_for_update()
    )
    if item is None:
        raise ProblemError(
            404, "Usage import not found", "Resource does not exist", "usage_import_missing"
        )
    if item.reversed_at is not None:
        return {"id": item.id, "status": item.status, "reversed_at": item.reversed_at}
    windows = _import_windows(item.normalized_rows, item.import_kind)
    finalized = None
    for starts_at, ends_at in windows:
        finalized = await session.scalar(
            select(BillingCycle.id)
            .where(
                BillingCycle.utility_account_id == account.id,
                BillingCycle.starts_at < ends_at,
                BillingCycle.ends_at > starts_at,
                BillingCycle.finalized_at.is_not(None),
            )
            .limit(1)
        )
        if finalized:
            break
    if finalized:
        raise ProblemError(
            409,
            "Finalized cycle is protected",
            "Add a reconciliation adjustment instead of reversing evidence used by a "
            "finalized cycle",
            "billing_cycle_finalized",
        )
    item.status = "reversed"
    item.reversed_at = datetime.now(UTC)
    affected_by_id: dict[str, BillingCycle] = {}
    for starts_at, ends_at in windows:
        for cycle in await session.scalars(
            select(BillingCycle).where(
                BillingCycle.utility_account_id == account.id,
                BillingCycle.starts_at < ends_at,
                BillingCycle.ends_at > starts_at,
                BillingCycle.finalized_at.is_(None),
            )
        ):
            affected_by_id[cycle.id] = cycle
    affected = list(affected_by_id.values())
    for cycle in affected:
        cycle.status = "recalculating"
        cycle.updated_at = datetime.now(UTC)
    session.add(
        audit_event(
            action="utility_account.usage_import_reversed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_usage_import",
            object_id=item.id,
            details={
                "utility_account_id": account.id,
                "content_sha256": item.content_sha256,
                "affected_cycle_count": len(affected),
            },
        )
    )
    await session.commit()
    return {"id": item.id, "status": item.status, "reversed_at": item.reversed_at}


def _import_windows(
    rows: list[dict[str, Any]], import_kind: str
) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    if import_kind in {"interval", "daily"}:
        windows.extend(
            (
                datetime.fromisoformat(str(row["start"])),
                datetime.fromisoformat(str(row["end"])),
            )
            for row in rows
        )
    elif import_kind in {"cycle_dates", "bill_total"}:
        windows.extend(
            (
                datetime.fromisoformat(str(row["starts_at"])),
                datetime.fromisoformat(str(row["ends_at"])),
            )
            for row in rows
        )
    elif import_kind == "cycle_cumulative":
        windows.extend(
            (
                datetime.fromisoformat(str(row["effective_at"])),
                datetime.fromisoformat(str(row["effective_at"])) + timedelta(microseconds=1),
            )
            for row in rows
        )
    return windows


async def _import_conflicts(
    session: DbSession,
    account: UtilityAccount,
    rows: list[dict[str, Any]],
    import_kind: str,
) -> int:
    if import_kind not in {"interval", "daily"}:
        return 0
    conflicts = 0
    for row in rows:
        start = datetime.fromisoformat(str(row["start"]))
        end = datetime.fromisoformat(str(row["end"]))
        found = await session.scalar(
            select(NormalizedInterval.id)
            .join(Device, Device.id == NormalizedInterval.device_id)
            .where(
                Device.utility_account_id == account.id,
                NormalizedInterval.interval_start < end,
                NormalizedInterval.interval_end > start,
            )
            .limit(1)
        )
        conflicts += int(found is not None)
    return conflicts


def _mapped_import_rows(
    rows: list[dict[str, Any]], field_mapping: dict[str, str]
) -> list[dict[str, Any]]:
    if not field_mapping:
        return rows
    mapped: list[dict[str, Any]] = []
    for row in rows:
        value = dict(row)
        for canonical_name, source_name in field_mapping.items():
            if source_name not in row:
                raise ValueError(f"mapped source field is missing: {source_name}")
            value[canonical_name] = row[source_name]
        mapped.append(value)
    return mapped


async def _import_cycle_impact(
    session: DbSession,
    account: UtilityAccount,
    rows: list[dict[str, Any]],
    import_kind: str,
) -> dict[str, Any]:
    cycle_ids: set[str] = set()
    finalized_ids: set[str] = set()
    new_cycle_count = 0
    for starts_at, ends_at in _import_windows(rows, import_kind):
        cycles = list(
            await session.scalars(
                select(BillingCycle).where(
                    BillingCycle.utility_account_id == account.id,
                    BillingCycle.starts_at < ends_at,
                    BillingCycle.ends_at > starts_at,
                )
            )
        )
        cycle_ids.update(item.id for item in cycles)
        finalized_ids.update(item.id for item in cycles if item.finalized_at is not None)
        if import_kind == "cycle_dates" and not cycles:
            new_cycle_count += 1
    return {
        "affected_cycle_count": len(cycle_ids) + new_cycle_count,
        "finalized_cycle_conflict": bool(finalized_ids),
    }


@router.post("/admin/utility-accounts/{account_id}/billing-cycles", status_code=201)
async def override_billing_cycle(
    account_id: str,
    payload: BillingCycleOverrideWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.manage")
    account = await _account(session, principal, account_id)
    overlapping = list(
        await session.scalars(
            select(BillingCycle)
            .where(
                BillingCycle.utility_account_id == account.id,
                BillingCycle.starts_at < payload.ends_at,
                BillingCycle.ends_at > payload.starts_at,
            )
            .with_for_update()
        )
    )
    if any(item.finalized_at is not None for item in overlapping):
        raise ProblemError(
            409,
            "Finalized cycle is protected",
            "Create a reconciliation adjustment instead of changing finalized dates",
            "billing_cycle_finalized",
        )
    if len(overlapping) > 1:
        raise ProblemError(
            409,
            "Dates overlap multiple billing cycles",
            "Correct one unfinalized cycle at a time",
            "billing_cycle_multiple_overlap",
        )
    cycle = overlapping[0] if overlapping else None
    if cycle is None:
        cycle = BillingCycle(
            utility_account_id=account.id,
            created_at=datetime.now(UTC),
            created_by=principal.user.id,
            override_revision=0,
            recalculation_version=0,
        )
        session.add(cycle)
    cycle.starts_at = payload.starts_at.astimezone(UTC)
    cycle.ends_at = payload.ends_at.astimezone(UTC)
    cycle.explicit_meter_dates = True
    cycle.status = "confirmed"
    cycle.boundary_source = payload.source
    cycle.override_revision += 1
    cycle.updated_at = datetime.now(UTC)
    cycle.updated_by = principal.user.id
    session.add(
        audit_event(
            action="billing_cycle.override_created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="billing_cycle",
            object_id=cycle.id,
            details={
                "utility_account_id": account.id,
                "starts_at": cycle.starts_at.isoformat(),
                "ends_at": cycle.ends_at.isoformat(),
                "source": cycle.boundary_source,
                "revision": cycle.override_revision,
                "reason": payload.reason,
            },
        )
    )
    await session.commit()
    return _cycle_response(cycle)


@router.get("/admin/utility-accounts/{account_id}/billing-cycles")
async def list_billing_cycles(
    account_id: str, principal: Viewer, session: DbSession
) -> list[dict[str, Any]]:
    _permission(principal, "utility_accounts.view")
    account = await _account(session, principal, account_id)
    cycles = list(
        await session.scalars(
            select(BillingCycle)
            .where(BillingCycle.utility_account_id == account.id)
            .order_by(BillingCycle.starts_at.desc())
        )
    )
    return [_cycle_response(item) for item in cycles]


def _cycle_response(cycle: BillingCycle) -> dict[str, Any]:
    return {
        "id": cycle.id,
        "starts_at": cycle.starts_at,
        "ends_at": cycle.ends_at,
        "status": cycle.status,
        "boundary_source": cycle.boundary_source,
        "exact_dates": cycle.explicit_meter_dates,
        "override_revision": cycle.override_revision,
        "recalculation_version": cycle.recalculation_version,
        "finalized_at": cycle.finalized_at,
        "locked_snapshot_hash": cycle.locked_snapshot_hash,
    }


@router.get("/admin/utility-accounts/{account_id}/billing-cycles/{cycle_id}/reconciliations")
async def list_reconciliations(
    account_id: str,
    cycle_id: str,
    principal: Viewer,
    session: DbSession,
) -> list[dict[str, Any]]:
    _permission(principal, "utility_accounts.view")
    account = await _account(session, principal, account_id)
    cycle = await _cycle(session, account, cycle_id)
    rows = list(
        await session.scalars(
            select(AccountReconciliationAdjustment)
            .where(AccountReconciliationAdjustment.billing_cycle_id == cycle.id)
            .order_by(AccountReconciliationAdjustment.created_at)
        )
    )
    return [_reconciliation_response(item) for item in rows]


@router.post(
    "/admin/utility-accounts/{account_id}/billing-cycles/{cycle_id}/reconciliations",
    status_code=201,
)
async def create_reconciliation(
    account_id: str,
    cycle_id: str,
    payload: ReconciliationAdjustmentWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.manage")
    account = await _account(session, principal, account_id)
    cycle = await _cycle(session, account, cycle_id)
    adjustment = AccountReconciliationAdjustment(
        utility_account_id=account.id,
        billing_cycle_id=cycle.id,
        component=payload.component,
        amount=payload.amount,
        notes=payload.notes,
        provenance=payload.provenance,
        created_by=principal.user.id,
        created_at=datetime.now(UTC),
    )
    session.add(adjustment)
    session.add(
        audit_event(
            action="billing_cycle.reconciliation_added",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="account_reconciliation_adjustment",
            object_id=adjustment.id,
            details={
                "utility_account_id": account.id,
                "billing_cycle_id": cycle.id,
                "component": adjustment.component,
                "amount": str(adjustment.amount),
                "finalized_cycle_unchanged": cycle.finalized_at is not None,
            },
        )
    )
    await session.commit()
    return _reconciliation_response(adjustment)


def _reconciliation_response(
    adjustment: AccountReconciliationAdjustment,
) -> dict[str, Any]:
    return {
        "id": adjustment.id,
        "billing_cycle_id": adjustment.billing_cycle_id,
        "component": adjustment.component,
        "amount": str(adjustment.amount),
        "notes": adjustment.notes,
        "provenance": adjustment.provenance,
        "created_at": adjustment.created_at,
    }


@router.post("/admin/utility-accounts/{account_id}/billing-cycles/{cycle_id}/recalculate")
async def recalculate_cycle(
    account_id: str,
    cycle_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "costs.recalculate")
    account = await _account(session, principal, account_id)
    cycle = await _cycle(session, account, cycle_id, lock=True)
    status = await calculate_cycle_tier_status(
        session,
        account,
        cycle,
        persist=True,
        actor_id=principal.user.id,
    )
    session.add(
        audit_event(
            action="billing_cycle.recalculated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="billing_cycle",
            object_id=cycle.id,
            details={
                "utility_account_id": account.id,
                "recalculation_version": cycle.recalculation_version,
                "finalized_cycle_protected": False,
            },
        )
    )
    await session.commit()
    return status


@router.post("/admin/utility-accounts/{account_id}/billing-cycles/{cycle_id}/finalize")
async def finalize_cycle(
    account_id: str,
    cycle_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "costs.recalculate")
    account = await _account(session, principal, account_id)
    cycle = await _cycle(session, account, cycle_id, lock=True)
    if cycle.finalized_at is not None:
        return _cycle_response(cycle)
    status = await calculate_cycle_tier_status(
        session,
        account,
        cycle,
        persist=True,
        actor_id=principal.user.id,
    )
    if not status.get("available"):
        raise ProblemError(
            409,
            "Billing cycle cannot be finalized",
            "Complete rate, usage-authority, and cycle calculation setup before finalizing",
            "billing_cycle_not_calculated",
            extra={"warnings": status.get("warnings", [])},
        )
    snapshot = json.dumps(status, sort_keys=True, default=str, separators=(",", ":")).encode()
    cycle.locked_snapshot_hash = hashlib.sha256(snapshot).hexdigest()
    cycle.finalized_at = datetime.now(UTC)
    cycle.status = "finalized"
    session.add(
        audit_event(
            action="billing_cycle.finalized",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="billing_cycle",
            object_id=cycle.id,
            details={
                "utility_account_id": account.id,
                "recalculation_version": cycle.recalculation_version,
                "snapshot_sha256": cycle.locked_snapshot_hash,
            },
        )
    )
    await session.commit()
    return _cycle_response(cycle)
