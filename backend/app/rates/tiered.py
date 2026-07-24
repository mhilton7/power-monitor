from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AccountReconciliationAdjustment,
    AccountUsageAuthority,
    AggregateMember,
    AggregateSet,
    BillingCycle,
    CycleTierSummary,
    Device,
    ManualAccountUsage,
    NormalizedInterval,
    RateAssignment,
    RateTierDefinition,
    RateVersion,
    RawReading,
    TierAllocationSegment,
    TierProjectionSnapshot,
    UtilityAccount,
    UtilityUsageImport,
)
from app.problem import ProblemError
from app.rates.documents import engine_plan
from app.rates.engine import RateEngine, project_billing_cycle
from app.rates.service import version_document

ZERO = Decimal("0")


@dataclass(frozen=True)
class AuthoritativeInterval:
    interval_id: str | None
    start: datetime
    end: datetime
    energy_kwh: Decimal
    quality_flags: tuple[str, ...] = ()
    import_id: str | None = None


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def expected_cycle_bounds(account: UtilityAccount, instant: datetime) -> tuple[datetime, datetime]:
    plan = {
        "timezone": account.timezone,
        "billing_cycle": {"expected_start_day": account.billing_cycle_start_day},
        "pricing_model": "flat",
        "flat_rate_per_kwh": "0",
    }
    return RateEngine(plan).billing_cycle_bounds(instant)


