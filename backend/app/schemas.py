from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class DeviceProtocolModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
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


class SessionView(ApiModel):
    authenticated: bool
    user: UserSummary | None = None
    expires_at: datetime | None = None
    csrf_token: str | None = None
    bootstrap_required: bool = False


class SiteCreate(ApiModel):
    name: str = Field(min_length=1, max_length=160)
    timezone: str = "America/Los_Angeles"
    allowed_cidrs: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    allow_public_polling: bool = False


class SiteView(SiteCreate):
    id: str


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
        "ct_utilization",
        "voltage_frequency_range",
        "reboot_loop",
        "firmware_failure",
        "server_failure",
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
    rate_version_id: str
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
    roles: list[Literal["admin", "operator", "viewer"]] = Field(min_length=1)


class PasswordReset(ApiModel):
    new_password: str = Field(min_length=14, max_length=1024)


class CredentialRotationRequest(ApiModel):
    overlap_seconds: int = Field(default=3600, ge=0, le=604800)


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
    recent_peak_w: Decimal
    disclosure: str
