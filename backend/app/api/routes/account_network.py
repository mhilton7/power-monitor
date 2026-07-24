from __future__ import annotations

import ipaddress
from calendar import monthrange
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, select

from app.api.deps import AppSettings, CsrfPrincipal, DbSession, Principal, Viewer, audit_event
from app.db.models import (
    AggregateMember,
    AggregateSet,
    BillingCycle,
    CostCalculationRun,
    Device,
    DeviceAddress,
    DeviceHeartbeat,
    NetworkPolicyRevision,
    RateAssignment,
    RateChangeCandidate,
    RatePlan,
    RateVersion,
    RawReading,
    SensorNetworkCidr,
    SensorNetworkPolicy,
    Site,
    Utility,
    UtilityAccount,
    UtilityAccountAdjustment,
    UtilityAccountSiteAssignment,
)
from app.network_policy import (
    POLICY_MODES,
    canonical_ip,
    canonical_private_network,
    effective_client_ip,
    ensure_site_policies,
    evaluate_policy,
    policy_cidrs,
    policy_summary,
    private_sensor_address,
)
from app.problem import ProblemError
from app.rates.documents import engine_plan
from app.rates.engine import RateEngine
from app.rates.service import version_document
from app.schemas import (
    NetworkAddressTest,
    NetworkCidrWrite,
    NetworkPolicyWrite,
    RateAssignmentWrite,
    UtilityAccountRateContextView,
    UtilityAccountUpdate,
    UtilityAccountWizardCreate,
    UtilityAdjustmentWrite,
    UtilityCostScopeWrite,
)

router = APIRouter(prefix="/api/v1", tags=["utility accounts and sensor network"])


def _permission(principal: Principal, permission: str) -> None:
    if permission not in principal.permissions:
        raise ProblemError(
            403,
            "Permission denied",
            "Your account does not have the required permission",
            "forbidden",
            extra={"required_permission": permission},
        )


def _site_allowed(principal: Principal, site_id: str) -> None:
    if not principal.can_access_site(site_id):
        raise ProblemError(404, "Resource not found", "Resource does not exist", "resource_missing")


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _billing_cycle(account: UtilityAccount, now: datetime) -> dict[str, datetime]:
    zone = ZoneInfo(account.timezone)
    local_now = now.astimezone(zone)

    def boundary(year: int, month: int) -> datetime:
        day = min(account.billing_cycle_start_day, monthrange(year, month)[1])
        return datetime(year, month, day, tzinfo=zone)

    candidate = boundary(local_now.year, local_now.month)
    if local_now < candidate:
        previous_month = 12 if local_now.month == 1 else local_now.month - 1
        previous_year = local_now.year - 1 if local_now.month == 1 else local_now.year
        starts_at = boundary(previous_year, previous_month)
    else:
        starts_at = candidate
    next_month = 1 if starts_at.month == 12 else starts_at.month + 1
    next_year = starts_at.year + 1 if starts_at.month == 12 else starts_at.year
    ends_at = boundary(next_year, next_month)
    return {"starts_at": starts_at.astimezone(UTC), "ends_at": ends_at.astimezone(UTC)}


async def _version_live_context(session: DbSession, version: RateVersion) -> dict[str, Any]:
    now = datetime.now(UTC)
    document = await version_document(session, version)
    engine = RateEngine(engine_plan(document))
    tier_context_required = engine.pricing_model in {"tiered", "time_of_use_tiered"}
    if tier_context_required:
        period = "Account usage required"
        price = None
        next_at = None
        next_period = None
        next_price = None
    else:
        period, price = engine.period_at(now)
        next_at, next_period, next_price = engine.next_period_at(now)
    return {
        "current_period": period.replace("_", " ").title(),
        "current_price_per_kwh": str(price) if price is not None else None,
        "next_period": next_period.replace("_", " ").title() if next_period else None,
        "next_price_per_kwh": str(next_price) if next_price is not None else None,
        "next_period_at": next_at,
        "provider_mode": document.provider_mode,
        "account_adjustments": [
            {
                "name": item.name,
                "component": item.component,
                "value": str(item.value),
                "unit": item.unit,
                "scope": item.scope,
            }
            for item in document.adjustments
        ],
    }


async def _account_or_404(
    session: DbSession, principal: Principal, account_id: str
) -> UtilityAccount:
    account = await session.get(UtilityAccount, account_id)
    if account is None:
        raise ProblemError(
            404, "Account not found", "Utility account does not exist", "account_missing"
        )
    _site_allowed(principal, account.site_id)
    return account


async def _version_or_error(session: DbSession, version_id: str) -> RateVersion:
    version = await session.get(RateVersion, version_id)
    if version is None or version.status not in {"active", "approved"}:
        raise ProblemError(
            422,
            "Rate version unavailable",
            "Choose an approved or published rate version",
            "rate_version_unavailable",
        )
    return version


async def _account_assignments(session: DbSession, account_id: str) -> list[RateAssignment]:
    return list(
        await session.scalars(
            select(RateAssignment)
            .where(RateAssignment.utility_account_id == account_id)
            .order_by(RateAssignment.effective_from, RateAssignment.created_at)
        )
    )


