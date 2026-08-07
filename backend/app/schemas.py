from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

SIGNED_BIGINT_MAX = 2**63 - 1
MAX_RESET_BOUNDARY = SIGNED_BIGINT_MAX - 2
FAILURE_CODE_PATTERN = r"^[a-z0-9][a-z0-9_]{0,79}$"
SignedSequence = Annotated[int, Field(ge=0, le=SIGNED_BIGINT_MAX)]
PositiveSignedSequence = Annotated[int, Field(ge=1, le=SIGNED_BIGINT_MAX)]
ResetBoundary = Annotated[int, Field(ge=0, le=MAX_RESET_BOUNDARY)]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class DeviceProtocolModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


def require_iana_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc
    return value


class Problem(ApiModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: str | None = None


class BootstrapRequest(ApiModel):
    bootstrap_secret: str = Field(min_length=16)
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=14, max_length=1024)


class LoginRequest(ApiModel):
    email: EmailStr
    password: str = Field(max_length=1024)
    totp_code: str | None = Field(default=None, pattern=r"^\d{6}$")


class UserSummary(ApiModel):
    id: str
    email: EmailStr
    display_name: str
    roles: list[str]
    permissions: list[str] = Field(default_factory=list)
    all_sites: bool = True
    site_ids: list[str] = Field(default_factory=list)
    access_revision: int = 1
    mfa_enabled: bool = False


class SessionView(ApiModel):
    authenticated: bool
    user: UserSummary | None = None
    expires_at: datetime | None = None
    csrf_token: str | None = None
    bootstrap_required: bool = False


class SiteCreate(ApiModel):
    name: str = Field(min_length=1, max_length=160)
    code: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    location_label: str | None = Field(default=None, max_length=160)
    organization: str | None = Field(default=None, max_length=160)
    timezone: str = "America/Los_Angeles"
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    locale: str = Field(default="en-US", pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    unit_system: Literal["imperial", "metric"] = "imperial"
    allowed_cidrs: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    allow_public_polling: bool = False

    _timezone_valid = field_validator("timezone")(require_iana_timezone)


class SiteView(SiteCreate):
    id: str
    code: str
    lifecycle_state: Literal["active", "disabled", "removed"]
    is_default: bool
    revision: int
    disabled_at: datetime | None = None
    removed_at: datetime | None = None
    removal_reason: str | None = None
    restored_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SiteAdminCreate(SiteCreate):
    code: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    initial_user_ids: list[str] = Field(default_factory=list, max_length=200)
    make_default: bool = False
    network_policy_mode: Literal["inherit", "explicit", "existing"] = "inherit"
    network_policy_id: str | None = None
    create_utility_account_after: bool = False
    confirmation: bool

    @model_validator(mode="after")
    def confirmed_and_consistent(self) -> SiteAdminCreate:
        if not self.confirmation:
            raise ValueError("site creation must be explicitly confirmed")
        if self.network_policy_mode == "existing" and not self.network_policy_id:
            raise ValueError("network_policy_id is required for an existing policy")
        return self


class SiteAdminUpdate(ApiModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    location_label: str | None = Field(default=None, max_length=160)
    organization: str | None = Field(default=None, max_length=160)
    timezone: str | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    locale: str | None = Field(default=None, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    unit_system: Literal["imperial", "metric"] | None = None
    timezone_change_confirmed: bool = False
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("timezone")
    @classmethod
    def timezone_valid(cls, value: str | None) -> str | None:
        return require_iana_timezone(value) if value else value


class SiteLifecycleRequest(ApiModel):
    revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class SiteRemoveRequest(SiteLifecycleRequest):
    confirmation: str = Field(min_length=1, max_length=160)
    dependency_reviewed: bool


class SiteRestoreRequest(SiteLifecycleRequest):
    confirm_high_risk: bool


class RatePlanLifecycleRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    expected_dependency_token: str | None = Field(default=None, min_length=64, max_length=64)
    reason: str = Field(min_length=3, max_length=500)
    confirmation: str = Field(min_length=1, max_length=160)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)


class RatePlanRestoreRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    expected_dependency_token: str | None = Field(default=None, min_length=64, max_length=64)
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)


class RatePlanDraftDeleteRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    expected_dependency_token: str | None = Field(default=None, min_length=64, max_length=64)
    confirmation: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=3, max_length=500)


class RatePlanUnassignRequest(ApiModel):
    utility_account_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=1)
    expected_dependency_token: str = Field(min_length=64, max_length=64)
    effective_at: datetime
    reason: str = Field(min_length=8, max_length=500)
    confirmation: str = Field(min_length=1, max_length=180)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)

    _effective_aware = field_validator("effective_at")(require_aware)


class RateVersionLifecycleRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)
    confirmation: str = Field(min_length=1, max_length=180)
    idempotency_key: str = Field(min_length=8, max_length=160)


class RateVersionRestoreRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=160)


class RateAssignmentEndRequest(ApiModel):
    utility_account_id: str = Field(min_length=1, max_length=36)
    effective_at: datetime
    reason: str = Field(min_length=8, max_length=500)
    confirmation: Literal["END CURRENT"]
    idempotency_key: str = Field(min_length=8, max_length=160)

    _effective_aware = field_validator("effective_at")(require_aware)


class RateAssignmentRepairRequest(ApiModel):
    utility_account_id: str = Field(min_length=1, max_length=36)
    keep_assignment_id: str = Field(min_length=1, max_length=36)
    expected_assignment_ids: list[str] = Field(min_length=2, max_length=50)
    reason: str = Field(min_length=8, max_length=500)
    confirmation: Literal["REPAIR ASSIGNMENTS"]
    idempotency_key: str = Field(min_length=8, max_length=160)


class SiteSensorResolution(ApiModel):
    device_id: str
    action: Literal["archive", "transfer"]
    target_site_id: str | None = None

    @model_validator(mode="after")
    def transfer_has_target(self) -> SiteSensorResolution:
        if self.action == "transfer" and not self.target_site_id:
            raise ValueError("target_site_id is required when transferring a sensor")
        return self


class SiteAccountResolution(ApiModel):
    utility_account_id: str
    action: Literal["archive", "transfer"]
    target_site_id: str | None = None

    @model_validator(mode="after")
    def transfer_has_target(self) -> SiteAccountResolution:
        if self.action == "transfer" and not self.target_site_id:
            raise ValueError("target_site_id is required when transferring an account")
        return self


class SiteDependencyResolution(ApiModel):
    revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)
    sensors: list[SiteSensorResolution] = Field(default_factory=list, max_length=500)
    utility_accounts: list[SiteAccountResolution] = Field(default_factory=list, max_length=200)
    end_user_access_ids: list[str] = Field(default_factory=list, max_length=500)


class CircuitCreate(ApiModel):
    site_id: str
    parent_id: str | None = None
    name: str = Field(min_length=1, max_length=160)
    measurement_role: Literal["main", "service-leg", "branch", "submeter", "informational"]
    split_phase_group: str | None = Field(default=None, max_length=80)


class CircuitView(CircuitCreate):
    id: str


class UtilityAccountCreate(ApiModel):
    site_id: str
    name: str = Field(min_length=1, max_length=160)
    timezone: str = "America/Los_Angeles"
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    billing_cycle_start_day: int = Field(default=1, ge=1, le=31)
    baseline_allocation_kwh: Decimal | None = Field(default=None, ge=0)
    generation_provider: Literal["sce", "cca", "direct_access"] = "sce"


class BillImportAccountSummary(ApiModel):
    id: str
    site_id: str
    site_name: str
    name: str
    utility_name: str
    timezone: str
    currency: str
    provider_mode: str


class BillImportRatePlanSummary(ApiModel):
    id: str
    code: str
    name: str


class BillImportRateAssignmentSummary(ApiModel):
    id: str
    rate_version_id: str
    effective_from: datetime
    effective_to: datetime | None


class BillImportRateVersionSummary(ApiModel):
    id: str
    version: int
    pricing_model: str
    effective_from: date
    effective_to: date | None
    status: str


class BillImportRatePeriodSummary(ApiModel):
    label: str
    price_per_kwh: Decimal | None
    currency: str


class BillImportRateReadiness(ApiModel):
    account_configured: bool
    rate_assigned: bool
    rate_effective: bool


class UtilityAccountRateContextView(ApiModel):
    schema_version: Literal["utility-account-rate-context/1.0"]
    api_version: str
    backend_version: str
    backend_commit: str | None
    generated_client_schema_version: Literal["utility-account-rate-context/1.0"]
    account_id: str | None
    site_id: str | None
    account: BillImportAccountSummary | None
    available_accounts: list[BillImportAccountSummary]
    current_plan: BillImportRatePlanSummary | None
    current_assignment: BillImportRateAssignmentSummary | None
    current_rate_version: BillImportRateVersionSummary | None
    current_period: BillImportRatePeriodSummary | None
    readiness: BillImportRateReadiness


class RateAssignmentWrite(ApiModel):
    rate_version_id: str
    effective_from: datetime
    effective_to: datetime | None = None
    assignment_reason: str | None = Field(default=None, max_length=500)
    replace_current: bool = False
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)

    _from_aware = field_validator("effective_from")(require_aware)
    _to_aware = field_validator("effective_to")(
        lambda value: require_aware(value) if value else value
    )

    @model_validator(mode="after")
    def valid_window(self) -> RateAssignmentWrite:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective-through must follow effective-from")
        return self


class RateAssignmentReplaceRequest(RateAssignmentWrite):
    utility_account_id: str = Field(min_length=1, max_length=36)
    confirmation: Literal["REPLACE CURRENT"]
    expected_account_revision: int | None = Field(default=None, ge=1)
    expected_current_assignment_revision: int | None = Field(default=None, ge=1)


class RateAssignmentResult(ApiModel):
    schema_version: Literal["rate-assignment-result/1.0"]
    assignment_id: str
    electric_service_id: str
    plan_id: str
    version_id: str
    version: int
    effective_from: datetime
    effective_to: datetime | None
    state: Literal["current", "scheduled", "historical", "cancelled"]
    effective_now: bool
    replaced_assignment_id: str | None
    replaced_assignment_ids: list[str]
    recalculation_job_id: str | None
    cost_recalculation: dict[str, Any]
    warnings: list[str]
    service_revision: int
    history_preserved: bool
    idempotent: bool


class CurrentRateAssignmentDetail(ApiModel):
    assignment_id: str
    assignment_revision: int
    plan_id: str | None
    plan_code: str | None
    plan_name: str | None
    version_id: str
    version: int | None
    pricing_model: str | None
    effective_from: datetime
    effective_to: datetime | None
    state: Literal["current"]


class CurrentRateAssignment(ApiModel):
    schema_version: Literal["current-rate-assignment/1.0"]
    home_id: str
    electric_service_id: str | None
    service_revision: int | None = None
    assignment: CurrentRateAssignmentDetail | None


class ConfigurationAction(ApiModel):
    id: str
    label: str
    target: str
    required_permissions: list[str] = Field(default_factory=list)


class ConfigurationIssue(ApiModel):
    id: str
    category: Literal[
        "electric_service",
        "rate_plan",
        "billing_cycle",
        "sensor",
        "notification",
        "backup",
        "rate_source",
        "system",
    ]
    state: Literal[
        "setup_needed",
        "partially_configured",
        "waiting_for_data",
        "attention_required",
        "error",
    ]
    title: str
    what_is_wrong: str
    why_it_matters: str
    how_to_fix: str
    blocking: bool
    action: ConfigurationAction


