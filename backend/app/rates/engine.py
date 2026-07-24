from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, TypedDict, cast
from zoneinfo import ZoneInfo


class PeriodTuple(TypedDict):
    start: int
    end: int
    bucket: str
    rate: Decimal


@dataclass(frozen=True)
class CostSlice:
    start: datetime
    end: datetime
    energy_kwh: Decimal
    bucket: str
    price_per_kwh: Decimal
    cost: Decimal
    tier_id: str | None = None
    tier_name: str | None = None
    tou_period: str | None = None
    cumulative_start_kwh: Decimal | None = None
    cumulative_end_kwh: Decimal | None = None


@dataclass(frozen=True)
class Calculation:
    slices: tuple[CostSlice, ...]
    energy_by_bucket: dict[str, Decimal]
    energy_by_tier: dict[str, Decimal]
    charge_by_tier: dict[str, Decimal]
    tier_thresholds: tuple[dict[str, Any], ...]
    energy_charge: Decimal
    fixed_charge: Decimal
    baseline_credit: Decimal
    cca_adjustment: Decimal
    other_adjustment: Decimal
    adjustment_breakdown: dict[str, Decimal]
    total: Decimal


@dataclass(frozen=True)
class Projection:
    method: str
    actual_energy_kwh: Decimal
    projected_energy_kwh: Decimal
    elapsed_percent: Decimal
    confidence: str


def project_billing_cycle(
    *,
    actual_energy_kwh: Decimal,
    elapsed_seconds: int,
    total_seconds: int,
    method: Literal["straight_line", "recent_7_day", "same_weekday_profile"],
    recent_daily_average_kwh: Decimal | None = None,
    profile_remaining_kwh: Decimal | None = None,
) -> Projection:
    if actual_energy_kwh < 0 or elapsed_seconds <= 0 or total_seconds < elapsed_seconds:
        raise ValueError("projection inputs must describe a positive partial billing cycle")
    remaining = total_seconds - elapsed_seconds
    if method == "straight_line":
        projected = actual_energy_kwh * Decimal(total_seconds) / Decimal(elapsed_seconds)
    elif method == "recent_7_day":
        if recent_daily_average_kwh is None or recent_daily_average_kwh < 0:
            raise ValueError("recent seven-day projection requires a non-negative daily average")
        projected = actual_energy_kwh + recent_daily_average_kwh * Decimal(remaining) / Decimal(
            86400
        )
    else:
        if profile_remaining_kwh is None or profile_remaining_kwh < 0:
            raise ValueError("same-weekday projection requires remaining profile energy")
        projected = actual_energy_kwh + profile_remaining_kwh
    elapsed_percent = Decimal(elapsed_seconds) / Decimal(total_seconds) * Decimal("100")
    confidence = "low" if elapsed_percent < 25 else "medium" if elapsed_percent < 60 else "high"
    return Projection(method, actual_energy_kwh, projected, elapsed_percent, confidence)


def seed_path() -> Path:
    return Path(__file__).resolve().parents[3] / "shared" / "schemas" / "sce-rates-2026-06-01.json"


def load_seed_plans(path: Path | None = None) -> dict[str, dict[str, Any]]:
    data = json.loads((path or seed_path()).read_text(encoding="utf-8"))
    return {str(plan["code"]): plan for plan in data["plans"]}


