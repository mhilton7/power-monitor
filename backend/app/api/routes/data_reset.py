from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.access import (
    require_data_reset_administrator,
    require_recent_reauthentication,
)
from app.api.deps import AppSettings, CsrfPrincipal, DbSession, Principal, audit_event
from app.data_reset.service import (
    canonical_sha256,
    create_reset_operation,
    create_reset_plan,
    mark_cancel_requested,
    operation_payload,
    public_plan_payload,
    retry_reset_operation,
)
from app.db.models import DataResetOperation, DataResetPlan
from app.problem import ProblemError
from app.schemas import (
    DataResetCancelRequest,
    DataResetExecuteRequest,
    DataResetOperationView,
    DataResetPlanRequest,
    DataResetPlanView,
    DataResetRetryRequest,
)

router = APIRouter(prefix="/api/v1/system/data-reset", tags=["data-only reset"])


def _authorize(principal: Principal, site_id: str) -> None:
    require_data_reset_administrator(
        roles=principal.roles,
        permissions=principal.permissions,
    )
    if not principal.can_access_site(site_id):
        raise ProblemError(
            404,
            "Site not found",
            "The requested reset site does not exist",
            "site_missing",
        )


async def _operation_for_update(session: DbSession, operation_id: str) -> DataResetOperation:
    operation = await session.scalar(
        select(DataResetOperation).where(DataResetOperation.id == operation_id).with_for_update()
    )
    if operation is None:
        raise ProblemError(
            404,
            "Data reset not found",
            "The requested reset operation does not exist",
            "data_reset_operation_missing",
        )
    return operation


def _record_action_idempotency(
    operation: DataResetOperation,
    *,
    action: str,
    idempotency_key: str,
    reason: str,
) -> bool:
    normalized_reason = " ".join(reason.split())
    if not 8 <= len(normalized_reason) <= 500:
        raise ProblemError(
            422,
            "Reset reason required",
            "Enter an operational reason of 8 to 500 characters",
            "data_reset_reason_invalid",
        )
    fingerprint = canonical_sha256(
        {
            "operation_id": operation.id,
            "action": action,
            "reason": normalized_reason,
        }
    )
    evidence = dict(operation.final_evidence or {})
    records = dict(evidence.get("_action_idempotency") or {})
    prior = records.get(idempotency_key)
    if prior is not None:
        if not isinstance(prior, dict) or prior.get("fingerprint") != fingerprint:
            raise ProblemError(
                409,
                "Idempotency conflict",
                "The idempotency key belongs to a different reset action",
                "idempotency_conflict",
            )
        return False
    records[idempotency_key] = {
        "action": action,
        "fingerprint": fingerprint,
    }
    evidence["_action_idempotency"] = records
    operation.final_evidence = evidence
    return True


@router.post("/plan", status_code=201, response_model=DataResetPlanView)
async def plan_data_reset(
    payload: DataResetPlanRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, Any]:
    _authorize(principal, payload.site_id)
    plan = await create_reset_plan(
        session,
        site_id=payload.site_id,
        requested_by=principal.user.id,
        categories=payload.categories,
        delete_imported_bill_documents=payload.delete_imported_bill_documents,
        disconnected_sensor_policy=payload.disconnected_sensor_policy,
        offline_after_seconds=settings.device_offline_after_seconds,
        settings=settings,
    )
    session.add(
        audit_event(
            action="data_reset.plan_created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="data_reset_plan",
            object_id=plan.id,
            details={
                "site_id": plan.site_id,
                "categories": list(plan.requested_categories),
                "delete_imported_bill_documents": plan.delete_imported_bill_documents,
                "disconnected_sensor_policy": plan.disconnected_sensor_policy,
                "plan_revision": plan.revision,
                "plan_fingerprint": plan.plan_fingerprint,
            },
        )
    )
    await session.commit()
    return public_plan_payload(plan)