class ConfigurationStatus(ApiModel):
    schema_version: Literal["configuration-status/1.0"]
    home_id: str
    electric_service_id: str | None
    state: Literal[
        "ready",
        "setup_needed",
        "partially_configured",
        "waiting_for_data",
        "attention_required",
        "error",
    ]
    label: str
    summary: str
    generated_at: datetime
    issues: list[ConfigurationIssue]


class UtilityAdjustmentWrite(ApiModel):
    component: Literal[
        "cca_generation",
        "direct_access",
        "baseline_credit",
        "service_charge",
        "tax_fee",
        "custom_fixed",
        "custom_per_kwh",
    ]
    value: Decimal
    unit: Literal["per_kwh", "fixed", "percent", "included"]
    provenance: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=500)
    evidence_reference: str | None = Field(default=None, max_length=500)
    effective_from: datetime
    effective_to: datetime | None = None
    enabled: bool = True

    _from_aware = field_validator("effective_from")(require_aware)
    _to_aware = field_validator("effective_to")(
        lambda value: require_aware(value) if value else value
    )

    @model_validator(mode="after")
    def valid_window(self) -> UtilityAdjustmentWrite:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective-through must follow effective-from")
        return self


class UtilityAdjustmentUpdate(UtilityAdjustmentWrite):
    revision: int = Field(ge=1)


class UtilityAccountWizardCreate(ApiModel):
    name: str = Field(min_length=1, max_length=160)
    nickname: str | None = Field(default=None, max_length=160)
    account_number_suffix: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{2,8}$")
    status: Literal["active"] = "active"
    utility_provider: Literal["sce", "cca", "direct_access", "custom"] = "sce"
    generation_provider: Literal["sce", "cca", "direct_access", "custom"] = "sce"
    provider_mode: Literal[
        "sce_bundled",
        "sce_delivery_generation",
        "sce_delivery_cca",
        "sce_delivery_direct_access",
        "custom_combined",
    ] = "sce_bundled"
    billing_cycle_start_day: int = Field(default=1, ge=1, le=31)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    baseline_allocation_kwh: Decimal | None = Field(default=None, ge=0)
    service_class: str | None = Field(default=None, max_length=80)
    rate_assignment: RateAssignmentWrite
    cost_scope: Literal["energy_only", "allocated_account_estimate", "full_account_estimate"] = (
        "energy_only"
    )
    allocation_method: str | None = Field(default=None, max_length=80)
    full_account_override: bool = False
    adjustments: list[UtilityAdjustmentWrite] = Field(default_factory=list, max_length=30)
    confirmation: bool


class UtilityAccountUpdate(ApiModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    nickname: str | None = Field(default=None, max_length=160)
    account_number_suffix: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{2,8}$")
    billing_cycle_start_day: int | None = Field(default=None, ge=1, le=31)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    baseline_allocation_kwh: Decimal | None = Field(default=None, ge=0)
    generation_provider: Literal["sce", "cca", "direct_access", "custom"] | None = None
    provider_mode: (
        Literal[
            "sce_bundled",
            "sce_delivery_generation",
            "sce_delivery_cca",
            "sce_delivery_direct_access",
            "custom_combined",
        ]
        | None
    ) = None
    service_class: str | None = Field(default=None, max_length=80)


class UtilityCostScopeWrite(ApiModel):
    revision: int = Field(ge=1)
    cost_scope: Literal["energy_only", "allocated_account_estimate", "full_account_estimate"]
    allocation_method: str | None = Field(default=None, max_length=80)
    full_account_override: bool = False


class AccountUsageAuthorityWrite(ApiModel):
    revision: int | None = Field(default=None, ge=1)
    authority_type: Literal[
        "complete_site_aggregate",
        "service_leg_pair",
        "whole_account_meter",
        "utility_interval_import",
        "manual_cycle_usage",
        "external_feed",
        "partial_monitored_circuits",
    ]
    aggregate_set_id: str | None = None
    device_ids: list[str] = Field(default_factory=list, max_length=32)
    source_reference: str | None = Field(default=None, max_length=500)
    confidence: Literal["unverified", "low", "medium", "high", "utility_verified"] = "unverified"
    complete_account: bool = False
    calculation_role: Literal["sensor_measurements", "advanced_external_correction"] | None = None
    confirmation: Literal["ALTER TIER PROGRESSION"] | None = None
    reason: str = Field(default="Reviewed usage-authority selection", min_length=8, max_length=500)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)

    @model_validator(mode="after")
    def required_source(self) -> AccountUsageAuthorityWrite:
        sensor_authorities = {
            "complete_site_aggregate",
            "service_leg_pair",
            "whole_account_meter",
        }
        expected_role: Literal["sensor_measurements", "advanced_external_correction"] = (
            "sensor_measurements"
            if self.authority_type in sensor_authorities
            else "advanced_external_correction"
        )
        if self.calculation_role is None:
            self.calculation_role = expected_role
        if self.calculation_role != expected_role:
            raise ValueError("calculation role does not match the selected usage authority")
        if expected_role == "advanced_external_correction":
            if self.confirmation != "ALTER TIER PROGRESSION":
                raise ValueError(
                    "advanced usage authority requires explicit tier-progression confirmation"
                )
            if (
                (self.source_reference or "")
                .lower()
                .startswith(("utility-bill:", "urn:power-monitor:utility-bill:"))
            ):
                raise ValueError("a standard utility bill cannot become usage authority")
        if self.authority_type == "complete_site_aggregate" and not self.aggregate_set_id:
            raise ValueError("complete site aggregate authority requires an aggregate set")
        if self.authority_type == "service_leg_pair" and len(set(self.device_ids)) != 2:
            raise ValueError("service-leg authority requires exactly two distinct sensors")
        if self.authority_type == "whole_account_meter" and len(set(self.device_ids)) != 1:
            raise ValueError("whole-account meter authority requires exactly one sensor")
        if self.authority_type == "partial_monitored_circuits" and self.complete_account:
            raise ValueError("partial monitored circuits cannot claim complete-account authority")
        return self


class ManualAccountUsageWrite(ApiModel):
    effective_at: datetime
    cumulative_kwh: Decimal = Field(ge=0)
    source_note: str = Field(min_length=1, max_length=500)
    evidence_reference: str | None = Field(default=None, max_length=500)
    verification_status: Literal["unverified", "verified", "reconciled"] = "unverified"
    idempotency_key: str = Field(min_length=8, max_length=128)
    calculation_role: Literal["advanced_external_correction"]
    confirmation: Literal["ALTER TIER PROGRESSION"]

    _effective_aware = field_validator("effective_at")(require_aware)


class UtilityUsageImportWrite(ApiModel):
    import_kind: Literal["interval", "daily", "cycle_cumulative", "cycle_dates", "bill_total"]
    timezone: str = Field(min_length=1, max_length=64)
    source_name: str = Field(min_length=1, max_length=240)
    field_mapping: dict[str, str] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=10000)
    conflict_policy: Literal["reject", "prefer_utility", "prefer_monitored", "keep_separate"] = (
        "reject"
    )
    commit: bool = False
    calculation_role: Literal["advanced_external_correction", "reference_only"]
    confirmation: Literal["ALTER TIER PROGRESSION"] | None = None

    @model_validator(mode="after")
    def protected_calculation_role(self) -> UtilityUsageImportWrite:
        reference_kinds = {"cycle_dates", "bill_total"}
        expected = (
            "reference_only"
            if self.import_kind in reference_kinds
            else "advanced_external_correction"
        )
        if self.calculation_role != expected:
            raise ValueError(f"{self.import_kind} imports require calculation_role={expected}")
        if (
            expected == "advanced_external_correction"
            and self.confirmation != "ALTER TIER PROGRESSION"
        ):
            raise ValueError(
                "usage imports that affect tier progression require explicit confirmation"
            )
        return self


class BillingCycleOverrideWrite(ApiModel):
    starts_at: datetime
    ends_at: datetime
    source: Literal["manual_override", "utility_import", "external_feed"] = "manual_override"
    reason: str = Field(min_length=1, max_length=500)

    _starts_aware = field_validator("starts_at")(require_aware)
    _ends_aware = field_validator("ends_at")(require_aware)

    @model_validator(mode="after")
    def valid_cycle(self) -> BillingCycleOverrideWrite:
        if self.ends_at <= self.starts_at:
            raise ValueError("billing-cycle end must follow its start")
        if (self.ends_at - self.starts_at).days < 20 or (self.ends_at - self.starts_at).days > 45:
            raise ValueError("billing cycle must be between 20 and 45 days")
        return self


class ReconciliationAdjustmentWrite(ApiModel):
    component: Literal[
        "utility_bill_difference",
        "tax",
        "credit",
        "fixed_charge",
        "provider_adjustment",
        "other",
    ]
    amount: Decimal
    notes: str = Field(min_length=1, max_length=1000)
    provenance: str = Field(min_length=1, max_length=500)


class NetworkPolicyWrite(ApiModel):
    revision: int = Field(ge=1)
    mode: Literal["allow_listed_private", "allow_all_private", "deny_all"]
    reason: str | None = Field(default=None, max_length=500)


