from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import CsrfPrincipal, DbSession, Principal, Viewer, audit_event
from app.db.models import (
    AggregateSet,
    BaselineRule,
    BillingCycle,
    CostCalculationRun,
    CostIntervalResult,
    FixedChargeRule,
    RateDayType,
    RatePeriod,
    RatePlan,
    RateSeason,
    RateVersion,
    UtilityAccount,
)
from app.problem import ProblemError
from app.rates.engine import RateEngine, load_seed_plans
from app.schemas import (
    CostComponent,
    CostRecalculationRequest,
    RatePreviewRequest,
    RatePreviewResponse,
)

router = APIRouter(prefix="/api/v1", tags=["rates and billing"])
DISCLOSURE = (
    "Estimate, not utility bill. Configured monitored energy and rates may differ because of "
    "meter accuracy, missing circuits, baseline allocation, generation provider, taxes, credits, "
    "rounding, tariff changes, and billing adjustments."
)


def _operator(principal: Principal) -> None:
    if not principal.roles.intersection({"admin", "operator"}):
        raise ProblemError(403, "Permission denied", "Operator access is required", "forbidden")


@router.get("/rate-plans")
async def list_rate_plans(_viewer: Viewer, session: DbSession) -> list[dict[str, Any]]:
    plans = list(await session.scalars(select(RatePlan).order_by(RatePlan.code)))
    output: list[dict[str, Any]] = []
    for plan in plans:
        versions = list(
            await session.scalars(
                select(RateVersion)
                .where(RateVersion.rate_plan_id == plan.id)
                .order_by(RateVersion.version.desc())
            )
        )
        output.append(
            {
                "id": plan.id,
                "code": plan.code,
                "name": plan.name,
                "description": plan.description,
                "versions": [
                    {
                        "id": version.id,
                        "version": version.version,
                        "effective_from": version.effective_from,
                        "effective_to": version.effective_to,
                        "timezone": version.timezone,
                        "currency": version.currency,
                        "source_url": version.source_url,
                        "source_checked_on": version.source_checked_on,
                        "source_notes": version.source_notes,
                        "content_hash": version.content_hash,
                        "immutable_after_use": version.immutable_after_use,
                        "is_active": version.is_active,
                    }
                    for version in versions
                ],
            }
        )
    return output


