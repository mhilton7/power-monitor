from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BaselineRule,
    BillingCycle,
    CostCalculationRun,
    FixedChargeRule,
    RateAdjustment,
    RateAssignment,
    RateDayType,
    RateExtractionResult,
    RatePeriod,
    RatePlan,
    RateSeason,
    RateSeasonalBaseline,
    RateSourceArtifact,
    RateThresholdRule,
    RateTierDefinition,
    RateVersion,
    RateVersionSource,
    Utility,
    UtilityAccount,
    UtilityBillFieldConflict,
    UtilityBillImport,
)
from app.problem import ProblemError
from app.rates.documents import (
    DayScheduleDocument,
    RateAdjustmentDocument,
    RatePeriodDocument,
    RatePlanDocument,
    RateSeasonDocument,
    ValidationReport,
    document_hash,
    validate_document,
)


async def version_document(session: AsyncSession, version: RateVersion) -> RatePlanDocument:
    if version.normalized_payload:
        return RatePlanDocument.model_validate(version.normalized_payload)
    plan = await session.get(RatePlan, version.rate_plan_id)
    if plan is None:
        raise ProblemError(
            404, "Rate plan not found", "Rate plan does not exist", "rate_plan_missing"
        )
    utility = await session.get(Utility, plan.utility_id)
    seasons = list(
        await session.scalars(
            select(RateSeason)
            .where(RateSeason.rate_version_id == version.id)
            .order_by(RateSeason.priority.desc(), RateSeason.name)
        )
    )
    day_types = list(
        await session.scalars(select(RateDayType).where(RateDayType.rate_version_id == version.id))
    )
    periods = list(
        await session.scalars(
            select(RatePeriod)
            .where(RatePeriod.rate_version_id == version.id)
            .order_by(RatePeriod.display_order, RatePeriod.start_minute)
        )
    )
    season_docs: list[RateSeasonDocument] = []
    for season in seasons:
        schedules: list[DayScheduleDocument] = []
        for day_type in day_types:
            matching = [
                item
                for item in periods
                if item.season_name == season.name and item.day_type == day_type.name
            ]
            if matching:
                schedules.append(
                    DayScheduleDocument(
                        day_type=day_type.name,
                        periods=[
                            RatePeriodDocument(
                                label=item.bucket,
                                start_minute=item.start_minute,
                                end_minute=item.end_minute,
                                price_per_kwh=str(item.price_per_kwh),
                                delivery_per_kwh=str(item.delivery_per_kwh),
                                generation_per_kwh=str(item.generation_per_kwh),
                                adjustment_per_kwh=str(item.adjustment_per_kwh),
                                display_order=item.display_order,
                            )
                            for item in matching
                        ],
                    )
                )
        season_docs.append(
            RateSeasonDocument(
                name=season.name,
                start=f"{season.start_month:02d}-{season.start_day:02d}",
                end=f"{season.end_month:02d}-{season.end_day:02d}",
                priority=season.priority,
                leap_day_behavior=season.leap_day_behavior,  # type: ignore[arg-type]
                schedules=schedules,
            )
        )
    adjustments = [
        RateAdjustmentDocument(
            name=item.name,
            component=item.component,  # type: ignore[arg-type]
            operation=item.operation,  # type: ignore[arg-type]
            value=str(item.amount),
            unit=item.unit,
            scope=item.scope,
            eligibility=item.eligibility,
            effective_from=item.effective_from,
            effective_to=item.effective_to,
            calculation_order=item.display_order,
            description=item.description,
        )
        for item in await session.scalars(
            select(RateAdjustment)
            .where(RateAdjustment.rate_version_id == version.id)
            .order_by(RateAdjustment.display_order)
        )
    ]
    if not adjustments:
        fixed = await session.scalar(
            select(FixedChargeRule).where(FixedChargeRule.rate_version_id == version.id)
        )
        baseline = await session.scalar(
            select(BaselineRule).where(BaselineRule.rate_version_id == version.id)
        )
        if fixed:
            adjustments.append(
                RateAdjustmentDocument(
                    name=fixed.name,
                    component="daily_fixed_charge",
                    value=str(fixed.amount_per_day),
                    unit="per_day",
                    scope="full_account_estimate",
                )
            )
        if baseline:
            adjustments.append(
                RateAdjustmentDocument(
                    name="Baseline credit",
                    component="baseline_credit",
                    operation="subtract",
                    value=str(baseline.credit_per_kwh),
                    unit="per_kwh",
                    scope="full_account_estimate",
                )
            )
    return RatePlanDocument(
        plan_name=plan.name,
        plan_code=plan.code,
        utility=utility.name if utility else "custom",
        description=plan.description,
        currency=version.currency,
        timezone=version.timezone,
        pricing_model=version.pricing_model,  # type: ignore[arg-type]
        ownership_scope=plan.ownership_scope,  # type: ignore[arg-type]
        owner_id=plan.owner_site_id or plan.owner_utility_account_id,
        effective_from=version.effective_from,
        effective_through=version.effective_to,
        cost_scope_default="energy_only",
        source_label=version.source_label or version.source_url,
        source_note=version.source_notes,
        provider_mode="sce_delivery_generation"
        if version.source_kind == "official_sce"
        else "custom_combined",
        seasons=season_docs,
        adjustments=adjustments,
        cloned_from_rate_version_id=plan.cloned_from_rate_version_id,
    )