class NetworkCidrWrite(ApiModel):
    policy_id: str
    network: str = Field(min_length=3, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    revision: int | None = Field(default=None, ge=1)


class NetworkAddressTest(ApiModel):
    policy_id: str
    address: str = Field(min_length=2, max_length=80)


class AggregateMemberInput(ApiModel):
    circuit_id: str | None = None
    device_id: str | None = None
    allocation_percent: Decimal = Field(default=Decimal("100"), gt=0, le=100)

    @model_validator(mode="after")
    def one_target(self) -> AggregateMemberInput:
        if (self.circuit_id is None) == (self.device_id is None):
            raise ValueError("exactly one of circuit_id and device_id is required")
        return self


class AggregateSetCreate(ApiModel):
    site_id: str
    utility_account_id: str | None = None
    name: str = Field(min_length=1, max_length=160)
    cost_scope: Literal["energy_only", "allocated_account", "full_account"] = "energy_only"
    is_default: bool = False
    confirm_overlap: bool = False
    members: list[AggregateMemberInput] = Field(min_length=1)


class AlertRuleWrite(ApiModel):
    name: str = Field(min_length=1, max_length=160)
    rule_type: Literal[
        "heartbeat_stale",
        "device_api_unreachable",
        "authentication_failure",
        "protocol_incompatible",
        "pzem_failure",
        "no_valid_reading",
        "sd_failure",
        "storage_sequence_reconciling",
        "storage_sequence_continuity_restored",
        "storage_cursor_regression",
        "storage_pressure_notice",
        "storage_pressure_warning",
        "storage_pressure_critical",
        "storage_pressure_emergency",
        "storage_cleanup_blocked",
        "storage_write_reserve_unavailable",
        "storage_interval_dropped",
        "sync_backlog",
        "sequence_gap",
        "time_untrusted",
        "low_rssi",
        "power_surge",
        "ct_utilization",
        "voltage_frequency_range",
        "reboot_loop",
        "firmware_failure",
        "server_failure",
        "device_address_outside_policy",
        # Bootstrap identifiers retained for backward-compatible rule editing.
        "api_unreachable",
        "reading_stale",
        "ct_limit_80",
        "ct_limit_90",
        "voltage_range",
        "frequency_range",
        "firmware_failed",
        "worker_failure",
        "backup_failure",
        "rate_check_succeeded",
        "rate_source_changed",
        "rate_candidate_pending",
        "rate_candidate_validation_failed",
        "rate_source_unavailable",
        "rate_candidate_approved",
        "rate_candidate_rejected",
        "rate_parser_failed",
        "rate_source_conflict",
        "rate_version_activated",
        "rate_version_auto_activated",
        "rate_retroactive_activated",
        "rate_estimates_recalculated",
        "rate_source_stale",
    ]
    severity: Literal["info", "warning", "error", "critical"]
    enabled: bool = True
    site_id: str | None = None
    device_id: str | None = None
    debounce_seconds: int = Field(default=0, ge=0, le=86400)
    resolve_seconds: int = Field(default=0, ge=0, le=86400)
    configuration: dict[str, Any] = Field(default_factory=dict)


class AlertSilence(ApiModel):
    until: datetime
    note: str = Field(default="", max_length=500)

    _aware = field_validator("until")(require_aware)


class MaintenanceWindow(ApiModel):
    until: datetime
    note: str = Field(default="", max_length=500)

    _aware = field_validator("until")(require_aware)


class DeviceCommandCreate(ApiModel):
    command_type: Literal[
        "apply_configuration",
        "reboot",
        "ota_update",
        "sync_now",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_current_state: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(
        default=None, min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    expires_in_seconds: int = Field(default=1800, ge=30, le=86400)


class NotificationChannelWrite(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    channel_type: Literal["smtp", "https_webhook", "in_app"]
    enabled: bool = True
    configuration: dict[str, Any] = Field(default_factory=dict)


class ReportDefinitionWrite(ApiModel):
    name: str = Field(min_length=1, max_length=160)
    report_type: Literal[
        "daily_summary",
        "monthly_summary",
        "billing_cycle_summary",
        "device_comparison",
        "tou_breakdown",
        "cost_component_breakdown",
    ]
    configuration: dict[str, Any] = Field(default_factory=dict)


class EnrollmentTokenCreate(ApiModel):
    expires_in_seconds: int = Field(default=600, ge=60, le=86400)
    site_id: str | None = None
    circuit_id: str | None = None
    name: str | None = Field(default=None, max_length=160)
    measurement_role: Literal["main", "service-leg", "branch", "submeter", "informational"] = (
        "submeter"
    )
    ct_rating_amps: Decimal = Field(default=Decimal("100"), gt=0, le=5000)
    connection_mode: Literal["pull", "push", "hybrid"] = "push"


class EnrollmentTokenView(ApiModel):
    id: str
    token: str
    expires_at: datetime
    preassignment: dict[str, Any]


class FirmwareOtaReadinessView(ApiModel):
    state: Literal[
        "ready",
        "legacy_signed_ota_only",
        "trust_missing",
        "bootstrap_required",
        "unsupported",
    ]
    label: str
    supported: bool
    protocol_version: int | None
    authentication_mode: str | None
    rollback_supported: bool
    partition_size_bytes: int | None


class DeviceListItem(ApiModel):
    id: str
    name: str
    site_id: str
    site_name: str | None
    utility_account_id: str | None
    circuit_id: str | None
    circuit_name: str | None
    connection_mode: str
    protocol_version: str
    measurement_role: str
    cost_scope: str
    included_in_default: bool
    ct_rating_amps: Decimal
    status: str
    lifecycle_status: str
    decommissioned_at: datetime | None
    decommissioned_by: str | None
    decommissioned_by_name: str | None
    decommission_reason: str | None
    removed_site_id: str | None
    removed_circuit_id: str | None
    removed_circuit_name: str | None
    retained_history: bool
    re_enrollment_allowed: bool
    last_seen_at: datetime | None
    firmware_version: str | None
    firmware_build_hash: str | None
    firmware_ota: FirmwareOtaReadinessView
    current_watts: Decimal | None
    voltage_volts: Decimal | None
    current_amps: Decimal | None
    frequency_hz: Decimal | None
    power_factor: Decimal | None
    latest_measurement_at: datetime | None
    measurement_received_at: datetime | None
    measurement_sequence: int | None
    measurement_source: Literal["heartbeat_live", "committed_reading"] | None
    measurement_freshness: Literal[
        "live",
        "waiting",
        "stale",
        "offline",
        "unavailable",
        "invalid",
        "needs_attention",
    ]
    measurement_invalid_metrics: list[str] = Field(default_factory=list)
    heartbeat_received_at: datetime | None
    heartbeat_age_seconds: int | None = Field(default=None, ge=0)
    heartbeat_freshness: Literal["never_received", "online", "offline"]
    offline_after_seconds: int = Field(ge=1)
    previous_outage_reason: str | None
    rssi_dbm: int | None
    pzem_ok: bool | None
    pzem_status: str | None
    pzem_details: dict[str, Any] | None
    sd_ok: bool | None
    sd_status: str | None
    sd_details: dict[str, Any] | None
    card_generation: str | None
    current_energy_wh: Decimal | None
    time_trusted: bool | None
    backlog: int


class DeviceMeasurementAssignmentWrite(ApiModel):
    circuit_id: str | None = None
    utility_account_id: str | None = None
    include_in_default_site_total: bool = False
    reason: str = Field(min_length=3, max_length=500)


class DeviceMeasurementAssignmentView(ApiModel):
    device_id: str
    site_id: str
    circuit_id: str | None
    circuit_name: str | None
    utility_account_id: str | None
    utility_account_name: str | None
    measurement_role: str
    cost_scope: str
    included_in_default_site_total: bool


class DeviceOtaCapability(DeviceProtocolModel):
    supported: bool
    protocol_version: int = Field(ge=1, le=100)
    authentication_mode: Literal["existing_device_hmac", "ed25519_legacy", "none"]
    rollback_supported: bool
    partition_size_bytes: int = Field(gt=0, le=32 * 1024 * 1024)


class DeviceCapabilities(DeviceProtocolModel):
    hardware_target: str = Field(min_length=1, max_length=120)
    pzem_model: str = Field(pattern=r"^PZEM-004T V4")
    sd_present: bool
    sd_required: bool = True
    supported_endpoints: list[str] = Field(default_factory=list)
    data_reset_protocol: Literal["data-reset/1.0.0"] | None = None
    ota: DeviceOtaCapability | None = None


class EnrollmentClaim(DeviceProtocolModel):
    token: str = Field(min_length=32, max_length=256)
    protocol_version: str
    hardware_id: str = Field(min_length=8, max_length=128)
    requested_name: str | None = Field(default=None, max_length=160)
    capabilities: DeviceCapabilities


class EnrollmentClaimResponse(ApiModel):
    protocol_version: str
    device_id: str
    enrollment_secret: str
    credential_fingerprint: str
    effective_metadata: dict[str, Any]
    server_ota_signing_public_key: str | None
    heartbeat_policy: dict[str, int]
    sync_policy: dict[str, int]


class LiveMeasurement(DeviceProtocolModel):
    measured_at: datetime
    voltage_v: Decimal = Field(ge=0, le=400)
    current_a: Decimal = Field(ge=0, le=5000)
    power_w: Decimal = Field(ge=0, le=10_000_000)
    power_factor: Decimal = Field(ge=0, le=1)
    frequency_hz: Decimal = Field(ge=40, le=70)
    energy_wh: Decimal = Field(ge=0)

    _aware = field_validator("measured_at")(require_aware)


class StorageHealthDetails(BaseModel):
    """Typed storage-integrity evidence with forward-compatible extra fields."""

    model_config = ConfigDict(extra="allow")

    # ``history_integrity_verified`` is the original firmware field. New
    # firmware names the same reading-index proof explicitly; retain both so
    # older enrolled sensors remain valid throughout rolling upgrades.
    history_integrity_verified: bool | None = None
    reading_index_integrity_verified: bool | None = None
    event_log_integrity_verified: bool | None = None
    event_log_integrity_status: (
        Literal[
            "verified",
            "not_scanned",
            "unavailable",
            "event_record_corruption_detected",
            "event_log_open_failed",
        ]
        | None
    ) = None

    def get(self, key: str, default: Any = None) -> Any:
        """Preserve the mapping-style access used by legacy heartbeat code."""

        return getattr(self, key, default)

    @property
    def effective_reading_index_integrity_verified(self) -> bool | None:
        if self.reading_index_integrity_verified is not None:
            return self.reading_index_integrity_verified
        return self.history_integrity_verified


class SubsystemHealth(DeviceProtocolModel):
    ok: bool
    status: str
    error_count: int = Field(default=0, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


class StorageSubsystemHealth(DeviceProtocolModel):
    ok: bool
    status: str
    error_count: int = Field(default=0, ge=0)
    details: StorageHealthDetails = Field(default_factory=StorageHealthDetails)


class TimeHealth(DeviceProtocolModel):
    trusted: bool
    source: str
    offset_ms: int | None = None
    last_sync_at: datetime | None = None


class HeartbeatDataResetStatus(DeviceProtocolModel):
    protocol: Literal["data-reset/1.0.0"] = "data-reset/1.0.0"
    state: str = Field(min_length=1, max_length=48)
    checkpoint: str = Field(min_length=1, max_length=48)
    operation_id: str | None = None
    target_generation: int | None = Field(default=None, gt=0)
    failure_code: str | None = Field(default=None, max_length=80, pattern=FAILURE_CODE_PATTERN)
    reset_boundary: ResetBoundary | None = None
    reset_required: bool = False


class Heartbeat(DeviceProtocolModel):
    protocol_version: str
    schema_version: str = "heartbeat/1.0.0"
    device_id: str
    boot_id: str
    firmware_version: str
    firmware_build_hash: str
    data_generation: int = Field(default=0, ge=0)
    uptime_seconds: int = Field(ge=0)
    reboot_reason: str
    current_ip: str | None = None
    hostname: str | None = None
    rssi_dbm: int | None = Field(default=None, ge=-127, le=0)
    connection_mode: Literal["pull", "push", "hybrid"]
    latest: LiveMeasurement | None = None
    pzem: SubsystemHealth
    sd: StorageSubsystemHealth
    oldest_stored_sequence: SignedSequence
    oldest_syncable_sequence: SignedSequence | None = None
    newest_syncable_sequence: SignedSequence | None = None
    newest_stored_sequence: SignedSequence
    server_ack_sequence: SignedSequence
    server_maximum_seen_sequence: SignedSequence | None = None
    backlog_estimate: int = Field(ge=0)
    configuration_version: int = Field(ge=0)
    time: TimeHealth
    resources: dict[str, Any] = Field(default_factory=dict)
    queue: dict[str, Any] = Field(default_factory=dict)
    ota: DeviceOtaCapability | None = None
    data_reset: HeartbeatDataResetStatus | None = None

    @model_validator(mode="after")
    def valid_sequence_bounds(self) -> Heartbeat:
        if (
            self.newest_stored_sequence
            and self.oldest_stored_sequence > self.newest_stored_sequence
        ):
            raise ValueError("oldest_stored_sequence exceeds newest_stored_sequence")
        if (
            self.oldest_syncable_sequence
            and self.oldest_syncable_sequence > self.newest_stored_sequence
        ):
            raise ValueError("oldest_syncable_sequence exceeds newest_stored_sequence")
        if (
            self.newest_syncable_sequence is not None
            and self.newest_syncable_sequence > self.newest_stored_sequence
        ):
            raise ValueError("newest_syncable_sequence exceeds newest_stored_sequence")
        if (
            self.oldest_syncable_sequence is not None
            and self.newest_syncable_sequence is not None
            and self.oldest_syncable_sequence > self.newest_syncable_sequence
        ):
            raise ValueError("oldest_syncable_sequence exceeds newest_syncable_sequence")
        return self


class SequenceCursorResponse(ApiModel):
    highest_contiguous_accepted_sequence: SignedSequence
    maximum_seen_sequence: SignedSequence
    next_sequence_floor: PositiveSignedSequence
    data_generation: int = Field(default=0, ge=0)
    reset_boundary: ResetBoundary = 0


class HeartbeatResponse(ApiModel):
    server_receive_time: datetime
    highest_contiguous_accepted_sequence: SignedSequence
    sequence_cursor: SequenceCursorResponse
    gap_ranges: list[tuple[PositiveSignedSequence, PositiveSignedSequence]]
    desired_configuration_version: int
    firmware_release_available: bool
    recommended_heartbeat_interval_seconds: int
    immediate_sync_requested: bool


class Reading(DeviceProtocolModel):
    data_generation: int = Field(default=0, ge=0)
    sequence: PositiveSignedSequence
    boot_id: str
    interval_start: datetime
    interval_end: datetime
    time_trusted: bool
    voltage_avg: Decimal | None = Field(default=None, ge=0, le=400)
    voltage_min: Decimal | None = Field(default=None, ge=0, le=400)
    voltage_max: Decimal | None = Field(default=None, ge=0, le=400)
    current_avg: Decimal | None = Field(default=None, ge=0, le=5000)
    current_min: Decimal | None = Field(default=None, ge=0, le=5000)
    current_max: Decimal | None = Field(default=None, ge=0, le=5000)
    power_avg: Decimal | None = Field(default=None, ge=0, le=10_000_000)
    power_min: Decimal | None = Field(default=None, ge=0, le=10_000_000)
    power_max: Decimal | None = Field(default=None, ge=0, le=10_000_000)
    power_factor: Decimal | None = Field(default=None, ge=0, le=1)
    frequency_hz: Decimal | None = Field(default=None, ge=40, le=70)
    pzem_energy_start_wh: Decimal | None = Field(default=None, ge=0)
    pzem_energy_end_wh: Decimal | None = Field(default=None, ge=0)
    device_lifetime_energy_wh: Decimal | None = Field(default=None, ge=0)
    interval_energy_wh: Decimal | None = Field(default=None, ge=0)
    energy_method: str = Field(max_length=40)
    ct_rating_amps: Decimal = Field(gt=0, le=5000)
    quality_flags: list[str] = Field(default_factory=list)
    firmware_version: str
    record_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    _aware_start = field_validator("interval_start")(require_aware)
    _aware_end = field_validator("interval_end")(require_aware)

    @model_validator(mode="after")
    def interval_order(self) -> Reading:
        if self.interval_end <= self.interval_start:
            raise ValueError("interval_end must be later than interval_start")
        if (self.interval_end - self.interval_start).total_seconds() > 86400:
            raise ValueError("a durable record cannot span more than 24 hours")
        return self


class UnavailableSequenceRange(DeviceProtocolModel):
    start_sequence: PositiveSignedSequence
    end_sequence: PositiveSignedSequence

    @model_validator(mode="after")
    def valid_bounds(self) -> UnavailableSequenceRange:
        if self.end_sequence < self.start_sequence:
            raise ValueError("end_sequence must be greater than or equal to start_sequence")
        return self


class ReadingBatch(DeviceProtocolModel):
    protocol_version: str
    schema_version: str = "reading-batch/1.0.0"
    device_id: str
    data_generation: int = Field(default=0, ge=0)
    readings: list[Reading] = Field(default_factory=list, max_length=500)
    unavailable_sequence_ranges: list[UnavailableSequenceRange] = Field(
        default_factory=list,
        max_length=500,
    )

    @model_validator(mode="after")
    def valid_sequence_coverage(self) -> ReadingBatch:
        if any(reading.data_generation != self.data_generation for reading in self.readings):
            raise ValueError("every reading must match the batch data_generation")
        reading_sequences = {reading.sequence for reading in self.readings}
        if not reading_sequences and not self.unavailable_sequence_ranges:
            raise ValueError("a reading batch must contain readings or unavailable sequence ranges")
        previous_end = 0
        unavailable_count = 0
        for unavailable in self.unavailable_sequence_ranges:
            if unavailable.start_sequence <= previous_end:
                raise ValueError("unavailable_sequence_ranges must be ordered and non-overlapping")
            if any(
                unavailable.start_sequence <= sequence <= unavailable.end_sequence
                for sequence in reading_sequences
            ):
                raise ValueError("a reading sequence cannot also be declared unavailable")
            unavailable_count += unavailable.end_sequence - unavailable.start_sequence + 1
            if unavailable_count > 500:
                raise ValueError("unavailable_sequence_ranges cannot cover more than 500 sequences")
            previous_end = unavailable.end_sequence
        return self


class RejectedReading(ApiModel):
    sequence: PositiveSignedSequence | None
    code: str
    detail: str


class ReadingBatchResponse(ApiModel):
    accepted: list[PositiveSignedSequence]
    duplicates: list[PositiveSignedSequence]
    rejected: list[RejectedReading]
    highest_contiguous_accepted_sequence: SignedSequence
    missing_ranges: list[tuple[PositiveSignedSequence, PositiveSignedSequence]]
    data_generation: int = Field(default=0, ge=0)
    reset_boundary: ResetBoundary = 0


DataResetCategory = Literal[
    "measurement_history",
    "cost_history",
    "pricing_history",
    "generated_outputs",
]
DataResetDisconnectedSensorPolicy = Literal["block", "defer_until_reconnect"]
DataResetBackupMode = Literal["verified_backup", "permanent_without_backup"]
DataResetOperationState = Literal[
    "planning",
    "awaiting_confirmation",
    "preparing_sensors",
    "sensors_prepared",
    "backup_running",
    "backup_verified",
    "database_reset_running",
    "database_reset_committed",
    "sensor_commit_running",
    "verification_running",
    "completed",
    "completed_with_resets_pending_on_reconnect",
    "partial_failure",
    "attention_required",
    "cancelled",
    "failed_before_commit",
]
DataResetParticipantState = Literal[
    "pending",
    "unreachable",
    "unsupported",
    "prepare_requested",
    "prepared",
    "commit_requested",
    "committed",
    "verified",
    "pending_reconnect",
    "failed",
    "attention_required",
    "not_applicable",
]


class DataResetPlanRequest(ApiModel):
    site_id: str = Field(min_length=1, max_length=36)
    categories: list[DataResetCategory] = Field(min_length=1, max_length=4)
    delete_imported_bill_documents: bool = False
    disconnected_sensor_policy: DataResetDisconnectedSensorPolicy = "defer_until_reconnect"

    @field_validator("categories")
    @classmethod
    def unique_categories(cls, value: list[DataResetCategory]) -> list[DataResetCategory]:
        if len(value) != len(set(value)):
            raise ValueError("reset categories must be unique")
        required = {
            "measurement_history",
            "cost_history",
            "pricing_history",
            "generated_outputs",
        }
        if set(value) != required:
            raise ValueError(
                "data-only reset requires every history category; imported bill "
                "documents are the only optional deletion scope"
            )
        return value


class DataResetCountSummary(ApiModel):
    raw_readings: int = Field(default=0, ge=0)
    normalized_intervals: int = Field(default=0, ge=0)
    rollups: int = Field(default=0, ge=0)
    heartbeat_rows: int = Field(default=0, ge=0)
    sequence_gaps: int = Field(default=0, ge=0)
    cost_rows: int = Field(default=0, ge=0)
    tier_rows: int = Field(default=0, ge=0)
    historical_pricing_rows: int = Field(default=0, ge=0)
    imported_bill_documents: int = Field(default=0, ge=0)
    exports: int = Field(default=0, ge=0)
    reports: int = Field(default=0, ge=0)
    sensor_records: int = Field(default=0, ge=0)
    estimated_database_bytes: int = Field(default=0, ge=0)


class DataResetPlanParticipant(ApiModel):
    model_config = ConfigDict(from_attributes=True, extra="allow")

    device_id: str
    name: str
    classification: Literal[
        "connected",
        "authentication_failed",
        "disconnected",
        "unsupported",
        "revoked",
        "removed",
    ]
    supported: bool
    boundary: ResetBoundary
    estimated_sensor_records: int | None = Field(default=None, ge=0)
    local_record_count: int | None = Field(default=None, ge=0)
    backlog_estimate: int | None = Field(default=None, ge=0)
    record_count_status: Literal[
        "exact_prepare_projection", "last_reported", "unavailable", "not_applicable"
    ]
    prepare_drain_records_projected: int = Field(default=0, ge=0, le=2)
    prepare_drain_first_sequence_projected: SignedSequence | None = None
    prepare_drain_last_sequence_projected: SignedSequence | None = None
    prepare_drain_syncable_records_projected: int = Field(default=0, ge=0, le=2)
    last_seen_at: datetime | None = None
    firmware_version: str | None = None
    firmware_build_hash: str | None = None
    data_generation: int = Field(default=0, ge=0)
    server_highest_contiguous: ResetBoundary = 0
    server_maximum_seen: ResetBoundary = 0
    sensor_ack_sequence: SignedSequence = 0
    sensor_newest_sequence: SignedSequence = 0
    old_sequence_floor: SignedSequence = 0
    old_next_sequence: PositiveSignedSequence = 1
    card_generation: str | None = None
    card_identity_status: str | None = None
    sd_status: str | None = None
    probe_status: str | None = None

    @model_validator(mode="after")
    def validate_prepare_projection(self) -> DataResetPlanParticipant:
        if self.record_count_status == "exact_prepare_projection" and (
            self.local_record_count is None or self.backlog_estimate is None
        ):
            raise ValueError("an exact prepare projection requires record and backlog counts")
        if self.prepare_drain_records_projected == 0:
            if (
                self.prepare_drain_first_sequence_projected is not None
                or self.prepare_drain_last_sequence_projected is not None
                or self.prepare_drain_syncable_records_projected != 0
            ):
                raise ValueError("an empty prepare drain cannot have sequence evidence")
        elif (
            self.prepare_drain_first_sequence_projected is None
            or self.prepare_drain_first_sequence_projected <= 0
            or self.prepare_drain_last_sequence_projected is None
            or self.prepare_drain_last_sequence_projected
            - self.prepare_drain_first_sequence_projected
            + 1
            != self.prepare_drain_records_projected
            or self.prepare_drain_syncable_records_projected > self.prepare_drain_records_projected
        ):
            raise ValueError("a projected prepare drain requires sequence evidence")
        return self


class DataResetPricingPlan(ApiModel):
    model_config = ConfigDict(from_attributes=True, extra="allow")

    utility_account_id: str
    rate_plan_id: str
    rate_version_id: str
    rate_assignment_id: str | None = None
    pricing_configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DataResetConfirmationPhrases(ApiModel):
    verified_backup: Literal["RESET ALL READINGS AND PRICING HISTORY"] = (
        "RESET ALL READINGS AND PRICING HISTORY"
    )
    permanent_without_backup: Literal[
        "PERMANENTLY RESET ALL READINGS AND PRICING HISTORY WITHOUT BACKUP"
    ] = "PERMANENTLY RESET ALL READINGS AND PRICING HISTORY WITHOUT BACKUP"


class DataResetPlanView(ApiModel):
    model_config = ConfigDict(from_attributes=True, extra="allow")

    plan_id: str
    site: dict[str, Any]
    categories: list[DataResetCategory]
    delete_imported_bill_documents: bool
    disconnected_sensor_policy: DataResetDisconnectedSensorPolicy
    reset_timestamp: datetime
    reset_generation: int = Field(gt=0)
    counts: dict[str, int]
    estimated_database_bytes: int = Field(ge=0)
    estimated_sensor_records: int = Field(ge=0)
    sensor_records_to_delete_now: int = Field(ge=0)
    participants: list[DataResetPlanParticipant]
    pricing: list[DataResetPricingPlan]
    preserved: list[str]
    confirmation_phrases: DataResetConfirmationPhrases
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(gt=0)
    created_at: datetime
    expires_at: datetime


class DataResetExecuteRequest(ApiModel):
    plan_id: str = Field(min_length=1, max_length=36)
    plan_revision: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=160)
    reason: str = Field(min_length=8, max_length=500)
    backup_mode: DataResetBackupMode = "verified_backup"
    confirmation_phrase: str = Field(min_length=1, max_length=240)
    permanent_without_backup_acknowledged: bool = False

    @model_validator(mode="after")
    def validate_backup_acknowledgement(self) -> DataResetExecuteRequest:
        if (
            self.backup_mode == "permanent_without_backup"
            and not self.permanent_without_backup_acknowledged
        ):
            raise ValueError("permanent reset without backup must be acknowledged")
        if self.backup_mode == "verified_backup" and self.permanent_without_backup_acknowledged:
            raise ValueError("no-backup acknowledgement is invalid when backup is enabled")
        return self


class DataResetRetryRequest(ApiModel):
    idempotency_key: str = Field(min_length=8, max_length=160)
    reason: str = Field(min_length=8, max_length=500)


class DataResetCancelRequest(ApiModel):
    idempotency_key: str = Field(min_length=8, max_length=160)
    reason: str = Field(min_length=8, max_length=500)


class DataResetParticipantView(ApiModel):
    device_id: str
    name: str
    state: DataResetParticipantState
    reset_generation: int = Field(gt=0)
    reset_boundary: ResetBoundary
    new_sequence_floor: SignedSequence | None = None
    new_next_sequence: PositiveSignedSequence | None = None
    firmware_version: str | None = None
    failure_code: str | None = None
    failure_summary: str | None = None
    last_attempt_at: datetime | None = None
    prepared_at: datetime | None = None
    committed_at: datetime | None = None
    verified_at: datetime | None = None


class DataResetOperationView(ApiModel):
    model_config = ConfigDict(from_attributes=True, extra="allow")

    operation_id: str
    plan_id: str
    site_id: str
    state: DataResetOperationState
    stage: str | dict[str, Any]
    revision: int = Field(gt=0)
    reset_generation: int = Field(gt=0)
    reset_timestamp: datetime
    backup: dict[str, Any]
    recoverability: str | dict[str, Any]
    participants: list[DataResetParticipantView]
    started_at: datetime
    central_commit_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = None
    failure_summary: str | None = None
    final_evidence: dict[str, Any] = Field(default_factory=dict)


class SensorDataResetPrepareRequest(DeviceProtocolModel):
    protocol: Literal["data-reset/1.0.0"] = "data-reset/1.0.0"
    operation_id: str
    device_id: str
    target_generation: int = Field(gt=0)
    reset_timestamp: datetime
    plan_revision: int = Field(gt=0)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    categories: list[Literal["measurement_history"]] = Field(min_length=1, max_length=1)
    expected_boundary: ResetBoundary
    server_highest_contiguous: ResetBoundary
    server_maximum_seen: ResetBoundary
    expected_firmware_version: str = Field(min_length=1, max_length=32)
    expected_build_hash: str | None = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expected_card_generation: str | None = Field(max_length=20, pattern=r"^[1-9][0-9]*$")

    _aware_reset = field_validator("reset_timestamp")(require_aware)


class SensorDataResetCommitRequest(DeviceProtocolModel):
    protocol: Literal["data-reset/1.0.0"] = "data-reset/1.0.0"
    operation_id: str
    device_id: str
    target_generation: int = Field(gt=0)
    plan_revision: int = Field(gt=0)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_boundary: ResetBoundary
    prepared_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


_DATA_RESET_SAFE_RECEIPT_FIELDS = frozenset(
    {
        "protocol",
        "operation_id",
        "device_id",
        "target_generation",
        "plan_revision",
        "plan_digest",
        "state",
        "checkpoint",
        "firmware_version",
        "firmware_build_hash",
        "boot_id",
        "card_generation",
        "sd_status",
        "reset_boundary",
        "sequence_floor",
        "next_sequence",
        "server_ack_sequence",
        "server_maximum_seen",
        "newest_stored_sequence",
        "newest_syncable_sequence",
        "local_records_before",
        "local_records_after",
        "backlog_before",
        "backlog_after",
        "prepare_drain_records_added",
        "prepare_drain_first_sequence",
        "prepare_drain_last_sequence",
        "prepare_drain_syncable_records_added",
        "measurement_pause_started_utc_ms",
        "measurement_pause_ended_utc_ms",
        "measurement_pause_evidenced",
        "records_deleted",
        "indexes_rebuilt",
        "queues_cleared",
        "exports_cleared",
        "pzem_baseline_captured",
        "prepared_pzem_energy_wh",
        "commit_pzem_energy_wh",
        "verified_pzem_energy_wh",
        "prepared_receipt_digest",
        "software_energy_baseline_before_wh",
        "configuration_preservation_digest_before",
        "configuration_preservation_digest_after",
        "configuration_preserved",
    }
)


class SensorDataResetResponse(ApiModel):
    """Authenticated sensor status; persisted server audit copies redact energy values."""

    protocol: Literal["data-reset/1.0.0"] = "data-reset/1.0.0"
    operation_id: str
    device_id: str
    target_generation: int = Field(gt=0)
    state: Literal[
        "preparing",
        "prepared",
        "commit_authorized",
        "sequence_advanced",
        "cursors_advanced",
        "readings_cleared",
        "baseline_installed",
        "verified",
        "completed",
        "attention_required",
        "cancelled",
    ]
    checkpoint: Literal[
        "preparing",
        "prepared",
        "commit_authorized",
        "sequence_advanced",
        "cursors_advanced",
        "readings_cleared",
        "baseline_installed",
        "verified",
        "completed",
        "attention_required",
        "cancelled",
    ]
    failure_code: str | None = Field(default=None, max_length=80, pattern=FAILURE_CODE_PATTERN)
    prepared_receipt: str | None = Field(default=None, max_length=16_384)
    prepared_receipt_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    commit_receipt: str | None = Field(default=None, max_length=16_384)
    commit_receipt_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    configuration_preservation_digest_before: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    configuration_preservation_digest_after: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null_receipt_evidence(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for field in (
                "prepared_receipt",
                "prepared_receipt_digest",
                "commit_receipt",
                "commit_receipt_digest",
                "configuration_preservation_digest_before",
                "configuration_preservation_digest_after",
            ):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} must be omitted instead of null")
        return value

    @model_validator(mode="after")
    def validate_redacted_receipts(self) -> SensorDataResetResponse:
        pairs = (
            (self.prepared_receipt, self.prepared_receipt_digest),
            (self.commit_receipt, self.commit_receipt_digest),
        )
        if any((receipt is None) != (digest is None) for receipt, digest in pairs):
            raise ValueError("a canonical reset receipt and its digest must appear together")
        for receipt, _digest in pairs:
            if receipt is None:
                continue
            try:
                parsed = json.loads(receipt)
            except json.JSONDecodeError as exc:
                raise ValueError("reset receipt must be canonical JSON text") from exc
            if not isinstance(parsed, dict) or not set(parsed) <= _DATA_RESET_SAFE_RECEIPT_FIELDS:
                raise ValueError("reset receipt contains a non-redacted field")
            canonical = json.dumps(
                parsed,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            if receipt != canonical:
                raise ValueError("reset receipt is not canonical JSON")
            if (
                parsed.get("protocol") != self.protocol
                or parsed.get("operation_id") != self.operation_id
                or parsed.get("device_id") != self.device_id
                or parsed.get("target_generation") != self.target_generation
            ):
                raise ValueError("reset receipt identity does not match the response")
        return self


class SensorDataResetCancelRequest(DeviceProtocolModel):
    protocol: Literal["data-reset/1.0.0"] = "data-reset/1.0.0"
    operation_id: str
    device_id: str
    target_generation: int = Field(gt=0)
    plan_revision: int = Field(gt=0)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class DeviceEventInput(DeviceProtocolModel):
    event_id: str = Field(min_length=1, max_length=80)
    occurred_at: datetime
    category: Literal[
        "boot",
        "pzem",
        "ct_limit",
        "sd",
        "network",
        "time",
        "configuration",
        "ota",
        "security",
    ]
    severity: Literal["info", "warning", "error", "critical"]
    evidence: dict[str, Any] = Field(default_factory=dict)

    _aware = field_validator("occurred_at")(require_aware)

    @field_validator("evidence")
    @classmethod
    def valid_event_sequence_evidence(cls, value: dict[str, Any]) -> dict[str, Any]:
        event_sequence = value.get("event_sequence")
        if event_sequence is not None and (
            isinstance(event_sequence, bool)
            or not isinstance(event_sequence, int)
            or not 1 <= event_sequence <= SIGNED_BIGINT_MAX
        ):
            raise ValueError("event_sequence must fit signed BIGINT storage")
        return value


class DeviceEventBatch(DeviceProtocolModel):
    protocol_version: str
    device_id: str
    data_generation: int = Field(default=0, ge=0)
    first_stored_event_sequence: PositiveSignedSequence | None = None
    events: list[DeviceEventInput] = Field(min_length=1, max_length=500)


class ConfigReport(DeviceProtocolModel):
    protocol_version: str
    device_id: str
    version: int = Field(gt=0)
    status: Literal["applied", "partially_applied", "rejected", "rolled_back"]
    applied: list[str] = Field(default_factory=list)
    rejected: dict[str, str] = Field(default_factory=dict)


class DeviceConfigCreate(ApiModel):
    settings: dict[str, Any]
    acknowledge_ct_rating_change: bool = False
    network_rollback_seconds: int = Field(default=300, ge=60, le=3600)


class StoragePolicyWrite(ApiModel):
    retention_mode: Literal["disabled", "strict_age", "continuous_protected"] = (
        "continuous_protected"
    )
    retention_days: int = Field(default=730, ge=1, le=3650)
    minimum_local_history_days: int = Field(default=30, ge=1, le=3650)
    storage_notice_percent: int = Field(default=20, ge=2, le=50)
    storage_warning_percent: int = Field(default=10, ge=2, le=49)
    storage_critical_percent: int = Field(default=5, ge=2, le=48)
    storage_emergency_percent: int = Field(default=2, ge=1, le=47)
    storage_emergency_reserve_bytes: int = Field(
        default=512 * 1024 * 1024, ge=64 * 1024 * 1024, le=64 * 1024**3
    )
    storage_cleanup_target_percent: int = Field(default=10, ge=2, le=50)
    storage_cleanup_target_bytes: int = Field(
        default=1024 * 1024 * 1024, ge=64 * 1024 * 1024, le=64 * 1024**3
    )
    event_retention_days: int = Field(default=730, ge=1, le=3650)
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def validate_storage_thresholds(self) -> StoragePolicyWrite:
        if not (
            self.storage_notice_percent
            > self.storage_warning_percent
            > self.storage_critical_percent
            > self.storage_emergency_percent
        ):
            raise ValueError("storage thresholds must decrease from notice to emergency")
        if self.minimum_local_history_days > self.retention_days:
            raise ValueError("minimum local history cannot exceed retention")
        if not (
            self.storage_warning_percent
            <= self.storage_cleanup_target_percent
            <= self.storage_notice_percent
        ):
            raise ValueError("cleanup target percent must be between warning and notice")
        if self.storage_cleanup_target_bytes < self.storage_emergency_reserve_bytes:
            raise ValueError("cleanup target bytes must cover the emergency reserve")
        return self


class StorageActionRequest(ApiModel):
    reason: str = Field(min_length=8, max_length=500)
    confirmation: str | None = Field(default=None, max_length=200)


class RatePeriodInput(ApiModel):
    season: str
    day_type: Literal["weekday", "weekend", "holiday"]
    start_minute: int = Field(ge=0, lt=1440)
    end_minute: int = Field(gt=0, le=1440)
    bucket: str = Field(min_length=1, max_length=40)
    price_per_kwh: Decimal = Field(ge=0)


class RatePreviewRequest(ApiModel):
    plan_code: str
    interval_start: datetime
    interval_end: datetime
    energy_kwh: Decimal = Field(gt=0)
    cost_scope: Literal["energy_only", "allocated_account", "full_account"] = "energy_only"
    baseline_allocation_kwh: Decimal | None = Field(default=None, ge=0)
    billing_days: int = Field(default=1, ge=1, le=366)
    cca_adjustment_per_kwh: Decimal = Decimal("0")
    other_adjustment: Decimal = Decimal("0")

    _aware_start = field_validator("interval_start")(require_aware)
    _aware_end = field_validator("interval_end")(require_aware)


class CostComponent(ApiModel):
    name: str
    amount: Decimal


class RatePreviewResponse(ApiModel):
    plan_code: str
    rate_version: str
    timezone: str
    energy_by_bucket_kwh: dict[str, Decimal]
    components: list[CostComponent]
    unrounded_total: Decimal
    display_total: Decimal
    coverage_percent: Decimal
    disclosure: str


class CostRecalculationRequest(ApiModel):
    utility_account_id: str
    aggregate_set_id: str
    rate_version_id: str | None = None
    input_start: datetime
    input_end: datetime

    _aware_start = field_validator("input_start")(require_aware)
    _aware_end = field_validator("input_end")(require_aware)

    @model_validator(mode="after")
    def valid_interval(self) -> CostRecalculationRequest:
        if self.input_end <= self.input_start:
            raise ValueError("input_end must be later than input_start")
        return self


class AlertAcknowledge(ApiModel):
    note: str = Field(default="", max_length=500)


class NotificationMetric(ApiModel):
    label: str
    value: str
    unit: str | None = None
    recorded_at: datetime | None = None


class NotificationExpected(ApiModel):
    label: str
    operator: str | None = None
    value: str
    unit: str | None = None


class NotificationEvidence(ApiModel):
    label: str
    value: str
    status: Literal["normal", "warning", "error"] | None = None


class NotificationResource(ApiModel):
    type: Literal[
        "sensor",
        "home",
        "server",
        "backup",
        "rate_source",
        "notification_channel",
        "firmware",
        "billing",
        "storage",
    ]
    id: str | None = None
    name: str


class NotificationCause(ApiModel):
    code: str
    explanation: str


class NotificationAction(ApiModel):
    label: str
    target: str
    required_permissions: list[str] = Field(default_factory=list)


class NotificationRemediation(ApiModel):
    summary: str
    steps: list[str] = Field(default_factory=list)
    automatic_recovery: str | None = None
    action: NotificationAction | None = None


class NotificationAcknowledgement(ApiModel):
    acknowledged_at: datetime
    acknowledged_by: str
    note: str | None = None


class NotificationSilenceView(ApiModel):
    silenced_until: datetime
    silenced_by: str
    note: str | None = None


class NotificationDelivery(ApiModel):
    attempted: bool
    channel_name: str | None = None
    last_attempt_at: datetime | None = None
    last_outcome: str | None = None
    retry_at: datetime | None = None
    safe_error_code: str | None = None
    safe_error_summary: str | None = None


class NotificationSuppressionView(ApiModel):
    dismissible: bool
    permanently_suppressible: bool
    suppression_key: str | None = None
    currently_suppressed: bool = False
    allowed_scopes: list[Literal["user", "home"]] = Field(default_factory=list)


class NotificationView(ApiModel):
    id: str
    code: str
    kind: Literal["operational_alert", "setup_recommendation", "delivery_issue"]
    category: str
    severity: Literal["info", "warning", "error", "critical"]
    state: Literal["open", "acknowledged", "silenced", "resolved", "dismissed", "suppressed"]
    title: str
    summary: str
    affected_resource: NotificationResource | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None = None
    occurrence_count: int = Field(ge=1)
    duration_seconds: int | None = Field(default=None, ge=0)
    observed: NotificationMetric | None = None
    expected: NotificationExpected | None = None
    cause: NotificationCause | None = None
    evidence: list[NotificationEvidence] = Field(default_factory=list)
    impact: str
    remediation: NotificationRemediation
    acknowledgement: NotificationAcknowledgement | None = None
    silence: NotificationSilenceView | None = None
    delivery: NotificationDelivery | None = None
    suppression: NotificationSuppressionView


class NotificationPage(ApiModel):
    items: list[NotificationView]
    page: int
    page_size: int
    total: int


class NotificationSuppressRequest(ApiModel):
    scope: Literal["user", "home"]
    reason: str = Field(default="", max_length=500)
    confirmed: bool
    expected_revision: int | None = Field(default=None, ge=1)


class NotificationSuppressionItem(ApiModel):
    id: str
    suppression_key: str
    category: str
    scope_type: Literal["user", "home"]
    scope_name: str
    created_by: str
    created_at: datetime
    reason: str | None = None
    source_notification_id: str
    active: bool
    restored_by: str | None = None
    restored_at: datetime | None = None
    revision: int


class NotificationHistoryItem(ApiModel):
    id: str
    notification_id: str
    event_type: str
    occurred_at: datetime
    actor_name: str | None = None
    site_id: str | None = None
    category: str
    severity: str
    resource_type: str | None = None
    resource_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class NotificationHistoryPage(ApiModel):
    items: list[NotificationHistoryItem]
    page: int
    page_size: int
    total: int


class DashboardAppearanceView(ApiModel):
    chart_power_color: str = Field(pattern=r"^#[0-9A-F]{6}$")
    chart_energy_color: str = Field(pattern=r"^#[0-9A-F]{6}$")
    chart_cost_color: str = Field(pattern=r"^#[0-9A-F]{6}$")
    revision: int = Field(ge=1)
    updated_at: datetime


class DashboardAppearanceWrite(ApiModel):
    chart_power_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    chart_energy_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    chart_cost_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    expected_revision: int = Field(ge=1)

    @field_validator("chart_power_color", "chart_energy_color", "chart_cost_color")
    @classmethod
    def normalize_chart_color(cls, value: str) -> str:
        return value.upper()


class UserCreate(ApiModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=14, max_length=1024)
    roles: list[Literal["admin", "operator", "rate-manager", "viewer"]] = Field(min_length=1)
    confirm_high_risk: bool = False


class ReauthenticateRequest(ApiModel):
    password: str | None = Field(default=None, max_length=1024)
    totp_code: str | None = Field(default=None, pattern=r"^\d{6}$")

    @model_validator(mode="after")
    def require_confirmation_method(self) -> ReauthenticateRequest:
        if not self.password and not self.totp_code:
            raise ValueError("enter the current password or a six-digit MFA code")
        return self


class UserAccessUpdate(ApiModel):
    role_ids: list[str] = Field(min_length=1, max_length=20)
    all_sites: bool
    site_ids: list[str] = Field(default_factory=list, max_length=500)
    expected_revision: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=500)
    confirm_high_risk: bool = False


class UserStatusChange(ApiModel):
    reason: str | None = Field(default=None, max_length=500)
    confirm_high_risk: bool = False
    expected_revision: int | None = Field(default=None, ge=1)


class UserRemovalRequest(ApiModel):
    reason: str = Field(min_length=3, max_length=500)
    confirmation: str = Field(min_length=1, max_length=320)
    expected_revision: int = Field(ge=1)
    confirm_high_risk: bool = False


class UserRestoreRequest(ApiModel):
    reason: str = Field(min_length=3, max_length=500)
    expected_revision: int = Field(ge=1)
    confirm_high_risk: bool = False


class RoleWrite(ApiModel):
    display_name: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=3, max_length=255)
    permissions: list[str] = Field(min_length=1, max_length=100)
    expected_revision: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=500)
    confirm_high_risk: bool = False


class InterfaceTextDraftWrite(ApiModel):
    base_revision: int = Field(ge=0)
    draft_revision: int | None = Field(default=None, ge=1)
    values: dict[str, str]
    reason: str | None = Field(default=None, max_length=500)


class InterfaceTextPublish(ApiModel):
    base_revision: int = Field(ge=0)
    draft_revision: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=500)
    confirm: bool = False


class InterfaceTextRestore(ApiModel):
    base_revision: int = Field(ge=0)
    reason: str | None = Field(default=None, max_length=500)
    confirm: bool = False


class InterfaceTextReset(ApiModel):
    base_revision: int = Field(ge=0)
    key: str | None = None
    section: str | None = None
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def exactly_one_scope(self) -> InterfaceTextReset:
        if bool(self.key) == bool(self.section):
            raise ValueError("provide exactly one of key or section")
        return self


class InterfaceTextImport(ApiModel):
    schema_version: Literal["power-monitor-interface-text/1.0"]
    base_revision: int = Field(ge=0)
    values: dict[str, str]
    reason: str | None = Field(default=None, max_length=500)


class StatusLayoutDraftWrite(ApiModel):
    base_revision: int = Field(ge=0)
    draft_revision: int | None = Field(default=None, ge=0)
    configuration: dict[str, Any]
    reason: str | None = Field(default=None, max_length=500)


class StatusLayoutValidate(ApiModel):
    configuration: dict[str, Any] | None = None


class StatusLayoutPreview(ApiModel):
    configuration: dict[str, Any] | None = None
    page: str = Field(default="overview", max_length=64)
    role: str = Field(default="admin", max_length=32)
    breakpoint: Literal["desktop", "tablet", "mobile"] = "desktop"
    scenario: Literal[
        "all_defaults",
        "one_disabled",
        "two_disabled",
        "one_only",
        "empty_zone",
        "many",
        "warning",
        "critical",
        "long_label",
    ] = "all_defaults"


class StatusLayoutPublish(ApiModel):
    base_revision: int = Field(ge=0)
    draft_revision: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=500)
    confirm: bool = False
    confirm_critical: bool = False