async def current_billing_cycle(
    session: AsyncSession,
    account: UtilityAccount,
    instant: datetime,
    *,
    create: bool,
    actor_id: str | None = None,
) -> BillingCycle:
    instant = _aware_utc(instant)
    cycle = await session.scalar(
        select(BillingCycle)
        .where(
            BillingCycle.utility_account_id == account.id,
            BillingCycle.starts_at <= instant,
            BillingCycle.ends_at > instant,
        )
        .order_by(BillingCycle.explicit_meter_dates.desc(), BillingCycle.override_revision.desc())
    )
    if cycle is not None:
        return cycle
    start, end = expected_cycle_bounds(account, instant)
    cycle = BillingCycle(
        utility_account_id=account.id,
        starts_at=start,
        ends_at=end,
        explicit_meter_dates=False,
        status="expected",
        boundary_source="generated",
        override_revision=0,
        recalculation_version=0,
        created_by=actor_id,
        updated_by=actor_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    if create:
        session.add(cycle)
        await session.flush()
    return cycle


async def usage_authority(session: AsyncSession, account_id: str) -> AccountUsageAuthority | None:
    result: AccountUsageAuthority | None = await session.scalar(
        select(AccountUsageAuthority).where(AccountUsageAuthority.utility_account_id == account_id)
    )
    return result


def authority_payload(authority: AccountUsageAuthority | None) -> dict[str, Any]:
    if authority is None:
        return {
            "configured": False,
            "authority_type": None,
            "complete_account": False,
            "confidence": "unknown",
            "source_reference": None,
            "aggregate_set_id": None,
            "device_ids": [],
            "revision": 0,
        }
    return {
        "configured": True,
        "authority_type": authority.authority_type,
        "complete_account": authority.complete_account,
        "confidence": authority.confidence,
        "source_reference": authority.source_reference,
        "aggregate_set_id": authority.aggregate_set_id,
        "device_ids": authority.device_ids,
        "revision": authority.revision,
        "updated_at": authority.updated_at,
    }


async def _authority_device_ids(
    session: AsyncSession, account: UtilityAccount, authority: AccountUsageAuthority
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    if authority.authority_type == "complete_site_aggregate":
        aggregate = (
            await session.get(AggregateSet, authority.aggregate_set_id)
            if authority.aggregate_set_id
            else None
        )
        if aggregate is None or aggregate.utility_account_id != account.id:
            raise ProblemError(
                422,
                "Usage authority is invalid",
                "The configured aggregate no longer belongs to this utility account",
                "usage_authority_aggregate_invalid",
            )
        if aggregate.overlap_confirmed_at is not None:
            warnings.append(
                "The selected aggregate has a confirmed parent/child overlap; "
                "tier totals are withheld until the overlap is removed."
            )
            return [], warnings
        members = list(
            await session.scalars(
                select(AggregateMember).where(AggregateMember.aggregate_set_id == aggregate.id)
            )
        )
        device_ids = {item.device_id for item in members if item.device_id}
        circuit_ids = {item.circuit_id for item in members if item.circuit_id}
        if circuit_ids:
            device_ids.update(
                await session.scalars(
                    select(Device.id).where(
                        Device.circuit_id.in_(circuit_ids),
                        Device.utility_account_id == account.id,
                        Device.lifecycle_status == "active",
                    )
                )
            )
        return sorted(device_ids), warnings
    if authority.device_ids:
        valid = set(
            await session.scalars(
                select(Device.id).where(
                    Device.id.in_(authority.device_ids),
                    Device.utility_account_id == account.id,
                    Device.lifecycle_status == "active",
                )
            )
        )
        missing = sorted(set(authority.device_ids) - valid)
        if missing:
            warnings.append("One or more configured authority sensors are unavailable.")
        return sorted(valid), warnings
    return [], warnings


async def _monitored_intervals(
    session: AsyncSession,
    *,
    device_ids: list[str],
    start: datetime,
    end: datetime,
) -> list[AuthoritativeInterval]:
    if not device_ids:
        return []
    rows = (
        await session.execute(
            select(NormalizedInterval, RawReading)
            .join(RawReading, RawReading.id == NormalizedInterval.raw_reading_id)
            .where(
                NormalizedInterval.device_id.in_(device_ids),
                NormalizedInterval.interval_start < end,
                NormalizedInterval.interval_end > start,
                NormalizedInterval.selected_energy_wh.is_not(None),
            )
            .order_by(NormalizedInterval.interval_start, NormalizedInterval.id)
        )
    ).all()
    result: list[AuthoritativeInterval] = []
    for normalized, raw in rows:
        raw_start = _aware_utc(normalized.interval_start)
        raw_end = _aware_utc(normalized.interval_end)
        left = max(raw_start, start)
        right = min(raw_end, end)
        total_seconds = Decimal(str((raw_end - raw_start).total_seconds()))
        covered_seconds = Decimal(str((right - left).total_seconds()))
        if total_seconds <= 0 or covered_seconds <= 0 or normalized.selected_energy_wh is None:
            continue
        result.append(
            AuthoritativeInterval(
                interval_id=normalized.id,
                start=left,
                end=right,
                energy_kwh=(
                    normalized.selected_energy_wh
                    / Decimal("1000")
                    * covered_seconds
                    / total_seconds
                ),
                quality_flags=tuple(raw.quality_flags),
            )
        )
    return result


def _parse_imported_intervals(
    imports: list[UtilityUsageImport], start: datetime, end: datetime
) -> list[AuthoritativeInterval]:
    result: list[AuthoritativeInterval] = []
    for item in imports:
        if item.import_kind not in {"interval", "daily"}:
            continue
        for row in item.normalized_rows:
            try:
                left = _aware_utc(datetime.fromisoformat(str(row["start"])))
                right = _aware_utc(datetime.fromisoformat(str(row["end"])))
                energy = Decimal(str(row["energy_kwh"]))
            except (KeyError, TypeError, ValueError):
                continue
            if energy < 0 or left >= end or right <= start or right <= left:
                continue
            clipped_left = max(left, start)
            clipped_right = min(right, end)
            fraction = Decimal(str((clipped_right - clipped_left).total_seconds())) / Decimal(
                str((right - left).total_seconds())
            )
            result.append(
                AuthoritativeInterval(
                    interval_id=None,
                    import_id=item.id,
                    start=clipped_left,
                    end=clipped_right,
                    energy_kwh=energy * fraction,
                    quality_flags=("utility_import",),
                )
            )
    return result


async def _manual_start(
    session: AsyncSession, account_id: str, cycle: BillingCycle
) -> ManualAccountUsage | None:
    result: ManualAccountUsage | None = await session.scalar(
        select(ManualAccountUsage)
        .where(
            ManualAccountUsage.utility_account_id == account_id,
            ManualAccountUsage.effective_at >= cycle.starts_at,
            ManualAccountUsage.effective_at < cycle.ends_at,
            ManualAccountUsage.superseded_at.is_(None),
        )
        .order_by(ManualAccountUsage.effective_at.desc())
    )
    return result


async def _committed_imports(session: AsyncSession, account_id: str) -> list[UtilityUsageImport]:
    return list(
        await session.scalars(
            select(UtilityUsageImport).where(
                UtilityUsageImport.utility_account_id == account_id,
                UtilityUsageImport.status == "committed",
            )
        )
    )


async def _rate_contexts(
    session: AsyncSession, account: UtilityAccount, cycle: BillingCycle
) -> list[tuple[datetime, datetime, RateVersion, RateEngine]]:
    assignments = list(
        await session.scalars(
            select(RateAssignment)
            .where(
                RateAssignment.utility_account_id == account.id,
                RateAssignment.effective_from < cycle.ends_at,
                (
                    RateAssignment.effective_to.is_(None)
                    | (RateAssignment.effective_to > cycle.starts_at)
                ),
            )
            .order_by(RateAssignment.effective_from)
        )
    )
    if not assignments and account.active_rate_version_id:
        version = await session.get(RateVersion, account.active_rate_version_id)
        if version:
            assignments = [
                RateAssignment(
                    utility_account_id=account.id,
                    rate_version_id=version.id,
                    effective_from=cycle.starts_at,
                    effective_to=cycle.ends_at,
                    created_at=cycle.starts_at,
                )
            ]
    contexts: list[tuple[datetime, datetime, RateVersion, RateEngine]] = []
    for assignment in assignments:
        version = await session.get(RateVersion, assignment.rate_version_id)
        if version is None:
            continue
        start = max(cycle.starts_at, _aware_utc(assignment.effective_from))
        end = min(
            cycle.ends_at,
            _aware_utc(assignment.effective_to) if assignment.effective_to else cycle.ends_at,
        )
        if end <= start:
            continue
        document = await version_document(session, version)
        contexts.append((start, end, version, RateEngine(engine_plan(document))))
    return contexts


def _context_at(
    contexts: list[tuple[datetime, datetime, RateVersion, RateEngine]], instant: datetime
) -> tuple[datetime, datetime, RateVersion, RateEngine] | None:
    return next((item for item in contexts if item[0] <= instant < item[1]), None)


def _split_for_rate_boundaries(
    interval: AuthoritativeInterval,
    contexts: list[tuple[datetime, datetime, RateVersion, RateEngine]],
) -> list[tuple[AuthoritativeInterval, RateVersion, RateEngine]]:
    boundaries = {interval.start, interval.end}
    for start, end, _version, _engine in contexts:
        if interval.start < start < interval.end:
            boundaries.add(start)
        if interval.start < end < interval.end:
            boundaries.add(end)
    ordered = sorted(boundaries)
    total_seconds = Decimal(str((interval.end - interval.start).total_seconds()))
    result: list[tuple[AuthoritativeInterval, RateVersion, RateEngine]] = []
    for left, right in pairwise(ordered):
        context = _context_at(contexts, left + (right - left) / 2)
        if context is None:
            continue
        seconds = Decimal(str((right - left).total_seconds()))
        result.append(
            (
                AuthoritativeInterval(
                    interval_id=interval.interval_id,
                    import_id=interval.import_id,
                    start=left,
                    end=right,
                    energy_kwh=interval.energy_kwh * seconds / total_seconds,
                    quality_flags=interval.quality_flags,
                ),
                context[2],
                context[3],
            )
        )
    return result


async def calculate_cycle_tier_status(
    session: AsyncSession,
    account: UtilityAccount,
    cycle: BillingCycle,
    *,
    persist: bool,
    actor_id: str | None = None,
) -> dict[str, Any]:
    authority = await usage_authority(session, account.id)
    authority_view = authority_payload(authority)
    if authority is None:
        return _unavailable_status(
            account,
            cycle,
            authority_view,
            "Configure an account-usage authority before calculating billing-cycle tiers.",
        )
    device_ids, warnings = await _authority_device_ids(session, account, authority)
    manual = await _manual_start(session, account.id, cycle)
    imports = await _committed_imports(session, account.id)
    initial_usage = ZERO
    monitored_start = cycle.starts_at
    context_import_id: str | None = None
    if manual is not None and authority.authority_type in {
        "manual_cycle_usage",
        "external_feed",
        "partial_monitored_circuits",
    }:
        initial_usage = manual.cumulative_kwh
        monitored_start = _aware_utc(manual.effective_at)
    intervals = await _monitored_intervals(
        session,
        device_ids=device_ids,
        start=monitored_start,
        end=cycle.ends_at,
    )
    if authority.authority_type == "utility_interval_import":
        intervals = _parse_imported_intervals(imports, cycle.starts_at, cycle.ends_at)
    elif authority.authority_type == "partial_monitored_circuits":
        # Partial circuits are priced with explicit account context but never added
        # to the account-total tier cursor.
        intervals = []
    if (
        authority.authority_type
        in {
            "manual_cycle_usage",
            "external_feed",
            "partial_monitored_circuits",
        }
        and not intervals
    ):
        imports_with_totals = [
            (item, row)
            for item in imports
            if item.import_kind == "cycle_cumulative"
            for row in item.normalized_rows
        ]
        if imports_with_totals:
            latest_import, latest = max(
                imports_with_totals,
                key=lambda pair: datetime.fromisoformat(str(pair[1]["effective_at"])),
            )
            initial_usage = Decimal(str(latest["cumulative_kwh"]))
            monitored_start = _aware_utc(datetime.fromisoformat(str(latest["effective_at"])))
            context_import_id = latest_import.id
    has_cumulative_import = any(
        item.import_kind == "cycle_cumulative" and item.normalized_rows for item in imports
    )
    if (
        not authority.complete_account
        and authority.authority_type == "partial_monitored_circuits"
        and manual is None
        and not has_cumulative_import
    ):
        return _unavailable_status(
            account,
            cycle,
            authority_view,
            "Partial circuits cannot determine account tier progression. "
            "Import or enter current whole-account cycle usage first.",
        )
    contexts = await _rate_contexts(session, account, cycle)
    if not contexts:
        return _unavailable_status(
            account,
            cycle,
            authority_view,
            "No effective rate version covers this billing cycle.",
        )
    if not any(
        version.pricing_model in {"tiered", "time_of_use_tiered"}
        for _start, _end, version, _engine in contexts
    ):
        return _unavailable_status(
            account,
            cycle,
            authority_view,
            "The assigned rate is not a billing-cycle tiered plan.",
            pricing_model=contexts[-1][2].pricing_model,
        )
    if not intervals and initial_usage == 0 and manual is None and not has_cumulative_import:
        return _unavailable_status(
            account,
            cycle,
            authority_view,
            "No authoritative account-total readings are available for this cycle. "
            "The server will not assume zero usage or guess Tier 1.",
            pricing_model=contexts[-1][2].pricing_model,
        )

    cumulative = initial_usage
    usage_by_tier: dict[str, Decimal] = defaultdict(Decimal)
    charge_by_tier: dict[str, Decimal] = defaultdict(Decimal)
    segment_rows: list[dict[str, Any]] = []
    segment_orders: dict[str, int] = defaultdict(int)
    missing_rate = False
    for interval in sorted(
        intervals,
        key=lambda value: (value.start, value.end, value.interval_id or ""),
    ):
        parts = _split_for_rate_boundaries(interval, contexts)
        if not parts:
            missing_rate = True
            continue
        for part, version, engine in parts:
            if engine.pricing_model not in {"tiered", "time_of_use_tiered"}:
                missing_rate = True
                continue
            calculation = engine.calculate(
                start=part.start,
                end=part.end,
                energy_kwh=part.energy_kwh,
                cost_scope="energy_only",
                cumulative_usage_before_kwh=cumulative,
                cycle_start=cycle.starts_at,
                cycle_end=cycle.ends_at,
            )
            interval_key = part.interval_id or (
                f"import:{part.import_id}:{interval.start.isoformat()}:{interval.end.isoformat()}"
            )
            for value in calculation.slices:
                if value.tier_id is None or value.tier_name is None:
                    continue
                segment_order = segment_orders[interval_key]
                segment_orders[interval_key] += 1
                usage_by_tier[value.tier_id] += value.energy_kwh
                charge_by_tier[value.tier_id] += value.cost
                segment_rows.append(
                    {
                        "interval_id": part.interval_id,
                        "import_id": part.import_id,
                        "segment_order": segment_order,
                        "start": value.start,
                        "end": value.end,
                        "rate_version": version,
                        "tier_id": value.tier_id,
                        "tier_name": value.tier_name,
                        "tou_period": value.tou_period,
                        "cumulative_start_kwh": value.cumulative_start_kwh,
                        "cumulative_end_kwh": value.cumulative_end_kwh,
                        "energy_kwh": value.energy_kwh,
                        "price_per_kwh": value.price_per_kwh,
                        "energy_charge": value.cost,
                        "threshold_kwh": next(
                            (
                                tier["upper_bound_kwh"]
                                for tier in calculation.tier_thresholds
                                if tier["tier_id"] == value.tier_id
                            ),
                            None,
                        ),
                        "quality_flags": list(part.quality_flags),
                    }
                )
            cumulative += part.energy_kwh
    if not intervals and (manual is not None or has_cumulative_import):
        context_start = max(monitored_start, cycle.starts_at)
        context_interval = AuthoritativeInterval(
            interval_id=None,
            import_id=context_import_id,
            start=context_start,
            end=cycle.ends_at,
            energy_kwh=ZERO,
            quality_flags=("account_tier_context_only",),
        )
        for part, version, engine in _split_for_rate_boundaries(context_interval, contexts):
            if engine.pricing_model not in {"tiered", "time_of_use_tiered"}:
                continue
            calculation = engine.calculate(
                start=part.start,
                end=part.end,
                energy_kwh=ZERO,
                cost_scope="energy_only",
                cumulative_usage_before_kwh=cumulative,
                cycle_start=cycle.starts_at,
                cycle_end=cycle.ends_at,
            )
            for value in calculation.slices:
                if value.tier_id is None or value.tier_name is None:
                    continue
                segment_rows.append(
                    {
                        "interval_id": None,
                        "import_id": part.import_id,
                        "segment_order": len(segment_rows),
                        "start": value.start,
                        "end": value.end,
                        "rate_version": version,
                        "tier_id": value.tier_id,
                        "tier_name": value.tier_name,
                        "tou_period": value.tou_period,
                        "cumulative_start_kwh": cumulative,
                        "cumulative_end_kwh": cumulative,
                        "energy_kwh": ZERO,
                        "price_per_kwh": value.price_per_kwh,
                        "energy_charge": ZERO,
                        "threshold_kwh": next(
                            (
                                tier["upper_bound_kwh"]
                                for tier in calculation.tier_thresholds
                                if tier["tier_id"] == value.tier_id
                            ),
                            None,
                        ),
                        "quality_flags": ["account_tier_context_only"],
                    }
                )
        warnings.append(
            "Account tier is based on the latest explicit cumulative-usage context; "
            "partial-circuit energy does not advance the account tier."
        )
    if missing_rate:
        warnings.append(
            "One or more authoritative intervals have no tiered rate coverage; totals are partial."
        )

    now = min(max(datetime.now(UTC), cycle.starts_at), cycle.ends_at)
    final_instant = cycle.ends_at - (cycle.ends_at - cycle.starts_at) / 1_000_000
    active_context = _context_at(contexts, min(now, final_instant))
    if active_context is None:
        active_context = contexts[-1]
    _context_start, _context_end, active_version, active_engine = active_context
    current_tier = active_engine.tier_at(
        cumulative, cycle_start=cycle.starts_at, cycle_end=cycle.ends_at
    )
    current_price_calculation = active_engine.calculate(
        start=now,
        end=min(cycle.ends_at, now + timedelta(seconds=1)),
        energy_kwh=ZERO,
        cost_scope="energy_only",
        cumulative_usage_before_kwh=cumulative,
        cycle_start=cycle.starts_at,
        cycle_end=cycle.ends_at,
    )
    current_price_slice = current_price_calculation.slices[0]
    thresholds = active_engine.resolved_tiers(cycle_start=cycle.starts_at, cycle_end=cycle.ends_at)
    remaining = (
        max(ZERO, current_tier["upper_bound_kwh"] - cumulative)
        if current_tier and current_tier["upper_bound_kwh"] is not None
        else None
    )
    elapsed_seconds = max(1, int((now - cycle.starts_at).total_seconds()))
    total_seconds = max(elapsed_seconds, int((cycle.ends_at - cycle.starts_at).total_seconds()))
    projection = project_billing_cycle(
        actual_energy_kwh=cumulative,
        elapsed_seconds=elapsed_seconds,
        total_seconds=total_seconds,
        method="straight_line",
    )
    projected_tier = active_engine.tier_at(
        projection.projected_energy_kwh,
        cycle_start=cycle.starts_at,
        cycle_end=cycle.ends_at,
    )
    actual_energy_charge = sum(charge_by_tier.values(), ZERO)
    projected_energy_charge = actual_energy_charge
    if projection.projected_energy_kwh > cumulative and now < cycle.ends_at:
        projected = active_engine.calculate(
            start=now,
            end=cycle.ends_at,
            energy_kwh=projection.projected_energy_kwh - cumulative,
            cost_scope="energy_only",
            cumulative_usage_before_kwh=cumulative,
            cycle_start=cycle.starts_at,
            cycle_end=cycle.ends_at,
        )
        projected_energy_charge += projected.energy_charge
    coverage_seconds = sum(
        (Decimal(str((item.end - item.start).total_seconds())) for item in intervals),
        ZERO,
    )
    coverage_percent = min(
        Decimal("100"),
        coverage_seconds / Decimal(elapsed_seconds) * Decimal("100"),
    )
    if coverage_percent < Decimal("80"):
        warnings.append("Projection confidence is reduced by incomplete cycle coverage.")
    confidence = (
        "low"
        if coverage_percent < Decimal("50")
        else "medium"
        if coverage_percent < Decimal("90")
        else projection.confidence
    )
    components = await _bill_components(
        session,
        account,
        active_engine,
        cycle,
        cumulative,
        projected_usage=projection.projected_energy_kwh,
        energy_charge=actual_energy_charge,
        projected_energy_charge=projected_energy_charge,
    )
    bill_rows = [
        row
        for item in imports
        if item.import_kind == "bill_total"
        for row in item.normalized_rows
        if _aware_utc(datetime.fromisoformat(str(row["starts_at"]))) < cycle.ends_at
        and _aware_utc(datetime.fromisoformat(str(row["ends_at"]))) > cycle.starts_at
    ]
    bill_comparison: dict[str, Any] | None = None
    if bill_rows:
        bill = bill_rows[-1]
        utility_total = Decimal(str(bill["total_amount"]))
        estimated_total = (
            Decimal(str(components["estimated_total"]))
            if components["estimated_total"] is not None
            else None
        )
        reconciliation_total = Decimal(
            str(
                await session.scalar(
                    select(
                        func.coalesce(func.sum(AccountReconciliationAdjustment.amount), 0)
                    ).where(AccountReconciliationAdjustment.billing_cycle_id == cycle.id)
                )
                or 0
            )
        )
        bill_comparison = {
            "utility_total": str(utility_total),
            "utility_usage_kwh": bill.get("usage_kwh"),
            "reference": bill.get("reference") or None,
            "estimated_total": str(estimated_total) if estimated_total is not None else None,
            "difference": (
                str(utility_total - estimated_total) if estimated_total is not None else None
            ),
            "reconciliation_adjustments": str(reconciliation_total),
            "unexplained_difference": (
                str(utility_total - estimated_total - reconciliation_total)
                if estimated_total is not None
                else None
            ),
        }
    recalculation_version = (
        cycle.recalculation_version + 1 if persist else cycle.recalculation_version
    )
    if persist:
        if cycle.finalized_at is not None or cycle.status == "finalized":
            raise ProblemError(
                409,
                "Billing cycle is finalized",
                "Finalized tier allocations are immutable",
                "billing_cycle_finalized",
            )
        cycle.recalculation_version = recalculation_version
        cycle.status = "confirmed" if cycle.explicit_meter_dates else "expected"
        cycle.updated_by = actor_id
        cycle.updated_at = datetime.now(UTC)
        tier_rows = {
            (item.rate_version_id, item.stable_tier_id): item
            for item in await session.scalars(
                select(RateTierDefinition).where(
                    RateTierDefinition.rate_version_id.in_(
                        {item["rate_version"].id for item in segment_rows}
                    )
                )
            )
        }
        for item in segment_rows:
            definition = tier_rows.get((item["rate_version"].id, item["tier_id"]))
            if definition is None:
                raise ProblemError(
                    409,
                    "Tier definition missing",
                    "The immutable rate version is missing normalized tier rows",
                    "tier_definition_missing",
                )
            session.add(
                TierAllocationSegment(
                    billing_cycle_id=cycle.id,
                    utility_account_id=account.id,
                    normalized_interval_id=item["interval_id"],
                    import_id=item["import_id"],
                    segment_order=item["segment_order"],
                    interval_start=item["start"],
                    interval_end=item["end"],
                    rate_version_id=item["rate_version"].id,
                    tier_definition_id=definition.id,
                    tier_stable_id=item["tier_id"],
                    tier_name=item["tier_name"],
                    tou_period=item["tou_period"],
                    cumulative_start_kwh=item["cumulative_start_kwh"],
                    cumulative_end_kwh=item["cumulative_end_kwh"],
                    segment_energy_kwh=item["energy_kwh"],
                    price_per_kwh=item["price_per_kwh"],
                    unrounded_energy_charge=item["energy_charge"],
                    derived_threshold_kwh=item["threshold_kwh"],
                    usage_authority_type=authority.authority_type,
                    quality_flags=item["quality_flags"],
                    recalculation_version=recalculation_version,
                    created_at=datetime.now(UTC),
                )
            )
        for tier in thresholds:
            session.add(
                CycleTierSummary(
                    billing_cycle_id=cycle.id,
                    tier_stable_id=tier["tier_id"],
                    recalculation_version=recalculation_version,
                    tier_name=tier["name"],
                    lower_bound_kwh=tier["lower_bound_kwh"],
                    upper_bound_kwh=tier["upper_bound_kwh"],
                    usage_kwh=usage_by_tier[tier["tier_id"]],
                    energy_charge=charge_by_tier[tier["tier_id"]],
                    calculated_at=datetime.now(UTC),
                )
            )
        session.add(
            TierProjectionSnapshot(
                billing_cycle_id=cycle.id,
                calculated_at=datetime.now(UTC),
                method=projection.method,
                projected_usage_kwh=projection.projected_energy_kwh,
                projected_energy_charge=projected_energy_charge,
                projected_tier_stable_id=projected_tier["tier_id"] if projected_tier else None,
                confidence=confidence,
                coverage_percent=coverage_percent,
            )
        )
    return {
        "available": True,
        "utility_account_id": account.id,
        "account_name": account.name,
        "currency": account.currency,
        "pricing_model": active_version.pricing_model,
        "rate_version_id": active_version.id,
        "rate_version": active_version.version,
        "cycle": _cycle_payload(cycle, account.timezone),
        "authoritative_usage_kwh": str(cumulative),
        "usage_authority": authority_view,
        "current_tier": _tier_payload(current_tier),
        "current_rate_period": current_price_slice.bucket,
        "current_energy_price": str(current_price_slice.price_per_kwh),
        "remaining_kwh": str(remaining) if remaining is not None else None,
        "tiers": [
            {
                **(_tier_payload(tier) or {}),
                "usage_kwh": str(usage_by_tier[tier["tier_id"]]),
                "energy_charge": str(charge_by_tier[tier["tier_id"]]),
            }
            for tier in thresholds
        ],
        "energy_charge": str(actual_energy_charge),
        "blended_energy_rate": (
            str(actual_energy_charge / (cumulative - initial_usage))
            if cumulative > initial_usage
            else None
        ),
        "projected_usage_kwh": str(projection.projected_energy_kwh),
        "projected_energy_charge": str(projected_energy_charge),
        "projected_final_tier": _tier_payload(projected_tier),
        "projection_method": projection.method,
        "projection_confidence": confidence,
        "coverage_percent": str(coverage_percent),
        "bill_components": components,
        "estimated_total_bill": components["estimated_total"],
        "projected_total_bill": components["projected_total"],
        "utility_bill_comparison": bill_comparison,
        "recalculation_version": recalculation_version,
        "warnings": warnings,
        "disclosure": (
            "Energy charges are chronologically allocated estimates. "
            "The total estimate is not a utility bill."
        ),
    }


async def _bill_components(
    session: AsyncSession,
    account: UtilityAccount,
    engine: RateEngine,
    cycle: BillingCycle,
    usage: Decimal,
    *,
    projected_usage: Decimal,
    energy_charge: Decimal,
    projected_energy_charge: Decimal,
) -> dict[str, Any]:
    scope = account.cost_scope_default
    if scope != "full_account_estimate":
        return {
            "energy_charge": str(energy_charge),
            "fixed_charge": None,
            "credits": None,
            "adjustments": None,
            "estimated_total": None,
            "projected_total": None,
            "scope": "energy_only",
        }
    current = engine.calculate(
        start=cycle.starts_at,
        end=min(datetime.now(UTC), cycle.ends_at),
        energy_kwh=usage,
        cost_scope="full_account_estimate",
        baseline_allocation_kwh=account.baseline_allocation_kwh,
        cumulative_usage_before_kwh=ZERO,
        cycle_start=cycle.starts_at,
        cycle_end=cycle.ends_at,
    )
    projected = engine.calculate(
        start=cycle.starts_at,
        end=cycle.ends_at,
        energy_kwh=projected_usage,
        cost_scope="full_account_estimate",
        baseline_allocation_kwh=account.baseline_allocation_kwh,
        cumulative_usage_before_kwh=ZERO,
        cycle_start=cycle.starts_at,
        cycle_end=cycle.ends_at,
    )
    current_non_energy = current.total - current.energy_charge
    projected_non_energy = projected.total - projected.energy_charge
    return {
        "energy_charge": str(energy_charge),
        "fixed_charge": str(current.fixed_charge),
        "credits": str(-current.baseline_credit),
        "adjustments": str(current.cca_adjustment + current.other_adjustment),
        "estimated_total": str(energy_charge + current_non_energy),
        "projected_total": str(projected_energy_charge + projected_non_energy),
        "scope": scope,
        "provenance": {
            "rate_version": engine.plan.get("code"),
            "account_adjustments": current.adjustment_breakdown,
        },
    }


def _tier_payload(tier: dict[str, Any] | None) -> dict[str, Any] | None:
    if tier is None:
        return None
    return {
        "tier_id": tier["tier_id"],
        "name": tier["name"],
        "order": tier["order"],
        "lower_bound_kwh": str(tier["lower_bound_kwh"]),
        "upper_bound_kwh": (
            str(tier["upper_bound_kwh"]) if tier["upper_bound_kwh"] is not None else None
        ),
        "price_per_kwh": str(tier["price_per_kwh"]),
        "threshold_basis": tier["threshold_basis"],
        "derived_baseline_kwh": (
            str(tier["derived_baseline_kwh"]) if tier["derived_baseline_kwh"] is not None else None
        ),
        "rounding_policy": tier["rounding_policy"],
    }


def _cycle_payload(cycle: BillingCycle, timezone: str) -> dict[str, Any]:
    zone = ZoneInfo(timezone)
    total_days = (
        _aware_utc(cycle.ends_at).astimezone(zone).date()
        - _aware_utc(cycle.starts_at).astimezone(zone).date()
    ).days
    remaining_seconds = max(0, int((_aware_utc(cycle.ends_at) - datetime.now(UTC)).total_seconds()))
    return {
        "id": cycle.id,
        "starts_at": cycle.starts_at,
        "ends_at": cycle.ends_at,
        "days": total_days,
        "days_remaining": (remaining_seconds + 86399) // 86400,
        "status": cycle.status,
        "boundary_source": cycle.boundary_source,
        "exact_dates": cycle.explicit_meter_dates,
        "finalized_at": cycle.finalized_at,
    }


def _unavailable_status(
    account: UtilityAccount,
    cycle: BillingCycle,
    authority: dict[str, Any],
    reason: str,
    *,
    pricing_model: str | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "utility_account_id": account.id,
        "account_name": account.name,
        "currency": account.currency,
        "pricing_model": pricing_model,
        "cycle": _cycle_payload(cycle, account.timezone),
        "usage_authority": authority,
        "current_tier": None,
        "remaining_kwh": None,
        "tiers": [],
        "energy_charge": None,
        "estimated_total_bill": None,
        "projected_total_bill": None,
        "warnings": [reason],
        "configuration_action": "/admin?tab=sites-accounts",
        "disclosure": "Tier status is unavailable; the server does not guess account usage.",
    }


def normalized_import_rows(
    payload_rows: list[dict[str, Any]],
    import_kind: str,
    timezone: str | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    zone = ZoneInfo(timezone) if timezone else UTC

    def timestamp(value: Any) -> datetime:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(UTC)

    if import_kind in {"interval", "daily"}:
        for row in payload_rows:
            start = timestamp(row["start"])
            end = timestamp(row["end"])
            energy = Decimal(str(row["energy_kwh"]))
            if end <= start or energy < 0:
                raise ValueError("usage intervals require positive windows and non-negative energy")
            normalized.append(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "energy_kwh": str(energy),
                }
            )
    elif import_kind == "cycle_cumulative":
        for row in payload_rows:
            effective = timestamp(row["effective_at"])
            cumulative = Decimal(str(row["cumulative_kwh"]))
            if cumulative < 0:
                raise ValueError("cumulative usage cannot be negative")
            normalized.append(
                {
                    "effective_at": effective.isoformat(),
                    "cumulative_kwh": str(cumulative),
                }
            )
    elif import_kind == "cycle_dates":
        for row in payload_rows:
            start = timestamp(row["starts_at"])
            end = timestamp(row["ends_at"])
            duration_days = Decimal(str((end - start).total_seconds())) / Decimal("86400")
            if end <= start or duration_days < 20 or duration_days > 45:
                raise ValueError("billing cycles must be between 20 and 45 days")
            normalized.append({"starts_at": start.isoformat(), "ends_at": end.isoformat()})
    elif import_kind == "bill_total":
        for row in payload_rows:
            start = timestamp(row["starts_at"])
            end = timestamp(row["ends_at"])
            amount = Decimal(str(row["total_amount"]))
            usage = Decimal(str(row["usage_kwh"])) if row.get("usage_kwh") is not None else None
            if end <= start or amount < 0 or (usage is not None and usage < 0):
                raise ValueError("bill totals require a positive window and non-negative values")
            normalized.append(
                {
                    "starts_at": start.isoformat(),
                    "ends_at": end.isoformat(),
                    "total_amount": str(amount),
                    "usage_kwh": str(usage) if usage is not None else None,
                    "reference": str(row.get("reference") or "")[:500],
                }
            )
    else:
        raise ValueError("unsupported usage import kind")
    ordering_key = {
        "interval": "start",
        "daily": "start",
        "cycle_cumulative": "effective_at",
        "cycle_dates": "starts_at",
        "bill_total": "starts_at",
    }[import_kind]
    normalized.sort(key=lambda row: str(row[ordering_key]))
    return normalized


def import_quality(rows: list[dict[str, Any]], import_kind: str) -> dict[str, int]:
    serialized = [
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True) for row in rows
    ]
    duplicate_count = len(serialized) - len(set(serialized))
    overlap_count = 0
    gap_count = 0
    if import_kind in {"interval", "daily", "cycle_dates", "bill_total"}:
        start_key = "start" if import_kind in {"interval", "daily"} else "starts_at"
        end_key = "end" if import_kind in {"interval", "daily"} else "ends_at"
        windows = sorted(
            (
                datetime.fromisoformat(str(row[start_key])),
                datetime.fromisoformat(str(row[end_key])),
            )
            for row in rows
        )
        furthest_end: datetime | None = None
        for start, end in windows:
            if furthest_end is not None:
                if start < furthest_end:
                    overlap_count += 1
                elif import_kind in {"interval", "daily"} and start > furthest_end:
                    gap_count += 1
            furthest_end = end if furthest_end is None else max(furthest_end, end)
    return {
        "duplicate_row_count": duplicate_count,
        "overlap_count": overlap_count,
        "gap_count": gap_count,
    }


def import_digest(import_kind: str, timezone: str, rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        {"kind": import_kind, "timezone": timezone, "rows": rows},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