async def _replace_version_rows(
    session: AsyncSession, version: RateVersion, document: RatePlanDocument
) -> None:
    for model in (
        RatePeriod,
        RateDayType,
        RateSeason,
        RateSeasonalBaseline,
        RateThresholdRule,
        RateTierDefinition,
        RateAdjustment,
        FixedChargeRule,
        BaselineRule,
    ):
        await session.execute(delete(model).where(model.rate_version_id == version.id))
    threshold = document.billing_cycle.threshold
    session.add(
        RateThresholdRule(
            rate_version_id=version.id,
            basis=threshold.basis,
            daily_baseline_kwh=(
                Decimal(threshold.daily_baseline_kwh)
                if threshold.daily_baseline_kwh is not None
                else None
            ),
            baseline_region=threshold.baseline_region,
            baseline_category=threshold.baseline_category,
            rounding_policy=threshold.rounding_policy,
            expected_cycle_start_day=document.billing_cycle.expected_start_day,
            source_citation=threshold.source_citation,
        )
    )
    for tier in document.tiers:
        session.add(
            RateTierDefinition(
                rate_version_id=version.id,
                stable_tier_id=tier.tier_id,
                name=tier.name,
                display_order=tier.order,
                lower_bound_kwh=Decimal(tier.lower_bound_inclusive_kwh),
                upper_bound_kwh=(
                    Decimal(tier.upper_bound_exclusive_kwh)
                    if tier.upper_bound_exclusive_kwh is not None
                    else None
                ),
                lower_bound_multiplier=(
                    Decimal(tier.lower_bound_multiplier)
                    if tier.lower_bound_multiplier is not None
                    else None
                ),
                upper_bound_multiplier=(
                    Decimal(tier.upper_bound_multiplier)
                    if tier.upper_bound_multiplier is not None
                    else None
                ),
                price_per_kwh=Decimal(tier.price_per_kwh),
                tou_prices=tier.tou_prices,
                season_name=tier.season,
                source_citation=tier.source_citation,
            )
        )
    for baseline in threshold.seasonal_baselines:
        start_month, start_day = (int(part) for part in baseline.start.split("-"))
        end_month, end_day = (int(part) for part in baseline.end.split("-"))
        session.add(
            RateSeasonalBaseline(
                rate_version_id=version.id,
                name=baseline.name,
                start_month=start_month,
                start_day=start_day,
                end_month=end_month,
                end_day=end_day,
                daily_kwh=Decimal(baseline.daily_kwh),
                source_citation=baseline.source_citation,
            )
        )
    day_types: dict[str, DayScheduleDocument] = {}
    for season in document.seasons:
        start_month, start_day = (int(part) for part in season.start.split("-"))
        end_month, end_day = (int(part) for part in season.end.split("-"))
        session.add(
            RateSeason(
                rate_version_id=version.id,
                name=season.name,
                start_month=start_month,
                start_day=start_day,
                end_month=end_month,
                end_day=end_day,
                priority=season.priority,
                leap_day_behavior=season.leap_day_behavior,
            )
        )
        for schedule in season.schedules:
            day_types.setdefault(schedule.day_type, schedule)
            for period in schedule.periods:
                session.add(
                    RatePeriod(
                        rate_version_id=version.id,
                        season_name=season.name,
                        day_type=schedule.day_type,
                        start_minute=period.start_minute,
                        end_minute=period.end_minute,
                        bucket=period.label,
                        price_per_kwh=Decimal(period.price_per_kwh),
                        delivery_per_kwh=Decimal(period.delivery_per_kwh),
                        generation_per_kwh=Decimal(period.generation_per_kwh),
                        adjustment_per_kwh=Decimal(period.adjustment_per_kwh),
                        display_order=period.display_order,
                    )
                )
    default_weekdays = {
        "weekday": [0, 1, 2, 3, 4],
        "weekend": [5, 6],
        "all-days": [0, 1, 2, 3, 4, 5, 6],
        "holiday": [],
        "date-override": [],
    }
    for name, schedule in day_types.items():
        session.add(
            RateDayType(
                rate_version_id=version.id,
                name=name,
                weekdays=default_weekdays[name],
                holiday_behavior="explicit" if name in {"holiday", "date-override"} else "weekday",
                holiday_source=(
                    ",".join(value.isoformat() for value in schedule.dates)
                    if schedule.dates
                    else "Administrator configured"
                ),
            )
        )
    for adjustment in document.adjustments:
        session.add(
            RateAdjustment(
                rate_version_id=version.id,
                name=adjustment.name,
                component=adjustment.component,
                operation=adjustment.operation,
                amount=Decimal(adjustment.value),
                unit=adjustment.unit,
                scope=adjustment.scope,
                eligibility=adjustment.eligibility,
                effective_from=adjustment.effective_from,
                effective_to=adjustment.effective_to,
                display_order=adjustment.calculation_order,
                description=adjustment.description,
                configuration={},
            )
        )
        if adjustment.component == "daily_fixed_charge":
            session.add(
                FixedChargeRule(
                    rate_version_id=version.id,
                    name=adjustment.name,
                    amount_per_day=Decimal(adjustment.value),
                    account_once=True,
                )
            )
        elif adjustment.component == "baseline_credit":
            session.add(
                BaselineRule(
                    rate_version_id=version.id,
                    credit_per_kwh=Decimal(adjustment.value),
                    requires_full_account=True,
                    allocation_source="user_configured",
                )
            )