class StatusLayoutReset(ApiModel):
    base_revision: int = Field(ge=0)
    draft_revision: int | None = Field(default=None, ge=0)
    scope: Literal["indicator", "zone", "page", "all"]
    indicator_key: str | None = Field(default=None, max_length=120)
    zone: str | None = Field(default=None, max_length=64)
    page: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=500)


class StatusLayoutRestore(ApiModel):
    base_revision: int = Field(ge=0)
    reason: str | None = Field(default=None, max_length=500)
    confirm: bool = False
    confirm_critical: bool = False


class StatusLayoutImport(ApiModel):
    schema_version: Literal["power-monitor-status-layout/1.0"]
    registry_version: str = Field(max_length=64)
    base_revision: int = Field(ge=0)
    configuration: dict[str, Any]
    reason: str | None = Field(default=None, max_length=500)


class PasswordReset(ApiModel):
    new_password: str = Field(min_length=14, max_length=1024)


class CredentialRotationRequest(ApiModel):
    overlap_seconds: int = Field(default=3600, ge=0, le=604800)


class DeviceUnclaimRequest(ApiModel):
    confirmation: str = Field(min_length=1, max_length=160)
    reason: (
        Literal[
            "replaced",
            "moved",
            "failed_hardware",
            "duplicate_enrollment",
            "testing_device",
            "other",
        ]
        | None
    ) = None