@router.get("/rate-plans/{rate_plan_id}/export")
async def export_rate_plan(
    rate_plan_id: str, _viewer: Viewer, session: DbSession
) -> dict[str, Any]:
    plan = await session.get(RatePlan, rate_plan_id)
    if plan is None:
        raise ProblemError(
            404, "Rate plan not found", "Rate plan does not exist", "rate_plan_missing"
        )
    version = await session.scalar(
        select(RateVersion)
        .where(RateVersion.rate_plan_id == plan.id)
        .order_by(RateVersion.version.desc())
        .limit(1)
    )
    if version is None:
        raise ProblemError(
            404, "Rate version not found", "Rate plan has no versions", "rate_version_missing"
        )
    periods = list(
        await session.scalars(
            select(RatePeriod)
            .where(RatePeriod.rate_version_id == version.id)
            .order_by(RatePeriod.season_name, RatePeriod.day_type, RatePeriod.start_minute)
        )
    )
    payload = {
        "schema_version": "rate-version/1.0.0",
        "code": plan.code,
        "name": plan.name,
        "version": version.version,
        "effective_from": version.effective_from.isoformat(),
        "effective_to": version.effective_to.isoformat() if version.effective_to else None,
        "timezone": version.timezone,
        "currency": version.currency,
        "source_url": version.source_url,
        "source_checked_on": version.source_checked_on.isoformat(),
        "periods": [
            {
                "season": period.season_name,
                "day_type": period.day_type,
                "start_minute": period.start_minute,
                "end_minute": period.end_minute,
                "bucket": period.bucket,
                "price_per_kwh": str(period.price_per_kwh),
            }
            for period in periods
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return {"rate_version": payload, "sha256": hashlib.sha256(canonical).hexdigest()}


@router.post("/rate-plans/{rate_plan_id}/versions", status_code=201)
async def clone_rate_version(
    rate_plan_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _operator(principal)
    plan = await session.get(RatePlan, rate_plan_id)
    if plan is None:
        raise ProblemError(
            404, "Rate plan not found", "Rate plan does not exist", "rate_plan_missing"
        )
    required = {
        "effective_from",
        "timezone",
        "currency",
        "source_url",
        "source_checked_on",
        "periods",
    }
    if missing := sorted(required - payload.keys()):
        raise ProblemError(
            422,
            "Invalid rate version",
            f"Missing fields: {', '.join(missing)}",
            "invalid_rate_version",
        )
    periods = payload["periods"]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for period in periods:
        key = (str(period["season"]), str(period["day_type"]))
        grouped.setdefault(key, []).append(period)
    for key, values in grouped.items():
        cursor = 0
        for period in sorted(values, key=lambda item: int(item["start_minute"])):
            if int(period["start_minute"]) != cursor or int(period["end_minute"]) <= cursor:
                raise ProblemError(
                    422, "Invalid rate periods", f"{key} is not contiguous", "rate_period_gap"
                )
            cursor = int(period["end_minute"])
        if cursor != 1440:
            raise ProblemError(
                422, "Invalid rate periods", f"{key} does not cover 24 hours", "rate_period_gap"
            )
    latest = await session.scalar(
        select(RateVersion.version)
        .where(RateVersion.rate_plan_id == plan.id)
        .order_by(RateVersion.version.desc())
        .limit(1)
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    version = RateVersion(
        rate_plan_id=plan.id,
        version=int(latest or 0) + 1,
        effective_from=datetime.fromisoformat(payload["effective_from"]).date(),
        effective_to=(
            datetime.fromisoformat(payload["effective_to"]).date()
            if payload.get("effective_to")
            else None
        ),
        timezone=payload["timezone"],
        currency=payload["currency"],
        source_url=payload["source_url"],
        source_checked_on=datetime.fromisoformat(payload["source_checked_on"]).date(),
        source_notes=payload.get("source_notes", "User-created clone; verify before activation"),
        content_hash=hashlib.sha256(canonical).hexdigest(),
        is_active=False,
        created_at=datetime.now(UTC),
        created_by=principal.user.id,
    )
    session.add(version)
    await session.flush()
    seasons = payload.get(
        "seasons",
        {
            "summer": {"start": "06-01", "end": "09-30"},
            "winter": {"start": "10-01", "end": "05-31"},
        },
    )
    for name, values in seasons.items():
        start_month, start_day = (int(part) for part in values["start"].split("-"))
        end_month, end_day = (int(part) for part in values["end"].split("-"))
        session.add(
            RateSeason(
                rate_version_id=version.id,
                name=name,
                start_month=start_month,
                start_day=start_day,
                end_month=end_month,
                end_day=end_day,
            )
        )
    for name, weekdays in (("weekday", [0, 1, 2, 3, 4]), ("weekend", [5, 6])):
        session.add(
            RateDayType(
                rate_version_id=version.id,
                name=name,
                weekdays=weekdays,
                holiday_behavior=payload.get("holiday_behavior", "weekday"),
                holiday_source=payload.get("holiday_source", "User configured"),
            )
        )
    for period in periods:
        session.add(
            RatePeriod(
                rate_version_id=version.id,
                season_name=period["season"],
                day_type=period["day_type"],
                start_minute=int(period["start_minute"]),
                end_minute=int(period["end_minute"]),
                bucket=period["bucket"],
                price_per_kwh=Decimal(str(period["price_per_kwh"])),
            )
        )
    if payload.get("base_service_charge_per_day") is not None:
        session.add(
            FixedChargeRule(
                rate_version_id=version.id,
                name="Base service charge",
                amount_per_day=Decimal(str(payload["base_service_charge_per_day"])),
            )
        )
    if payload.get("baseline_credit_per_kwh") is not None:
        session.add(
            BaselineRule(
                rate_version_id=version.id,
                credit_per_kwh=Decimal(str(payload["baseline_credit_per_kwh"])),
            )
        )
    session.add(
        audit_event(
            action="rate_version.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_version",
            object_id=version.id,
            details={"content_hash": version.content_hash, "version": version.version},
        )
    )
    await session.commit()
    return {"id": version.id, "version": version.version, "content_hash": version.content_hash}


@router.post("/rate-versions/{version_id}/activate")
async def activate_rate_version(
    version_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, bool]:
    _operator(principal)
    version = await session.get(RateVersion, version_id)
    if version is None:
        raise ProblemError(
            404, "Rate version not found", "Version does not exist", "rate_version_missing"
        )
    for sibling in await session.scalars(
        select(RateVersion).where(RateVersion.rate_plan_id == version.rate_plan_id)
    ):
        sibling.is_active = sibling.id == version.id
    session.add(
        audit_event(
            action="rate_version.activated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="rate_version",
            object_id=version.id,
        )
    )
    await session.commit()
    return {"active": True}


@router.post("/rates/preview", response_model=RatePreviewResponse)
async def preview_rate(payload: RatePreviewRequest, _viewer: Viewer) -> RatePreviewResponse:
    plans = load_seed_plans()
    plan = plans.get(payload.plan_code)
    if plan is None:
        raise ProblemError(
            404, "Rate preset not found", "Plan code is not bundled", "rate_plan_missing"
        )
    engine = RateEngine(plan)
    result = engine.calculate(
        start=payload.interval_start,
        end=payload.interval_end,
        energy_kwh=payload.energy_kwh,
        cost_scope=payload.cost_scope,
        baseline_allocation_kwh=payload.baseline_allocation_kwh,
        billing_days=payload.billing_days,
        cca_adjustment_per_kwh=payload.cca_adjustment_per_kwh,
        other_adjustment=payload.other_adjustment,
    )
    components = [
        CostComponent(name="Energy charge", amount=result.energy_charge),
        CostComponent(name="Base service charge", amount=result.fixed_charge),
        CostComponent(name="Baseline credit", amount=-result.baseline_credit),
        CostComponent(name="CCA/Direct Access adjustment", amount=result.cca_adjustment),
        CostComponent(name="Other account adjustment", amount=result.other_adjustment),
    ]
    return RatePreviewResponse(
        plan_code=payload.plan_code,
        rate_version=f"2026-06-01-v{plan['version']}",
        timezone=plan["timezone"],
        energy_by_bucket_kwh=result.energy_by_bucket,
        components=components,
        unrounded_total=result.total,
        display_total=engine.display_currency(result.total),
        coverage_percent=Decimal("100"),
        disclosure=DISCLOSURE,
    )


@router.get("/billing/cycles")
async def billing_cycles(_viewer: Viewer, session: DbSession) -> list[dict[str, Any]]:
    cycles = list(
        await session.scalars(
            select(BillingCycle).order_by(BillingCycle.starts_at.desc()).limit(100)
        )
    )
    return [
        {
            "id": cycle.id,
            "utility_account_id": cycle.utility_account_id,
            "starts_at": cycle.starts_at,
            "ends_at": cycle.ends_at,
            "finalized_at": cycle.finalized_at,
        }
        for cycle in cycles
    ]


@router.get("/billing/runs")
async def billing_runs(_viewer: Viewer, session: DbSession) -> list[dict[str, Any]]:
    runs = list(
        await session.scalars(
            select(CostCalculationRun).order_by(CostCalculationRun.created_at.desc()).limit(100)
        )
    )
    output: list[dict[str, Any]] = []
    for run in runs:
        intervals = list(
            await session.scalars(
                select(CostIntervalResult).where(CostIntervalResult.run_id == run.id)
            )
        )
        total = sum((item.unrounded_cost for item in intervals), Decimal("0"))
        output.append(
            {
                "id": run.id,
                "status": run.status,
                "input_start": run.input_start,
                "input_end": run.input_end,
                "coverage_percent": run.coverage_percent,
                "unrounded_energy_cost": total,
                "algorithm_version": run.algorithm_version,
                "disclosure": DISCLOSURE,
            }
        )
    return output


@router.post("/billing/recalculations", status_code=202)
async def queue_recalculation(
    payload: CostRecalculationRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _operator(principal)
    account = await session.get(UtilityAccount, payload.utility_account_id)
    aggregate = await session.get(AggregateSet, payload.aggregate_set_id)
    version = await session.get(RateVersion, payload.rate_version_id)
    if account is None or aggregate is None or version is None:
        raise ProblemError(
            422,
            "Invalid calculation scope",
            "Account, aggregate set, or rate version does not exist",
            "calculation_scope_invalid",
        )
    if aggregate.utility_account_id != account.id:
        raise ProblemError(
            409,
            "Aggregate account mismatch",
            "The aggregate set is not assigned to the selected utility account",
            "aggregate_account_mismatch",
        )
    run = CostCalculationRun(
        utility_account_id=account.id,
        aggregate_set_id=aggregate.id,
        rate_version_id=version.id,
        input_start=payload.input_start,
        input_end=payload.input_end,
        algorithm_version=RateEngine.algorithm_version,
        status="queued",
        coverage_percent=Decimal("0"),
        created_at=datetime.now(UTC),
    )
    session.add(run)
    session.add(
        audit_event(
            action="billing.recalculation_queued",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="cost_calculation_run",
            object_id=run.id,
            details={"aggregate_set_id": aggregate.id, "rate_version_id": version.id},
        )
    )
    await session.commit()
    return {"id": run.id, "status": run.status}