async def create_custom_plan(
    session: AsyncSession,
    document: RatePlanDocument,
    user_id: str,
    *,
    duplicate_suffix: bool = False,
) -> tuple[RatePlan, RateVersion]:
    utility = await session.scalar(
        select(Utility).where(func.lower(Utility.name) == document.utility.lower())
    )
    if utility is None:
        utility = Utility(name=document.utility, website=None)
        session.add(utility)
        await session.flush()
    code = document.plan_code
    duplicate = await session.scalar(
        select(RatePlan).where(RatePlan.utility_id == utility.id, RatePlan.code == code)
    )
    if duplicate and not duplicate_suffix:
        raise ProblemError(
            409, "Rate plan code exists", "Choose a unique plan code", "rate_plan_code_conflict"
        )
    if duplicate:
        base = re.sub(r"-COPY-\d+$", "", code)[:68]
        number = 2
        while await session.scalar(
            select(RatePlan.id).where(
                RatePlan.utility_id == utility.id, RatePlan.code == f"{base}-COPY-{number}"
            )
        ):
            number += 1
        code = f"{base}-COPY-{number}"
        document = document.model_copy(update={"plan_code": code})
    now = datetime.now(UTC)
    plan = RatePlan(
        utility_id=utility.id,
        code=code,
        name=document.plan_name,
        description=document.description,
        plan_kind="custom",
        ownership_scope=document.ownership_scope,
        owner_site_id=document.owner_id if document.ownership_scope == "site" else None,
        owner_utility_account_id=(
            document.owner_id if document.ownership_scope == "utility_account" else None
        ),
        currency=document.currency,
        timezone=document.timezone,
        status="draft",
        created_by=user_id,
        cloned_from_rate_version_id=document.cloned_from_rate_version_id,
    )
    session.add(plan)
    await session.flush()
    version = RateVersion(
        rate_plan_id=plan.id,
        version=1,
        effective_from=document.effective_from,
        effective_to=document.effective_through,
        timezone=document.timezone,
        currency=document.currency,
        pricing_model=document.pricing_model,
        source_url="urn:power-monitor:custom-rate-plan",
        source_checked_on=date.today(),
        source_checked_at=now,
        source_notes=document.source_note,
        source_label=document.source_label,
        source_kind="custom",
        content_hash=document_hash(document),
        status="draft",
        normalized_payload=document.model_dump(mode="json"),
        immutable_after_use=False,
        is_active=False,
        created_at=now,
        created_by=user_id,
    )
    session.add(version)
    await session.flush()
    await _replace_version_rows(session, version, document)
    return plan, version