class BackupRequestCreate(ApiModel):
    operation: Literal["create", "restore_preflight"]
    backup_id: str | None = Field(default=None, min_length=36, max_length=36)
    confirmation: str | None = Field(default=None, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=160)

    @model_validator(mode="after")
    def restore_fields(self) -> BackupRequestCreate:
        if self.operation == "restore_preflight":
            if not self.backup_id:
                raise ValueError("backup_id is required for restore preflight")
            if self.confirmation != "VERIFY RESTORE":
                raise ValueError("restore preflight requires the exact confirmation")
        elif self.backup_id is not None or self.confirmation is not None:
            raise ValueError("create requests do not accept restore fields")
        return self


class BackupVerifyRequest(ApiModel):
    idempotency_key: str = Field(min_length=8, max_length=160)


class BackupDeleteRequest(ApiModel):
    confirmation: Literal["DELETE"]
    backup_id_confirmation: str = Field(min_length=8, max_length=36)
    reason: str = Field(min_length=3, max_length=500)


class BackupReplaceAllRequest(ApiModel):
    confirmation: Literal["REPLACE ALL BACKUPS"]
    idempotency_key: str = Field(min_length=8, max_length=160)


class BackupView(ApiModel):
    id: str
    started_at: datetime
    completed_at: datetime | None
    status: str
    manifest_hash: str | None
    verified_at: datetime | None
    verification_details: dict[str, Any]
    size_bytes: int | None
    encrypted: bool
    verification_started_at: datetime | None
    verification_completed_at: datetime | None
    verification_attempt_count: int
    failed_stage: str | None
    safe_error_code: str | None
    safe_error_summary: str | None
    exit_code: int | None
    deleted_at: datetime | None
    deletion_reason: str | None
    artifact_removal_result: str | None
    pre_deletion_status: str | None
    replaced_by_backup_id: str | None