@router.post("/execute", status_code=202, response_model=DataResetOperationView)
async def execute_data_reset(
    payload: DataResetExecuteRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, Any]:
    plan = await session.get(DataResetPlan, payload.plan_id)
    if plan is None:
        raise ProblemError(
            404,
            "Reset plan not found",
            "Generate a new reset plan",
            "data_reset_plan_missing",
        )
    _authorize(principal, plan.site_id)
    require_recent_reauthentication(principal.session.reauthenticated_at)
    plan_site_id = plan.site_id
    existing_operation = await session.scalar(
        select(DataResetOperation).where(
            DataResetOperation.site_id == plan.site_id,
            DataResetOperation.idempotency_key == payload.idempotency_key,
        )
    )
    try:
        operation = await create_reset_operation(
            session,
            plan_id=payload.plan_id,
            plan_revision=payload.plan_revision,
            requested_by=principal.user.id,
            idempotency_key=payload.idempotency_key,
            reason=payload.reason,
            backup_mode=payload.backup_mode,
            confirmation_phrase=payload.confirmation_phrase,
            permanent_without_backup_acknowledged=(payload.permanent_without_backup_acknowledged),
            offline_after_seconds=settings.device_offline_after_seconds,
            settings=settings,
        )
    except ProblemError as exc:
        if exc.code in {"data_reset_plan_expired", "data_reset_plan_stale"}:
            now = datetime.now(UTC)
            if plan.invalidated_at is None:
                plan.invalidated_at = now
                plan.invalidation_reason = (
                    "expired" if exc.code == "data_reset_plan_expired" else "material_state_changed"
                )
            session.add(
                audit_event(
                    action=(
                        "data_reset.plan_expired"
                        if exc.code == "data_reset_plan_expired"
                        else "data_reset.plan_invalidated"
                    ),
                    actor_type="user",
                    actor_id=principal.user.id,
                    request=request,
                    object_type="data_reset_plan",
                    object_id=plan.id,
                    outcome="denied",
                    details={"site_id": plan.site_id, "reason_code": exc.code},
                )
            )
            await session.commit()
        raise
    except IntegrityError:
        await session.rollback()
        active = await session.scalar(
            select(DataResetOperation.id).where(
                DataResetOperation.site_id == plan_site_id,
                DataResetOperation.state.not_in(["completed", "cancelled", "failed_before_commit"]),
            )
        )
        if active is not None:
            raise ProblemError(
                409,
                "Data reset already active",
                "Wait for the existing site reset and every pending sensor to finish",
                "data_reset_active",
                extra={"operation_id": active},
            ) from None
        raise
    if existing_operation is None:
        session.add(
            audit_event(
                action="data_reset.execution_authorized",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="data_reset_operation",
                object_id=operation.id,
                details={
                    "site_id": operation.site_id,
                    "plan_id": operation.plan_id,
                    "plan_revision": operation.plan_revision,
                    "backup_mode": operation.backup_mode,
                    "categories": list(operation.requested_categories),
                    "reason": operation.reason,
                },
            )
        )
    await session.commit()
    return await operation_payload(session, operation)


@router.get("/{operation_id}", response_model=DataResetOperationView)
async def get_data_reset(
    operation_id: str,
    principal: Principal,
    session: DbSession,
) -> dict[str, Any]:
    operation = await session.get(DataResetOperation, operation_id)
    if operation is None:
        raise ProblemError(
            404,
            "Data reset not found",
            "The requested reset operation does not exist",
            "data_reset_operation_missing",
        )
    _authorize(principal, operation.site_id)
    return await operation_payload(session, operation)


@router.post("/{operation_id}/retry", response_model=DataResetOperationView)
async def retry_data_reset(
    operation_id: str,
    payload: DataResetRetryRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    operation = await _operation_for_update(session, operation_id)
    _authorize(principal, operation.site_id)
    require_recent_reauthentication(principal.session.reauthenticated_at)
    first_request = _record_action_idempotency(
        operation,
        action="retry",
        idempotency_key=payload.idempotency_key,
        reason=payload.reason,
    )
    if first_request:
        await retry_reset_operation(session, operation, now=datetime.now(UTC))
        session.add(
            audit_event(
                action="data_reset.retried",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="data_reset_operation",
                object_id=operation.id,
                details={"site_id": operation.site_id, "reason": " ".join(payload.reason.split())},
            )
        )
    await session.commit()
    return await operation_payload(session, operation)


@router.post("/{operation_id}/cancel", response_model=DataResetOperationView)
async def cancel_data_reset(
    operation_id: str,
    payload: DataResetCancelRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    operation = await _operation_for_update(session, operation_id)
    _authorize(principal, operation.site_id)
    require_recent_reauthentication(principal.session.reauthenticated_at)
    first_request = _record_action_idempotency(
        operation,
        action="cancel",
        idempotency_key=payload.idempotency_key,
        reason=payload.reason,
    )
    if first_request:
        await mark_cancel_requested(session, operation, now=datetime.now(UTC))
        session.add(
            audit_event(
                action="data_reset.cancel_requested",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="data_reset_operation",
                object_id=operation.id,
                details={"site_id": operation.site_id, "reason": " ".join(payload.reason.split())},
            )
        )
    await session.commit()
    return await operation_payload(session, operation)
