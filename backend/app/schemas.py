from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


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
    reason: str = Field(min_length=3, max_length=500)
    confirmation: str = Field(min_length=1, max_length=160)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)


class RatePlanRestoreRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)


class RatePlanDraftDeleteRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    confirmation: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=3, max_length=500)


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

    _from_aware = field_validator("effective_from")(require_aware)
    _to_aware = field_validator("effective_to")(
        lambda value: require_aware(value) if value else value
    )

    @model_validator(mode="after")
    def valid_window(self) -> RateAssignmentWrite:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective-through must follow effective-from")
        return self


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
    effective_from: datetime
    effective_to: datetime | None = None
    enabled: bool = True

    _from_aware = field_validator("effective_from")(require_aware)
    _to_aware = field_validator("effective_to")(
        lambda value: require_aware(value) if value else value
    )


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

    @model_validator(mode="after")
    def required_source(self) -> AccountUsageAuthorityWrite:
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


class DeviceCapabilities(DeviceProtocolModel):
    hardware_target: str = Field(min_length=1, max_length=120)
    pzem_model: str = Field(pattern=r"^PZEM-004T V4")
    sd_present: bool
    sd_required: bool = True
    supported_endpoints: list[str] = Field(default_factory=list)


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


class SubsystemHealth(DeviceProtocolModel):
    ok: bool
    status: str
    error_count: int = Field(default=0, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


class TimeHealth(DeviceProtocolModel):
    trusted: bool
    source: str
    offset_ms: int | None = None
    last_sync_at: datetime | None = None


class Heartbeat(DeviceProtocolModel):
    protocol_version: str
    schema_version: str = "heartbeat/1.0.0"
    device_id: str
    boot_id: str
    firmware_version: str
    firmware_build_hash: str
    uptime_seconds: int = Field(ge=0)
    reboot_reason: str
    current_ip: str | None = None
    hostname: str | None = None
    rssi_dbm: int | None = Field(default=None, ge=-127, le=0)
    connection_mode: Literal["pull", "push", "hybrid"]
    latest: LiveMeasurement | None = None
    pzem: SubsystemHealth
    sd: SubsystemHealth
    oldest_stored_sequence: int = Field(ge=0)
    newest_stored_sequence: int = Field(ge=0)
    server_ack_sequence: int = Field(ge=0)
    backlog_estimate: int = Field(ge=0)
    configuration_version: int = Field(ge=0)
    time: TimeHealth
    resources: dict[str, Any] = Field(default_factory=dict)
    queue: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_sequence_bounds(self) -> Heartbeat:
        if (
            self.newest_stored_sequence
            and self.oldest_stored_sequence > self.newest_stored_sequence
        ):
            raise ValueError("oldest_stored_sequence exceeds newest_stored_sequence")
        return self


class HeartbeatResponse(ApiModel):
    server_receive_time: datetime
    highest_contiguous_accepted_sequence: int
    gap_ranges: list[tuple[int, int]]
    desired_configuration_version: int
    firmware_release_available: bool
    recommended_heartbeat_interval_seconds: int
    immediate_sync_requested: bool


class Reading(DeviceProtocolModel):
    sequence: int = Field(gt=0)
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


class ReadingBatch(DeviceProtocolModel):
    protocol_version: str
    schema_version: str = "reading-batch/1.0.0"
    device_id: str
    readings: list[Reading] = Field(min_length=1, max_length=500)


class RejectedReading(ApiModel):
    sequence: int | None
    code: str
    detail: str


class ReadingBatchResponse(ApiModel):
    accepted: list[int]
    duplicates: list[int]
    rejected: list[RejectedReading]
    highest_contiguous_accepted_sequence: int
    missing_ranges: list[tuple[int, int]]


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


class DeviceEventBatch(DeviceProtocolModel):
    protocol_version: str
    device_id: str
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


class UserCreate(ApiModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=14, max_length=1024)
    roles: list[Literal["admin", "operator", "rate-manager", "viewer"]] = Field(min_length=1)
    confirm_high_risk: bool = False


class ReauthenticateRequest(ApiModel):
    password: str = Field(max_length=1024)
    totp_code: str | None = Field(default=None, pattern=r"^\d{6}$")


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


class LogExportCreate(ApiModel):
    start_date: date | None = None
    end_date: date | None = None
    services: list[str] | None = Field(default=None, min_length=1, max_length=6)


class FirmwareDeploymentCreate(ApiModel):
    firmware_release_id: str
    device_ids: list[str] = Field(min_length=1, max_length=1000)
    scheduled_at: datetime

    _aware = field_validator("scheduled_at")(require_aware)


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


class FleetSummary(ApiModel):
    site_id: str | None
    current_load_w: Decimal
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
    recent_peak_w: Decimal
    has_live_data: bool
    has_energy_data: bool
    has_cost_data: bool
    reporting_devices: int
    latest_heartbeat_at: datetime | None
    coverage_percent: Decimal | None
    disclosure: str