class LogExportCreate(ApiModel):
    start_date: date | None = None
    end_date: date | None = None
    services: list[str] | None = Field(default=None, min_length=1, max_length=6)


class FirmwareDeploymentCreate(ApiModel):
    firmware_release_id: str
    device_ids: list[str] = Field(min_length=1, max_length=1000)
    scheduled_at: datetime
    expires_at: datetime | None = None
    allow_downgrade: bool = False
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=128)
    canary_first: bool = True
    maximum_concurrency: Literal[1] = 1

    _aware = field_validator("scheduled_at")(require_aware)

    @model_validator(mode="after")
    def valid_window(self) -> FirmwareDeploymentCreate:
        if len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("device_ids must not contain duplicates")
        if len(self.device_ids) > 1 and not self.canary_first:
            raise ValueError("multi-sensor deployments require canary_first")
        if self.expires_at is not None:
            require_aware(self.expires_at)
            if self.expires_at <= self.scheduled_at:
                raise ValueError("expires_at must be later than scheduled_at")
        return self


class FirmwareDeploymentReport(DeviceProtocolModel):
    device_id: str
    deployment_id: str
    release_id: str
    attempt: int = Field(ge=1)
    state: Literal[
        "manifest_authenticated",
        "waiting_for_schedule",
        "download_started",
        "downloading",
        "binary_verified",
        "partition_written",
        "rebooting",
        "post_boot_validation",
        "validated",
        "failed",
        "rollback_detected",
        "rolled_back",
    ]
    current_firmware_version: str = Field(min_length=1, max_length=80)
    current_build_hash: str = Field(min_length=1, max_length=128)
    target_version: str = Field(min_length=1, max_length=80)
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes_received: int = Field(ge=0)
    image_size: int = Field(gt=0)
    progress: int = Field(ge=0, le=100)
    boot_id: str = Field(min_length=1, max_length=80)
    # Monotonic, deployment-attempt-scoped retained evidence. Legacy firmware
    # may omit this additive field; current firmware persists it alongside each
    # OTA state transition so delayed reports can be ordered against signed
    # heartbeat evidence from the same boot.
    evidence_sequence: int | None = Field(default=None, ge=0, le=2**64 - 1)
    failure_code: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_]{0,79}$")
    failure_summary: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def valid_progress(self) -> FirmwareDeploymentReport:
        if self.bytes_received > self.image_size:
            raise ValueError("bytes_received exceeds image_size")
        if self.state in {
            "binary_verified",
            "partition_written",
            "rebooting",
            "post_boot_validation",
            "validated",
        } and (self.bytes_received != self.image_size or self.progress != 100):
            raise ValueError("post-download milestones require the complete image")
        if self.state == "failed" and not self.failure_code:
            raise ValueError("failed report requires failure_code")
        if self.state not in {"failed", "rollback_detected", "rolled_back"} and (
            self.failure_code or self.failure_summary
        ):
            raise ValueError("failure evidence is accepted only for failure or rollback reports")
        return self