async def update_draft_version(
    session: AsyncSession,
    version: RateVersion,
    document: RatePlanDocument,
) -> ValidationReport:
    if version.status != "draft" or version.is_active or version.immutable_after_use:
        raise ProblemError(
            409,
            "Rate version is immutable",
            "Create a new version to edit an active or used rate",
            "rate_version_immutable",
        )
    plan = await session.get(RatePlan, version.rate_plan_id)
    if plan is None:
        raise ProblemError(
            404, "Rate plan not found", "Rate plan does not exist", "rate_plan_missing"
        )
    plan.name = document.plan_name
    plan.description = document.description
    plan.ownership_scope = document.ownership_scope
    plan.owner_site_id = document.owner_id if document.ownership_scope == "site" else None
    plan.owner_utility_account_id = (
        document.owner_id if document.ownership_scope == "utility_account" else None
    )
    plan.currency = document.currency
    plan.timezone = document.timezone
    version.effective_from = document.effective_from
    version.effective_to = document.effective_through
    version.timezone = document.timezone
    version.currency = document.currency
    version.pricing_model = document.pricing_model
    version.source_label = document.source_label
    version.source_notes = document.source_note
    version.normalized_payload = document.model_dump(mode="json")
    version.content_hash = document_hash(document)
    await _replace_version_rows(session, version, document)
    return validate_document(document)


async def validate_version(session: AsyncSession, version: RateVersion) -> ValidationReport:
    document = await version_document(session, version)
    evidence: dict[str, Any] | None = None
    if version.source_kind in {"official_sce_candidate", "utility_bill_candidate"}:
        link = await session.scalar(
            select(RateVersionSource).where(RateVersionSource.rate_version_id == version.id)
        )
        artifact = await session.get(RateSourceArtifact, link.artifact_id) if link else None
        extraction = (
            await session.get(RateExtractionResult, link.extraction_result_id)
            if link and link.extraction_result_id
            else None
        )
        evidence = {
            "artifact_id": artifact.id if artifact else None,
            "sha256": artifact.sha256 if artifact else None,
            "parser_id": extraction.parser_id if extraction else None,
            "parser_version": extraction.parser_version if extraction else None,
        }
    return validate_document(
        document,
        require_source_evidence=version.source_kind
        in {"official_sce_candidate", "utility_bill_candidate"},
        source_evidence=evidence,
    )


async def activate_version(
    session: AsyncSession,
    version: RateVersion,
    user_id: str | None,
    *,
    automatically: bool = False,
) -> tuple[str, ValidationReport]:
    if version.source_kind == "utility_bill_candidate":
        bill = await session.scalar(
            select(UtilityBillImport).where(UtilityBillImport.rate_version_id == version.id)
        )
        unresolved_conflict = (
            await session.scalar(
                select(UtilityBillFieldConflict.id)
                .where(
                    UtilityBillFieldConflict.bill_import_id == bill.id,
                    UtilityBillFieldConflict.blocking.is_(True),
                    UtilityBillFieldConflict.status == "unresolved",
                )
                .limit(1)
            )
            if bill
            else None
        )
        if (
            bill is None
            or bill.status not in {"ready_to_publish", "published"}
            or bill.blocking_warnings
            or unresolved_conflict
            or automatically
        ):
            raise ProblemError(
                409,
                "Utility-bill review required",
                "A utility bill can never activate a rate until an administrator "
                "reviews every required field and source conflict",
                "utility_bill_review_required",
            )
    report = await validate_version(session, version)
    if not report.valid:
        raise ProblemError(
            422,
            "Rate version failed validation",
            "Correct all blocking validation errors before activation",
            "rate_validation_failed",
            extra={"validation": report.model_dump(mode="json")},
        )
    now = datetime.now(UTC)
    if version.effective_from > date.today():
        version.status = "approved"
        version.approved_by = user_id
        version.approved_at = now
        version.automatically_activated = automatically
        return "scheduled", report
    siblings = list(
        await session.scalars(
            select(RateVersion).where(RateVersion.rate_plan_id == version.rate_plan_id)
        )
    )
    superseded_ids: list[str] = []
    for sibling in siblings:
        if sibling.id != version.id and sibling.is_active:
            superseded_ids.append(sibling.id)
            sibling.is_active = False
            sibling.status = "superseded"
    version.is_active = True
    version.status = "active"
    version.immutable_after_use = True
    version.approved_by = version.approved_by or user_id
    version.approved_at = version.approved_at or now
    version.activated_by = user_id
    version.activated_at = now
    version.automatically_activated = automatically
    plan = await session.get(RatePlan, version.rate_plan_id)
    if plan:
        plan.status = "active"
    await _move_assignments_and_queue_recalculations(session, version, superseded_ids, user_id)
    return "active", report


