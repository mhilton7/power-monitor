from __future__ import annotations

import csv
import io
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AggregateMember,
    AggregateSet,
    BillingCycle,
    Circuit,
    Device,
    NormalizedInterval,
    RateAssignment,
    RatePlan,
    RateVersion,
    RawReading,
    Site,
    TierAllocationSegment,
    UtilityAccount,
)
from app.problem import ProblemError
from app.rates.documents import engine_plan
from app.rates.engine import RateEngine
from app.rates.service import version_document
from app.schemas import (
    HistoryBucket,
    HistoryIndividualSeries,
    HistoryQueryRequest,
    HistoryQueryResponse,
    HistoryRangeSummary,
    HistoryRateContribution,
    HistoryResolvedScope,
)
from app.security.browser import SessionPrincipal

MAX_HISTORY_RANGE = timedelta(days=366)
MAX_HISTORY_SENSORS = 32
MAX_HISTORY_BUCKETS = 2000
MAX_SOURCE_ROWS = 250_000
ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _offset_text(value: datetime) -> str:
    offset = value.utcoffset() or timedelta(0)
    sign = "+" if offset >= timedelta(0) else "-"
    total_minutes = abs(int(offset.total_seconds() // 60))
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _csv_safe(value: object) -> object:
    if value is None:
        return ""
    text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


@dataclass
class ResolvedHistoryScope:
    scope_type: str
    display_name: str
    site: Site
    devices: list[Device]
    circuits: dict[str, Circuit]
    allocations: dict[str, Decimal]
    excluded_device_ids: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    overlap: bool = False


@dataclass(frozen=True)
class RateContext:
    account_id: str
    version: RateVersion
    plan: RatePlan
    engine: RateEngine
    start: datetime
    end: datetime
    adjustment_boundaries: tuple[datetime, ...]


@dataclass
class CostPart:
    account_id: str
    plan_id: str
    plan_name: str
    version_id: str
    version_number: int
    effective_from: date
    tou_period: str
    tier_id: str | None = None
    tier_name: str | None = None
    cumulative_start_kwh: Decimal | None = None
    cumulative_end_kwh: Decimal | None = None
    recalculation_version: int | None = None
    usage_authority_type: str | None = None
    energy_kwh: Decimal = ZERO
    cost: Decimal = ZERO


@dataclass
class DeviceBucketAccumulator:
    coverage_ranges: list[tuple[datetime, datetime]] = field(default_factory=list)
    power_weighted: Decimal = ZERO
    power_seconds: Decimal = ZERO
    peak_power_w: Decimal | None = None
    energy_kwh: Decimal = ZERO
    energy_available: bool = False
    voltage_weighted: Decimal = ZERO
    voltage_seconds: Decimal = ZERO
    voltage_min_v: Decimal | None = None
    voltage_max_v: Decimal | None = None
    current_weighted: Decimal = ZERO
    current_seconds: Decimal = ZERO
    factor_weighted: Decimal = ZERO
    factor_weight: Decimal = ZERO
    frequency_weighted: Decimal = ZERO
    frequency_seconds: Decimal = ZERO
    quality_flags: set[str] = field(default_factory=set)
    cost_parts: dict[tuple[str, str, str, str | None, Decimal, int | None], CostPart] = field(
        default_factory=dict
    )
    cost_missing: bool = False

    def add_cost(
        self,
        *,
        context: RateContext,
        tou_period: str,
        rate: Decimal,
        energy_kwh: Decimal,
        cost: Decimal,
        tier_id: str | None = None,
        tier_name: str | None = None,
        cumulative_start_kwh: Decimal | None = None,
        cumulative_end_kwh: Decimal | None = None,
        recalculation_version: int | None = None,
        usage_authority_type: str | None = None,
    ) -> None:
        key = (
            context.account_id,
            context.version.id,
            tou_period,
            tier_id,
            rate,
            recalculation_version,
        )
        part = self.cost_parts.get(key)
        if part is None:
            part = CostPart(
                account_id=context.account_id,
                plan_id=context.plan.id,
                plan_name=context.plan.name,
                version_id=context.version.id,
                version_number=context.version.version,
                effective_from=context.version.effective_from,
                tou_period=tou_period,
                tier_id=tier_id,
                tier_name=tier_name,
                cumulative_start_kwh=cumulative_start_kwh,
                cumulative_end_kwh=cumulative_end_kwh,
                recalculation_version=recalculation_version,
                usage_authority_type=usage_authority_type,
            )
            self.cost_parts[key] = part
        part.energy_kwh += energy_kwh
        part.cost += cost
        if cumulative_start_kwh is not None:
            part.cumulative_start_kwh = (
                cumulative_start_kwh
                if part.cumulative_start_kwh is None
                else min(part.cumulative_start_kwh, cumulative_start_kwh)
            )
        if cumulative_end_kwh is not None:
            part.cumulative_end_kwh = (
                cumulative_end_kwh
                if part.cumulative_end_kwh is None
                else max(part.cumulative_end_kwh, cumulative_end_kwh)
            )


def _merge_duration(ranges: list[tuple[datetime, datetime]]) -> Decimal:
    if not ranges:
        return ZERO
    ordered = sorted(ranges)
    start, end = ordered[0]
    total = ZERO
    for next_start, next_end in ordered[1:]:
        if next_start <= end:
            end = max(end, next_end)
            continue
        total += Decimal(str((end - start).total_seconds()))
        start, end = next_start, next_end
    return total + Decimal(str((end - start).total_seconds()))


def _circuit_ancestor(
    child_id: str | None, possible_ancestor: str | None, circuits: dict[str, Circuit]
) -> bool:
    if child_id is None or possible_ancestor is None or child_id == possible_ancestor:
        return child_id == possible_ancestor and child_id is not None
    current = circuits.get(child_id)
    visited: set[str] = set()
    while current and current.parent_id and current.parent_id not in visited:
        if current.parent_id == possible_ancestor:
            return True
        visited.add(current.parent_id)
        current = circuits.get(current.parent_id)
    return False


def _overlap_pairs(
    devices: list[Device], circuits: dict[str, Circuit]
) -> list[tuple[Device, Device]]:
    conflicts: list[tuple[Device, Device]] = []
    for index, left in enumerate(devices):
        for right in devices[index + 1 :]:
            if left.id == right.id:
                conflicts.append((left, right))
                continue
            if (
                left.circuit_id
                and right.circuit_id
                and (
                    _circuit_ancestor(left.circuit_id, right.circuit_id, circuits)
                    or _circuit_ancestor(right.circuit_id, left.circuit_id, circuits)
                )
            ):
                conflicts.append((left, right))
    return conflicts


async def resolve_history_scope(
    session: AsyncSession, principal: SessionPrincipal, request: HistoryQueryRequest
) -> ResolvedHistoryScope:
    scope = request.scope
    selected_ids: list[str] = []
    allocations: dict[str, Decimal] = {}
    site_id: str | None = None
    display_name = "History"
    warnings: list[dict[str, Any]] = []

    if scope.type == "device":
        selected_ids = [scope.device_id or ""]
    elif scope.type == "devices":
        selected_ids = list(scope.device_ids)
    elif scope.type == "circuit":
        circuit = await session.get(Circuit, scope.circuit_id or "")
        if circuit is None or not principal.can_access_site(circuit.site_id):
            raise ProblemError(
                404, "Resource not found", "Resource does not exist", "resource_missing"
            )
        site_id = circuit.site_id
        display_name = circuit.name
        selected_ids = list(
            await session.scalars(
                select(Device.id).where(
                    Device.site_id == circuit.site_id,
                    Device.circuit_id == circuit.id,
                    Device.lifecycle_status == "active",
                )
            )
        )
    elif scope.type == "site":
        site_id = scope.site_id
        if not site_id or not principal.can_access_site(site_id):
            raise ProblemError(
                404, "Resource not found", "Resource does not exist", "resource_missing"
            )
        selected_ids = list(
            await session.scalars(
                select(Device.id).where(
                    Device.site_id == site_id,
                    Device.include_in_default_site_total.is_(True),
                    Device.lifecycle_status == "active",
                )
            )
        )
    else:
        aggregate = await session.get(AggregateSet, scope.aggregate_set_id or "")
        if aggregate is None or not principal.can_access_site(aggregate.site_id):
            raise ProblemError(
                404, "Resource not found", "Resource does not exist", "resource_missing"
            )
        site_id = aggregate.site_id
        display_name = aggregate.name
        members = list(
            await session.scalars(
                select(AggregateMember).where(AggregateMember.aggregate_set_id == aggregate.id)
            )
        )
        for member in members:
            member_ids: list[str]
            if member.device_id:
                member_ids = [member.device_id]
            else:
                member_ids = list(
                    await session.scalars(
                        select(Device.id).where(
                            Device.site_id == aggregate.site_id,
                            Device.circuit_id == member.circuit_id,
                            Device.lifecycle_status == "active",
                        )
                    )
                )
            for device_id in member_ids:
                if device_id in allocations:
                    warnings.append(
                        {
                            "code": "duplicate_aggregate_member",
                            "message": "A saved aggregate resolves the same sensor more than once.",
                            "device_ids": [device_id],
                        }
                    )
                selected_ids.append(device_id)
                allocations[device_id] = member.allocation_percent / ONE_HUNDRED

    selected_ids = [value for value in selected_ids if value]
    if len(set(selected_ids)) > MAX_HISTORY_SENSORS:
        raise ProblemError(
            422,
            "Too many sensors",
            f"History supports at most {MAX_HISTORY_SENSORS} sensors per query",
            "history_sensor_limit",
        )
    unique_ids = list(dict.fromkeys(selected_ids))
    devices = (
        list(
            await session.scalars(
                select(Device).where(Device.id.in_(unique_ids)).order_by(Device.name, Device.id)
            )
        )
        if unique_ids
        else []
    )
    if len(devices) != len(unique_ids):
        raise ProblemError(404, "Resource not found", "Resource does not exist", "resource_missing")
    for device in devices:
        if not principal.can_access_site(device.site_id):
            raise ProblemError(
                404, "Resource not found", "Resource does not exist", "resource_missing"
            )
    device_sites = {device.site_id for device in devices}
    if site_id:
        device_sites.add(site_id)
    if len(device_sites) > 1:
        raise ProblemError(
            422,
            "Cross-site history is unavailable",
            "Select sensors from one authorized site at a time",
            "history_cross_site",
        )
    if not site_id:
        site_id = next(iter(device_sites), None)
    if site_id is None:
        raise ProblemError(
            422, "Empty history scope", "The selected scope has no site", "history_scope_empty"
        )
    site = await session.get(Site, site_id)
    if site is None or not principal.can_access_site(site.id):
        raise ProblemError(404, "Resource not found", "Resource does not exist", "resource_missing")
    if scope.type == "site":
        display_name = f"{site.name} total"
    elif scope.type in {"device", "devices"}:
        display_name = " + ".join(device.name for device in devices)

    circuit_rows = list(await session.scalars(select(Circuit).where(Circuit.site_id == site.id)))
    circuits = {item.id: item for item in circuit_rows}
    conflicts = _overlap_pairs(devices, circuits)
    excluded: list[str] = []
    if conflicts and scope.type == "site":
        excluded_set: set[str] = set()
        for left, right in conflicts:
            if _circuit_ancestor(left.circuit_id, right.circuit_id, circuits):
                excluded_set.add(left.id)
            elif _circuit_ancestor(right.circuit_id, left.circuit_id, circuits):
                excluded_set.add(right.id)
            else:
                excluded_set.add(max(left.id, right.id))
        devices = [device for device in devices if device.id not in excluded_set]
        excluded = sorted(excluded_set)
        warnings.append(
            {
                "code": "topology_items_excluded",
                "message": (
                    "Overlapping child or duplicate sensors were excluded from the site total."
                ),
                "device_ids": excluded,
            }
        )
        conflicts = _overlap_pairs(devices, circuits)
    if any(device.circuit_id is None for device in devices):
        warnings.append(
            {
                "code": "topology_incomplete",
                "message": (
                    "One or more sensors have no circuit assignment; "
                    "overlap cannot be fully verified."
                ),
                "device_ids": [device.id for device in devices if device.circuit_id is None],
            }
        )
    if conflicts:
        conflict_ids = sorted({device.id for pair in conflicts for device in pair})
        warnings.append(
            {
                "code": "topology_overlap",
                "message": "Selected parent, child, or duplicate circuit measurements overlap.",
                "device_ids": conflict_ids,
            }
        )
        if request.display_mode in {"combined", "combined_plus_individual"}:
            raise ProblemError(
                422,
                "Overlapping history selection",
                "Combined totals cannot include parent/child or duplicate circuit measurements",
                "history_topology_overlap",
                extra={"warnings": warnings},
            )
    for device in devices:
        allocations.setdefault(device.id, Decimal("1"))
    return ResolvedHistoryScope(
        scope_type=scope.type,
        display_name=display_name or site.name,
        site=site,
        devices=devices,
        circuits=circuits,
        allocations=allocations,
        excluded_device_ids=excluded,
        warnings=warnings,
        overlap=bool(conflicts),
    )


def _effective_bounds(version: RateVersion) -> tuple[datetime, datetime]:
    zone = ZoneInfo(version.timezone)
    start = datetime.combine(version.effective_from, time.min, tzinfo=zone).astimezone(UTC)
    end = (
        datetime.combine(
            version.effective_to + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(UTC)
        if version.effective_to
        else datetime.max.replace(tzinfo=UTC)
    )
    return start, end


def _adjustment_boundaries(plan: dict[str, Any], zone: ZoneInfo) -> tuple[datetime, ...]:
    values: set[datetime] = set()
    for adjustment in plan.get("adjustments", []):
        if adjustment.get("effective_from"):
            values.add(
                datetime.combine(
                    date.fromisoformat(str(adjustment["effective_from"])), time.min, tzinfo=zone
                ).astimezone(UTC)
            )
        if adjustment.get("effective_to"):
            values.add(
                datetime.combine(
                    date.fromisoformat(str(adjustment["effective_to"])) + timedelta(days=1),
                    time.min,
                    tzinfo=zone,
                ).astimezone(UTC)
            )
    return tuple(sorted(values))


async def _load_rate_contexts(
    session: AsyncSession,
    scope: ResolvedHistoryScope,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, list[RateContext]], dict[str, str | None]]:
    account_ids = {
        device.utility_account_id for device in scope.devices if device.utility_account_id
    }
    site_accounts = list(
        await session.scalars(select(UtilityAccount).where(UtilityAccount.site_id == scope.site.id))
    )
    fallback_account_id = site_accounts[0].id if len(site_accounts) == 1 else None
    device_accounts = {
        device.id: device.utility_account_id or fallback_account_id for device in scope.devices
    }
    account_ids.update(value for value in device_accounts.values() if value)
    accounts = {item.id: item for item in site_accounts if item.id in account_ids}
    assignments = (
        list(
            await session.scalars(
                select(RateAssignment).where(
                    RateAssignment.utility_account_id.in_(account_ids),
                    RateAssignment.effective_from < end,
                    (RateAssignment.effective_to.is_(None) | (RateAssignment.effective_to > start)),
                )
            )
        )
        if account_ids
        else []
    )
    version_ids = {item.rate_version_id for item in assignments}
    version_ids.update(
        account.active_rate_version_id
        for account in accounts.values()
        if account.active_rate_version_id
    )
    versions = {
        item.id: item
        for item in (
            list(await session.scalars(select(RateVersion).where(RateVersion.id.in_(version_ids))))
            if version_ids
            else []
        )
    }
    plan_ids = {version.rate_plan_id for version in versions.values()}
    plans = {
        item.id: item
        for item in (
            list(await session.scalars(select(RatePlan).where(RatePlan.id.in_(plan_ids))))
            if plan_ids
            else []
        )
    }
    contexts: dict[str, list[RateContext]] = defaultdict(list)
    assignments_by_account: dict[str, list[RateAssignment]] = defaultdict(list)
    for assignment in assignments:
        assignments_by_account[assignment.utility_account_id].append(assignment)

    engine_cache: dict[str, tuple[RateEngine, tuple[datetime, ...]]] = {}
    for account_id, account in accounts.items():
        account_assignments = assignments_by_account.get(account_id, [])
        if not account_assignments and account.active_rate_version_id:
            version = versions.get(account.active_rate_version_id)
            if version:
                effective_start, effective_end = _effective_bounds(version)
                account_assignments = [
                    RateAssignment(
                        utility_account_id=account_id,
                        rate_version_id=version.id,
                        effective_from=effective_start,
                        effective_to=effective_end,
                        created_at=effective_start,
                    )
                ]
        for assignment in account_assignments:
            version = versions.get(assignment.rate_version_id)
            if version is None:
                continue
            plan = plans.get(version.rate_plan_id)
            if plan is None:
                continue
            if version.id not in engine_cache:
                document = await version_document(session, version)
                calculated_plan = engine_plan(document)
                engine = RateEngine(calculated_plan)
                engine_cache[version.id] = (
                    engine,
                    _adjustment_boundaries(calculated_plan, engine.zone),
                )
            engine, adjustment_dates = engine_cache[version.id]
            version_start, version_end = _effective_bounds(version)
            context_start = max(_aware_utc(assignment.effective_from), version_start)
            context_end = min(
                _aware_utc(assignment.effective_to)
                if assignment.effective_to
                else datetime.max.replace(tzinfo=UTC),
                version_end,
            )
            if context_end <= start or context_start >= end or context_end <= context_start:
                continue
            contexts[account_id].append(
                RateContext(
                    account_id=account_id,
                    version=version,
                    plan=plan,
                    engine=engine,
                    start=context_start,
                    end=context_end,
                    adjustment_boundaries=adjustment_dates,
                )
            )
        contexts[account_id].sort(key=lambda item: item.start)
    return contexts, device_accounts


def _automatic_bucket(start: datetime, end: datetime) -> str:
    duration = end - start
    if duration <= timedelta(hours=12):
        return "5m"
    if duration <= timedelta(days=2):
        return "15m"
    if duration <= timedelta(days=31):
        return "1h"
    return "1d"


def _bucket_boundaries(
    start: datetime, end: datetime, bucket: str, zone: ZoneInfo
) -> list[datetime]:
    start = _aware_utc(start)
    end = _aware_utc(end)
    if bucket == "1d":
        local_start = start.astimezone(zone)
        boundaries = [start]
        day = local_start.date() + timedelta(days=1)
        while True:
            boundary = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
            if boundary >= end:
                break
            if boundary > start:
                boundaries.append(boundary)
            day += timedelta(days=1)
        boundaries.append(end)
        return sorted(set(boundaries))
    seconds = {"raw": 60, "5m": 300, "15m": 900, "1h": 3600}[bucket]
    start_epoch = int(start.timestamp())
    cursor_epoch = (start_epoch // seconds + 1) * seconds
    boundaries = [start]
    while cursor_epoch < int(end.timestamp()) and len(boundaries) <= MAX_HISTORY_BUCKETS:
        boundaries.append(datetime.fromtimestamp(cursor_epoch, UTC))
        cursor_epoch += seconds
    boundaries.append(end)
    return boundaries


def _find_cost_contexts(
    contexts: list[RateContext], start: datetime, end: datetime
) -> list[tuple[datetime, datetime, RateContext | None]]:
    boundaries = {start, end}
    for context in contexts:
        if start < context.start < end:
            boundaries.add(context.start)
        if start < context.end < end:
            boundaries.add(context.end)
        boundaries.update(value for value in context.adjustment_boundaries if start < value < end)
    ordered = sorted(boundaries)
    result: list[tuple[datetime, datetime, RateContext | None]] = []
    for left, right in pairwise(ordered):
        midpoint = left + (right - left) / 2
        matching = next((item for item in contexts if item.start <= midpoint < item.end), None)
        result.append((left, right, matching))
    return result


def _apply_cost(
    accumulator: DeviceBucketAccumulator,
    *,
    contexts: list[RateContext],
    start: datetime,
    end: datetime,
    energy_kwh: Decimal,
    normalized_interval_id: str | None,
    tier_segments: list[TierAllocationSegment],
) -> None:
    seconds = Decimal(str((end - start).total_seconds()))
    if seconds <= 0:
        return
    for left, right, context in _find_cost_contexts(contexts, start, end):
        part_seconds = Decimal(str((right - left).total_seconds()))
        part_energy = energy_kwh * part_seconds / seconds
        if context is None:
            accumulator.cost_missing = True
            continue
        if context.engine.pricing_model in {"tiered", "time_of_use_tiered"}:
            matching_segments = [
                segment
                for segment in tier_segments
                if segment.rate_version_id == context.version.id
                and _aware_utc(segment.interval_start) < right
                and _aware_utc(segment.interval_end) > left
            ]
            if not matching_segments:
                accumulator.cost_missing = True
                accumulator.quality_flags.add("tier_recalculation_required")
                continue
            exact_segments = [
                segment
                for segment in matching_segments
                if segment.normalized_interval_id == normalized_interval_id
            ]
            account_schedule_fallback = not exact_segments
            if exact_segments:
                matching_segments = exact_segments
            weighted_seconds = sum(
                (
                    Decimal(
                        str(
                            (
                                min(right, _aware_utc(segment.interval_end))
                                - max(left, _aware_utc(segment.interval_start))
                            ).total_seconds()
                        )
                    )
                    for segment in matching_segments
                ),
                ZERO,
            )
            for segment in matching_segments:
                segment_start = _aware_utc(segment.interval_start)
                segment_end = _aware_utc(segment.interval_end)
                overlap_start = max(left, segment_start)
                overlap_end = min(right, segment_end)
                segment_seconds = Decimal(str((segment_end - segment_start).total_seconds()))
                overlap_seconds = Decimal(str((overlap_end - overlap_start).total_seconds()))
                if segment_seconds <= 0 or overlap_seconds <= 0:
                    continue
                fraction = overlap_seconds / segment_seconds
                allocated_energy = (
                    part_energy * overlap_seconds / weighted_seconds
                    if account_schedule_fallback and weighted_seconds
                    else segment.segment_energy_kwh * fraction
                )
                allocated_cost = (
                    allocated_energy * segment.price_per_kwh
                    if account_schedule_fallback
                    else segment.unrounded_energy_charge * fraction
                )
                offset_fraction = (
                    Decimal(str((overlap_start - segment_start).total_seconds())) / segment_seconds
                )
                cumulative_start = (
                    segment.cumulative_start_kwh + segment.segment_energy_kwh * offset_fraction
                )
                cumulative_end = cumulative_start + allocated_energy
                label = (
                    f"{segment.tier_name} / {segment.tou_period}"
                    if segment.tou_period
                    else segment.tier_name
                )
                accumulator.add_cost(
                    context=context,
                    tou_period=label,
                    rate=segment.price_per_kwh,
                    energy_kwh=allocated_energy,
                    cost=allocated_cost,
                    tier_id=segment.tier_stable_id,
                    tier_name=segment.tier_name,
                    cumulative_start_kwh=cumulative_start,
                    cumulative_end_kwh=cumulative_end,
                    recalculation_version=segment.recalculation_version,
                    usage_authority_type=segment.usage_authority_type,
                )
            continue
        calculation = context.engine.calculate(
            start=left, end=right, energy_kwh=part_energy, cost_scope="energy_only"
        )
        energy_charge = calculation.energy_charge
        extra = calculation.total - energy_charge
        for item in calculation.slices:
            extra_share = extra * item.energy_kwh / part_energy if part_energy else ZERO
            cost = item.cost + extra_share
            rate = cost / item.energy_kwh if item.energy_kwh else item.price_per_kwh
            accumulator.add_cost(
                context=context,
                tou_period=item.bucket,
                rate=rate,
                energy_kwh=item.energy_kwh,
                cost=cost,
            )


def _add_reading(
    accumulator: DeviceBucketAccumulator,
    raw: RawReading,
    normalized: NormalizedInterval | None,
    left: datetime,
    right: datetime,
    contexts: list[RateContext],
    tier_segments: list[TierAllocationSegment],
) -> None:
    raw_start = _aware_utc(raw.interval_start)
    raw_end = _aware_utc(raw.interval_end)
    duration = Decimal(str((raw_end - raw_start).total_seconds()))
    seconds = Decimal(str((right - left).total_seconds()))
    if duration <= 0 or seconds <= 0:
        return
    accumulator.coverage_ranges.append((left, right))
    accumulator.quality_flags.update(raw.quality_flags)
    if raw.power_avg is not None:
        accumulator.power_weighted += raw.power_avg * seconds
        accumulator.power_seconds += seconds
    if raw.power_max is not None:
        accumulator.peak_power_w = (
            raw.power_max
            if accumulator.peak_power_w is None
            else max(accumulator.peak_power_w, raw.power_max)
        )
    selected_energy_wh = (
        normalized.selected_energy_wh if normalized else raw.device_interval_energy_wh
    )
    if selected_energy_wh is not None:
        energy_kwh = selected_energy_wh / Decimal("1000") * seconds / duration
        accumulator.energy_kwh += energy_kwh
        accumulator.energy_available = True
        _apply_cost(
            accumulator,
            contexts=contexts,
            start=left,
            end=right,
            energy_kwh=energy_kwh,
            normalized_interval_id=normalized.id if normalized else None,
            tier_segments=tier_segments,
        )
    else:
        accumulator.quality_flags.add("energy_unavailable")
    if raw.voltage_avg is not None:
        accumulator.voltage_weighted += raw.voltage_avg * seconds
        accumulator.voltage_seconds += seconds
        minimum = raw.voltage_min if raw.voltage_min is not None else raw.voltage_avg
        maximum = raw.voltage_max if raw.voltage_max is not None else raw.voltage_avg
        accumulator.voltage_min_v = (
            minimum
            if accumulator.voltage_min_v is None
            else min(accumulator.voltage_min_v, minimum)
        )
        accumulator.voltage_max_v = (
            maximum
            if accumulator.voltage_max_v is None
            else max(accumulator.voltage_max_v, maximum)
        )
    if raw.current_avg is not None:
        accumulator.current_weighted += raw.current_avg * seconds
        accumulator.current_seconds += seconds
    if raw.power_factor is not None:
        weight = abs(raw.power_avg or ZERO) * seconds
        if weight:
            accumulator.factor_weighted += raw.power_factor * weight
            accumulator.factor_weight += weight
    if raw.frequency_hz is not None:
        accumulator.frequency_weighted += raw.frequency_hz * seconds
        accumulator.frequency_seconds += seconds


def _rate_contributions(
    accumulator: DeviceBucketAccumulator, scale: Decimal = Decimal("1")
) -> list[HistoryRateContribution]:
    return [
        HistoryRateContribution(
            utility_account_id=part.account_id,
            rate_plan_id=part.plan_id,
            rate_plan_name=part.plan_name,
            rate_version_id=part.version_id,
            rate_version=part.version_number,
            rate_effective_from=part.effective_from,
            tou_period=part.tou_period,
            tier_id=part.tier_id,
            tier_name=part.tier_name,
            cumulative_start_kwh=part.cumulative_start_kwh,
            cumulative_end_kwh=part.cumulative_end_kwh,
            recalculation_version=part.recalculation_version,
            usage_authority_type=part.usage_authority_type,
            energy_kwh=part.energy_kwh * scale,
            rate_per_kwh=(part.cost / part.energy_kwh if part.energy_kwh else ZERO),
            energy_cost=part.cost * scale,
        )
        for part in sorted(
            accumulator.cost_parts.values(),
            key=lambda item: (
                item.version_id,
                item.recalculation_version or 0,
                item.cumulative_start_kwh or ZERO,
                item.tou_period,
            ),
        )
    ]


def _labels_from_contributions(
    contributions: list[HistoryRateContribution],
) -> tuple[str | None, Decimal | None, Decimal | None, str | None, str | None, date | None, bool]:
    if not contributions:
        return None, None, None, None, None, None, False
    periods = sorted({item.tou_period for item in contributions})
    plans = sorted({item.rate_plan_name for item in contributions})
    versions = sorted({item.rate_version_id for item in contributions})
    total_energy = sum((item.energy_kwh for item in contributions), ZERO)
    total_cost = sum((item.energy_cost for item in contributions), ZERO)
    rate = total_cost / total_energy if total_energy else contributions[0].rate_per_kwh
    return (
        " + ".join(periods),
        rate,
        total_cost,
        plans[0] if len(plans) == 1 else "Mixed rates",
        versions[0] if len(versions) == 1 else None,
        contributions[0].rate_effective_from if len(versions) == 1 else None,
        len({(item.rate_plan_id, item.rate_version_id) for item in contributions}) > 1,
    )


def _individual_bucket(
    *,
    accumulator: DeviceBucketAccumulator,
    device: Device,
    left: datetime,
    right: datetime,
    zone: ZoneInfo,
) -> HistoryBucket:
    bucket_seconds = Decimal(str((right - left).total_seconds()))
    covered = min(bucket_seconds, _merge_duration(accumulator.coverage_ranges))
    coverage = covered / bucket_seconds * ONE_HUNDRED if bucket_seconds else ZERO
    contributions = _rate_contributions(accumulator)
    period, rate, cost, plan_name, version_id, effective_from, mixed = _labels_from_contributions(
        contributions
    )
    quality = set(accumulator.quality_flags)
    if coverage < ONE_HUNDRED:
        quality.add("partial_coverage")
    if accumulator.energy_available and accumulator.cost_missing:
        quality.add("rate_unavailable")
        cost = None
    local_start = left.astimezone(zone)
    local_end = right.astimezone(zone)
    return HistoryBucket(
        interval_start_utc=left,
        interval_end_utc=right,
        local_start=local_start.isoformat(),
        local_end=local_end.isoformat(),
        utc_offset=_offset_text(local_start),
        series_id=device.id,
        series_name=device.name,
        device_id=device.id,
        included_sensor_count=1,
        contributing_sensor_count=1 if covered else 0,
        energy_kwh=accumulator.energy_kwh if accumulator.energy_available else None,
        average_power_w=(
            accumulator.power_weighted / accumulator.power_seconds
            if accumulator.power_seconds
            else None
        ),
        peak_power_w=accumulator.peak_power_w,
        voltage_min_v=accumulator.voltage_min_v,
        voltage_avg_v=(
            accumulator.voltage_weighted / accumulator.voltage_seconds
            if accumulator.voltage_seconds
            else None
        ),
        voltage_max_v=accumulator.voltage_max_v,
        current_a=(
            accumulator.current_weighted / accumulator.current_seconds
            if accumulator.current_seconds
            else None
        ),
        power_factor=(
            accumulator.factor_weighted / accumulator.factor_weight
            if accumulator.factor_weight
            else None
        ),
        frequency_hz=(
            accumulator.frequency_weighted / accumulator.frequency_seconds
            if accumulator.frequency_seconds
            else None
        ),
        tou_period=period,
        rate_per_kwh=rate if not accumulator.cost_missing else None,
        energy_cost=cost,
        rate_plan_name=plan_name,
        rate_version_id=version_id,
        rate_effective_from=effective_from,
        mixed_rates=mixed,
        coverage_percent=coverage,
        missing_sensor_ids=[] if covered == bucket_seconds else [device.id],
        quality_flags=sorted(quality),
        rate_contributions=contributions,
    )


def _combined_bucket(
    *,
    accumulators: dict[str, DeviceBucketAccumulator],
    devices: list[Device],
    allocations: dict[str, Decimal],
    display_name: str,
    left: datetime,
    right: datetime,
    zone: ZoneInfo,
    strict: bool,
) -> HistoryBucket:
    bucket_seconds = Decimal(str((right - left).total_seconds()))
    expected = bucket_seconds * Decimal(len(devices))
    covered_total = ZERO
    missing: list[str] = []
    contributing = 0
    energy = ZERO
    energy_available = False
    power = ZERO
    power_available = False
    peak = ZERO
    peak_available = False
    voltage_weighted = ZERO
    voltage_weight = ZERO
    voltage_min: Decimal | None = None
    voltage_max: Decimal | None = None
    factor_weighted = ZERO
    factor_weight = ZERO
    frequency_weighted = ZERO
    frequency_weight = ZERO
    quality: set[str] = set()
    contributions: list[HistoryRateContribution] = []
    cost_missing = False
    for device in devices:
        accumulator = accumulators[device.id]
        scale = allocations.get(device.id, Decimal("1"))
        covered = min(bucket_seconds, _merge_duration(accumulator.coverage_ranges))
        covered_total += covered
        if covered:
            contributing += 1
        if covered < bucket_seconds:
            missing.append(device.id)
        if accumulator.energy_available:
            energy += accumulator.energy_kwh * scale
            energy_available = True
            if accumulator.cost_missing:
                cost_missing = True
        if accumulator.power_seconds:
            power += accumulator.power_weighted / accumulator.power_seconds * scale
            power_available = True
        if accumulator.peak_power_w is not None:
            peak += accumulator.peak_power_w * scale
            peak_available = True
        if accumulator.voltage_seconds:
            voltage_weighted += accumulator.voltage_weighted / accumulator.voltage_seconds * covered
            voltage_weight += covered
            voltage_min = (
                accumulator.voltage_min_v
                if voltage_min is None
                else min(voltage_min, accumulator.voltage_min_v or voltage_min)
            )
            voltage_max = (
                accumulator.voltage_max_v
                if voltage_max is None
                else max(voltage_max, accumulator.voltage_max_v or voltage_max)
            )
        if accumulator.factor_weight:
            average_power = abs(accumulator.power_weighted / accumulator.power_seconds)
            factor_weighted += (
                accumulator.factor_weighted / accumulator.factor_weight * average_power
            )
            factor_weight += average_power
        if accumulator.frequency_seconds:
            frequency_weighted += (
                accumulator.frequency_weighted / accumulator.frequency_seconds * covered
            )
            frequency_weight += covered
        quality.update(accumulator.quality_flags)
        contributions.extend(_rate_contributions(accumulator, scale))
    coverage = covered_total / expected * ONE_HUNDRED if expected else ZERO
    if coverage < ONE_HUNDRED:
        quality.add("partial_coverage")
    if len(devices) > 1:
        quality.add("aggregate_current_unavailable")
    period, rate, cost, plan_name, version_id, effective_from, mixed = _labels_from_contributions(
        contributions
    )
    if energy_available and cost_missing:
        quality.add("rate_unavailable")
        rate = None
        cost = None
    withheld = strict and coverage < ONE_HUNDRED
    if withheld:
        quality.add("strict_coverage_withheld")
    local_start = left.astimezone(zone)
    local_end = right.astimezone(zone)
    return HistoryBucket(
        interval_start_utc=left,
        interval_end_utc=right,
        local_start=local_start.isoformat(),
        local_end=local_end.isoformat(),
        utc_offset=_offset_text(local_start),
        series_id="combined",
        series_name=display_name,
        included_sensor_count=len(devices),
        contributing_sensor_count=contributing,
        energy_kwh=None if withheld or not energy_available else energy,
        average_power_w=None if withheld or not power_available else power,
        peak_power_w=None if withheld or not peak_available else peak,
        voltage_min_v=None if withheld else voltage_min,
        voltage_avg_v=(
            None if withheld or not voltage_weight else voltage_weighted / voltage_weight
        ),
        voltage_max_v=None if withheld else voltage_max,
        current_a=None,
        power_factor=(None if withheld or not factor_weight else factor_weighted / factor_weight),
        frequency_hz=(
            None if withheld or not frequency_weight else frequency_weighted / frequency_weight
        ),
        tou_period=period,
        rate_per_kwh=None if withheld else rate,
        energy_cost=None if withheld else cost,
        rate_plan_name=plan_name,
        rate_version_id=version_id,
        rate_effective_from=effective_from,
        mixed_rates=mixed,
        coverage_percent=coverage,
        missing_sensor_ids=missing,
        quality_flags=sorted(quality),
        rate_contributions=contributions,
    )


def _summary(points: list[HistoryBucket], start: datetime, end: datetime) -> HistoryRangeSummary:
    selected = [
        point
        for point in points
        if point.interval_start_utc < end and point.interval_end_utc > start
    ]
    energy_values = [point.energy_kwh for point in selected if point.energy_kwh is not None]
    cost_values = [point.energy_cost for point in selected if point.energy_cost is not None]
    energy = sum(energy_values, ZERO) if energy_values else None
    cost_complete = all(
        point.energy_cost is not None
        for point in selected
        if point.energy_kwh is not None and point.energy_kwh != ZERO
    )
    cost = sum(cost_values, ZERO) if cost_values and cost_complete else None
    duration_seconds = sum(
        (
            Decimal(str((point.interval_end_utc - point.interval_start_utc).total_seconds()))
            for point in selected
            if point.average_power_w is not None
        ),
        ZERO,
    )
    average_power = (
        sum(
            (
                (point.average_power_w or ZERO)
                * Decimal(str((point.interval_end_utc - point.interval_start_utc).total_seconds()))
                for point in selected
                if point.average_power_w is not None
            ),
            ZERO,
        )
        / duration_seconds
        if duration_seconds
        else None
    )
    peak_points = [point for point in selected if point.peak_power_w is not None]
    highest_cost = max(
        (point for point in selected if point.energy_cost is not None),
        key=lambda point: point.energy_cost or ZERO,
        default=None,
    )
    highest_usage = max(
        (point for point in selected if point.energy_kwh is not None),
        key=lambda point: point.energy_kwh or ZERO,
        default=None,
    )
    tou: dict[str, dict[str, Decimal]] = {}
    for point in selected:
        for contribution in point.rate_contributions:
            item = tou.setdefault(
                contribution.tou_period, {"energy_kwh": ZERO, "energy_cost": ZERO}
            )
            item["energy_kwh"] += contribution.energy_kwh
            item["energy_cost"] += contribution.energy_cost
    weighted_coverage_seconds = sum(
        (
            point.coverage_percent
            * Decimal(str((point.interval_end_utc - point.interval_start_utc).total_seconds()))
            for point in selected
        ),
        ZERO,
    )
    all_seconds = sum(
        (
            Decimal(str((point.interval_end_utc - point.interval_start_utc).total_seconds()))
            for point in selected
        ),
        ZERO,
    )
    return HistoryRangeSummary(
        start_utc=start,
        end_utc=end,
        energy_kwh=energy,
        energy_cost=cost,
        blended_rate_per_kwh=cost / energy if cost is not None and energy else None,
        average_power_w=average_power,
        peak_power_w=max((point.peak_power_w or ZERO for point in peak_points), default=None),
        highest_cost_bucket_start=highest_cost.interval_start_utc if highest_cost else None,
        highest_cost_bucket_value=highest_cost.energy_cost if highest_cost else None,
        highest_usage_bucket_start=highest_usage.interval_start_utc if highest_usage else None,
        highest_usage_bucket_kwh=highest_usage.energy_kwh if highest_usage else None,
        coverage_percent=weighted_coverage_seconds / all_seconds if all_seconds else ZERO,
        contributing_sensor_count=max(
            (point.contributing_sensor_count for point in selected), default=0
        ),
        tou_breakdown=tou,
    )


def _withhold_overlapping_totals(summary: HistoryRangeSummary) -> None:
    """Keep coverage evidence but never expose a parent/child aggregate as a total."""
    summary.energy_kwh = None
    summary.energy_cost = None
    summary.blended_rate_per_kwh = None
    summary.average_power_w = None
    summary.peak_power_w = None
    summary.highest_cost_bucket_start = None
    summary.highest_cost_bucket_value = None
    summary.highest_usage_bucket_start = None
    summary.highest_usage_bucket_kwh = None
    summary.tou_breakdown = {}


async def query_history(
    session: AsyncSession, principal: SessionPrincipal, request: HistoryQueryRequest
) -> HistoryQueryResponse:
    start = _aware_utc(request.start_utc)
    end = _aware_utc(request.end_utc)
    if end - start > MAX_HISTORY_RANGE:
        raise ProblemError(
            422,
            "History range is too large",
            "Select a range of 366 days or less",
            "history_range_limit",
        )
    resolved = await resolve_history_scope(session, principal, request)
    try:
        zone = ZoneInfo(request.timezone or resolved.site.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ProblemError(
            422, "Invalid timezone", "Use a valid IANA timezone", "history_timezone_invalid"
        ) from exc
    bucket = _automatic_bucket(start, end) if request.bucket == "auto" else request.bucket
    if bucket == "raw" and end - start > timedelta(days=2):
        raise ProblemError(
            422,
            "Raw history range is too large",
            "Raw history is limited to two days; select an aggregated bucket",
            "history_raw_range_limit",
        )
    boundaries = _bucket_boundaries(start, end, bucket, zone)
    total_buckets = len(boundaries) - 1
    if total_buckets > MAX_HISTORY_BUCKETS:
        raise ProblemError(
            422,
            "Too many history buckets",
            "Select a coarser bucket size",
            "history_bucket_limit",
        )
    device_ids = [device.id for device in resolved.devices]
    rows = (
        list(
            (
                await session.execute(
                    select(RawReading, NormalizedInterval)
                    .join(
                        NormalizedInterval,
                        NormalizedInterval.raw_reading_id == RawReading.id,
                        isouter=True,
                    )
                    .where(
                        RawReading.device_id.in_(device_ids),
                        RawReading.interval_end > start,
                        RawReading.interval_start < end,
                    )
                    .order_by(RawReading.interval_start, RawReading.device_id, RawReading.sequence)
                    .limit(MAX_SOURCE_ROWS + 1)
                )
            ).all()
        )
        if device_ids
        else []
    )
    if len(rows) > MAX_SOURCE_ROWS:
        raise ProblemError(
            422,
            "History query is too large",
            "Select a shorter range or coarser scope",
            "history_source_row_limit",
        )
    contexts, device_accounts = await _load_rate_contexts(session, resolved, start, end)
    tier_segments_by_account: dict[str, list[TierAllocationSegment]] = defaultdict(list)
    account_ids = {value for value in device_accounts.values() if value}
    if account_ids:
        tier_segments = list(
            await session.scalars(
                select(TierAllocationSegment)
                .join(
                    BillingCycle,
                    BillingCycle.id == TierAllocationSegment.billing_cycle_id,
                )
                .where(
                    TierAllocationSegment.utility_account_id.in_(account_ids),
                    TierAllocationSegment.recalculation_version
                    == BillingCycle.recalculation_version,
                    TierAllocationSegment.interval_start < end,
                    TierAllocationSegment.interval_end > start,
                )
                .order_by(
                    TierAllocationSegment.interval_start,
                    TierAllocationSegment.segment_order,
                )
            )
        )
        for segment in tier_segments:
            tier_segments_by_account[segment.utility_account_id].append(segment)
    accumulators: list[dict[str, DeviceBucketAccumulator]] = [
        {device.id: DeviceBucketAccumulator() for device in resolved.devices}
        for _ in range(total_buckets)
    ]
    for raw, normalized in rows:
        raw_start = max(start, _aware_utc(raw.interval_start))
        raw_end = min(end, _aware_utc(raw.interval_end))
        if raw_end <= raw_start:
            continue
        index = max(0, bisect_right(boundaries, raw_start) - 1)
        while index < total_buckets and boundaries[index] < raw_end:
            left = max(raw_start, boundaries[index])
            right = min(raw_end, boundaries[index + 1])
            if right > left:
                account_id = device_accounts.get(raw.device_id)
                _add_reading(
                    accumulators[index][raw.device_id],
                    raw,
                    normalized,
                    left,
                    right,
                    contexts.get(account_id or "", []),
                    tier_segments_by_account.get(account_id or "", []),
                )
            index += 1
    combined_all: list[HistoryBucket] = []
    individual_all: dict[str, list[HistoryBucket]] = {device.id: [] for device in resolved.devices}
    for index in range(total_buckets):
        left, right = boundaries[index], boundaries[index + 1]
        combined_all.append(
            _combined_bucket(
                accumulators=accumulators[index],
                devices=resolved.devices,
                allocations=resolved.allocations,
                display_name=resolved.display_name,
                left=left,
                right=right,
                zone=zone,
                strict=request.strict_coverage,
            )
        )
        for device in resolved.devices:
            individual_all[device.id].append(
                _individual_bucket(
                    accumulator=accumulators[index][device.id],
                    device=device,
                    left=left,
                    right=right,
                    zone=zone,
                )
            )
    summary = _summary(combined_all, start, end)
    if resolved.overlap and request.display_mode == "individual":
        _withhold_overlapping_totals(summary)
    selected_summary = (
        _summary(
            combined_all,
            _aware_utc(request.selection_start_utc),
            _aware_utc(request.selection_end_utc),
        )
        if request.selection_start_utc and request.selection_end_utc
        else None
    )
    if resolved.overlap and request.display_mode == "individual" and selected_summary:
        _withhold_overlapping_totals(selected_summary)
    page_start = (request.page - 1) * request.page_size
    page_end = page_start + request.page_size
    combined = (
        combined_all[page_start:page_end]
        if request.display_mode in {"combined", "combined_plus_individual"}
        else []
    )
    individual = (
        [
            HistoryIndividualSeries(
                device_id=device.id,
                name=device.name,
                circuit_name=(
                    resolved.circuits[device.circuit_id].name
                    if device.circuit_id in resolved.circuits
                    else None
                ),
                status=device.status,
                points=individual_all[device.id][page_start:page_end],
            )
            for device in resolved.devices
        ]
        if request.display_mode in {"individual", "combined_plus_individual"}
        else []
    )
    all_contributions = [
        contribution for point in combined_all for contribution in point.rate_contributions
    ]
    version_map: dict[str, dict[str, Any]] = {}
    for contribution in all_contributions:
        version_map[contribution.rate_version_id] = {
            "rate_plan_id": contribution.rate_plan_id,
            "rate_plan_name": contribution.rate_plan_name,
            "rate_version_id": contribution.rate_version_id,
            "rate_version": contribution.rate_version,
            "effective_from": contribution.rate_effective_from,
        }
    if any(
        point.energy_kwh is not None and "rate_unavailable" in point.quality_flags
        for point in combined_all
    ):
        tier_recalculation_required = any(
            "tier_recalculation_required" in point.quality_flags for point in combined_all
        )
        resolved.warnings.append(
            {
                "code": "rate_unavailable",
                "message": (
                    "Cost is unavailable until chronological billing-cycle tier "
                    "allocation is recalculated."
                    if tier_recalculation_required
                    else "Cost is unavailable where a selected sensor has no "
                    "historically effective rate assignment."
                ),
                "device_ids": [
                    device.id
                    for device in resolved.devices
                    if not contexts.get(device_accounts.get(device.id) or "")
                ],
            }
        )
    mixed_rates = len(
        {(item.rate_plan_id, item.rate_version_id) for item in all_contributions}
    ) > 1 or any(point.mixed_rates for point in combined_all)
    return HistoryQueryResponse(
        scope=HistoryResolvedScope(
            type=request.scope.type,
            display_name=resolved.display_name,
            site_id=resolved.site.id,
            site_name=resolved.site.name,
            timezone=zone.key,
            included_device_ids=[device.id for device in resolved.devices],
            included_device_names=[device.name for device in resolved.devices],
            excluded_device_ids=resolved.excluded_device_ids,
            mixed_rates=mixed_rates,
        ),
        display_mode=request.display_mode,
        metrics=list(request.metrics),
        bucket=bucket,
        summary=summary,
        selected_summary=selected_summary,
        combined=combined,
        individual=individual,
        rate_versions_used=list(version_map.values()),
        warnings=resolved.warnings,
        total_buckets=total_buckets,
        page=request.page,
        page_size=request.page_size,
        next_page=request.page + 1 if page_end < total_buckets else None,
    )


def history_csv(response: HistoryQueryResponse) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["power-monitor-history-export/1.0"])
    writer.writerow(["scope_type", _csv_safe(response.scope.type)])
    writer.writerow(["scope_name", _csv_safe(response.scope.display_name)])
    writer.writerow(["site_id", _csv_safe(response.scope.site_id)])
    writer.writerow(["timezone", _csv_safe(response.scope.timezone)])
    writer.writerow(["display_mode", _csv_safe(response.display_mode)])
    writer.writerow(["bucket", _csv_safe(response.bucket)])
    writer.writerow(["included_device_ids", "|".join(response.scope.included_device_ids)])
    writer.writerow(
        ["included_device_names", _csv_safe("|".join(response.scope.included_device_names))]
    )
    writer.writerow(["excluded_device_ids", "|".join(response.scope.excluded_device_ids)])
    writer.writerow([])
    writer.writerow(
        [
            "series_type",
            "series_id",
            "series_name",
            "device_id",
            "interval_start_utc",
            "interval_end_utc",
            "local_start",
            "local_end",
            "utc_offset",
            "energy_kwh",
            "average_power_w",
            "peak_power_w",
            "voltage_min_v",
            "voltage_avg_v",
            "voltage_max_v",
            "current_a",
            "power_factor",
            "frequency_hz",
            "tou_period",
            "rate_per_kwh",
            "interval_energy_cost",
            "rate_plan",
            "rate_version_id",
            "mixed_rates",
            "included_sensor_count",
            "contributing_sensor_count",
            "coverage_percent",
            "missing_sensor_ids",
            "quality_flags",
            "rate_contributions_json",
        ]
    )

    def write_point(series_type: str, point: HistoryBucket) -> None:
        import json

        writer.writerow(
            [
                series_type,
                _csv_safe(point.series_id),
                _csv_safe(point.series_name),
                _csv_safe(point.device_id),
                point.interval_start_utc.isoformat(),
                point.interval_end_utc.isoformat(),
                point.local_start,
                point.local_end,
                point.utc_offset,
                point.energy_kwh,
                point.average_power_w,
                point.peak_power_w,
                point.voltage_min_v,
                point.voltage_avg_v,
                point.voltage_max_v,
                point.current_a,
                point.power_factor,
                point.frequency_hz,
                _csv_safe(point.tou_period),
                point.rate_per_kwh,
                point.energy_cost,
                _csv_safe(point.rate_plan_name),
                point.rate_version_id,
                point.mixed_rates,
                point.included_sensor_count,
                point.contributing_sensor_count,
                point.coverage_percent,
                "|".join(point.missing_sensor_ids),
                "|".join(point.quality_flags),
                json.dumps(
                    [item.model_dump(mode="json") for item in point.rate_contributions],
                    separators=(",", ":"),
                ),
            ]
        )

    for point in response.combined:
        write_point("combined", point)
    for series in response.individual:
        for point in series.points:
            write_point("individual", point)
    return output.getvalue()