class FirmwareVerificationCheck(ApiModel):
    key: str
    label: str
    status: Literal["pending", "passed", "failed", "unavailable"]
    detail: str
    observed_at: datetime | None = None


class FirmwareVerificationBlocker(ApiModel):
    code: str
    state: str
    title: str
    detail: str
    action: str


class FirmwareVerificationStatus(ApiModel):
    checks: list[FirmwareVerificationCheck]
    blocker: FirmwareVerificationBlocker | None = None
    target_version_expected: str | None = None
    target_version_observed: str | None = None
    target_build_hash_expected: str | None = None
    target_build_hash_observed: str | None = None
    target_boot_id_observed: str | None = None
    previous_boot_stage: str | None = None
    previous_reset_reason: str | None = None
    rollback_state: str
    exact_failure_code: str | None = None
    blocking_critical_alert_count: int = Field(ge=0)
    verification_heartbeat_count: int = Field(ge=0)
    verification_heartbeat_required: int = Field(ge=1)
    last_sensor_activity_at: datetime | None = None
    last_report_at: datetime | None = None
    stabilization_elapsed_seconds: int = Field(ge=0)
    stabilization_required_seconds: int = Field(ge=0)


class FirmwareReleaseCompatibilityView(ApiModel):
    verified_image: bool
    device_selection_required: bool


class FirmwareReleaseView(ApiModel):
    id: str
    version: str
    project_name: str | None
    hardware_target: str
    protocol_min: str
    protocol_max: str
    size_bytes: int
    sha256: str
    build_hash: str | None
    build_timestamp: datetime | None
    trust_mode: str
    verification_status: str
    verification_evidence: dict[str, Any]
    verified_at: datetime
    active: bool
    artifact_download_path: str
    compatibility: FirmwareReleaseCompatibilityView
    duplicate: bool


class FirmwareReleaseDeviceCompatibilityView(ApiModel):
    ready: bool
    reasons: list[str]
    ota: FirmwareOtaReadinessView
    current_version: str | None
    current_build_hash: str | None
    target_version: str
    target_build_hash: str | None


class FirmwareBootstrapView(ApiModel):
    required: bool
    firmware_filename: str
    sha256: str
    expected_version: str
    expected_build_hash: str | None
    artifact_download_path: str
    usb_command: str
    preserves: list[str]


class FirmwareReadinessView(ApiModel):
    device_id: str
    current_firmware_version: str | None
    current_firmware_build_hash: str | None
    firmware_ota: FirmwareOtaReadinessView
    release_id: str | None = None
    compatibility: FirmwareReleaseDeviceCompatibilityView | None = None
    bootstrap: FirmwareBootstrapView | None = None


class FirmwareDeploymentView(ApiModel):
    id: str
    firmware_release_id: str
    device_id: str
    state: str
    status: str
    revision: int
    attempt: int
    progress: int
    bytes_received: int
    progress_mode: Literal["determinate", "indeterminate"]
    display_state: str
    scheduled_at: datetime
    expires_at: datetime | None
    allow_downgrade: bool
    rollout_group_id: str | None
    rollout_order: int
    promoted_at: datetime | None
    downloaded_at: datetime | None
    installed_at: datetime | None
    validated_at: datetime | None
    last_report_at: datetime | None
    failure_code: str | None
    failure_summary: str | None
    validated_version: str | None
    validated_build_hash: str | None
    rollback_at: datetime | None
    rollback_version: str | None
    rollback_build_hash: str | None
    verification_heartbeats: int
    reading_confirmed_at: datetime | None
    state_changed_at: datetime
    terminal_at: datetime | None
    created_at: datetime
    target_version: str | None
    target_sha256: str | None
    target_build_hash: str | None
    source_version: str | None
    source_build_hash: str | None
    source_boot_id: str | None
    interruption_evidence: dict[str, Any]
    verification: FirmwareVerificationStatus


class FirmwareDeploymentCreateResponse(ApiModel):
    deployment_ids: list[str]
    scheduled: int
    deployments: list[FirmwareDeploymentView]


class FirmwareDeploymentReportResponse(ApiModel):
    recorded: bool
    duplicate: bool
    state: str
    revision: int
    attempt: int


class FirmwareCanaryPromotionResponse(ApiModel):
    promoted: bool
    rollout_complete: bool
    deployment: FirmwareDeploymentView | None = None


class OtaManifestUnavailable(ApiModel):
    available: Literal[False]
    protocol_version: Literal["pm-protocol/1.0.0"]


class OtaManifestV2(ApiModel):
    schema_version: Literal["pm-ota-manifest/2"]
    protocol_version: Literal["pm-protocol/1.0.0"]
    deployment_id: str
    release_id: str
    device_id: str
    version: str
    project_name: Literal["power-monitor-sensor"]
    hardware_target: Literal["esp32-s3"]
    protocol_min: Literal["pm-protocol/1.0.0"]
    protocol_max: Literal["pm-protocol/1.0.0"]
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    not_before: str
    expires_at: str
    allow_downgrade: bool
    attempt: int = Field(ge=1)
    hmac_algorithm: Literal["HMAC-SHA256"]
    hmac_key_context: Literal["pm-ota-manifest-v2/server-to-device"]
    download_path: str
    manifest_hmac: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class FirmwareManifest(ApiModel):
    version: str = Field(min_length=1, max_length=80)
    channel: Literal["development", "canary", "stable"]
    hardware_target: str = Field(min_length=1, max_length=120)
    protocol_min: str
    protocol_max: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: str = Field(min_length=32)
    signing_key_id: str = Field(min_length=1, max_length=128)
    release_notes: str = Field(max_length=20000)


class HistoryPoint(ApiModel):
    timestamp: datetime
    power_w: Decimal | None
    energy_wh: Decimal | None
    voltage_v: Decimal | None
    current_a: Decimal | None
    power_factor: Decimal | None
    frequency_hz: Decimal | None
    quality_flags: list[str]


class HistoryResponse(ApiModel):
    points: list[HistoryPoint]
    missing_ranges: list[dict[str, Any]]
    coverage_percent: Decimal
    next_cursor: str | None = None


class HistoryScopeQuery(ApiModel):
    type: Literal["device", "devices", "circuit", "site", "aggregate_set"]
    device_id: str | None = None
    device_ids: list[str] = Field(default_factory=list, max_length=32)
    circuit_id: str | None = None
    site_id: str | None = None
    aggregate_set_id: str | None = None

    @model_validator(mode="after")
    def exactly_one_scope(self) -> HistoryScopeQuery:
        identifiers = {
            "device": bool(self.device_id),
            "devices": len(self.device_ids) >= 2,
            "circuit": bool(self.circuit_id),
            "site": bool(self.site_id),
            "aggregate_set": bool(self.aggregate_set_id),
        }
        if not identifiers[self.type]:
            raise ValueError(f"{self.type} scope is missing its identifier")
        supplied = (
            int(bool(self.device_id))
            + int(bool(self.device_ids))
            + int(bool(self.circuit_id))
            + int(bool(self.site_id))
            + int(bool(self.aggregate_set_id))
        )
        if supplied != 1:
            raise ValueError("provide identifiers for exactly one history scope")
        if self.type == "devices" and len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("device_ids cannot contain duplicates")
        return self


HistoryMetric = Literal[
    "power_w",
    "energy_kwh",
    "voltage_v",
    "current_a",
    "power_factor",
    "frequency_hz",
    "energy_cost",
    "usage_cost",
]


