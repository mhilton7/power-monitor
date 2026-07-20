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
    if not document.seasons:
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
        "timezone": document.timezone,
        "currency": document.currency,
        "effective_from": document.effective_from.isoformat(),
        "effective_to": document.effective_through.isoformat()
        if document.effective_through
        else None,
        "base_service_charge_per_day": daily,
        "baseline_credit_per_kwh": baseline,
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