async def _create_assignment(
    session: DbSession,
    account: UtilityAccount,
    payload: RateAssignmentWrite,
    actor_id: str,
) -> tuple[RateAssignment, bool]:
    # Serialize assignment-window changes for one account. Application-level
    # overlap checks alone are vulnerable to concurrent administrator saves.
    locked_account = await session.scalar(
        select(UtilityAccount).where(UtilityAccount.id == account.id).with_for_update()
    )
    if locked_account is None:
        raise ProblemError(404, "Utility account not found", "Unknown account", "not_found")
    account = locked_account
    version = await _version_or_error(session, payload.rate_version_id)
    start = _as_utc(payload.effective_from)
    end = _as_utc(payload.effective_to) if payload.effective_to else None
    assignments = await _account_assignments(session, account.id)
    close_previous: RateAssignment | None = None
    for item in assignments:
        item_start = (
            item.effective_from.replace(tzinfo=UTC)
            if item.effective_from.tzinfo is None
            else item.effective_from
        )
        item_end = item.effective_to
        if item_end and item_end.tzinfo is None:
            item_end = item_end.replace(tzinfo=UTC)
        overlaps = item_start < (end or datetime.max.replace(tzinfo=UTC)) and (
            item_end is None or item_end > start
        )
        if not overlaps:
            continue
        if item_end is None and item_start < start:
            close_previous = item
            continue
        raise ProblemError(
            409,
            "Rate assignment overlaps",
            "The account already has a rate assignment in this effective window",
            "rate_assignment_overlap",
            extra={"conflicting_assignment_id": item.id},
        )
    if close_previous:
        close_previous.effective_to = start
    assignment = RateAssignment(
        utility_account_id=account.id,
        rate_version_id=version.id,
        effective_from=start,
        effective_to=end,
        assignment_reason=payload.assignment_reason,
        assigned_by=actor_id,
        created_at=datetime.now(UTC),
    )
    session.add(assignment)
    now = datetime.now(UTC)
    effective_now = start <= now and (end is None or end > now)
    if effective_now:
        account.active_rate_version_id = version.id
    await session.flush()
    return assignment, effective_now


