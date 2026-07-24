from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION: Literal["power-monitor-rate-plan/1.0"] = "power-monitor-rate-plan/1.0"
VALID_DAY_TYPES = {"weekday", "weekend", "all-days", "holiday", "date-override"}
VALID_PROVIDER_MODES = {
    "sce_delivery_generation",
    "sce_delivery_cca",
    "sce_delivery_direct_access",
    "custom_combined",
}
VALID_COST_SCOPES = {
    "energy_only",
    "allocated_account_estimate",
    "full_account_estimate",
}
PRICING_MODELS = {"flat", "time_of_use", "tiered", "time_of_use_tiered"}
THRESHOLD_BASES = {"fixed_cycle_kwh", "daily_baseline_kwh"}
ROUNDING_POLICIES = {"none", "nearest_kwh", "floor_kwh", "ceil_kwh"}
HYBRID_METHODS = {"tier_period_matrix", "tier_base_plus_tou_adder", "tou_base_plus_tier_adder"}


def _exact_decimal(value: str, *, label: str, places: int = 8) -> str:
    try:
        decimal = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be an exact decimal string") from exc
    if not decimal.is_finite() or abs(cast(int, decimal.as_tuple().exponent)) > places:
        raise ValueError(f"{label} must be finite with no more than {places} decimal places")
    return format(decimal, "f")


class SeasonalBaselineDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    start: str = Field(pattern=r"^\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{2}-\d{2}$")
    daily_kwh: str
    source_citation: str | None = Field(default=None, max_length=500)

    @field_validator("daily_kwh")
    @classmethod
    def exact_daily_kwh(cls, value: str) -> str:
        value = _exact_decimal(value, label="daily baseline")
        if Decimal(value) <= 0:
            raise ValueError("daily baseline must be greater than zero")
        return value

    @field_validator("start", "end")
    @classmethod
    def valid_month_day(cls, value: str) -> str:
        month, day = (int(part) for part in value.split("-"))
        try:
            date(2024, month, day)
        except ValueError as exc:
            raise ValueError("month-day is invalid") from exc
        return value


class TierThresholdDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basis: Literal["fixed_cycle_kwh", "daily_baseline_kwh"] = "fixed_cycle_kwh"
    daily_baseline_kwh: str | None = None
    baseline_region: str | None = Field(default=None, max_length=120)
    baseline_category: str | None = Field(default=None, max_length=120)
    rounding_policy: Literal["none", "nearest_kwh", "floor_kwh", "ceil_kwh"] = "none"
    seasonal_baselines: list[SeasonalBaselineDocument] = Field(default_factory=list)
    source_citation: str | None = Field(default=None, max_length=500)

    @field_validator("daily_baseline_kwh")
    @classmethod
    def exact_daily_kwh(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = _exact_decimal(value, label="daily baseline")
        if Decimal(value) <= 0:
            raise ValueError("daily baseline must be greater than zero")
        return value

    @model_validator(mode="after")
    def complete_threshold(self) -> TierThresholdDocument:
        if (
            self.basis == "daily_baseline_kwh"
            and self.daily_baseline_kwh is None
            and not self.seasonal_baselines
        ):
            raise ValueError("daily baseline thresholds require a baseline allocation")
        if self.basis == "fixed_cycle_kwh" and (
            self.daily_baseline_kwh is not None or self.seasonal_baselines
        ):
            raise ValueError("fixed-cycle thresholds cannot include daily baseline settings")
        return self


class TierDefinitionDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=120)
    order: int = Field(ge=0)
    lower_bound_inclusive_kwh: str = "0"
    upper_bound_exclusive_kwh: str | None = None
    lower_bound_multiplier: str | None = None
    upper_bound_multiplier: str | None = None
    price_per_kwh: str
    tou_prices: dict[str, str] = Field(default_factory=dict)
    season: str | None = Field(default=None, max_length=80)
    source_citation: str | None = Field(default=None, max_length=500)

    @field_validator(
        "lower_bound_inclusive_kwh",
        "upper_bound_exclusive_kwh",
        "lower_bound_multiplier",
        "upper_bound_multiplier",
        "price_per_kwh",
    )
    @classmethod
    def exact_value(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = _exact_decimal(value, label="tier value")
        if Decimal(value) < 0:
            raise ValueError("tier bounds, multipliers, and energy prices must be non-negative")
        return value

    @field_validator("tou_prices")
    @classmethod
    def exact_tou_prices(cls, value: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for bucket, raw_price in value.items():
            if not bucket or len(bucket) > 80:
                raise ValueError("hybrid period labels must be between 1 and 80 characters")
            price = _exact_decimal(raw_price, label=f"{bucket} price")
            if Decimal(price) < 0:
                raise ValueError("hybrid energy prices must be non-negative")
            result[bucket] = price
        return result


class BillingCycleBehaviorDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_start_day: int = Field(default=1, ge=1, le=31)
    threshold: TierThresholdDocument = Field(default_factory=TierThresholdDocument)


class HybridPricingDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal[
        "tier_period_matrix",
        "tier_base_plus_tou_adder",
        "tou_base_plus_tier_adder",
    ] = "tier_period_matrix"


class RatePeriodDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    start_minute: int = Field(ge=0, le=1439)
    end_minute: int = Field(ge=1, le=1440)
    price_per_kwh: str
    delivery_per_kwh: str = "0"
    generation_per_kwh: str = "0"
    adjustment_per_kwh: str = "0"
    display_order: int = 0

    @field_validator(
        "price_per_kwh", "delivery_per_kwh", "generation_per_kwh", "adjustment_per_kwh"
    )
    @classmethod
    def exact_decimal(cls, value: str) -> str:
        try:
            decimal = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("rate must be an exact decimal string") from exc
        if not decimal.is_finite() or abs(cast(int, decimal.as_tuple().exponent)) > 8:
            raise ValueError("rate must be finite with no more than eight decimal places")
        return format(decimal, "f")

    @model_validator(mode="after")
    def valid_interval(self) -> RatePeriodDocument:
        if self.end_minute <= self.start_minute:
            raise ValueError("end_minute must be after start_minute")
        return self


class DayScheduleDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day_type: str
    dates: list[date] = Field(default_factory=list)
    periods: list[RatePeriodDocument] = Field(default_factory=list)

    @field_validator("day_type")
    @classmethod
    def known_day_type(cls, value: str) -> str:
        if value not in VALID_DAY_TYPES:
            raise ValueError("day_type is not supported")
        return value


class RateSeasonDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    start: str = Field(pattern=r"^\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{2}-\d{2}$")
    priority: int = 0
    leap_day_behavior: Literal["include", "previous_day", "next_day"] = "include"
    schedules: list[DayScheduleDocument] = Field(default_factory=list)

    @field_validator("start", "end")
    @classmethod
    def valid_month_day(cls, value: str) -> str:
        month, day = (int(part) for part in value.split("-"))
        try:
            date(2024, month, day)
        except ValueError as exc:
            raise ValueError("month-day is invalid") from exc
        return value


class RateAdjustmentDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    component: Literal[
        "daily_fixed_charge",
        "monthly_fixed_charge",
        "minimum_charge",
        "baseline_credit",
        "percentage_tax",
        "fixed_tax",
        "generation_provider",
        "cca",
        "direct_access",
        "manual_credit",
        "other",
    ]
    operation: Literal["add", "subtract", "minimum", "multiply"] = "add"
    value: str
    unit: str
    scope: str = "full_account_estimate"
    eligibility: dict[str, Any] = Field(default_factory=dict)
    effective_from: date | None = None
    effective_to: date | None = None
    calculation_order: int = 0
    description: str = ""

    @field_validator("value")
    @classmethod
    def exact_decimal(cls, value: str) -> str:
        try:
            decimal = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("adjustment must be an exact decimal string") from exc
        if not decimal.is_finite() or abs(cast(int, decimal.as_tuple().exponent)) > 8:
            raise ValueError("adjustment must have no more than eight decimal places")
        return format(decimal, "f")

    @model_validator(mode="after")
    def dates_in_order(self) -> RateAdjustmentDocument:
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("adjustment effective_to must not precede effective_from")
        return self


class RatePlanDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["power-monitor-rate-plan/1.0"] = SCHEMA_VERSION
    plan_name: str = Field(min_length=1, max_length=160)
    plan_code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9._-]{1,79}$")
    utility: str = Field(min_length=1, max_length=160)
    description: str = ""
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    timezone: str
    pricing_model: Literal["flat", "time_of_use", "tiered", "time_of_use_tiered"] = "time_of_use"
    flat_rate_per_kwh: str | None = None
    billing_cycle: BillingCycleBehaviorDocument = Field(
        default_factory=BillingCycleBehaviorDocument
    )
    tiers: list[TierDefinitionDocument] = Field(default_factory=list)
    hybrid_pricing: HybridPricingDocument | None = None
    ownership_scope: Literal["global", "site", "utility_account"] = "global"
    owner_id: str | None = None
    effective_from: date
    effective_through: date | None = None
    cost_scope_default: str = "energy_only"
    source_label: str = "Administrator-defined rate plan"
    source_note: str = ""
    provider_mode: str = "custom_combined"
    seasons: list[RateSeasonDocument] = Field(default_factory=list)
    adjustments: list[RateAdjustmentDocument] = Field(default_factory=list)
    custom_notes: str = ""
    cloned_from_rate_version_id: str | None = None

    @field_validator("flat_rate_per_kwh")
    @classmethod
    def exact_flat_rate(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = _exact_decimal(value, label="flat rate")
        if Decimal(value) < 0:
            raise ValueError("flat energy prices must be non-negative")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone is not an IANA timezone") from exc
        return value

    @field_validator("cost_scope_default")
    @classmethod
    def valid_cost_scope(cls, value: str) -> str:
        if value not in VALID_COST_SCOPES:
            raise ValueError("cost_scope_default is not supported")
        return value

    @field_validator("provider_mode")
    @classmethod
    def valid_provider_mode(cls, value: str) -> str:
        if value not in VALID_PROVIDER_MODES:
            raise ValueError("provider_mode is not supported")
        return value

    @model_validator(mode="after")
    def dates_and_owner(self) -> RatePlanDocument:
        if self.effective_through and self.effective_through < self.effective_from:
            raise ValueError("effective_through must not precede effective_from")
        if self.ownership_scope != "global" and not self.owner_id:
            raise ValueError("owner_id is required for site or utility-account scope")
        if self.pricing_model == "flat" and self.flat_rate_per_kwh is None:
            raise ValueError("flat pricing requires flat_rate_per_kwh")
        if self.pricing_model in {"tiered", "time_of_use_tiered"} and len(self.tiers) < 2:
            raise ValueError("tiered pricing requires at least two tiers")
        if self.pricing_model == "time_of_use_tiered" and self.hybrid_pricing is None:
            raise ValueError("hybrid pricing requires a hybrid calculation method")
        if self.pricing_model != "time_of_use_tiered" and self.hybrid_pricing is not None:
            raise ValueError("hybrid pricing settings apply only to time-of-use tiered plans")
        return self


class ValidationIssue(BaseModel):
    level: Literal["error", "warning"]
    code: str
    path: str
    message: str


class ValidationReport(BaseModel):
    valid: bool
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    integrity_sha256: str
    coverage: dict[str, bool]


def canonical_document(document: RatePlanDocument | dict[str, Any]) -> bytes:
    payload = (
        document.model_dump(mode="json", exclude_none=False)
        if isinstance(document, RatePlanDocument)
        else document
    )
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def document_hash(document: RatePlanDocument | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_document(document)).hexdigest()


def _season_days(season: RateSeasonDocument) -> set[int]:
    start_month, start_day = (int(part) for part in season.start.split("-"))
    end_month, end_day = (int(part) for part in season.end.split("-"))
    start = date(2024, start_month, start_day).timetuple().tm_yday
    end = date(2024, end_month, end_day).timetuple().tm_yday
    if start <= end:
        return set(range(start, end + 1))
    return set(range(start, 367)) | set(range(1, end + 1))


def validate_document(
    document: RatePlanDocument,
    *,
    require_source_evidence: bool = False,
    source_evidence: dict[str, Any] | None = None,
    max_energy_rate: Decimal = Decimal("5"),
    max_fixed_daily: Decimal = Decimal("25"),
) -> ValidationReport:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    coverage: dict[str, bool] = {}

    if document.currency != "USD":
        errors.append(
            ValidationIssue(
                level="error",
                code="currency",
                path="currency",
                message="SCE and local cost calculations require USD",
            )
        )
    if document.utility.lower() not in {"southern california edison", "sce", "custom"}:
        warnings.append(
            ValidationIssue(
                level="warning",
                code="utility_unrecognized",
                path="utility",
                message="Utility name is not a recognized SCE or custom label",
            )
        )
    needs_time_schedule = document.pricing_model in {"time_of_use", "time_of_use_tiered"}
    if needs_time_schedule and not document.seasons:
        errors.append(
            ValidationIssue(
                level="error",
                code="season_required",
                path="seasons",
                message="At least one season is required",
            )
        )

    day_owners: dict[int, list[RateSeasonDocument]] = {day: [] for day in range(1, 367)}
    for season in document.seasons:
        for day in _season_days(season):
            day_owners[day].append(season)
        schedule_types = {schedule.day_type for schedule in season.schedules}
        has_normal_days = "all-days" in schedule_types or {"weekday", "weekend"}.issubset(
            schedule_types
        )
        if not has_normal_days:
            errors.append(
                ValidationIssue(
                    level="error",
                    code="day_type_coverage",
                    path=f"seasons.{season.name}.schedules",
                    message="Each season needs all-days or both weekday and weekend schedules",
                )
            )
        for schedule in season.schedules:
            key = f"{season.name}/{schedule.day_type}"
            cursor = 0
            valid = True
            for index, period in enumerate(
                sorted(schedule.periods, key=lambda item: item.start_minute)
            ):
                path = f"seasons.{season.name}.{schedule.day_type}.periods.{index}"
                if period.start_minute < cursor:
                    errors.append(
                        ValidationIssue(
                            level="error",
                            code="period_overlap",
                            path=path,
                            message="One or more schedule periods overlap",
                        )
                    )
                    valid = False
                elif period.start_minute > cursor:
                    errors.append(
                        ValidationIssue(
                            level="error",
                            code="period_gap",
                            path=path,
                            message=f"Schedule has an uncovered gap starting at minute {cursor}",
                        )
                    )
                    valid = False
                cursor = max(cursor, period.end_minute)
                price = Decimal(period.price_per_kwh)
                if price < 0:
                    errors.append(
                        ValidationIssue(
                            level="error",
                            code="negative_rate",
                            path=f"{path}.price_per_kwh",
                            message=(
                                "Energy prices must be non-negative; model credits as adjustments"
                            ),
                        )
                    )
                if price > max_energy_rate:
                    warnings.append(
                        ValidationIssue(
                            level="warning",
                            code="rate_sanity",
                            path=f"{path}.price_per_kwh",
                            message="Energy price exceeds the configured sanity threshold",
                        )
                    )
            if cursor != 1440:
                errors.append(
                    ValidationIssue(
                        level="error",
                        code="period_gap",
                        path=f"seasons.{season.name}.{schedule.day_type}",
                        message="Schedule must cover all 1,440 minutes",
                    )
                )
                valid = False
            coverage[key] = valid and cursor == 1440

    for day, owners in day_owners.items():
        if not needs_time_schedule:
            break
        if not owners:
            errors.append(
                ValidationIssue(
                    level="error",
                    code="annual_gap",
                    path="seasons",
                    message=f"No season covers leap-year day {day}",
                )
            )
            break
        top_priority = max(item.priority for item in owners)
        if sum(item.priority == top_priority for item in owners) > 1:
            errors.append(
                ValidationIssue(
                    level="error",
                    code="season_conflict",
                    path="seasons",
                    message=f"Conflicting seasons have equal priority on leap-year day {day}",
                )
            )
            break

    if (
        document.pricing_model == "flat"
        and document.flat_rate_per_kwh is not None
        and Decimal(document.flat_rate_per_kwh) > max_energy_rate
    ):
        warnings.append(
            ValidationIssue(
                level="warning",
                code="rate_sanity",
                path="flat_rate_per_kwh",
                message="Flat energy price exceeds the configured sanity threshold",
            )
        )

    if document.pricing_model in {"tiered", "time_of_use_tiered"}:
        ordered_tiers = sorted(document.tiers, key=lambda item: item.order)
        orders = [item.order for item in ordered_tiers]
        if orders != list(range(len(ordered_tiers))):
            errors.append(
                ValidationIssue(
                    level="error",
                    code="tier_order",
                    path="tiers",
                    message="Tier order values must be unique and contiguous starting at zero",
                )
            )
        if len({item.tier_id for item in ordered_tiers}) != len(ordered_tiers):
            errors.append(
                ValidationIssue(
                    level="error",
                    code="tier_id_duplicate",
                    path="tiers",
                    message="Tier IDs must be unique within a rate version",
                )
            )
        threshold_basis = document.billing_cycle.threshold.basis
        tier_cursor = Decimal("0")
        for index, tier in enumerate(ordered_tiers):
            final = index == len(ordered_tiers) - 1
            price = Decimal(tier.price_per_kwh)
            if price > max_energy_rate:
                warnings.append(
                    ValidationIssue(
                        level="warning",
                        code="rate_sanity",
                        path=f"tiers.{index}.price_per_kwh",
                        message="Tier energy price exceeds the configured sanity threshold",
                    )
                )
            if threshold_basis == "fixed_cycle_kwh":
                lower = Decimal(tier.lower_bound_inclusive_kwh)
                upper = (
                    Decimal(tier.upper_bound_exclusive_kwh)
                    if tier.upper_bound_exclusive_kwh is not None
                    else None
                )
                if lower != tier_cursor:
                    errors.append(
                        ValidationIssue(
                            level="error",
                            code="tier_gap_or_overlap",
                            path=f"tiers.{index}.lower_bound_inclusive_kwh",
                            message=(
                                f"Tier must begin at the prior exclusive boundary ({tier_cursor})"
                            ),
                        )
                    )
                if final and upper is not None:
                    errors.append(
                        ValidationIssue(
                            level="error",
                            code="final_tier_open",
                            path=f"tiers.{index}.upper_bound_exclusive_kwh",
                            message="The final tier must be open-ended",
                        )
                    )
                elif not final and upper is None:
                    errors.append(
                        ValidationIssue(
                            level="error",
                            code="tier_upper_required",
                            path=f"tiers.{index}.upper_bound_exclusive_kwh",
                            message="Every non-final tier needs an exclusive upper boundary",
                        )
                    )
                elif upper is not None and upper <= lower:
                    errors.append(
                        ValidationIssue(
                            level="error",
                            code="tier_bounds",
                            path=f"tiers.{index}.upper_bound_exclusive_kwh",
                            message="Tier upper boundary must be greater than its lower boundary",
                        )
                    )
                if upper is not None:
                    tier_cursor = upper
                if (
                    tier.lower_bound_multiplier is not None
                    or tier.upper_bound_multiplier is not None
                ):
                    errors.append(
                        ValidationIssue(
                            level="error",
                            code="threshold_basis_mismatch",
                            path=f"tiers.{index}",
                            message="Fixed-cycle tiers cannot include baseline multipliers",
                        )
                    )
            else:
                lower = Decimal(tier.lower_bound_multiplier or "0")
                upper = (
                    Decimal(tier.upper_bound_multiplier)
                    if tier.upper_bound_multiplier is not None
                    else None
                )
                if lower != tier_cursor:
                    errors.append(
                        ValidationIssue(
                            level="error",
                            code="tier_gap_or_overlap",
                            path=f"tiers.{index}.lower_bound_multiplier",
                            message=f"Tier baseline multiplier must begin at {tier_cursor}",
                        )
                    )
                if final and upper is not None:
                    errors.append(
                        ValidationIssue(
                            level="error",
                            code="final_tier_open",
                            path=f"tiers.{index}.upper_bound_multiplier",
                            message="The final baseline-derived tier must be open-ended",
                        )
                    )
                elif not final and upper is None:
                    errors.append(
                        ValidationIssue(
                            level="error",
                            code="tier_multiplier_required",
                            path=f"tiers.{index}.upper_bound_multiplier",
                            message="Every non-final tier needs an upper baseline multiplier",
                        )
                    )
                elif upper is not None and upper <= lower:
                    errors.append(
                        ValidationIssue(
                            level="error",
                            code="tier_bounds",
                            path=f"tiers.{index}.upper_bound_multiplier",
                            message="Tier upper multiplier must exceed its lower multiplier",
                        )
                    )
                if upper is not None:
                    tier_cursor = upper
            if not final and (
                (threshold_basis == "fixed_cycle_kwh" and tier.upper_bound_exclusive_kwh is None)
                or (threshold_basis == "daily_baseline_kwh" and tier.upper_bound_multiplier is None)
            ):
                errors.append(
                    ValidationIssue(
                        level="error",
                        code="open_tier_position",
                        path=f"tiers.{index}",
                        message="Only the final tier may be open-ended",
                    )
                )

        if document.pricing_model == "time_of_use_tiered":
            period_labels = {
                period.label
                for season in document.seasons
                for schedule in season.schedules
                for period in schedule.periods
            }
            method = (
                document.hybrid_pricing.method if document.hybrid_pricing else "tier_period_matrix"
            )
            if method == "tier_period_matrix":
                for index, tier in enumerate(ordered_tiers):
                    missing = sorted(period_labels - set(tier.tou_prices))
                    extra = sorted(set(tier.tou_prices) - period_labels)
                    if missing or extra:
                        errors.append(
                            ValidationIssue(
                                level="error",
                                code="hybrid_matrix_coverage",
                                path=f"tiers.{index}.tou_prices",
                                message=(
                                    "Hybrid matrix must exactly cover all TOU periods; "
                                    f"missing={missing}, extra={extra}"
                                ),
                            )
                        )
    for index, adjustment in enumerate(document.adjustments):
        value = abs(Decimal(adjustment.value))
        if adjustment.component == "daily_fixed_charge" and value > max_fixed_daily:
            warnings.append(
                ValidationIssue(
                    level="warning",
                    code="fixed_charge_sanity",
                    path=f"adjustments.{index}.value",
                    message="Daily fixed charge exceeds the configured sanity threshold",
                )
            )
        if adjustment.component == "baseline_credit" and adjustment.unit != "per_kwh":
            errors.append(
                ValidationIssue(
                    level="error",
                    code="baseline_unit",
                    path=f"adjustments.{index}.unit",
                    message="Baseline credits must use per_kwh",
                )
            )
        if (
            document.cost_scope_default == "energy_only"
            and adjustment.scope == "full_account_estimate"
        ):
            warnings.append(
                ValidationIssue(
                    level="warning",
                    code="inactive_account_charge",
                    path=f"adjustments.{index}.scope",
                    message="Whole-account adjustment is disabled by the default energy-only scope",
                )
            )

    if require_source_evidence:
        evidence = source_evidence or {}
        for field in ("artifact_id", "sha256", "parser_id", "parser_version"):
            if not evidence.get(field):
                errors.append(
                    ValidationIssue(
                        level="error",
                        code="source_evidence",
                        path=f"source.{field}",
                        message=f"Official candidates require {field}",
                    )
                )
    return ValidationReport(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        integrity_sha256=document_hash(document),
        coverage=coverage,
    )


def engine_plan(document: RatePlanDocument) -> dict[str, Any]:
    periods: dict[str, dict[str, list[list[Any]]]] = {}
    special_schedules: dict[str, dict[str, list[list[Any]]]] = {}
    for season in document.seasons:
        by_day: dict[str, list[list[Any]]] = {}
        for schedule in season.schedules:
            values = [
                [item.start_minute, item.end_minute, item.label, item.price_per_kwh]
                for item in sorted(schedule.periods, key=lambda value: value.start_minute)
            ]
            if schedule.day_type == "all-days":
                by_day["weekday"] = values
                by_day["weekend"] = values
            elif schedule.day_type in {"weekday", "weekend"}:
                by_day[schedule.day_type] = values
            elif schedule.day_type in {"holiday", "date-override"}:
                for scheduled_date in schedule.dates:
                    special_schedules.setdefault(season.name, {})[scheduled_date.isoformat()] = (
                        values
                    )
        periods[season.name] = by_day
    daily = next(
        (item.value for item in document.adjustments if item.component == "daily_fixed_charge"),
        "0",
    )
    baseline = next(
        (item.value for item in document.adjustments if item.component == "baseline_credit"),
        None,
    )
    return {
        "code": document.plan_code,
        "name": document.plan_name,
        "pricing_model": document.pricing_model,
        "timezone": document.timezone,
        "currency": document.currency,
        "effective_from": document.effective_from.isoformat(),
        "effective_to": document.effective_through.isoformat()
        if document.effective_through
        else None,
        "base_service_charge_per_day": daily,
        "baseline_credit_per_kwh": baseline,
        "flat_rate_per_kwh": document.flat_rate_per_kwh,
        "billing_cycle": document.billing_cycle.model_dump(mode="json"),
        "tiers": [item.model_dump(mode="json") for item in document.tiers],
        "hybrid_pricing": (
            document.hybrid_pricing.model_dump(mode="json")
            if document.hybrid_pricing is not None
            else None
        ),
        "periods": periods,
        "special_schedules": special_schedules,
        "seasons": {
            season.name: {
                "start": season.start,
                "end": season.end,
                "priority": season.priority,
            }
            for season in document.seasons
        },
        "adjustments": [item.model_dump(mode="json") for item in document.adjustments],
    }