def _round_currency(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class RateEngine:
    algorithm_version = "rate-engine/2.0.0"

    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        self.zone = ZoneInfo(str(plan["timezone"]))
        self.pricing_model = str(plan.get("pricing_model", "time_of_use"))
        self.validate_plan()

    def validate_plan(self) -> None:
        if self.pricing_model not in {
            "flat",
            "time_of_use",
            "tiered",
            "time_of_use_tiered",
        }:
            raise ValueError("unsupported pricing model")
        if self.pricing_model == "flat":
            if self.plan.get("flat_rate_per_kwh") is None:
                raise ValueError("flat pricing requires a rate")
            if Decimal(str(self.plan["flat_rate_per_kwh"])) < 0:
                raise ValueError("flat energy rate cannot be negative")
        if self.pricing_model in {"tiered", "time_of_use_tiered"}:
            tiers = sorted(self.plan.get("tiers", []), key=lambda item: int(item["order"]))
            if len(tiers) < 2 or [int(item["order"]) for item in tiers] != list(range(len(tiers))):
                raise ValueError("tiered pricing requires ordered contiguous tiers")
            threshold = self.plan.get("billing_cycle", {}).get("threshold", {})
            basis = str(threshold.get("basis", "fixed_cycle_kwh"))
            if basis not in {"fixed_cycle_kwh", "daily_baseline_kwh"}:
                raise ValueError("unsupported tier threshold basis")
            tier_cursor = Decimal("0")
            lower_key = (
                "lower_bound_multiplier"
                if basis == "daily_baseline_kwh"
                else "lower_bound_inclusive_kwh"
            )
            upper_key = (
                "upper_bound_multiplier"
                if basis == "daily_baseline_kwh"
                else "upper_bound_exclusive_kwh"
            )
            tier_ids: set[str] = set()
            for index, tier in enumerate(tiers):
                tier_id = str(tier.get("tier_id", ""))
                if not tier_id or tier_id in tier_ids:
                    raise ValueError("tier IDs must be present and unique")
                tier_ids.add(tier_id)
                lower = Decimal(str(tier.get(lower_key) or "0"))
                upper_raw = tier.get(upper_key)
                upper = Decimal(str(upper_raw)) if upper_raw is not None else None
                price = Decimal(str(tier.get("price_per_kwh", "0")))
                if lower != tier_cursor or price < 0:
                    raise ValueError("tier bounds must be gap-free and rates non-negative")
                if index < len(tiers) - 1 and (upper is None or upper <= lower):
                    raise ValueError("every non-final tier needs a greater upper boundary")
                if upper is not None:
                    tier_cursor = upper
            if (
                tiers[-1].get("upper_bound_exclusive_kwh") is not None
                or tiers[-1].get("upper_bound_multiplier") is not None
            ):
                raise ValueError("the final tier must be open-ended")
        if self.pricing_model not in {"time_of_use", "time_of_use_tiered"}:
            return
        schedules: list[tuple[str, dict[str, list[list[Any]]]]] = list(self.plan["periods"].items())
        schedules.extend(
            (f"{season}/special", date_schedules)
            for season, date_schedules in self.plan.get("special_schedules", {}).items()
        )
        for season, day_types in schedules:
            for day_type, raw_periods in day_types.items():
                periods = sorted(raw_periods, key=lambda item: int(item[0]))
                cursor = 0
                for start, end, bucket, price in periods:
                    if int(start) != cursor or int(end) <= int(start) or Decimal(str(price)) < 0:
                        raise ValueError(
                            f"{season}/{day_type} periods must be non-overlapping and contiguous"
                        )
                    if not bucket:
                        raise ValueError("rate bucket cannot be empty")
                    cursor = int(end)
                if cursor != 1440:
                    raise ValueError(f"{season}/{day_type} does not cover 24 hours")

    @staticmethod
    def _date_in_range(value: date, start_text: str, end_text: str) -> bool:
        start_month, start_day = (int(part) for part in start_text.split("-"))
        end_month, end_day = (int(part) for part in end_text.split("-"))
        current = (value.month, value.day)
        start = (start_month, start_day)
        end = (end_month, end_day)
        return (start <= end and start <= current <= end) or (
            start > end and (current >= start or current <= end)
        )

    def _baseline_for_cycle(self, cycle_start: datetime, cycle_end: datetime) -> Decimal:
        threshold = self.plan.get("billing_cycle", {}).get("threshold", {})
        default = threshold.get("daily_baseline_kwh")
        seasonal = threshold.get("seasonal_baselines", [])
        start_day = cycle_start.astimezone(self.zone).date()
        end_day = cycle_end.astimezone(self.zone).date()
        total = Decimal("0")
        cursor = start_day
        while cursor < end_day:
            configured = next(
                (
                    item
                    for item in seasonal
                    if self._date_in_range(cursor, str(item["start"]), str(item["end"]))
                ),
                None,
            )
            raw = configured.get("daily_kwh") if configured else default
            if raw is None:
                raise ValueError(f"no daily baseline is configured for {cursor.isoformat()}")
            total += Decimal(str(raw))
            cursor += timedelta(days=1)
        if end_day <= start_day:
            raise ValueError("billing cycle must span at least one local day")
        return total

    @staticmethod
    def _round_threshold(value: Decimal, policy: str) -> Decimal:
        if policy == "none":
            return value
        rounding = {
            "nearest_kwh": ROUND_HALF_UP,
            "floor_kwh": ROUND_FLOOR,
            "ceil_kwh": ROUND_CEILING,
        }[policy]
        return value.quantize(Decimal("1"), rounding=rounding)

    def resolved_tiers(
        self, *, cycle_start: datetime, cycle_end: datetime
    ) -> tuple[dict[str, Any], ...]:
        if self.pricing_model not in {"tiered", "time_of_use_tiered"}:
            return ()
        threshold = self.plan.get("billing_cycle", {}).get("threshold", {})
        basis = str(threshold.get("basis", "fixed_cycle_kwh"))
        policy = str(threshold.get("rounding_policy", "none"))
        baseline = (
            self._baseline_for_cycle(cycle_start, cycle_end)
            if basis == "daily_baseline_kwh"
            else None
        )
        result: list[dict[str, Any]] = []
        for tier in sorted(self.plan.get("tiers", []), key=lambda item: int(item["order"])):
            if basis == "daily_baseline_kwh":
                lower = self._round_threshold(
                    (baseline or Decimal("0"))
                    * Decimal(str(tier.get("lower_bound_multiplier") or "0")),
                    policy,
                )
                raw_upper = tier.get("upper_bound_multiplier")
                upper = (
                    self._round_threshold(
                        (baseline or Decimal("0")) * Decimal(str(raw_upper)), policy
                    )
                    if raw_upper is not None
                    else None
                )
            else:
                lower = Decimal(str(tier.get("lower_bound_inclusive_kwh", "0")))
                raw_upper = tier.get("upper_bound_exclusive_kwh")
                upper = Decimal(str(raw_upper)) if raw_upper is not None else None
            result.append(
                {
                    **tier,
                    "lower_bound_kwh": lower,
                    "upper_bound_kwh": upper,
                    "threshold_basis": basis,
                    "derived_baseline_kwh": baseline,
                    "rounding_policy": policy,
                }
            )
        return tuple(result)

    def tier_at(
        self,
        cumulative_usage_kwh: Decimal,
        *,
        cycle_start: datetime,
        cycle_end: datetime,
    ) -> dict[str, Any] | None:
        for tier in self.resolved_tiers(cycle_start=cycle_start, cycle_end=cycle_end):
            upper = cast(Decimal | None, tier["upper_bound_kwh"])
            if cumulative_usage_kwh >= cast(Decimal, tier["lower_bound_kwh"]) and (
                upper is None or cumulative_usage_kwh < upper
            ):
                return tier
        return None

    def _season(self, local_date: date) -> str:
        configured = self.plan.get("seasons")
        if configured:
            for name, value in sorted(
                configured.items(), key=lambda item: int(item[1].get("priority", 0)), reverse=True
            ):
                start_month, start_day = (int(part) for part in value["start"].split("-"))
                end_month, end_day = (int(part) for part in value["end"].split("-"))
                current = (local_date.month, local_date.day)
                start = (start_month, start_day)
                end = (end_month, end_day)
                if (start <= end and start <= current <= end) or (
                    start > end and (current >= start or current <= end)
                ):
                    return str(name)
            raise RuntimeError("validated rate plan has no matching season")
        return (
            "summer"
            if date(local_date.year, 6, 1) <= local_date <= date(local_date.year, 9, 30)
            else "winter"
        )

    @staticmethod
    def _day_type(local_date: date) -> str:
        return "weekend" if local_date.weekday() >= 5 else "weekday"

    def _periods_for_date(self, local_date: date) -> list[list[Any]]:
        if self.pricing_model not in {"time_of_use", "time_of_use_tiered"}:
            return [[0, 1440, "all-hours", "0"]]
        season = self._season(local_date)
        special = self.plan.get("special_schedules", {}).get(season, {})
        return cast(
            list[list[Any]],
            special.get(
                local_date.isoformat(),
                self.plan["periods"][season][self._day_type(local_date)],
            ),
        )

    def period_at(self, instant: datetime) -> tuple[str, Decimal]:
        if instant.tzinfo is None:
            raise ValueError("instant must be timezone-aware")
        if self.pricing_model == "flat":
            return "flat", Decimal(str(self.plan["flat_rate_per_kwh"]))
        if self.pricing_model == "tiered":
            return "all-hours", Decimal("0")
        local = instant.astimezone(self.zone)
        minute = local.hour * 60 + local.minute
        periods = self._periods_for_date(local.date())
        for start, end, bucket, price in periods:
            if int(start) <= minute < int(end):
                return str(bucket), Decimal(str(price))
        raise RuntimeError("validated rate plan has no matching period")

    def next_period_at(self, instant: datetime) -> tuple[datetime, str, Decimal]:
        """Return the next tariff boundary and the period effective after it."""
        if instant.tzinfo is None:
            raise ValueError("instant must be timezone-aware")
        if self.pricing_model in {"flat", "tiered"}:
            local = instant.astimezone(self.zone)
            next_midnight = datetime.combine(
                local.date() + timedelta(days=1), time.min, tzinfo=self.zone
            ).astimezone(UTC)
            return (
                next_midnight,
                self.period_at(next_midnight + timedelta(seconds=1))[0],
                (self.period_at(next_midnight + timedelta(seconds=1))[1]),
            )
        end = instant + timedelta(days=3)
        for boundary in self._boundaries(instant, end):
            if boundary <= instant.astimezone(UTC):
                continue
            bucket, price = self.period_at(boundary + timedelta(seconds=1))
            return boundary, bucket, price
        raise RuntimeError("validated rate plan has no future period boundary")

    def _valid_wall_instants(self, day: date, minute: int) -> set[datetime]:
        if minute == 1440:
            day += timedelta(days=1)
            minute = 0
        naive = datetime.combine(day, time(minute // 60, minute % 60))
        candidates: set[datetime] = set()
        for fold in (0, 1):
            local = naive.replace(tzinfo=self.zone, fold=fold)
            utc = local.astimezone(UTC)
            roundtrip = utc.astimezone(self.zone)
            if roundtrip.replace(tzinfo=None) == naive and roundtrip.fold == fold:
                candidates.add(utc)
        return candidates

    def _boundaries(self, start: datetime, end: datetime) -> list[datetime]:
        if self.pricing_model in {"flat", "tiered"}:
            return sorted({start.astimezone(UTC), end.astimezone(UTC)})
        local_start = start.astimezone(self.zone).date() - timedelta(days=1)
        local_end = end.astimezone(self.zone).date() + timedelta(days=1)
        candidates = {start.astimezone(UTC), end.astimezone(UTC)}
        day = local_start
        while day <= local_end:
            candidates.update(self._valid_wall_instants(day, 0))
            for raw_period in self._periods_for_date(day):
                candidates.update(self._valid_wall_instants(day, int(raw_period[0])))
                candidates.update(self._valid_wall_instants(day, int(raw_period[1])))
            day += timedelta(days=1)
        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        # UTC-offset transitions are tariff boundaries even when the price on either
        # side happens to be equal. This preserves both fall-back folds and avoids
        # fabricating a spring-forward wall-time interval.
        probe = start_utc
        prior_offset = probe.astimezone(self.zone).utcoffset()
        while probe < end_utc:
            next_probe = min(end_utc, probe + timedelta(minutes=15))
            next_offset = next_probe.astimezone(self.zone).utcoffset()
            if next_offset != prior_offset:
                low, high = probe, next_probe
                while (high - low).total_seconds() > 1:
                    midpoint = low + (high - low) / 2
                    if midpoint.astimezone(self.zone).utcoffset() == prior_offset:
                        low = midpoint
                    else:
                        high = midpoint
                candidates.add(high.replace(microsecond=0))
            probe = next_probe
            prior_offset = next_offset
        return sorted(item for item in candidates if start_utc <= item <= end_utc)

    def billing_cycle_bounds(self, instant: datetime) -> tuple[datetime, datetime]:
        if instant.tzinfo is None:
            raise ValueError("instant must be timezone-aware")
        local = instant.astimezone(self.zone)
        expected_day = int(self.plan.get("billing_cycle", {}).get("expected_start_day", 1))

        def boundary(year: int, month: int) -> datetime:
            next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            last_day = (next_month - timedelta(days=1)).day
            return datetime(year, month, min(expected_day, last_day), tzinfo=self.zone)

        current = boundary(local.year, local.month)
        if local < current:
            year = local.year - 1 if local.month == 1 else local.year
            month = 12 if local.month == 1 else local.month - 1
            start = boundary(year, month)
        else:
            start = current
        year = start.year + 1 if start.month == 12 else start.year
        month = 1 if start.month == 12 else start.month + 1
        return start.astimezone(UTC), boundary(year, month).astimezone(UTC)

    def _tier_price(self, tier: dict[str, Any], tou_bucket: str, tou_rate: Decimal) -> Decimal:
        if self.pricing_model == "tiered":
            return Decimal(str(tier["price_per_kwh"]))
        method = str((self.plan.get("hybrid_pricing") or {}).get("method", "tier_period_matrix"))
        if method == "tier_period_matrix":
            return Decimal(str(tier.get("tou_prices", {})[tou_bucket]))
        tier_value = Decimal(str(tier["price_per_kwh"]))
        return tier_value + tou_rate

    def calculate(
        self,
        *,
        start: datetime,
        end: datetime,
        energy_kwh: Decimal,
        cost_scope: Literal[
            "energy_only",
            "allocated_account",
            "full_account",
            "allocated_account_estimate",
            "full_account_estimate",
        ] = "energy_only",
        baseline_allocation_kwh: Decimal | None = None,
        billing_days: int | None = None,
        cca_adjustment_per_kwh: Decimal = Decimal("0"),
        other_adjustment: Decimal = Decimal("0"),
        cumulative_usage_before_kwh: Decimal = Decimal("0"),
        cycle_start: datetime | None = None,
        cycle_end: datetime | None = None,
    ) -> Calculation:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("calculation timestamps must be timezone-aware")
        if end <= start or energy_kwh < 0:
            raise ValueError("calculation interval and energy must be valid")
        if cumulative_usage_before_kwh < 0:
            raise ValueError("cumulative usage cannot be negative")
        if self.pricing_model in {"tiered", "time_of_use_tiered"}:
            inferred_start, inferred_end = self.billing_cycle_bounds(start)
            cycle_start = cycle_start or inferred_start
            cycle_end = cycle_end or inferred_end
            if cycle_start.tzinfo is None or cycle_end.tzinfo is None or cycle_end <= cycle_start:
                raise ValueError("tiered calculations require an exact aware billing cycle")
        boundaries = self._boundaries(start, end)
        total_seconds = Decimal(str((end - start).total_seconds()))
        slices: list[CostSlice] = []
        by_bucket: dict[str, Decimal] = {}
        by_tier: dict[str, Decimal] = {}
        charge_by_tier: dict[str, Decimal] = {}
        resolved_tiers = (
            self.resolved_tiers(cycle_start=cycle_start, cycle_end=cycle_end)
            if cycle_start is not None and cycle_end is not None
            else ()
        )
        cumulative = cumulative_usage_before_kwh
        for left, right in pairwise(boundaries):
            seconds = Decimal(str((right - left).total_seconds()))
            allocated = energy_kwh * seconds / total_seconds
            tou_bucket, tou_rate = self.period_at(left + (right - left) / 2)
            if not resolved_tiers:
                price = (
                    Decimal(str(self.plan["flat_rate_per_kwh"]))
                    if self.pricing_model == "flat"
                    else tou_rate
                )
                cost = allocated * price
                bucket = "Flat" if self.pricing_model == "flat" else tou_bucket
                slices.append(
                    CostSlice(
                        left,
                        right,
                        allocated,
                        bucket,
                        price,
                        cost,
                        tou_period=tou_bucket if self.pricing_model == "time_of_use" else None,
                    )
                )
                by_bucket[bucket] = by_bucket.get(bucket, Decimal("0")) + allocated
                continue

            remaining = allocated
            segment_left = left
            while remaining > 0:
                tier = self.tier_at(
                    cumulative,
                    cycle_start=cast(datetime, cycle_start),
                    cycle_end=cast(datetime, cycle_end),
                )
                if tier is None:
                    raise RuntimeError("validated tiers do not cover cumulative usage")
                upper = cast(Decimal | None, tier["upper_bound_kwh"])
                capacity = remaining if upper is None else max(Decimal("0"), upper - cumulative)
                segment_energy = min(remaining, capacity)
                if segment_energy <= 0:
                    raise RuntimeError("tier allocation made no chronological progress")
                fraction = segment_energy / allocated if allocated else Decimal("1")
                segment_seconds = seconds * fraction
                segment_microseconds = int(
                    (segment_seconds * Decimal("1000000")).to_integral_value(rounding=ROUND_HALF_UP)
                )
                segment_right = (
                    right
                    if segment_energy == remaining
                    else segment_left + timedelta(microseconds=segment_microseconds)
                )
                price = self._tier_price(tier, tou_bucket, tou_rate)
                cost = segment_energy * price
                tier_name = str(tier["name"])
                bucket = (
                    f"{tier_name} · {tou_bucket}"
                    if self.pricing_model == "time_of_use_tiered"
                    else tier_name
                )
                slices.append(
                    CostSlice(
                        segment_left,
                        segment_right,
                        segment_energy,
                        bucket,
                        price,
                        cost,
                        tier_id=str(tier["tier_id"]),
                        tier_name=tier_name,
                        tou_period=(
                            tou_bucket if self.pricing_model == "time_of_use_tiered" else None
                        ),
                        cumulative_start_kwh=cumulative,
                        cumulative_end_kwh=cumulative + segment_energy,
                    )
                )
                by_bucket[bucket] = by_bucket.get(bucket, Decimal("0")) + segment_energy
                by_tier[tier_name] = by_tier.get(tier_name, Decimal("0")) + segment_energy
                charge_by_tier[tier_name] = charge_by_tier.get(tier_name, Decimal("0")) + cost
                cumulative += segment_energy
                remaining -= segment_energy
                segment_left = segment_right
            if allocated == 0:
                tier = self.tier_at(
                    cumulative,
                    cycle_start=cast(datetime, cycle_start),
                    cycle_end=cast(datetime, cycle_end),
                )
                if tier:
                    price = self._tier_price(tier, tou_bucket, tou_rate)
                    tier_name = str(tier["name"])
                    bucket = (
                        f"{tier_name} · {tou_bucket}"
                        if self.pricing_model == "time_of_use_tiered"
                        else tier_name
                    )
                    slices.append(
                        CostSlice(
                            left,
                            right,
                            Decimal("0"),
                            bucket,
                            price,
                            Decimal("0"),
                            tier_id=str(tier["tier_id"]),
                            tier_name=tier_name,
                            tou_period=(
                                tou_bucket if self.pricing_model == "time_of_use_tiered" else None
                            ),
                            cumulative_start_kwh=cumulative,
                            cumulative_end_kwh=cumulative,
                        )
                    )
        energy_charge = sum((item.cost for item in slices), Decimal("0"))
        fixed = Decimal("0")
        baseline = Decimal("0")
        adjustment_breakdown: dict[str, Decimal] = {}
        if cost_scope in {"full_account", "full_account_estimate"}:
            if billing_days is None:
                local_days = {item.start.astimezone(self.zone).date() for item in slices} | {
                    end.astimezone(self.zone).date()
                }
                billing_days = max(1, len(local_days))
            if not self.plan.get("adjustments"):
                fixed = Decimal(str(self.plan["base_service_charge_per_day"])) * billing_days
                raw_credit = self.plan.get("baseline_credit_per_kwh")
                if raw_credit is not None and baseline_allocation_kwh is not None:
                    baseline = min(energy_kwh, baseline_allocation_kwh) * Decimal(str(raw_credit))
        cca = energy_kwh * cca_adjustment_per_kwh
        adjustments = other_adjustment if cost_scope != "energy_only" else Decimal("0")
        total = energy_charge + fixed - baseline + cca + adjustments
        for item in sorted(
            self.plan.get("adjustments", []),
            key=lambda value: int(value.get("calculation_order", 0)),
        ):
            scope = str(item.get("scope", "full_account_estimate"))
            full_scope = cost_scope in {"full_account", "full_account_estimate"}
            allocated_scope = cost_scope in {
                "allocated_account",
                "allocated_account_estimate",
                "full_account",
                "full_account_estimate",
            }
            if (scope == "full_account_estimate" and not full_scope) or (
                scope == "allocated_account_estimate" and not allocated_scope
            ):
                continue
            effective_from = (
                date.fromisoformat(str(item["effective_from"]))
                if item.get("effective_from")
                else None
            )
            effective_to = (
                date.fromisoformat(str(item["effective_to"])) if item.get("effective_to") else None
            )
            eligible_slices = [
                value
                for value in slices
                if (
                    effective_from is None
                    or value.start.astimezone(self.zone).date() >= effective_from
                )
                and (
                    effective_to is None or value.start.astimezone(self.zone).date() <= effective_to
                )
            ]
            if not eligible_slices:
                continue
            eligible_energy = sum((value.energy_kwh for value in eligible_slices), Decimal("0"))
            eligible_days = {value.start.astimezone(self.zone).date() for value in eligible_slices}
            value = Decimal(str(item["value"]))
            component = str(item["component"])
            unit = str(item.get("unit", "fixed"))
            if component == "baseline_credit":
                if baseline_allocation_kwh is None or not full_scope:
                    continue
                amount = min(eligible_energy, baseline_allocation_kwh) * value
            elif unit == "per_kwh":
                amount = eligible_energy * value
            elif unit == "per_day":
                amount = value * Decimal(len(eligible_days) or 1)
            elif unit == "per_month":
                months = {(day.year, day.month) for day in eligible_days}
                amount = value * Decimal(len(months) or 1)
            elif unit == "percent":
                amount = total * value / Decimal("100")
            else:
                amount = value
            operation = str(item.get("operation", "add"))
            signed = -amount if operation == "subtract" else amount
            if operation == "multiply":
                signed = total * (value - Decimal("1"))
            if component == "minimum_charge":
                signed = max(Decimal("0"), amount - total)
            total += signed
            adjustment_breakdown[str(item["name"])] = signed
            if component in {"daily_fixed_charge", "monthly_fixed_charge"}:
                fixed += signed
            elif component == "baseline_credit":
                baseline += amount
            elif component in {"cca", "direct_access", "generation_provider"}:
                cca += signed
            else:
                adjustments += signed
        return Calculation(
            slices=tuple(slices),
            energy_by_bucket=by_bucket,
            energy_by_tier=by_tier,
            charge_by_tier=charge_by_tier,
            tier_thresholds=resolved_tiers,
            energy_charge=energy_charge,
            fixed_charge=fixed,
            baseline_credit=baseline,
            cca_adjustment=cca,
            other_adjustment=adjustments,
            adjustment_breakdown=adjustment_breakdown,
            total=total,
        )

    @staticmethod
    def display_currency(value: Decimal) -> Decimal:
        return _round_currency(value)