async def _move_assignments_and_queue_recalculations(
    session: AsyncSession,
    version: RateVersion,
    superseded_ids: list[str],
    user_id: str | None,
) -> tuple[int, int]:
    """Move current assignments and recalculate only non-finalized estimates."""
    if not superseded_ids:
        return 0, 0
    now = datetime.now(UTC)
    effective_at = datetime.combine(
        version.effective_from, time.min, tzinfo=ZoneInfo(version.timezone)
    ).astimezone(UTC)
    accounts = list(
        await session.scalars(
            select(UtilityAccount).where(UtilityAccount.active_rate_version_id.in_(superseded_ids))
        )
    )
    assignments_created = 0
    queued = 0
    for account in accounts:
        current_assignments = list(
            await session.scalars(
                select(RateAssignment).where(
                    RateAssignment.utility_account_id == account.id,
                    RateAssignment.effective_to.is_(None),
                )
            )
        )
        for assignment in current_assignments:
            assignment.effective_to = max(effective_at, assignment.effective_from)
        session.add(
            RateAssignment(
                utility_account_id=account.id,
                rate_version_id=version.id,
                effective_from=effective_at,
                effective_to=None,
                assigned_by=user_id,
                created_at=now,
            )
        )
        account.active_rate_version_id = version.id
        assignments_created += 1

        prior_runs = list(
            await session.scalars(
                select(CostCalculationRun).where(
                    CostCalculationRun.utility_account_id == account.id,
                    CostCalculationRun.rate_version_id.in_(superseded_ids),
                    CostCalculationRun.status == "completed",
                    CostCalculationRun.input_end > effective_at,
                )
            )
        )
        for prior in prior_runs:
            recalc_start = max(prior.input_start, effective_at)
            finalized = await session.scalar(
                select(BillingCycle.id).where(
                    BillingCycle.utility_account_id == account.id,
                    BillingCycle.finalized_at.is_not(None),
                    BillingCycle.starts_at < prior.input_end,
                    BillingCycle.ends_at > recalc_start,
                )
            )
            if finalized:
                continue
            duplicate = await session.scalar(
                select(CostCalculationRun.id).where(
                    CostCalculationRun.utility_account_id == account.id,
                    CostCalculationRun.aggregate_set_id == prior.aggregate_set_id,
                    CostCalculationRun.rate_version_id == version.id,
                    CostCalculationRun.input_start == recalc_start,
                    CostCalculationRun.input_end == prior.input_end,
                    CostCalculationRun.status.in_(["queued", "running", "completed"]),
                )
            )
            if duplicate:
                continue
            session.add(
                CostCalculationRun(
                    utility_account_id=account.id,
                    aggregate_set_id=prior.aggregate_set_id,
                    rate_version_id=version.id,
                    input_start=recalc_start,
                    input_end=prior.input_end,
                    algorithm_version="rate-engine/1.0.0",
                    status="queued",
                    coverage_percent=Decimal("0"),
                    created_at=now,
                )
            )
            queued += 1
    return assignments_created, queued


async def clone_plan_version(
    session: AsyncSession, version: RateVersion, user_id: str
) -> tuple[RatePlan, RateVersion]:
    source = await version_document(session, version)
    base_code = re.sub(r"[^A-Z0-9._-]", "-", source.plan_code.upper())[:64]
    cloned = source.model_copy(
        update={
            "plan_name": f"{source.plan_name} Copy",
            "plan_code": f"{base_code}-COPY",
            "source_label": f"Custom clone of {source.plan_code} v{version.version}",
            "source_note": "Review all values before activation.",
            "cloned_from_rate_version_id": version.id,
        }
    )
    return await create_custom_plan(session, cloned, user_id, duplicate_suffix=True)


async def version_usage_count(session: AsyncSession, version_id: str) -> int:
    calculations = await session.scalar(
        select(func.count())
        .select_from(CostCalculationRun)
        .where(CostCalculationRun.rate_version_id == version_id)
    )
    assignments = await session.scalar(
        select(func.count())
        .select_from(RateAssignment)
        .where(RateAssignment.rate_version_id == version_id)
    )
    accounts = await session.scalar(
        select(func.count())
        .select_from(UtilityAccount)
        .where(UtilityAccount.active_rate_version_id == version_id)
    )
    return int(calculations or 0) + int(assignments or 0) + int(accounts or 0)
