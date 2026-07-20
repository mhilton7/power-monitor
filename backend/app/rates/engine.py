from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, TypedDict
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


@dataclass(frozen=True)
class Calculation:
    slices: tuple[CostSlice, ...]
    energy_by_bucket: dict[str, Decimal]
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
    algorithm_version = "rate-engine/1.0.0"

    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        self.zone = ZoneInfo(str(plan["timezone"]))
        self.validate_plan()

    def validate_plan(self) -> None:
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
        season = self._season(local_date)
        special = self.plan.get("special_schedules", {}).get(season, {})
        return special.get(
            local_date.isoformat(),
            self.plan["periods"][season][self._day_type(local_date)],
        )

    def period_at(self, instant: datetime) -> tuple[str, Decimal]:
        if instant.tzinfo is None:
            raise ValueError("instant must be timezone-aware")
        local = instant.astimezone(self.zone)
        minute = local.hour * 60 + local.minute
        periods = self._periods_for_date(local.date())
        for start, end, bucket, price in periods:
            if int(start) <= minute < int(end):
                return str(bucket), Decimal(str(price))
        raise RuntimeError("validated rate plan has no matching period")

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
    ) -> Calculation:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("calculation timestamps must be timezone-aware")
        if end <= start or energy_kwh < 0:
            raise ValueError("calculation interval and energy must be valid")
        boundaries = self._boundaries(start, end)
        total_seconds = Decimal(str((end - start).total_seconds()))
        slices: list[CostSlice] = []
        by_bucket: dict[str, Decimal] = {}
        for left, right in pairwise(boundaries):
            seconds = Decimal(str((right - left).total_seconds()))
            allocated = energy_kwh * seconds / total_seconds
            bucket, price = self.period_at(left + (right - left) / 2)
            cost = allocated * price
            slices.append(CostSlice(left, right, allocated, bucket, price, cost))
            by_bucket[bucket] = by_bucket.get(bucket, Decimal("0")) + allocated
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