def default_history_metrics() -> list[HistoryMetric]:
    return ["power_w"]


class HistoryQueryRequest(ApiModel):
    scope: HistoryScopeQuery
    display_mode: Literal["combined", "individual", "combined_plus_individual"] = "combined"
    metrics: list[HistoryMetric] = Field(
        default_factory=default_history_metrics, min_length=1, max_length=8
    )
    start_utc: datetime
    end_utc: datetime
    bucket: Literal["auto", "raw", "5m", "15m", "1h", "1d"] = "auto"
    timezone: str | None = Field(default=None, max_length=64)
    strict_coverage: bool = False
    selection_start_utc: datetime | None = None
    selection_end_utc: datetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=250, ge=1, le=500)
    continuation_token: str | None = Field(default=None, max_length=32_768)

    @model_validator(mode="after")
    def valid_range(self) -> HistoryQueryRequest:
        for value in (
            self.start_utc,
            self.end_utc,
            self.selection_start_utc,
            self.selection_end_utc,
        ):
            if value is not None:
                require_aware(value)
        if self.end_utc <= self.start_utc:
            raise ValueError("end_utc must be after start_utc")
        if (self.selection_start_utc is None) != (self.selection_end_utc is None):
            raise ValueError("selection_start_utc and selection_end_utc must be provided together")
        if (
            self.selection_start_utc
            and self.selection_end_utc
            and not (
                self.start_utc <= self.selection_start_utc < self.selection_end_utc <= self.end_utc
            )
        ):
            raise ValueError("selected range must be inside the requested history range")
        self.metrics = list(dict.fromkeys(self.metrics))
        return self


class HistoryRateContribution(ApiModel):
    utility_account_id: str
    rate_plan_id: str
    rate_plan_name: str
    rate_version_id: str
    rate_version: int
    rate_effective_from: date
    tou_period: str
    tier_id: str | None = None
    tier_name: str | None = None
    cumulative_start_kwh: Decimal | None = None
    cumulative_end_kwh: Decimal | None = None
    recalculation_version: int | None = None
    usage_authority_type: str | None = None
    energy_kwh: Decimal
    rate_per_kwh: Decimal
    energy_cost: Decimal


class HistoryBucket(ApiModel):
    interval_start_utc: datetime
    interval_end_utc: datetime
    local_start: str
    local_end: str
    utc_offset: str
    series_id: str
    series_name: str
    device_id: str | None = None
    included_sensor_count: int
    contributing_sensor_count: int
    energy_kwh: Decimal | None
    average_power_w: Decimal | None
    peak_power_w: Decimal | None
    voltage_min_v: Decimal | None
    voltage_avg_v: Decimal | None
    voltage_max_v: Decimal | None
    current_a: Decimal | None
    power_factor: Decimal | None
    frequency_hz: Decimal | None
    tou_period: str | None
    rate_per_kwh: Decimal | None
    energy_cost: Decimal | None
    rate_plan_name: str | None
    rate_version_id: str | None
    rate_effective_from: date | None
    mixed_rates: bool = False
    coverage_percent: Decimal
    missing_sensor_ids: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    rate_contributions: list[HistoryRateContribution] = Field(default_factory=list)


class HistoryIndividualSeries(ApiModel):
    device_id: str
    name: str
    circuit_name: str | None = None
    status: str
    points: list[HistoryBucket]


class HistoryRangeSummary(ApiModel):
    start_utc: datetime
    end_utc: datetime
    energy_kwh: Decimal | None
    energy_cost: Decimal | None
    blended_rate_per_kwh: Decimal | None
    average_power_w: Decimal | None
    peak_power_w: Decimal | None
    highest_cost_bucket_start: datetime | None
    highest_cost_bucket_value: Decimal | None
    highest_usage_bucket_start: datetime | None
    highest_usage_bucket_kwh: Decimal | None
    coverage_percent: Decimal
    contributing_sensor_count: int
    tou_breakdown: dict[str, dict[str, Decimal]] = Field(default_factory=dict)


class HistoryResolvedScope(ApiModel):
    type: Literal["device", "devices", "circuit", "site", "aggregate_set"]
    display_name: str
    site_id: str
    site_name: str
    timezone: str
    included_device_ids: list[str]
    included_device_names: list[str]
    excluded_device_ids: list[str] = Field(default_factory=list)
    mixed_rates: bool = False


class HistoryQueryResponse(ApiModel):
    scope: HistoryResolvedScope
    display_mode: Literal["combined", "individual", "combined_plus_individual"]
    metrics: list[str]
    bucket: str
    summary: HistoryRangeSummary
    selected_summary: HistoryRangeSummary | None = None
    combined: list[HistoryBucket] = Field(default_factory=list)
    individual: list[HistoryIndividualSeries] = Field(default_factory=list)
    rate_versions_used: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    total_buckets: int
    page: int
    page_size: int
    next_page: int | None = None
    next_continuation_token: str | None = None


class FleetSummary(ApiModel):
    site_id: str | None
    current_load_w: Decimal | None
    energy_today_kwh: Decimal
    estimated_cost_today: Decimal
    billing_cycle_energy_kwh: Decimal
    estimated_billing_cycle_cost: Decimal
    online_devices: int
    synchronized_devices: int
    total_devices: int
    active_alerts: int
    current_tou_bucket: str | None
    current_rate_plan: str | None = None
    current_rate_version: int | None = None
    current_rate_price_per_kwh: Decimal | None = None
    rate_configured: bool = False
    recent_peak_w: Decimal | None
    has_live_data: bool
    has_energy_data: bool
    has_cost_data: bool
    reporting_devices: int
    latest_data_at: datetime | None
    latest_measurement_at: datetime | None
    latest_received_at: datetime | None
    latest_heartbeat_at: datetime | None
    server_now: datetime
    coverage_percent: Decimal | None
    disclosure: str


class HealthRemediation(ApiModel):
    label: str
    route: str | None = None
    action: str | None = None


class HealthComponent(ApiModel):
    key: Literal[
        "api",
        "database",
        "worker",
        "storage",
        "backups",
        "live_data",
        "rate_engine",
    ]
    label: str
    status: Literal["healthy", "degraded", "unhealthy", "unknown"]
    summary: str
    checked_at: datetime
    last_success_at: datetime | None = None
    latency_ms: Decimal | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    remediation: HealthRemediation | None = None
    can_retry: bool = True


class HealthEvent(ApiModel):
    occurred_at: datetime
    component: str
    status: Literal["healthy", "degraded", "unhealthy", "unknown"]
    summary: str


class SystemHealthResponse(ApiModel):
    schema_version: Literal["system-health/1.0"] = "system-health/1.0"
    status: Literal["healthy", "degraded", "unhealthy", "unknown"]
    checked_at: datetime
    components: list[HealthComponent]
    versions: dict[str, str | None]
    recent_events: list[HealthEvent] = Field(default_factory=list)


class SensorTestModeWrite(ApiModel):
    sensor_count: int = Field(default=1, ge=0, le=32)
    load_profile: Literal[
        "steady",
        "home_cycle",
        "variable_household",
        "evening_peak",
        "morning_evening_peaks",
        "high_load",
        "low_load",
        "solar_day",
        "custom",
    ] = "variable_household"
    offline_sensor_indexes: list[int] = Field(default_factory=list, max_length=32)
    custom_load_w: Decimal | None = Field(default=None, ge=0, le=250_000)
    base_load_w: Decimal = Field(default=Decimal("1000"), ge=0, le=250_000)
    variation_percent: Decimal = Field(default=Decimal("20"), ge=0, le=100)
    sample_interval_seconds: int = Field(default=5, ge=1, le=60)
    expires_in_minutes: int | None = Field(default=15, ge=5, le=1440)
    cost_preview_enabled: bool = False
    paused: bool = False
    site_id: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=160)

    @model_validator(mode="after")
    def validate_test_sensor_configuration(self) -> SensorTestModeWrite:
        if len(set(self.offline_sensor_indexes)) != len(self.offline_sensor_indexes):
            raise ValueError("offline sensor indexes must be unique")
        if any(index < 1 or index > self.sensor_count for index in self.offline_sensor_indexes):
            raise ValueError("offline sensor indexes must refer to configured sensors")
        if self.load_profile == "custom" and self.custom_load_w is None:
            raise ValueError("custom load profile requires custom_load_w")
        return self


class SensorTestModeUpdate(ApiModel):
    sensor_count: int | None = Field(default=None, ge=0, le=32)
    load_profile: (
        Literal[
            "steady",
            "home_cycle",
            "variable_household",
            "evening_peak",
            "morning_evening_peaks",
            "high_load",
            "low_load",
            "solar_day",
            "custom",
        ]
        | None
    ) = None
    offline_sensor_indexes: list[int] | None = Field(default=None, max_length=32)
    custom_load_w: Decimal | None = Field(default=None, ge=0, le=250_000)
    base_load_w: Decimal | None = Field(default=None, ge=0, le=250_000)
    variation_percent: Decimal | None = Field(default=None, ge=0, le=100)
    sample_interval_seconds: int | None = Field(default=None, ge=1, le=60)
    expires_in_minutes: int | None = Field(default=None, ge=5, le=1440)
    cost_preview_enabled: bool | None = None
    paused: bool | None = None
    idempotency_key: str = Field(min_length=8, max_length=160)


class SensorTestModeAction(ApiModel):
    idempotency_key: str = Field(min_length=8, max_length=160)


class SensorTestModeSensorUpdate(ApiModel):
    offline: bool | None = None
    load_override_w: Decimal | None = Field(default=None, ge=0, le=250_000)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=160)


class SensorTestModeSensor(ApiModel):
    id: str
    name: str
    index: int
    online: bool
    current_power_w: Decimal
    energy_kwh: Decimal
    load_override_w: Decimal | None = None
    source_type: Literal["simulated"] = "simulated"
    environment: Literal["test_mode"] = "test_mode"


class SensorTestModePoint(ApiModel):
    recorded_at: datetime
    sensor_id: str
    sensor_name: str
    online: bool
    power_w: Decimal
    interval_energy_kwh: Decimal
    source_type: Literal["simulated"] = "simulated"
    environment: Literal["test_mode"] = "test_mode"


class SensorTestModeCostPreview(ApiModel):
    enabled: bool
    available: bool
    energy_kwh: Decimal
    estimated_energy_cost: Decimal | None = None
    currency: str | None = None
    rate_plan: str | None = None
    rate_version: int | None = None
    disclosure: str


class SensorTestModeState(ApiModel):
    enabled: bool
    session_id: str | None = None
    site_id: str | None = None
    started_at: datetime | None = None
    expires_at: datetime | None = None
    remaining_seconds: int = 0
    sensor_count: int = 0
    online_sensors: int = 0
    offline_sensors: int = 0
    load_profile: str | None = None
    custom_load_w: Decimal | None = None
    base_load_w: Decimal = Decimal("1000")
    variation_percent: Decimal = Decimal("20")
    sample_interval_seconds: int = 5
    cost_preview_enabled: bool = False
    paused: bool = False
    current_power_w: Decimal = Decimal("0")
    total_energy_kwh: Decimal = Decimal("0")
    source_type: Literal["simulated"] = "simulated"
    environment: Literal["test_mode"] = "test_mode"
    ended_at: datetime | None = None
    end_reason: Literal["disabled", "expired"] | None = None
    isolation: dict[str, bool] = Field(
        default_factory=lambda: {
            "real_readings": True,
            "bills_and_finalized_costs": True,
            "exports_and_backups": True,
            "alerts": True,
            "credentials_and_firmware": True,
        }
    )
    cost_preview: SensorTestModeCostPreview | None = None