async def _rate_context(
    session: DbSession, account: UtilityAccount, assignments: list[RateAssignment]
) -> dict[str, Any]:
    now = datetime.now(UTC)

    def normalized(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    current = next(
        (
            item
            for item in reversed(assignments)
            if normalized(item.effective_from) <= now
            and (item.effective_to is None or normalized(item.effective_to) > now)
        ),
        None,
    )
    future = next((item for item in assignments if normalized(item.effective_from) > now), None)
    result: dict[str, Any] = {
        "state": "no_rate_assignment",
        "current_plan": None,
        "current_version": None,
        "current_period": None,
        "current_price_per_kwh": None,
        "next_period": None,
        "next_price_per_kwh": None,
        "next_period_at": None,
        "current_currency": account.currency,
        "billing_cycle": _billing_cycle(account, now),
        "assignment_effective_from": None,
        "next_assignment": None,
    }
    if future:
        future_version = await session.get(RateVersion, future.rate_version_id)
        future_plan = (
            await session.get(RatePlan, future_version.rate_plan_id) if future_version else None
        )
        result["next_assignment"] = {
            "rate_version_id": future.rate_version_id,
            "plan": future_plan.name if future_plan else "Scheduled rate",
            "effective_from": future.effective_from,
        }
    if current is None:
        if future:
            result["state"] = "rate_not_yet_effective"
        return result
    version = await session.get(RateVersion, current.rate_version_id)
    if version is None:
        return result
    plan = await session.get(RatePlan, version.rate_plan_id)
    result.update(
        {
            "state": "rate_configured_effective",
            "current_plan": plan.name if plan else "Rate plan",
            "plan_code": plan.code if plan else None,
            "current_version": version.version,
            "rate_version_id": version.id,
            "assignment_effective_from": current.effective_from,
            "source_type": version.source_kind,
            "source_checked_at": version.source_checked_at,
        }
    )
    try:
        result.update(await _version_live_context(session, version))
        if version.pricing_model in {"tiered", "time_of_use_tiered"}:
            from app.rates.tiered import calculate_cycle_tier_status, current_billing_cycle

            cycle = await current_billing_cycle(session, account, now, create=False)
            tier_status = await calculate_cycle_tier_status(session, account, cycle, persist=False)
            result["current_period"] = tier_status.get("current_rate_period")
            result["current_price_per_kwh"] = tier_status.get("current_energy_price")
            result["next_period"] = None
            result["next_price_per_kwh"] = None
            result["next_period_at"] = None
            result["tier_context_available"] = bool(tier_status.get("available"))
            result["tier_context_warning"] = next(iter(tier_status.get("warnings", [])), None)
    except (KeyError, RuntimeError, ValueError):
        result["state"] = "rate_configuration_invalid"
    return result


async def _topology_is_complete(session: DbSession, account_id: str) -> bool:
    aggregates = list(
        await session.scalars(
            select(AggregateSet).where(
                AggregateSet.utility_account_id == account_id,
                AggregateSet.cost_scope == "full_account",
            )
        )
    )
    if not aggregates:
        return False
    return bool(
        await session.scalar(
            select(func.count())
            .select_from(AggregateMember)
            .where(AggregateMember.aggregate_set_id.in_([item.id for item in aggregates]))
        )
    )


async def _account_view(session: DbSession, account: UtilityAccount) -> dict[str, Any]:
    site = await session.get(Site, account.site_id)
    utility = await session.get(Utility, account.utility_id)
    assignments = await _account_assignments(session, account.id)
    context = await _rate_context(session, account, assignments)
    device_count = int(
        await session.scalar(
            select(func.count()).select_from(Device).where(Device.utility_account_id == account.id)
        )
        or 0
    )
    reading_count = int(
        await session.scalar(
            select(func.count())
            .select_from(RawReading)
            .join(Device, Device.id == RawReading.device_id)
            .where(Device.utility_account_id == account.id)
        )
        or 0
    )
    return {
        "id": account.id,
        "site_id": account.site_id,
        "site_name": site.name if site else "Unknown site",
        "utility_id": account.utility_id,
        "utility_name": utility.name if utility else "Unknown utility",
        "name": account.name,
        "nickname": account.nickname,
        "account_number_suffix": account.account_number_suffix,
        "status": account.status,
        "timezone": account.timezone,
        "currency": account.currency,
        "billing_cycle_start_day": account.billing_cycle_start_day,
        "baseline_allocation_kwh": account.baseline_allocation_kwh,
        "generation_provider": account.generation_provider,
        "provider_mode": account.provider_mode,
        "service_class": account.service_class,
        "cost_scope": account.cost_scope_default,
        "allocation_method": account.allocation_method,
        "full_account_override": account.full_account_override,
        "revision": account.revision,
        "archived_at": account.archived_at,
        "rate_context": context,
        "assignment_count": len(assignments),
        "device_count": device_count,
        "readiness": {
            "rate": context["state"],
            "cost": "cost_calculation_ready"
            if context["state"] == "rate_configured_effective" and reading_count
            else "cost_blocked_missing_readings"
            if context["state"] == "rate_configured_effective"
            else "cost_blocked_rate_setup",
            "topology_complete": await _topology_is_complete(session, account.id),
        },
    }


@router.get("/rates/versions/{version_id}/current-context")
async def rate_version_current_context(
    version_id: str, principal: Viewer, session: DbSession
) -> dict[str, Any]:
    _permission(principal, "rates.view")
    version = await _version_or_error(session, version_id)
    plan = await session.get(RatePlan, version.rate_plan_id)
    context = await _version_live_context(session, version)
    return {
        "plan_code": plan.code if plan else None,
        "plan_name": plan.name if plan else None,
        "version": version.version,
        "effective_from": version.effective_from,
        "effective_through": version.effective_to,
        **context,
    }


@router.get(
    "/admin/utility-bill-import-context",
    response_model=UtilityAccountRateContextView,
)
async def utility_bill_import_context(
    principal: Viewer,
    session: DbSession,
    settings: AppSettings,
    account_id: str | None = None,
) -> UtilityAccountRateContextView:
    """Return one stable, explicit-null context for Custom Plan bill imports."""

    _permission(principal, "utility_bills.view")
    account_query = select(UtilityAccount).where(UtilityAccount.status == "active")
    if principal.site_ids:
        account_query = account_query.where(UtilityAccount.site_id.in_(principal.site_ids))
    accounts = list(await session.scalars(account_query.order_by(UtilityAccount.name)))
    views = [await _account_view(session, account) for account in accounts]
    summaries = [
        {
            "id": account.id,
            "site_id": account.site_id,
            "site_name": view["site_name"],
            "name": account.name,
            "utility_name": view["utility_name"],
            "timezone": account.timezone,
            "currency": account.currency,
            "provider_mode": account.provider_mode,
        }
        for account, view in zip(accounts, views, strict=True)
    ]

    selected = next((account for account in accounts if account.id == account_id), None)
    if account_id and selected is None:
        raise ProblemError(
            404,
            "Utility account not found",
            "The selected active utility account does not exist or is outside your site access",
            "utility_account_missing",
        )

    current_assignment = None
    current_version = None
    current_plan = None
    current_period = None
    if selected is not None:
        now = datetime.now(UTC)
        assignments = await _account_assignments(session, selected.id)

        def normalized(value: datetime) -> datetime:
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value

        current_assignment = next(
            (
                item
                for item in reversed(assignments)
                if normalized(item.effective_from) <= now
                and (item.effective_to is None or normalized(item.effective_to) > now)
            ),
            None,
        )
        if current_assignment is not None:
            current_version = await session.get(RateVersion, current_assignment.rate_version_id)
            current_plan = (
                await session.get(RatePlan, current_version.rate_plan_id)
                if current_version is not None
                else None
            )
            if current_version is not None:
                try:
                    live = await _version_live_context(session, current_version)
                except (KeyError, RuntimeError, ValueError):
                    live = {}
                label = live.get("current_period")
                if label is not None:
                    current_period = {
                        "label": str(label),
                        "price_per_kwh": live.get("current_price_per_kwh"),
                        "currency": current_version.currency,
                    }

    selected_summary = next(
        (summary for summary in summaries if summary["id"] == account_id),
        None,
    )
    return UtilityAccountRateContextView.model_validate(
        {
            "schema_version": "utility-account-rate-context/1.0",
            "api_version": "1.0.0",
            "backend_version": settings.power_monitor_version,
            "backend_commit": settings.release_commit,
            "generated_client_schema_version": "utility-account-rate-context/1.0",
            "account_id": selected.id if selected is not None else None,
            "site_id": selected.site_id if selected is not None else None,
            "account": selected_summary,
            "available_accounts": summaries,
            "current_plan": (
                {"id": current_plan.id, "code": current_plan.code, "name": current_plan.name}
                if current_plan is not None
                else None
            ),
            "current_assignment": (
                {
                    "id": current_assignment.id,
                    "rate_version_id": current_assignment.rate_version_id,
                    "effective_from": current_assignment.effective_from,
                    "effective_to": current_assignment.effective_to,
                }
                if current_assignment is not None
                else None
            ),
            "current_rate_version": (
                {
                    "id": current_version.id,
                    "version": current_version.version,
                    "pricing_model": current_version.pricing_model,
                    "effective_from": current_version.effective_from,
                    "effective_to": current_version.effective_to,
                    "status": current_version.status,
                }
                if current_version is not None
                else None
            ),
            "current_period": current_period,
            "readiness": {
                "account_configured": selected is not None,
                "rate_assigned": current_assignment is not None and current_version is not None,
                "rate_effective": (
                    current_assignment is not None
                    and current_version is not None
                    and current_plan is not None
                ),
            },
        }
    )


@router.get("/admin/sites/{site_id}/utility-accounts")
async def list_site_accounts(
    site_id: str, principal: Viewer, session: DbSession, include_archived: bool = False
) -> list[dict[str, Any]]:
    _permission(principal, "utility_accounts.view")
    _site_allowed(principal, site_id)
    query = select(UtilityAccount).where(UtilityAccount.site_id == site_id)
    if not include_archived:
        query = query.where(UtilityAccount.status == "active")
    accounts = list(await session.scalars(query.order_by(UtilityAccount.name)))
    return [await _account_view(session, account) for account in accounts]


@router.post("/admin/sites/{site_id}/utility-accounts", status_code=201)
async def create_site_account(
    site_id: str,
    payload: UtilityAccountWizardCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.manage")
    _permission(principal, "rates.assign")
    _site_allowed(principal, site_id)
    if not payload.confirmation:
        raise ProblemError(
            422, "Confirmation required", "Review and confirm the account", "confirmation_required"
        )
    site = await session.get(Site, site_id)
    if site is None:
        raise ProblemError(
            404, "Site not found", "The selected site does not exist", "site_missing"
        )
    if site.lifecycle_state != "active":
        raise ProblemError(
            409,
            "Active site required",
            "Enable the site before creating new utility-account assignments",
            "site_not_assignable",
        )
    utility_name = (
        "Southern California Edison"
        if payload.utility_provider in {"sce", "cca", "direct_access"}
        else "Custom/manual provider"
    )
    utility = await session.scalar(select(Utility).where(Utility.name == utility_name))
    if utility is None:
        utility = Utility(name=utility_name)
        session.add(utility)
        await session.flush()
    account = UtilityAccount(
        site_id=site.id,
        utility_id=utility.id,
        name=payload.name,
        nickname=payload.nickname,
        account_number_suffix=payload.account_number_suffix,
        status="active",
        timezone=site.timezone,
        currency=payload.currency,
        billing_cycle_start_day=payload.billing_cycle_start_day,
        baseline_allocation_kwh=payload.baseline_allocation_kwh,
        generation_provider=payload.generation_provider,
        provider_mode=payload.provider_mode,
        service_class=payload.service_class,
        cost_scope_default=payload.cost_scope,
        allocation_method=payload.allocation_method,
        full_account_override=payload.full_account_override,
        adjustment_config={},
        revision=1,
    )
    session.add(account)
    await session.flush()
    session.add(
        UtilityAccountSiteAssignment(
            utility_account_id=account.id,
            site_id=site.id,
            effective_from=datetime.now(UTC),
            assigned_by=principal.user.id,
            reason="Initial utility-account assignment",
            created_at=datetime.now(UTC),
        )
    )
    if payload.cost_scope == "allocated_account_estimate" and not payload.allocation_method:
        raise ProblemError(
            422,
            "Allocation method required",
            "Describe how account charges are allocated",
            "allocation_method_required",
        )
    if payload.cost_scope == "full_account_estimate" and not payload.full_account_override:
        raise ProblemError(
            422,
            "Complete topology required",
            "Complete-account estimates require a verified full-account topology "
            "or an explicit override",
            "incomplete_account_topology",
        )
    assignment, effective_now = await _create_assignment(
        session, account, payload.rate_assignment, principal.user.id
    )
    for item in payload.adjustments:
        session.add(
            UtilityAccountAdjustment(
                utility_account_id=account.id,
                component=item.component,
                value=item.value,
                unit=item.unit,
                provenance=item.provenance,
                effective_from=_as_utc(item.effective_from),
                effective_to=_as_utc(item.effective_to) if item.effective_to else None,
                enabled=item.enabled,
                created_by=principal.user.id,
                created_at=datetime.now(UTC),
            )
        )
    session.add(
        audit_event(
            action="utility_account.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_account",
            object_id=account.id,
            details={
                "site_id": site.id,
                "utility_provider": payload.utility_provider,
                "provider_mode": payload.provider_mode,
                "cost_scope": payload.cost_scope,
                "rate_assignment_id": assignment.id,
                "rate_version_id": assignment.rate_version_id,
                "effective_now": effective_now,
                "adjustment_count": len(payload.adjustments),
                "account_number_stored": False,
            },
        )
    )
    session.add(
        audit_event(
            action="rate_assignment.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_assignment",
            object_id=assignment.id,
            details={
                "utility_account_id": account.id,
                "rate_version_id": assignment.rate_version_id,
            },
        )
    )
    await session.commit()
    return await _account_view(session, account)


@router.get("/admin/utility-accounts/{account_id}")
async def get_account(account_id: str, principal: Viewer, session: DbSession) -> dict[str, Any]:
    _permission(principal, "utility_accounts.view")
    return await _account_view(session, await _account_or_404(session, principal, account_id))


@router.put("/admin/utility-accounts/{account_id}")
async def update_account(
    account_id: str,
    payload: UtilityAccountUpdate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.manage")
    account = await _account_or_404(session, principal, account_id)
    if account.revision != payload.revision:
        raise ProblemError(409, "Account changed", "Reload before saving", "stale_revision")
    for key, value in payload.model_dump(exclude={"revision"}, exclude_unset=True).items():
        setattr(account, key, value)
    account.revision += 1
    session.add(
        audit_event(
            action="utility_account.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_account",
            object_id=account.id,
            details={"revision": account.revision},
        )
    )
    await session.commit()
    return await _account_view(session, account)


@router.post("/admin/utility-accounts/{account_id}/archive")
async def archive_account(
    account_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.manage")
    account = await _account_or_404(session, principal, account_id)
    if account.status != "archived":
        account.status = "archived"
        account.archived_at = datetime.now(UTC)
        account.archived_by = principal.user.id
        account.revision += 1
        session.add(
            audit_event(
                action="utility_account.archived",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="utility_account",
                object_id=account.id,
                details={"history_preserved": True},
            )
        )
        await session.commit()
    return await _account_view(session, account)


@router.get("/admin/utility-accounts/{account_id}/rate-assignments")
async def account_rate_assignments(
    account_id: str, principal: Viewer, session: DbSession
) -> list[dict[str, Any]]:
    _permission(principal, "utility_accounts.view")
    await _account_or_404(session, principal, account_id)
    result = []
    for item in await _account_assignments(session, account_id):
        version = await session.get(RateVersion, item.rate_version_id)
        plan = await session.get(RatePlan, version.rate_plan_id) if version else None
        result.append(
            {
                "id": item.id,
                "rate_version_id": item.rate_version_id,
                "plan_code": plan.code if plan else None,
                "plan_name": plan.name if plan else None,
                "version": version.version if version else None,
                "status": version.status if version else "missing",
                "effective_from": item.effective_from,
                "effective_to": item.effective_to,
                "assignment_reason": item.assignment_reason,
                "created_at": item.created_at,
            }
        )
    return result


@router.post("/admin/utility-accounts/{account_id}/rate-assignments", status_code=201)
async def add_account_rate_assignment(
    account_id: str,
    payload: RateAssignmentWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "rates.assign")
    account = await _account_or_404(session, principal, account_id)
    assignment, effective_now = await _create_assignment(
        session, account, payload, principal.user.id
    )
    session.add(
        audit_event(
            action="rate_assignment.created" if effective_now else "rate_assignment.scheduled",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_assignment",
            object_id=assignment.id,
            details={
                "utility_account_id": account.id,
                "rate_version_id": assignment.rate_version_id,
            },
        )
    )
    await session.commit()
    return {
        "id": assignment.id,
        "effective_from": assignment.effective_from,
        "effective_now": effective_now,
    }


@router.post("/admin/utility-accounts/{account_id}/cost-scope")
async def change_cost_scope(
    account_id: str,
    payload: UtilityCostScopeWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.manage")
    account = await _account_or_404(session, principal, account_id)
    if account.revision != payload.revision:
        raise ProblemError(409, "Account changed", "Reload before saving", "stale_revision")
    if payload.cost_scope == "allocated_account_estimate" and not payload.allocation_method:
        raise ProblemError(
            422,
            "Allocation method required",
            "Describe the allocation method",
            "allocation_method_required",
        )
    topology_complete = await _topology_is_complete(session, account.id)
    if payload.cost_scope == "full_account_estimate" and not (
        topology_complete or payload.full_account_override
    ):
        raise ProblemError(
            422,
            "Complete topology required",
            "Verify account coverage or explicitly confirm the override",
            "incomplete_account_topology",
        )
    account.cost_scope_default = payload.cost_scope
    account.allocation_method = payload.allocation_method
    account.full_account_override = payload.full_account_override
    account.revision += 1
    session.add(
        audit_event(
            action="utility_account.cost_scope_changed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_account",
            object_id=account.id,
            details={
                "cost_scope": payload.cost_scope,
                "topology_complete": topology_complete,
                "override": payload.full_account_override,
            },
        )
    )
    await session.commit()
    return await _account_view(session, account)


@router.post("/admin/utility-accounts/{account_id}/recalculate", status_code=202)
async def recalculate_account_costs(
    account_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.manage")
    account = await _account_or_404(session, principal, account_id)
    finalized = list(
        await session.scalars(
            select(BillingCycle).where(
                BillingCycle.utility_account_id == account.id,
                BillingCycle.finalized_at.is_not(None),
            )
        )
    )
    queued = 0
    for run in await session.scalars(
        select(CostCalculationRun).where(CostCalculationRun.utility_account_id == account.id)
    ):
        protected = any(
            run.input_start < cycle.ends_at and run.input_end > cycle.starts_at
            for cycle in finalized
        )
        if not protected:
            run.status = "queued"
            queued += 1
    session.add(
        audit_event(
            action="utility_account.cost_recalculation_requested",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_account",
            object_id=account.id,
            details={"queued_runs": queued, "finalized_cycles_preserved": len(finalized)},
        )
    )
    await session.commit()
    return {"status": "queued", "queued_runs": queued, "finalized_cycles_preserved": len(finalized)}


@router.get("/admin/utility-accounts/{account_id}/adjustments")
async def list_account_adjustments(
    account_id: str, principal: Viewer, session: DbSession
) -> list[dict[str, Any]]:
    _permission(principal, "utility_accounts.view")
    await _account_or_404(session, principal, account_id)
    rows = await session.scalars(
        select(UtilityAccountAdjustment)
        .where(UtilityAccountAdjustment.utility_account_id == account_id)
        .order_by(UtilityAccountAdjustment.effective_from.desc())
    )
    return [
        {
            "id": item.id,
            "component": item.component,
            "value": item.value,
            "unit": item.unit,
            "provenance": item.provenance,
            "effective_from": item.effective_from,
            "effective_to": item.effective_to,
            "enabled": item.enabled,
        }
        for item in rows
    ]


@router.post("/admin/utility-accounts/{account_id}/adjustments", status_code=201)
async def add_account_adjustment(
    account_id: str,
    payload: UtilityAdjustmentWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.manage")
    account = await _account_or_404(session, principal, account_id)
    item = UtilityAccountAdjustment(
        utility_account_id=account.id,
        component=payload.component,
        value=payload.value,
        unit=payload.unit,
        provenance=payload.provenance,
        effective_from=_as_utc(payload.effective_from),
        effective_to=_as_utc(payload.effective_to) if payload.effective_to else None,
        enabled=payload.enabled,
        created_by=principal.user.id,
        created_at=datetime.now(UTC),
    )
    session.add(item)
    account.revision += 1
    session.add(
        audit_event(
            action="utility_account.adjustment_added",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_account_adjustment",
            object_id=item.id,
            details={
                "utility_account_id": account.id,
                "component": item.component,
                "unit": item.unit,
                "provenance": item.provenance,
            },
        )
    )
    await session.commit()
    return {
        "id": item.id,
        "component": item.component,
        "value": item.value,
        "unit": item.unit,
        "provenance": item.provenance,
        "effective_from": item.effective_from,
        "effective_to": item.effective_to,
        "enabled": item.enabled,
    }


@router.get("/sites/{site_id}/setup-readiness")
async def setup_readiness(
    site_id: str, principal: Viewer, session: DbSession, settings: AppSettings
) -> dict[str, Any]:
    _permission(principal, "sites.view")
    _site_allowed(principal, site_id)
    devices = list(
        await session.scalars(
            select(Device).where(Device.site_id == site_id, Device.lifecycle_status == "active")
        )
    )
    account_rows = list(
        await session.scalars(
            select(UtilityAccount).where(
                UtilityAccount.site_id == site_id, UtilityAccount.status == "active"
            )
        )
    )
    latest = await session.scalar(
        select(DeviceHeartbeat.received_at)
        .join(Device, Device.id == DeviceHeartbeat.device_id)
        .where(Device.site_id == site_id)
        .order_by(DeviceHeartbeat.received_at.desc())
        .limit(1)
    )
    account_views = [await _account_view(session, item) for item in account_rows]
    effective = [
        item
        for item in account_views
        if item["rate_context"]["state"] == "rate_configured_effective"
    ]
    pending_rate_candidates = int(
        await session.scalar(
            select(func.count())
            .select_from(RateChangeCandidate)
            .where(RateChangeCandidate.status == "pending_review")
        )
        or 0
    )
    latest_aware = latest.replace(tzinfo=UTC) if latest and latest.tzinfo is None else latest
    stale = bool(
        latest_aware
        and latest_aware
        < datetime.now(UTC) - timedelta(seconds=settings.heartbeat_expectation_seconds * 4)
    )
    monitoring_state = (
        "no_sensors_enrolled"
        if not devices
        else "waiting_for_first_signed_heartbeat"
        if latest is None
        else "sensor_data_stale_or_unavailable"
        if stale
        else "sensors_reporting"
    )
    rate_state = (
        "no_utility_account"
        if not account_rows
        else "rate_configured_effective"
        if effective
        else "rate_candidate_awaiting_approval"
        if pending_rate_candidates
        else "utility_account_without_effective_rate"
    )
    cost_ready = sum(
        1 for item in account_views if item["readiness"]["cost"] == "cost_calculation_ready"
    )
    cost_state = (
        "cost_calculation_ready"
        if cost_ready
        else "cost_calculation_blocked_missing_readings"
        if effective
        else "cost_calculation_blocked_invalid_configuration"
    )
    return {
        "site_id": site_id,
        "monitoring": {
            "state": monitoring_state,
            "device_count": len(devices),
            "latest_signed_heartbeat_at": latest,
            "action": "/enrollment" if not devices else None,
        },
        "rate_and_cost": {
            "state": rate_state,
            "cost_state": cost_state,
            "account_count": len(account_rows),
            "effective_account_count": len(effective),
            "cost_ready_account_count": cost_ready,
            "pending_candidate_count": pending_rate_candidates,
            "action": f"/billing/accounts?site={site_id}" if not effective else None,
        },
    }


async def _policy_view(session: DbSession, policy: SensorNetworkPolicy) -> dict[str, Any]:
    site = await session.get(Site, policy.site_id)
    cidrs = await policy_cidrs(session, policy.id)
    return {
        "id": policy.id,
        "site_id": policy.site_id,
        "site_name": site.name if site else "Unknown site",
        "direction": policy.direction,
        "mode": policy.mode,
        "revision": policy.revision,
        "migration_notice_pending": policy.migration_notice_pending,
        "migrated_from_legacy": policy.migrated_from_legacy,
        "effective_summary": policy_summary(policy, sum(1 for item in cidrs if item.enabled)),
        "cidrs": [
            {
                "id": item.id,
                "network": item.network,
                "label": item.label,
                "enabled": item.enabled,
                "revision": item.revision,
            }
            for item in cidrs
        ],
    }


@router.get("/admin/network/policies")
async def list_network_policies(principal: Viewer, session: DbSession) -> list[dict[str, Any]]:
    _permission(principal, "network.view")
    query = select(Site).order_by(Site.name)
    if not principal.all_sites:
        query = query.where(Site.id.in_(principal.site_ids))
    policies: list[SensorNetworkPolicy] = []
    for site in await session.scalars(query):
        policies.extend(await ensure_site_policies(session, site))
    await session.commit()
    return [await _policy_view(session, item) for item in policies]


@router.get("/admin/network/policies/{policy_id}")
async def get_network_policy(
    policy_id: str, principal: Viewer, session: DbSession
) -> dict[str, Any]:
    _permission(principal, "network.view")
    policy = await session.get(SensorNetworkPolicy, policy_id)
    if policy is None:
        raise ProblemError(
            404,
            "Policy not found",
            "Sensor network policy does not exist",
            "network_policy_missing",
        )
    _site_allowed(principal, policy.site_id)
    return await _policy_view(session, policy)


async def _record_policy_revision(
    session: DbSession, policy: SensorNetworkPolicy, actor_id: str, reason: str | None
) -> None:
    cidrs = await policy_cidrs(session, policy.id)
    session.add(
        NetworkPolicyRevision(
            policy_id=policy.id,
            revision=policy.revision,
            mode=policy.mode,
            cidrs=[
                {"network": item.network, "label": item.label, "enabled": item.enabled}
                for item in cidrs
            ],
            changed_by=actor_id,
            changed_at=datetime.now(UTC),
            reason=reason,
        )
    )


@router.put("/admin/network/policies/{policy_id}")
async def update_network_policy(
    policy_id: str,
    payload: NetworkPolicyWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "network.manage")
    policy = await session.get(SensorNetworkPolicy, policy_id)
    if policy is None:
        raise ProblemError(
            404,
            "Policy not found",
            "Sensor network policy does not exist",
            "network_policy_missing",
        )
    _site_allowed(principal, policy.site_id)
    if policy.revision != payload.revision:
        raise ProblemError(409, "Policy changed", "Reload before saving", "stale_revision")
    if payload.mode not in POLICY_MODES:
        raise ProblemError(
            422, "Invalid policy", "Choose a supported policy mode", "invalid_policy_mode"
        )
    cidrs = await policy_cidrs(session, policy.id)
    if payload.mode == "allow_listed_private" and not any(item.enabled for item in cidrs):
        raise ProblemError(
            422,
            "CIDR required",
            "Add and enable a private CIDR before selecting listed networks",
            "network_cidr_required",
        )
    previous = policy.mode
    policy.mode = payload.mode
    policy.revision += 1
    policy.migration_notice_pending = False
    policy.updated_by = principal.user.id
    await _record_policy_revision(session, policy, principal.user.id, payload.reason)
    session.add(
        audit_event(
            action="network_policy.deny_all_enabled"
            if payload.mode == "deny_all"
            else "network_policy.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="sensor_network_policy",
            object_id=policy.id,
            details={
                "direction": policy.direction,
                "previous_mode": previous,
                "mode": policy.mode,
                "revision": policy.revision,
            },
        )
    )
    await session.commit()
    return await _policy_view(session, policy)


@router.get("/admin/network/cidrs")
async def list_network_cidrs(
    principal: Viewer, session: DbSession, policy_id: str | None = Query(default=None)
) -> list[dict[str, Any]]:
    _permission(principal, "network.view")
    query = select(SensorNetworkCidr).order_by(SensorNetworkCidr.network)
    if policy_id:
        policy = await session.get(SensorNetworkPolicy, policy_id)
        if policy is None:
            raise ProblemError(
                404,
                "Policy not found",
                "Sensor network policy does not exist",
                "network_policy_missing",
            )
        _site_allowed(principal, policy.site_id)
        query = query.where(SensorNetworkCidr.policy_id == policy_id)
    elif not principal.all_sites:
        policy_ids = select(SensorNetworkPolicy.id).where(
            SensorNetworkPolicy.site_id.in_(principal.site_ids)
        )
        query = query.where(SensorNetworkCidr.policy_id.in_(policy_ids))
    return [
        {
            "id": item.id,
            "policy_id": item.policy_id,
            "network": item.network,
            "label": item.label,
            "enabled": item.enabled,
            "revision": item.revision,
        }
        for item in await session.scalars(query)
    ]


@router.post("/admin/network/cidrs", status_code=201)
async def add_network_cidr(
    payload: NetworkCidrWrite, request: Request, principal: CsrfPrincipal, session: DbSession
) -> dict[str, Any]:
    _permission(principal, "network.manage")
    policy = await session.get(SensorNetworkPolicy, payload.policy_id)
    if policy is None:
        raise ProblemError(
            404,
            "Policy not found",
            "Sensor network policy does not exist",
            "network_policy_missing",
        )
    _site_allowed(principal, policy.site_id)
    network = canonical_private_network(payload.network)
    existing = await session.scalar(
        select(SensorNetworkCidr).where(
            SensorNetworkCidr.policy_id == policy.id, SensorNetworkCidr.network == network
        )
    )
    if existing:
        raise ProblemError(
            409,
            "CIDR already exists",
            "This policy already contains the normalized CIDR",
            "duplicate_cidr",
        )
    candidate = ipaddress.ip_network(network)
    overlaps = [
        item
        for item in await policy_cidrs(session, policy.id)
        if candidate.overlaps(ipaddress.ip_network(item.network))
    ]
    item = SensorNetworkCidr(
        policy_id=policy.id,
        network=network,
        label=payload.label,
        enabled=payload.enabled,
        revision=1,
    )
    session.add(item)
    policy.revision += 1
    await session.flush()
    await _record_policy_revision(session, policy, principal.user.id, "CIDR added")
    session.add(
        audit_event(
            action="network_cidr.added",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="sensor_network_cidr",
            object_id=item.id,
            details={"policy_id": policy.id, "network": network, "overlap_count": len(overlaps)},
        )
    )
    await session.commit()
    return {
        "id": item.id,
        "policy_id": item.policy_id,
        "network": item.network,
        "label": item.label,
        "enabled": item.enabled,
        "revision": item.revision,
        "warnings": [f"Overlaps {entry.label} ({entry.network})" for entry in overlaps],
    }


@router.put("/admin/network/cidrs/{cidr_id}")
async def update_network_cidr(
    cidr_id: str,
    payload: NetworkCidrWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "network.manage")
    item = await session.get(SensorNetworkCidr, cidr_id)
    if item is None:
        raise ProblemError(
            404, "CIDR not found", "Sensor network CIDR does not exist", "network_cidr_missing"
        )
    policy = await session.get(SensorNetworkPolicy, item.policy_id)
    if policy is None:
        raise ProblemError(
            404,
            "Policy not found",
            "Sensor network policy does not exist",
            "network_policy_missing",
        )
    _site_allowed(principal, policy.site_id)
    if payload.revision is None or item.revision != payload.revision:
        raise ProblemError(409, "CIDR changed", "Reload before saving", "stale_revision")
    network = canonical_private_network(payload.network)
    duplicate = await session.scalar(
        select(SensorNetworkCidr.id).where(
            SensorNetworkCidr.policy_id == policy.id,
            SensorNetworkCidr.network == network,
            SensorNetworkCidr.id != item.id,
        )
    )
    if duplicate:
        raise ProblemError(
            409,
            "CIDR already exists",
            "This policy already contains the normalized CIDR",
            "duplicate_cidr",
        )
    if policy.mode == "allow_listed_private" and item.enabled and not payload.enabled:
        enabled_count = int(
            await session.scalar(
                select(func.count())
                .select_from(SensorNetworkCidr)
                .where(
                    SensorNetworkCidr.policy_id == policy.id,
                    SensorNetworkCidr.enabled.is_(True),
                )
            )
            or 0
        )
        if enabled_count <= 1:
            raise ProblemError(
                409,
                "CIDR is required",
                "Change the policy mode before disabling its last enabled CIDR",
                "network_cidr_required",
            )
    candidate = ipaddress.ip_network(network)
    overlaps = [
        other
        for other in await policy_cidrs(session, policy.id)
        if other.id != item.id and candidate.overlaps(ipaddress.ip_network(other.network))
    ]
    item.network = network
    item.label = payload.label
    item.enabled = payload.enabled
    item.revision += 1
    policy.revision += 1
    await _record_policy_revision(session, policy, principal.user.id, "CIDR edited")
    session.add(
        audit_event(
            action="network_cidr.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="sensor_network_cidr",
            object_id=item.id,
            details={"network": network, "overlap_count": len(overlaps)},
        )
    )
    await session.commit()
    return {
        "id": item.id,
        "policy_id": item.policy_id,
        "network": item.network,
        "label": item.label,
        "enabled": item.enabled,
        "revision": item.revision,
        "warnings": [f"Overlaps {entry.label} ({entry.network})" for entry in overlaps],
    }


@router.delete("/admin/network/cidrs/{cidr_id}", status_code=204, response_class=Response)
async def delete_network_cidr(
    cidr_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> Response:
    _permission(principal, "network.manage")
    item = await session.get(SensorNetworkCidr, cidr_id)
    if item is None:
        return Response(status_code=204)
    policy = await session.get(SensorNetworkPolicy, item.policy_id)
    if policy is None:
        return Response(status_code=204)
    _site_allowed(principal, policy.site_id)
    if policy.mode == "allow_listed_private" and item.enabled:
        enabled_count = int(
            await session.scalar(
                select(func.count())
                .select_from(SensorNetworkCidr)
                .where(
                    SensorNetworkCidr.policy_id == policy.id, SensorNetworkCidr.enabled.is_(True)
                )
            )
            or 0
        )
        if enabled_count <= 1:
            raise ProblemError(
                409,
                "CIDR is required",
                "Change the policy mode before removing its last enabled CIDR",
                "network_cidr_required",
            )
    details: dict[str, object] = {"network": item.network, "policy_id": policy.id}
    await session.delete(item)
    policy.revision += 1
    await session.flush()
    await _record_policy_revision(session, policy, principal.user.id, "CIDR removed")
    session.add(
        audit_event(
            action="network_cidr.removed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="sensor_network_cidr",
            object_id=cidr_id,
            details=details,
        )
    )
    await session.commit()
    return Response(status_code=204)


@router.post("/admin/network/test-address")
async def test_network_address(
    payload: NetworkAddressTest, request: Request, principal: CsrfPrincipal, session: DbSession
) -> dict[str, Any]:
    _permission(principal, "network.view")
    policy = await session.get(SensorNetworkPolicy, payload.policy_id)
    if policy is None:
        raise ProblemError(
            404,
            "Policy not found",
            "Sensor network policy does not exist",
            "network_policy_missing",
        )
    _site_allowed(principal, policy.site_id)
    decision = await evaluate_policy(session, policy, payload.address)
    session.add(
        audit_event(
            action="network_policy.address_tested",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="sensor_network_policy",
            object_id=policy.id,
            details={
                "direction": policy.direction,
                "address": decision.address,
                "allowed": decision.allowed,
                "matching_rule": decision.matching_rule,
            },
        )
    )
    await session.commit()
    return decision.__dict__


@router.get("/admin/network/observed-devices")
async def observed_devices(
    principal: Viewer, session: DbSession, site_id: str | None = Query(default=None)
) -> list[dict[str, Any]]:
    _permission(principal, "network.view")
    query = (
        select(DeviceAddress, Device)
        .join(Device, Device.id == DeviceAddress.device_id)
        .where(DeviceAddress.source == "heartbeat")
    )
    if site_id:
        _site_allowed(principal, site_id)
        query = query.where(Device.site_id == site_id)
    elif not principal.all_sites:
        query = query.where(Device.site_id.in_(principal.site_ids))
    query = query.order_by(DeviceAddress.last_seen_at.desc())
    return [
        {
            "device_id": device.id,
            "device_name": device.name,
            "site_id": device.site_id,
            "address": address.host,
            "last_seen_at": address.last_seen_at,
            "validation_error": address.validation_error,
        }
        for address, device in (await session.execute(query)).all()
    ]


@router.get("/admin/network/suggest-current")
async def suggest_current_network(
    request: Request, principal: Viewer, session: DbSession, settings: AppSettings
) -> dict[str, Any]:
    _permission(principal, "network.view")
    direct = request.client.host if request.client else ""
    try:
        resolved = effective_client_ip(
            direct,
            request.headers.get("x-forwarded-for"),
            settings.trusted_proxy_cidrs,
        )
        address = canonical_ip(resolved)
    except ProblemError:
        return {"available": False, "reason": "The direct client address is unavailable."}
    if not private_sensor_address(address):
        return {
            "available": False,
            "reason": (
                "A safe private client network could not be determined. "
                "Enter the sensor VLAN CIDR manually."
            ),
        }
    prefix = 24 if address.version == 4 else 64
    network = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
    return {
        "available": True,
        "address": str(address),
        "proposed_cidr": str(network),
        "requires_confirmation": True,
        "forwarded_headers_used": str(address) != direct,
    }


@router.get("/admin/network/runtime")
async def network_runtime(principal: Viewer, settings: AppSettings) -> dict[str, Any]:
    _permission(principal, "network.view")
    return {
        "sensor_server_url": settings.public_origin,
        "tls_verification_required": True,
        "certificate_trust": "Deployment-managed CA or publicly trusted certificate",
        "default_device_api_port": 443,
        "communication_modes": ["push", "pull", "hybrid"],
        "mdns_authoritative": False,
        "heartbeat_expectation_seconds": settings.heartbeat_expectation_seconds,
        "stale_device_seconds": settings.heartbeat_expectation_seconds * 4,
        "server_time": datetime.now(UTC),
        "server_timezone": "UTC",
        "trusted_forwarded_headers": bool(settings.trusted_proxy_cidrs.strip()),
        "address_source": "Signed device heartbeat",
    }
