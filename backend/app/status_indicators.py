from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    AggregateSet,
    AlertInstance,
    AlertRule,
    BackupRun,
    CostCalculationRun,
    CostIntervalResult,
    Device,
    EnrollmentToken,
    FirmwareDeployment,
    NormalizedInterval,
    NotificationAttempt,
    RateAssignment,
    RateChangeCandidate,
    RateSource,
    RateSyncConfiguration,
    RateVersion,
    Site,
    StatusLayoutRevision,
    StatusLayoutState,
    UtilityAccount,
    WorkerState,
)
from app.home_aggregate import resolve_home_aggregate_devices
from app.live_measurements import load_latest_measurements
from app.problem import ProblemError
from app.rates.documents import engine_plan
from app.rates.engine import RateEngine
from app.rates.service import version_document
from app.rates.tiered import calculate_cycle_tier_status, current_billing_cycle

REGISTRY_VERSION = "status-indicators/1.0"
LAYOUT_SCHEMA_VERSION = "power-monitor-status-layout/1.0"
BREAKPOINTS = ("default", "desktop", "tablet", "mobile")
PAGES = (
    "overview",
    "devices",
    "device_detail",
    "topology",
    "usage",
    "history",
    "costs",
    "rates",
    "rate_sources",
    "alerts",
    "enrollment",
    "administration",
    "system_health",
    "backups",
)
GLOBAL_ZONES = ("top_bar",)
PAGE_ZONES = (
    "workspace_header",
    "page_summary",
    "overview_summary",
    "administration_diagnostics",
)
MOBILE_ZONES = ("mobile_status_drawer",)
ZONES = GLOBAL_ZONES + PAGE_ZONES + MOBILE_ZONES
LEGACY_ZONE_MAP = {
    "global_header_left": "top_bar",
    "global_header_center": "top_bar",
    "global_header_right": "top_bar",
    "sidebar_upper": "mobile_status_drawer",
    "sidebar_lower": "mobile_status_drawer",
    "global_footer": "page_summary",
    "page_header_primary": "workspace_header",
    "page_header_secondary": "workspace_header",
    "page_status_row": "page_summary",
    "page_summary_strip": "page_summary",
    "page_footer": "page_summary",
    "overview_site_state": "overview_summary",
    "overview_site_summary": "overview_summary",
    "history_context": "page_summary",
    "diagnostics_summary": "administration_diagnostics",
    "mobile_header": "mobile_status_drawer",
    "mobile_status_strip": "mobile_status_drawer",
}


@dataclass(frozen=True)
class IndicatorDefinition:
    key: str
    default_label: str
    description: str
    category: str
    data_source: str
    current_value_schema: dict[str, str]
    severity_capability: tuple[str, ...]
    default_enabled: bool
    default_zone: str
    allowed_zones: tuple[str, ...]
    default_order: int
    supported_pages: tuple[str, ...]
    global_shell_support: bool
    minimum_display_width: int
    preferred_display_width: int
    presentations: tuple[str, ...]
    icon_supported: bool
    label_supported: bool
    value_supported: bool
    freshness_supported: bool
    role_visibility_supported: bool
    permission_required: str
    configurable: bool
    critical_fallback: str | None
    renderer: str
    icon: str
    metric_identity: str
    canonical_priority: int
    allow_duplicate: bool
    suppress_when_empty: bool
    hide_in_zero_data_state: bool
    diagnostics_only: bool
    registry_version: str = REGISTRY_VERSION


def _definition(
    key: str,
    label: str,
    description: str,
    category: str,
    data_source: str,
    permission: str,
    zone: str,
    order: int,
    *,
    pages: tuple[str, ...] = PAGES,
    allowed: tuple[str, ...] | None = None,
    enabled: bool = True,
    renderer: str = "health",
    icon: str = "activity",
    freshness: bool = True,
    critical_fallback: str | None = None,
    global_shell: bool | None = None,
    metric_identity: str | None = None,
    canonical_priority: int = 300,
    allow_duplicate: bool = False,
    suppress_when_empty: bool = True,
    hide_in_zero_data_state: bool = False,
    diagnostics_only: bool = False,
) -> IndicatorDefinition:
    return IndicatorDefinition(
        key=key,
        default_label=label,
        description=description,
        category=category,
        data_source=data_source,
        current_value_schema={
            "status": "string",
            "severity": "info|success|warning|critical|unknown",
            "display_value": "string",
            "detail": "string|null",
            "freshness_at": "RFC3339|null",
        },
        severity_capability=("info", "success", "warning", "critical", "unknown"),
        default_enabled=enabled,
        default_zone=zone,
        allowed_zones=allowed
        or ((GLOBAL_ZONES + MOBILE_ZONES) if zone in GLOBAL_ZONES else (PAGE_ZONES + MOBILE_ZONES)),
        default_order=order,
        supported_pages=pages,
        global_shell_support=zone in GLOBAL_ZONES if global_shell is None else global_shell,
        minimum_display_width=140,
        preferred_display_width=220,
        presentations=("compact", "standard", "detailed"),
        icon_supported=True,
        label_supported=True,
        value_supported=True,
        freshness_supported=freshness,
        role_visibility_supported=True,
        permission_required=permission,
        configurable=True,
        critical_fallback=critical_fallback,
        renderer=renderer,
        icon=icon,
        metric_identity=metric_identity or key,
        canonical_priority=canonical_priority,
        allow_duplicate=allow_duplicate,
        suppress_when_empty=suppress_when_empty,
        hide_in_zero_data_state=hide_in_zero_data_state,
        diagnostics_only=diagnostics_only,
    )


INDICATOR_DEFINITIONS = (
    _definition(
        "system.api_health",
        "API",
        "FastAPI process readiness.",
        "System",
        "health.ready",
        "settings.view",
        "administration_diagnostics",
        10,
        pages=("system_health",),
        allowed=("administration_diagnostics", "mobile_status_drawer"),
        renderer="health",
        icon="server",
        metric_identity="system.api_health",
        canonical_priority=1000,
        diagnostics_only=True,
        global_shell=False,
        critical_fallback="API failures remain on the affected page and in TrueNAS health checks.",
    ),
    _definition(
        "system.database_health",
        "Database",
        "PostgreSQL readiness and migration state.",
        "System",
        "health.ready.database",
        "settings.view",
        "administration_diagnostics",
        20,
        pages=("system_health",),
        allowed=("administration_diagnostics", "mobile_status_drawer"),
        renderer="health",
        icon="database",
        metric_identity="system.database_health",
        canonical_priority=1000,
        diagnostics_only=True,
        global_shell=False,
        critical_fallback=(
            "Database failures remain in API readiness and TrueNAS application health."
        ),
    ),
    _definition(
        "system.worker_health",
        "Worker",
        "Background worker loop freshness.",
        "System",
        "worker_state",
        "settings.view",
        "administration_diagnostics",
        30,
        pages=("system_health",),
        allowed=("administration_diagnostics", "mobile_status_drawer"),
        renderer="health",
        icon="cpu",
        metric_identity="system.worker_health",
        canonical_priority=1000,
        diagnostics_only=True,
        global_shell=False,
        critical_fallback=(
            "Worker failures continue generating alerts and remain under "
            "Administration diagnostics."
        ),
    ),
    _definition(
        "data.live_connection",
        "Live data",
        "Fresh signed heartbeat availability.",
        "Live data",
        "device_heartbeats",
        "overview.view",
        "top_bar",
        10,
        allowed=ZONES,
        renderer="health",
        icon="radio",
        metric_identity="data.live_state",
        critical_fallback="Stale-device alerts and device details remain available.",
    ),
    _definition(
        "data.current_power",
        "Current load",
        "Aggregate current power from latest signed heartbeats.",
        "Live data",
        "device_heartbeats.current_watts",
        "overview.view",
        "top_bar",
        20,
        renderer="power",
        icon="zap",
        freshness=False,
        metric_identity="power.current",
    ),
    _definition(
        "site.current",
        "Current site",
        "Current permitted site scope.",
        "Sites",
        "sites",
        "sites.view",
        "top_bar",
        10,
        enabled=False,
        renderer="text",
        icon="map-pin",
        freshness=False,
        metric_identity="site.current",
    ),
    _definition(
        "alerts.active_count",
        "Active alerts",
        "Open alert count for permitted sites.",
        "Alerts",
        "alert_instances",
        "alerts.view",
        "top_bar",
        10,
        allowed=ZONES,
        renderer="count",
        icon="bell",
        metric_identity="alerts.active_count",
        critical_fallback="Alert records remain on Alerts & Notifications and delivery continues.",
    ),
    _definition(
        "alerts.critical_count",
        "Critical alerts",
        "Active critical alert count for the selected site scope.",
        "Alerts",
        "alert_instances.severity",
        "alerts.view",
        "page_summary",
        10,
        pages=("alerts",),
        renderer="count",
        icon="bell",
        critical_fallback="Critical alert records remain in the alert timeline.",
    ),
    _definition(
        "alerts.warning_count",
        "Warning alerts",
        "Active warning alert count for the selected site scope.",
        "Alerts",
        "alert_instances.severity",
        "alerts.view",
        "page_summary",
        20,
        pages=("alerts",),
        renderer="count",
        icon="clock-3",
    ),
    _definition(
        "alerts.enabled_rule_count",
        "Rules enabled",
        "Enabled alert-rule count for the selected site scope.",
        "Alerts",
        "alert_rules.enabled",
        "alerts.view",
        "page_summary",
        30,
        pages=("alerts",),
        renderer="count",
        icon="shield-check",
    ),
    _definition(
        "alerts.disconnect_rule_state",
        "Disconnect alerts",
        "Whether at least one signed-heartbeat disconnect rule is enabled.",
        "Alerts",
        "alert_rules.rule_type",
        "alerts.view",
        "page_summary",
        40,
        pages=("alerts",),
        renderer="health",
        icon="unplug",
        critical_fallback=(
            "Heartbeat monitoring continues and rule state remains in Administration."
        ),
    ),
    _definition(
        "device.online_count",
        "Devices online",
        "Sensors currently online by signed heartbeat state.",
        "Devices",
        "devices.status",
        "devices.view",
        "page_summary",
        10,
        pages=("overview", "devices"),
        renderer="count",
        icon="radio",
        metric_identity="devices.online",
        critical_fallback="Device state remains on Devices and device detail pages.",
    ),
    _definition(
        "device.offline_count",
        "Offline or stale",
        "Sensors offline or outside heartbeat freshness limits.",
        "Devices",
        "devices.status",
        "devices.view",
        "page_summary",
        20,
        pages=("overview", "devices"),
        renderer="count",
        icon="unplug",
        metric_identity="devices.offline",
        critical_fallback="Disconnect alerts and device state remain active.",
    ),
    _definition(
        "device.synchronized_count",
        "Synchronized",
        "Sensors without a historical synchronization backlog.",
        "Devices",
        "devices.status",
        "devices.view",
        "page_summary",
        40,
        pages=("overview", "devices"),
        renderer="fraction",
        icon="refresh-cw",
        metric_identity="data.synchronization",
    ),
    _definition(
        "data.energy_today",
        "Energy today",
        "Monitored energy since the local-day boundary.",
        "Energy",
        "readings",
        "overview.view",
        "overview_summary",
        10,
        pages=("overview",),
        allowed=("overview_summary", "mobile_status_drawer"),
        renderer="energy",
        icon="battery-charging",
        freshness=False,
        metric_identity="energy.today",
        hide_in_zero_data_state=True,
    ),
    _definition(
        "data.recent_peak",
        "Recent peak",
        "Recent aggregate power peak.",
        "Energy",
        "readings",
        "overview.view",
        "overview_summary",
        30,
        pages=("overview",),
        allowed=("overview_summary", "page_summary", "mobile_status_drawer"),
        enabled=False,
        renderer="power",
        icon="gauge",
        freshness=False,
        metric_identity="power.recent_peak",
        hide_in_zero_data_state=True,
    ),
    _definition(
        "data.aggregate_coverage",
        "Data coverage",
        "Current synchronized-device coverage of the selected aggregate.",
        "Data quality",
        "devices.status",
        "history.view",
        "page_summary",
        10,
        pages=("overview", "history"),
        enabled=False,
        renderer="percent",
        icon="chart-no-axes-combined",
        metric_identity="data.coverage",
        hide_in_zero_data_state=True,
    ),
    _definition(
        "rate.current_plan",
        "Current rate plan",
        "Effective rate assignment for monitored accounts.",
        "Rates",
        "rate_assignments",
        "rates.view",
        "page_summary",
        30,
        pages=("rates",),
        renderer="text",
        icon="badge-dollar-sign",
        freshness=False,
        metric_identity="rate.current_plan",
    ),
    _definition(
        "rate.current_period",
        "Current rate period",
        "Current time-of-use period from the effective rate.",
        "Rates",
        "rate_engine",
        "rates.view",
        "page_summary",
        20,
        pages=("rates",),
        renderer="text",
        icon="clock-3",
        freshness=False,
        metric_identity="rate.current_period",
    ),
    _definition(
        "rate.current_price",
        "Current energy price",
        "Current energy-only price where an effective rate is assigned.",
        "Rates",
        "rate_engine",
        "rates.view",
        "page_summary",
        25,
        pages=("rates",),
        renderer="money-rate",
        icon="circle-dollar-sign",
        freshness=False,
        metric_identity="rate.current_price",
    ),
    _definition(
        "cost.today",
        "Estimated today",
        "Estimated energy cost since the local-day boundary.",
        "Costs",
        "cost_interval_results",
        "overview.view",
        "overview_summary",
        20,
        pages=("overview",),
        allowed=("overview_summary", "mobile_status_drawer"),
        renderer="money",
        icon="circle-dollar-sign",
        freshness=False,
        metric_identity="cost.today",
        canonical_priority=500,
        hide_in_zero_data_state=True,
    ),
    _definition(
        "energy.billing_cycle",
        "Billing-cycle energy",
        "Monitored energy in the current calculation cycle.",
        "Energy",
        "cost_interval_results",
        "overview.view",
        "overview_summary",
        30,
        pages=("overview",),
        allowed=("overview_summary", "mobile_status_drawer"),
        renderer="energy",
        icon="battery-charging",
        freshness=False,
        metric_identity="energy.billing_cycle",
        canonical_priority=500,
        hide_in_zero_data_state=True,
    ),
    _definition(
        "cost.billing_cycle_estimate",
        "Cycle estimate",
        "Estimated monitored energy cost in the current calculation cycle.",
        "Costs",
        "cost_interval_results",
        "overview.view",
        "overview_summary",
        40,
        pages=("overview",),
        allowed=("overview_summary", "mobile_status_drawer"),
        renderer="money",
        icon="circle-dollar-sign",
        freshness=False,
        metric_identity="cost.billing_cycle_estimate",
        canonical_priority=500,
        hide_in_zero_data_state=True,
    ),
    _definition(
        "rate.current_context",
        "Rate context",
        "Effective plan, current time-of-use period, and energy price.",
        "Rates",
        "rate_engine",
        "rates.view",
        "page_summary",
        20,
        pages=("overview", "history"),
        allowed=("page_summary", "overview_summary", "mobile_status_drawer"),
        renderer="text",
        icon="badge-dollar-sign",
        freshness=False,
        metric_identity="rate.current_context",
        canonical_priority=550,
    ),
    _definition(
        "rate.source_health",
        "Rate source health",
        "Most recent approved-source synchronization outcome.",
        "Rates",
        "rate_sync_configuration",
        "rates.view",
        "page_summary",
        10,
        pages=("rate_sources", "rates"),
        renderer="health",
        icon="shield-check",
        critical_fallback=(
            "Source errors remain on Rate Sources and never activate a candidate silently."
        ),
    ),
    _definition(
        "rate.update_pending",
        "Rate update pending",
        "Candidates awaiting administrator review.",
        "Rates",
        "rate_change_candidates",
        "rates.view",
        "page_summary",
        20,
        pages=("rate_sources", "rates"),
        renderer="count",
        icon="inbox",
    ),
    _definition(
        "rate.last_successful_check",
        "Last source check",
        "Most recent successful rate-source synchronization.",
        "Rates",
        "rate_sync_configuration.last_successful_run",
        "rates.manage_sources",
        "page_summary",
        10,
        pages=("rate_sources", "rates"),
        renderer="freshness",
        icon="calendar-check",
    ),
    _definition(
        "rate.next_scheduled_check",
        "Next source check",
        "Next scheduled approved-source synchronization.",
        "Rates",
        "rate_sync_configuration.next_scheduled_run",
        "rates.manage_sources",
        "page_summary",
        20,
        pages=("rate_sources", "rates"),
        renderer="freshness",
        icon="calendar-clock",
    ),
    _definition(
        "rate.review_policy",
        "Review policy",
        "Configured candidate activation policy.",
        "Rates",
        "rate_sync_configuration.approval_mode",
        "rates.manage_sources",
        "page_summary",
        30,
        pages=("rate_sources", "rates"),
        renderer="text",
        icon="archive",
        freshness=False,
    ),
    _definition(
        "device.pzem_health",
        "PZEM communication",
        "Aggregate meter communication health from signed heartbeats.",
        "Device health",
        "device_heartbeats.pzem_ok",
        "devices.view",
        "page_summary",
        30,
        pages=("devices", "device_detail"),
        renderer="health",
        icon="gauge",
        critical_fallback="PZEM failures remain in device health details and alerts.",
    ),
    _definition(
        "device.sd_health",
        "microSD health",
        "Aggregate on-device storage health from signed heartbeats.",
        "Device health",
        "device_heartbeats.sd_ok",
        "devices.view",
        "page_summary",
        40,
        pages=("devices", "device_detail"),
        renderer="health",
        icon="database",
        critical_fallback="Storage failures remain in device health details and alerts.",
    ),
    _definition(
        "device.sync_backlog",
        "Synchronization backlog",
        "Total pending historical readings reported by sensors.",
        "Device health",
        "device_heartbeats.backlog_estimate",
        "devices.view",
        "page_summary",
        10,
        pages=("devices", "device_detail"),
        renderer="count",
        icon="refresh-cw",
    ),
    _definition(
        "device.time_sync",
        "Clock synchronization",
        "Sensors reporting a trusted device clock.",
        "Device health",
        "device_heartbeats.time_trusted",
        "devices.view",
        "page_summary",
        20,
        pages=("devices", "device_detail"),
        renderer="fraction",
        icon="clock-3",
    ),
    _definition(
        "device.wifi_signal",
        "Wi-Fi signal",
        "Average RSSI reported by current sensors.",
        "Device health",
        "device_heartbeats.rssi_dbm",
        "devices.view",
        "page_summary",
        30,
        pages=("devices", "device_detail"),
        renderer="signal",
        icon="wifi",
    ),
    _definition(
        "device.heartbeat_freshness",
        "Latest heartbeat",
        "Freshness of the newest signed device heartbeat.",
        "Device health",
        "device_heartbeats.received_at",
        "devices.view",
        "page_summary",
        10,
        pages=("devices", "device_detail"),
        renderer="freshness",
        icon="heart-pulse",
    ),
    _definition(
        "firmware.update_state",
        "Firmware updates",
        "Scheduled or in-progress firmware deployments.",
        "Firmware",
        "firmware_deployments",
        "firmware.view",
        "page_summary",
        50,
        pages=("devices", "administration"),
        renderer="count",
        icon="package-check",
    ),
    _definition(
        "backup.last_result",
        "Latest backup",
        "Most recent logical backup result.",
        "Backups",
        "backup_runs",
        "backups.view",
        "page_summary",
        10,
        pages=("backups", "administration"),
        renderer="health",
        icon="archive",
        critical_fallback="Backup failures remain visible on the Backups page and in alerts.",
    ),
    _definition(
        "backup.verification",
        "Backup verification",
        "Latest checksum or restore verification state.",
        "Backups",
        "backup_runs.verified_at",
        "backups.view",
        "page_summary",
        10,
        pages=("backups", "administration"),
        renderer="health",
        icon="shield-check",
        critical_fallback="Verification evidence remains on the Backups page.",
    ),
    _definition(
        "notifications.delivery_health",
        "Notification delivery",
        "Most recent SMTP notification delivery outcome.",
        "Notifications",
        "notification_attempts",
        "alerts.manage_delivery",
        "page_summary",
        30,
        pages=("alerts", "administration"),
        renderer="health",
        icon="send",
        critical_fallback="Delivery attempts and failures remain under Alerts & Notifications.",
    ),
    _definition(
        "enrollment.availability",
        "Enrollment availability",
        "Unexpired, unclaimed enrollment token count.",
        "Enrollment",
        "enrollment_tokens",
        "enrollment.view",
        "page_summary",
        10,
        pages=("enrollment",),
        renderer="count",
        icon="scan-line",
    ),
    _definition(
        "topology.aggregate_overlap",
        "Aggregate overlap",
        "Aggregate definitions with explicitly confirmed overlap.",
        "Topology",
        "aggregate_sets",
        "topology.view",
        "page_summary",
        10,
        pages=("topology",),
        renderer="count",
        icon="triangle-alert",
        critical_fallback=(
            "Topology and History continue showing overlap warnings where calculations "
            "are affected."
        ),
    ),
)
INDICATOR_REGISTRY = {item.key: item for item in INDICATOR_DEFINITIONS}


# Page-specific defaults move a global indicator into its canonical location without
# changing that indicator's placement on other pages. Exact-scope administrator
# settings replace these defaults during materialization.
DEFAULT_OVERRIDES: tuple[dict[str, Any], ...] = (
    {
        "indicator_key": "data.live_connection",
        "page": "overview",
        "role": "*",
        "breakpoint": "default",
        "zone": "overview_summary",
        "order": 20,
        "density": "compact",
    },
    {
        "indicator_key": "data.current_power",
        "page": "overview",
        "role": "*",
        "breakpoint": "default",
        "visible": False,
    },
    {
        "indicator_key": "device.online_count",
        "page": "overview",
        "role": "*",
        "breakpoint": "default",
        "zone": "overview_summary",
        "order": 10,
        "density": "compact",
    },
    {
        "indicator_key": "device.offline_count",
        "page": "overview",
        "role": "*",
        "breakpoint": "default",
        "zone": "overview_summary",
        "order": 30,
        "density": "compact",
    },
    {
        "indicator_key": "device.synchronized_count",
        "page": "overview",
        "role": "*",
        "breakpoint": "default",
        "zone": "overview_summary",
        "order": 50,
    },
    {
        "indicator_key": "alerts.active_count",
        "page": "overview",
        "role": "*",
        "breakpoint": "default",
        "zone": "overview_summary",
        "order": 60,
    },
    {
        "indicator_key": "rate.current_context",
        "page": "overview",
        "role": "*",
        "breakpoint": "default",
        "visible": False,
        "zone": "overview_summary",
        "order": 70,
    },
)


def registry_payload(
    *, permissions: set[str] | frozenset[str] | None = None
) -> list[dict[str, Any]]:
    definitions: tuple[IndicatorDefinition, ...] = INDICATOR_DEFINITIONS
    if permissions is not None:
        definitions = tuple(item for item in definitions if item.permission_required in permissions)
    return [asdict(item) for item in definitions]


def default_item(definition: IndicatorDefinition) -> dict[str, Any]:
    return {
        "indicator_key": definition.key,
        "page": "*",
        "role": "*",
        "breakpoint": "default",
        "visible": definition.default_enabled,
        "zone": definition.default_zone,
        "order": definition.default_order,
        "density": "standard",
        "show_icon": True,
        "show_label": True,
        "show_value": True,
        "show_freshness": definition.freshness_supported,
        "show_severity": True,
        "show_tooltip": True,
    }


def compiled_configuration() -> dict[str, Any]:
    return {
        "schema_version": LAYOUT_SCHEMA_VERSION,
        "registry_version": REGISTRY_VERSION,
        "personalization_enabled": False,
        "items": [default_item(item) for item in INDICATOR_DEFINITIONS]
        + copy.deepcopy(list(DEFAULT_OVERRIDES)),
    }


def materialize_configuration(configuration: dict[str, Any] | None) -> dict[str, Any]:
    result = compiled_configuration()
    if not isinstance(configuration, dict):
        return result
    result["personalization_enabled"] = bool(configuration.get("personalization_enabled", False))
    configured = copy.deepcopy(configuration.get("items", []))
    if not isinstance(configured, list):
        return result
    for item in configured:
        if isinstance(item, dict) and isinstance(item.get("zone"), str):
            item["zone"] = LEGACY_ZONE_MAP.get(item["zone"], item["zone"])
    explicit_scopes = {
        (
            item.get("indicator_key"),
            item.get("page", "*"),
            item.get("role", "*"),
            item.get("breakpoint", "default"),
        )
        for item in configured
        if isinstance(item, dict)
    }
    result["items"] = [
        item
        for item in result["items"]
        if (
            item.get("indicator_key"),
            item.get("page", "*"),
            item.get("role", "*"),
            item.get("breakpoint", "default"),
        )
        not in explicit_scopes
    ] + copy.deepcopy(configured)
    return result


def _problem(code: str, detail: str, **extra: Any) -> ProblemError:
    return ProblemError(422, "Invalid status layout", detail, code, extra=extra or None)


FIXED_PAGE_METRICS: dict[str, dict[str, str]] = {
    "overview": {
        "site.current": "the site selector",
        "power.current": "the Live Power hero",
    },
    "history": {
        "data.coverage": "the History result summary",
        "power.recent_peak": "the History result summary",
    },
}
for _page in PAGES:
    FIXED_PAGE_METRICS.setdefault(_page, {}).setdefault("site.current", "the site selector")

ZONE_PRIORITY = {
    "administration_diagnostics": 1000,
    "overview_summary": 900,
    "workspace_header": 800,
    "page_summary": 700,
    "top_bar": 500,
    "mobile_status_drawer": 100,
}


def _effective_states(
    configuration: dict[str, Any], *, page: str, role: str, breakpoint: str
) -> list[tuple[IndicatorDefinition, dict[str, Any]]]:
    states: list[tuple[IndicatorDefinition, dict[str, Any]]] = []
    roles = {role} if role != "*" else set()
    for definition in INDICATOR_DEFINITIONS:
        if definition.diagnostics_only and page != "system_health":
            continue
        if not definition.global_shell_support and page not in definition.supported_pages:
            continue
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        for item in configuration.get("items", []):
            if not isinstance(item, dict) or item.get("indicator_key") != definition.key:
                continue
            score = _scope_score(item, page, roles, breakpoint)
            if score is not None:
                candidates.append((score, str(item.get("role", "*")), item))
        state = default_item(definition)
        for _score, _role, candidate in sorted(candidates, key=lambda value: (value[0], value[1])):
            state.update(
                {
                    key: value
                    for key, value in candidate.items()
                    if key not in {"indicator_key", "page", "role", "breakpoint"}
                }
            )
        if state.get("visible", True):
            states.append((definition, state))
    return states


def duplicate_metric_conflicts(
    configuration: dict[str, Any], *, roles: set[str] | None = None
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    role_ids = sorted(roles or {"*"})
    for page in PAGES:
        for role in role_ids:
            for breakpoint in ("desktop", "tablet", "mobile"):
                groups: dict[str, list[tuple[IndicatorDefinition, dict[str, Any]]]] = {}
                for definition, state in _effective_states(
                    configuration, page=page, role=role, breakpoint=breakpoint
                ):
                    if definition.allow_duplicate:
                        continue
                    groups.setdefault(definition.metric_identity, []).append((definition, state))
                for metric_identity, candidates in groups.items():
                    fixed_surface = FIXED_PAGE_METRICS.get(page, {}).get(metric_identity)
                    if len(candidates) < 2 and fixed_surface is None:
                        continue
                    ordered = sorted(
                        candidates,
                        key=lambda value: (
                            value[0].canonical_priority,
                            ZONE_PRIORITY.get(str(value[1].get("zone")), 0),
                            -int(value[1].get("order", 0)),
                            value[0].key,
                        ),
                        reverse=True,
                    )
                    recommended = (
                        fixed_surface
                        if fixed_surface is not None
                        else f"{ordered[0][0].default_label} in {ordered[0][1].get('zone')}"
                    )
                    conflicts.append(
                        {
                            "metric_identity": metric_identity,
                            "page": page,
                            "role": role,
                            "breakpoint": breakpoint,
                            "indicator_keys": [item[0].key for item in ordered],
                            "recommended_placement": recommended,
                            "message": (
                                f"{ordered[0][0].default_label} is already displayed in "
                                f"{recommended}. Choose one location or keep the "
                                "recommended placement."
                            ),
                        }
                    )
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for conflict in conflicts:
        identity = (
            conflict["metric_identity"],
            conflict["page"],
            conflict["role"],
            conflict["breakpoint"],
        )
        unique.setdefault(identity, conflict)
    return list(unique.values())


def repair_configuration(
    configuration: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Repair legacy placements without changing unrelated layout choices."""

    repaired = materialize_configuration(configuration)
    repairs: list[dict[str, Any]] = []
    for item in repaired["items"]:
        if not isinstance(item, dict):
            continue
        definition = INDICATOR_REGISTRY.get(str(item.get("indicator_key")))
        if definition is None:
            continue
        if definition.diagnostics_only and (
            item.get("page", "*") != "system_health"
            or item.get("zone") != "administration_diagnostics"
        ):
            repairs.append(
                {
                    "indicator_key": definition.key,
                    "action": "moved_to_system_health",
                    "from_zone": item.get("zone"),
                }
            )
            item.update({"page": "system_health", "zone": "administration_diagnostics"})
        elif item.get("zone") not in definition.allowed_zones:
            repairs.append(
                {
                    "indicator_key": definition.key,
                    "action": "moved_to_registered_default",
                    "from_zone": item.get("zone"),
                    "to_zone": definition.default_zone,
                }
            )
            item["zone"] = definition.default_zone

    def disable_for_page(indicator_key: str, page: str, reason: str) -> None:
        exact = next(
            (
                item
                for item in repaired["items"]
                if item.get("indicator_key") == indicator_key
                and item.get("page", "*") == page
                and item.get("role", "*") == "*"
                and item.get("breakpoint", "default") == "default"
            ),
            None,
        )
        if exact is None:
            exact = {
                "indicator_key": indicator_key,
                "page": page,
                "role": "*",
                "breakpoint": "default",
            }
            repaired["items"].append(exact)
        exact["visible"] = False
        repairs.append(
            {
                "indicator_key": indicator_key,
                "action": "suppressed_duplicate",
                "reason": reason,
            }
        )

    # These facts are rendered by required functional controls/result summaries.
    disable_for_page("site.current", "overview", "site selector is canonical")
    disable_for_page("data.current_power", "overview", "Live Power hero is canonical")
    disable_for_page("data.aggregate_coverage", "history", "History result summary is canonical")

    # If two registry items represent one fact in an identical saved scope, retain
    # the item with the strongest registered placement priority.
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for item in repaired["items"]:
        if not isinstance(item, dict) or item.get("visible", True) is False:
            continue
        definition = INDICATOR_REGISTRY.get(str(item.get("indicator_key")))
        if definition is None or definition.allow_duplicate:
            continue
        scope = (
            definition.metric_identity,
            str(item.get("page", "*")),
            str(item.get("role", "*")),
            str(item.get("breakpoint", "default")),
        )
        groups.setdefault(scope, []).append(item)
    for scope, items in groups.items():
        if len(items) < 2:
            continue
        keep = max(
            items,
            key=lambda item: (
                INDICATOR_REGISTRY[str(item["indicator_key"])].canonical_priority,
                ZONE_PRIORITY.get(str(item.get("zone")), 0),
                -int(item.get("order", 0)),
                str(item["indicator_key"]),
            ),
        )
        for item in items:
            if item is keep:
                continue
            item["visible"] = False
            repairs.append(
                {
                    "indicator_key": item["indicator_key"],
                    "metric_identity": scope[0],
                    "action": "suppressed_duplicate",
                    "kept_indicator_key": keep["indicator_key"],
                }
            )
    return repaired, repairs


def validate_configuration(
    configuration: dict[str, Any],
    *,
    roles: set[str] | None = None,
    role_permissions: dict[str, set[str]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if len(json.dumps(configuration, separators=(",", ":"), ensure_ascii=False).encode()) > 512_000:
        raise _problem("status_layout_too_large", "Layout profiles are limited to 512 KiB")
    if configuration.get("schema_version") != LAYOUT_SCHEMA_VERSION:
        raise _problem("status_layout_schema_invalid", "Use the supported layout schema version")
    if configuration.get("registry_version") != REGISTRY_VERSION:
        raise _problem(
            "status_layout_registry_invalid",
            "Preview and update this layout for the current registry",
        )
    if configuration.get("personalization_enabled") not in {False, None}:
        raise _problem(
            "status_layout_personalization_unavailable",
            "Per-user personalization is not enabled in this release",
        )
    items = configuration.get("items")
    if not isinstance(items, list) or len(items) > 400:
        raise _problem("status_layout_items_invalid", "Provide no more than 400 layout items")
    seen: set[tuple[str, str, str, str]] = set()
    normalized: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    allowed_fields = {
        "indicator_key",
        "page",
        "role",
        "breakpoint",
        "visible",
        "zone",
        "order",
        "density",
        "show_icon",
        "show_label",
        "show_value",
        "show_freshness",
        "show_severity",
        "show_tooltip",
    }
    for position, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise _problem(
                "status_layout_item_invalid", "Every layout item must be an object", index=position
            )
        unknown_fields = sorted(set(raw) - allowed_fields)
        if unknown_fields:
            raise _problem(
                "status_layout_field_unknown",
                "Layout items contain unsupported fields",
                fields=unknown_fields,
            )
        key = raw.get("indicator_key")
        definition = INDICATOR_REGISTRY.get(str(key))
        if definition is None:
            raise _problem(
                "status_indicator_unknown",
                "The layout references an unregistered indicator",
                indicator_key=key,
            )
        page = str(raw.get("page", "*"))
        role = str(raw.get("role", "*"))
        breakpoint = str(raw.get("breakpoint", "default"))
        if page != "*" and page not in definition.supported_pages:
            raise _problem(
                "status_indicator_page_unsupported",
                "The indicator is not supported on that page",
                indicator_key=key,
                page=page,
            )
        if role != "*":
            if not definition.role_visibility_supported:
                raise _problem(
                    "status_indicator_role_unsupported",
                    "The indicator does not support role overrides",
                    indicator_key=key,
                )
            if roles is not None and role not in roles:
                raise _problem("status_layout_role_unknown", "The role does not exist", role=role)
        if breakpoint not in BREAKPOINTS:
            raise _problem(
                "status_layout_breakpoint_invalid",
                "Use default, desktop, tablet, or mobile",
                breakpoint=breakpoint,
            )
        identity = (definition.key, page, role, breakpoint)
        if identity in seen:
            raise _problem(
                "status_indicator_duplicate",
                "A single-instance indicator appears twice in the same scope",
                indicator_key=key,
                page=page,
                role=role,
                breakpoint=breakpoint,
            )
        seen.add(identity)
        item: dict[str, Any] = {
            "indicator_key": definition.key,
            "page": page,
            "role": role,
            "breakpoint": breakpoint,
        }
        for field in (
            "visible",
            "show_icon",
            "show_label",
            "show_value",
            "show_freshness",
            "show_severity",
            "show_tooltip",
        ):
            if field in raw:
                if not isinstance(raw[field], bool):
                    raise _problem(
                        "status_layout_boolean_invalid",
                        f"{field} must be true or false",
                        indicator_key=key,
                    )
                item[field] = raw[field]
        if "zone" in raw:
            zone = str(raw["zone"])
            if zone not in ZONES or zone not in definition.allowed_zones:
                raise _problem(
                    "status_indicator_zone_unsupported",
                    "The indicator cannot be placed in that zone",
                    indicator_key=key,
                    zone=zone,
                )
            item["zone"] = zone
        if "order" in raw:
            order = raw["order"]
            if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 9999:
                raise _problem(
                    "status_indicator_order_invalid",
                    "Order must be an integer from 0 through 9999",
                    indicator_key=key,
                )
            item["order"] = order
        if "density" in raw:
            density = str(raw["density"])
            if density not in definition.presentations:
                raise _problem(
                    "status_indicator_density_unsupported",
                    "The requested presentation is unsupported",
                    indicator_key=key,
                    density=density,
                )
            item["density"] = density
        if (
            role != "*"
            and item.get("visible", True)
            and role_permissions is not None
            and definition.permission_required not in role_permissions.get(role, set())
        ):
            raise _problem(
                "status_indicator_role_permission",
                "The role cannot view the indicator data",
                indicator_key=key,
                role=role,
                permission=definition.permission_required,
            )
        if (
            item.get("show_label") is False
            and item.get("show_icon") is False
            and item.get("show_value") is False
        ):
            warnings.append(
                {
                    "code": "accessible_name_preserved",
                    "indicator_key": key,
                    "message": "The renderer will retain the registered accessible name.",
                }
            )
        normalized.append(item)
    normalized_configuration = {
        "schema_version": LAYOUT_SCHEMA_VERSION,
        "registry_version": REGISTRY_VERSION,
        "personalization_enabled": False,
        "items": normalized,
    }
    conflicts = duplicate_metric_conflicts(normalized_configuration, roles=roles)
    if conflicts:
        first = conflicts[0]
        raise _problem(
            "status_metric_duplicate",
            first["message"],
            metric_identity=first["metric_identity"],
            page=first["page"],
            duplicates=conflicts,
            repair_action="Keep recommended placement",
        )
    return normalized_configuration, warnings


async def current_layout(
    session: AsyncSession,
) -> tuple[int, dict[str, Any], StatusLayoutRevision | None]:
    state = await session.get(StatusLayoutState, "current")
    if state is None or state.current_revision_id is None:
        return 0, compiled_configuration(), None
    revision = await session.get(StatusLayoutRevision, state.current_revision_id)
    if revision is None:
        return 0, compiled_configuration(), None
    return revision.revision, materialize_configuration(dict(revision.configuration)), revision


def _scope_score(item: dict[str, Any], page: str, roles: set[str], breakpoint: str) -> int | None:
    item_page = item.get("page", "*")
    item_role = item.get("role", "*")
    item_breakpoint = item.get("breakpoint", "default")
    if item_page not in {"*", page} or (item_role != "*" and item_role not in roles):
        return None
    if item_breakpoint not in {"default", breakpoint}:
        return None
    scope = (200 if item_page == page else 0) + (100 if item_role != "*" else 0)
    return scope + (10 if item_breakpoint == breakpoint else 0)


def _mobile_zone(zone: str) -> str:
    return "mobile_status_drawer" if zone != "mobile_status_drawer" else zone


def resolve_layout(
    configuration: dict[str, Any],
    *,
    page: str,
    roles: set[str],
    permissions: set[str] | frozenset[str],
    breakpoint: Literal["desktop", "tablet", "mobile"],
    revision: int,
) -> dict[str, Any]:
    if page not in PAGES:
        page = "overview"
    materialized = materialize_configuration(configuration)
    configured_keys = {
        item.get("indicator_key")
        for item in configuration.get("items", [])
        if isinstance(item, dict)
    }
    warnings: list[dict[str, Any]] = []
    for raw in configuration.get("items", []):
        if isinstance(raw, dict) and raw.get("indicator_key") not in INDICATOR_REGISTRY:
            warnings.append(
                {
                    "code": "retired_indicator",
                    "indicator_key": raw.get("indicator_key"),
                    "message": "A saved indicator is no longer registered and was ignored.",
                }
            )
    resolved_items: list[dict[str, Any]] = []
    for definition in INDICATOR_DEFINITIONS:
        if definition.permission_required not in permissions:
            continue
        if definition.diagnostics_only and page != "system_health":
            continue
        if not definition.global_shell_support and page not in definition.supported_pages:
            continue
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        for item in materialized["items"]:
            if item.get("indicator_key") != definition.key:
                continue
            score = _scope_score(item, page, roles, breakpoint)
            if score is not None:
                candidates.append((score, str(item.get("role", "*")), item))
        state = default_item(definition)
        for _score, _role, candidate in sorted(candidates, key=lambda value: (value[0], value[1])):
            state.update(
                {
                    key: value
                    for key, value in candidate.items()
                    if key not in {"indicator_key", "page", "role", "breakpoint"}
                }
            )
        if not state.get("visible", True):
            continue
        zone = str(state.get("zone", definition.default_zone))
        if breakpoint == "mobile" and not any(
            candidate.get("breakpoint") == "mobile" and "zone" in candidate
            for _score, _role, candidate in candidates
        ):
            zone = _mobile_zone(zone)
        if zone not in definition.allowed_zones and zone not in MOBILE_ZONES:
            warnings.append(
                {
                    "code": "invalid_saved_zone",
                    "indicator_key": definition.key,
                    "message": (
                        "The saved placement is no longer supported; the registered "
                        "default was used."
                    ),
                }
            )
            zone = (
                _mobile_zone(definition.default_zone)
                if breakpoint == "mobile"
                else definition.default_zone
            )
        resolved_items.append(
            {
                **state,
                "indicator_key": definition.key,
                "zone": zone,
                "definition": asdict(definition),
            }
        )
        if revision > 1 and definition.key not in configured_keys:
            warnings.append(
                {
                    "code": "new_indicator",
                    "indicator_key": definition.key,
                    "message": "A newly registered indicator is using its compiled default.",
                }
            )
    canonical_items: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in resolved_items:
        definition = INDICATOR_REGISTRY[item["indicator_key"]]
        if definition.allow_duplicate:
            canonical_items.append(item)
        else:
            grouped.setdefault(definition.metric_identity, []).append(item)
    for metric_identity, metric_candidates in grouped.items():
        fixed_surface = FIXED_PAGE_METRICS.get(page, {}).get(metric_identity)
        if fixed_surface is not None:
            for duplicate in metric_candidates:
                warnings.append(
                    {
                        "code": "duplicate_metric_suppressed",
                        "indicator_key": duplicate["indicator_key"],
                        "metric_identity": metric_identity,
                        "canonical_surface": fixed_surface,
                        "message": (
                            f"{duplicate['definition']['default_label']} was suppressed because "
                            f"{fixed_surface} is the canonical page placement."
                        ),
                    }
                )
            continue
        ordered = sorted(
            metric_candidates,
            key=lambda item: (
                int(item["definition"]["canonical_priority"]),
                ZONE_PRIORITY.get(str(item.get("zone")), 0),
                -int(item.get("order", 0)),
                item["indicator_key"],
            ),
            reverse=True,
        )
        canonical_items.append(ordered[0])
        for duplicate in ordered[1:]:
            warnings.append(
                {
                    "code": "duplicate_metric_suppressed",
                    "indicator_key": duplicate["indicator_key"],
                    "metric_identity": metric_identity,
                    "kept_indicator_key": ordered[0]["indicator_key"],
                    "message": "A lower-priority duplicate metric placement was suppressed.",
                }
            )

    zones = []
    for zone in ZONES:
        items = sorted(
            (item for item in canonical_items if item["zone"] == zone),
            key=lambda item: (int(item.get("order", 0)), item["indicator_key"]),
        )
        if items:
            zones.append({"key": zone, "items": items})
    return {
        "schema_version": LAYOUT_SCHEMA_VERSION,
        "registry_version": REGISTRY_VERSION,
        "published_revision": revision,
        "page": page,
        "roles": sorted(roles),
        "breakpoint": breakpoint,
        "zones": zones,
        "warnings": warnings,
        "personalization_enabled": False,
    }


def _value(
    status: str,
    display_value: str,
    *,
    severity: str = "info",
    detail: str | None = None,
    freshness_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "severity": severity,
        "display_value": display_value,
        "detail": detail,
        "freshness_at": freshness_at,
    }


@dataclass(frozen=True)
class CurrentRateSnapshot:
    account_name: str
    plan_name: str
    version: int
    period: str
    price_per_kwh: Decimal
    currency: str
    local_time: str


def _period_label(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _money_rate(value: Decimal, currency: str) -> str:
    rendered = format(value, "f").rstrip("0").rstrip(".")
    if "." not in rendered:
        rendered = f"{rendered}.00"
    else:
        whole, fraction = rendered.split(".", 1)
        rendered = f"{whole}.{fraction.ljust(2, '0')}"
    prefix = "$" if currency.upper() == "USD" else f"{currency.upper()} "
    return f"{prefix}{rendered}/kWh"


def _local_rate_time(instant: datetime, engine: RateEngine) -> str:
    local = instant.astimezone(engine.zone)
    clock = local.strftime("%I:%M %p")
    if clock.startswith("0"):
        clock = clock[1:]
    zone = local.tzname() or str(engine.zone)
    return f"{local:%b} {local.day}, {local.year} at {clock} {zone}"


def _account_label(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{names[0]} + {len(names) - 1} more"


async def _current_rate_values(
    session: AsyncSession,
    *,
    selected_site_ids: set[str],
    devices: list[Device],
    now: datetime,
) -> dict[str, dict[str, Any]]:
    if not selected_site_ids:
        return {
            "rate.current_plan": _value(
                "unconfigured",
                "Not configured",
                severity="warning",
                detail="No permitted site is available",
            ),
            "rate.current_period": _value(
                "unconfigured",
                "Not configured",
                severity="warning",
                detail="Assign an active rate to a utility account",
            ),
            "rate.current_price": _value(
                "unconfigured",
                "Not configured",
                severity="warning",
                detail="Assign an active rate to a utility account",
            ),
        }

    all_accounts = list(
        await session.scalars(
            select(UtilityAccount)
            .where(
                UtilityAccount.site_id.in_(selected_site_ids),
                UtilityAccount.status == "active",
            )
            .order_by(UtilityAccount.name, UtilityAccount.id)
        )
    )
    accounts_by_site: dict[str, list[UtilityAccount]] = {}
    for account in all_accounts:
        accounts_by_site.setdefault(account.site_id, []).append(account)

    selected_account_ids = {
        device.utility_account_id for device in devices if device.utility_account_id
    }
    for device in devices:
        site_accounts = accounts_by_site.get(device.site_id, [])
        if device.utility_account_id is None and len(site_accounts) == 1:
            selected_account_ids.add(site_accounts[0].id)
    if not selected_account_ids:
        selected_account_ids = {account.id for account in all_accounts}
    accounts = [account for account in all_accounts if account.id in selected_account_ids]

    if not accounts:
        return {
            "rate.current_plan": _value(
                "unconfigured",
                "Not configured",
                severity="warning",
                detail="Create a utility account and assign an active rate",
            ),
            "rate.current_period": _value(
                "unconfigured",
                "Not configured",
                severity="warning",
                detail="No utility account is available for the selected scope",
            ),
            "rate.current_price": _value(
                "unconfigured",
                "Not configured",
                severity="warning",
                detail="No utility account is available for the selected scope",
            ),
        }

    account_ids = [account.id for account in accounts]
    assignments = list(
        await session.scalars(
            select(RateAssignment)
            .where(
                RateAssignment.utility_account_id.in_(account_ids),
                RateAssignment.effective_from <= now,
                or_(RateAssignment.effective_to.is_(None), RateAssignment.effective_to > now),
            )
            .order_by(
                RateAssignment.utility_account_id,
                RateAssignment.effective_from.desc(),
                RateAssignment.created_at.desc(),
            )
        )
    )
    assignment_by_account: dict[str, RateAssignment] = {}
    for assignment in assignments:
        assignment_by_account.setdefault(assignment.utility_account_id, assignment)

    version_ids = {assignment.rate_version_id for assignment in assignment_by_account.values()}
    version_ids.update(
        account.active_rate_version_id for account in accounts if account.active_rate_version_id
    )
    versions = {
        version.id: version
        for version in (
            list(await session.scalars(select(RateVersion).where(RateVersion.id.in_(version_ids))))
            if version_ids
            else []
        )
    }

    snapshots: list[CurrentRateSnapshot] = []
    unavailable: list[str] = []
    for account in accounts:
        current_assignment = assignment_by_account.get(account.id)
        version_id = (
            current_assignment.rate_version_id
            if current_assignment
            else account.active_rate_version_id
        )
        version = versions.get(version_id) if version_id else None
        if version is None:
            unavailable.append(f"{account.name}: no active rate")
            continue
        try:
            document = await version_document(session, version)
            engine = RateEngine(engine_plan(document))
            local_date = now.astimezone(engine.zone).date()
            if local_date < version.effective_from or (
                version.effective_to is not None and local_date > version.effective_to
            ):
                unavailable.append(f"{account.name}: rate is outside its effective dates")
                continue
            if version.pricing_model in {"tiered", "time_of_use_tiered"}:
                cycle = await current_billing_cycle(session, account, now, create=False)
                tier_status = await calculate_cycle_tier_status(
                    session, account, cycle, persist=False
                )
                if not tier_status["available"]:
                    unavailable.append(f"{account.name}: {tier_status['warnings'][0]}")
                    continue
                calculation = engine.calculate(
                    start=now,
                    end=now + timedelta(seconds=1),
                    energy_kwh=Decimal("0"),
                    cumulative_usage_before_kwh=Decimal(
                        str(tier_status["authoritative_usage_kwh"])
                    ),
                    cycle_start=cycle.starts_at,
                    cycle_end=cycle.ends_at,
                )
                current_slice = calculation.slices[0]
                period = current_slice.bucket
                price = current_slice.price_per_kwh
            else:
                period, price = engine.period_at(now)
        except (KeyError, RuntimeError, ValueError) as error:
            unavailable.append(f"{account.name}: rate cannot be evaluated ({type(error).__name__})")
            continue
        snapshots.append(
            CurrentRateSnapshot(
                account_name=account.name,
                plan_name=document.plan_name,
                version=version.version,
                period=_period_label(period),
                price_per_kwh=price,
                currency=document.currency,
                local_time=_local_rate_time(now, engine),
            )
        )

    account_names = [account.name for account in accounts]
    if not snapshots:
        unavailable_detail = "; ".join(unavailable) or "No effective rate assignment"
        return {
            "rate.current_plan": _value(
                "unconfigured",
                _account_label(account_names),
                severity="warning",
                detail=unavailable_detail,
            ),
            "rate.current_period": _value(
                "unavailable", "Unavailable", severity="warning", detail=unavailable_detail
            ),
            "rate.current_price": _value(
                "unavailable", "Unavailable", severity="warning", detail=unavailable_detail
            ),
        }

    snapshot_names = [snapshot.account_name for snapshot in snapshots]
    plan_detail = "; ".join(
        f"{snapshot.account_name}: {snapshot.plan_name} v{snapshot.version}"
        for snapshot in snapshots
    )
    if unavailable:
        plan_detail = f"{plan_detail}; {'; '.join(unavailable)}"

    period_values = {snapshot.period for snapshot in snapshots}
    period_display = next(iter(period_values)) if len(period_values) == 1 else "Multiple periods"
    period_detail = "; ".join(
        f"{snapshot.account_name}: {snapshot.period} ({snapshot.local_time})"
        for snapshot in snapshots
    )

    price_values = {(snapshot.price_per_kwh, snapshot.currency.upper()) for snapshot in snapshots}
    price_display = (
        _money_rate(snapshots[0].price_per_kwh, snapshots[0].currency)
        if len(price_values) == 1
        else "Multiple rates"
    )
    price_detail = "; ".join(
        f"{snapshot.account_name}: "
        f"{_money_rate(snapshot.price_per_kwh, snapshot.currency)} "
        f"during {snapshot.period} ({snapshot.local_time})"
        for snapshot in snapshots
    )
    plan_values = {snapshot.plan_name for snapshot in snapshots}
    plan_display = next(iter(plan_values)) if len(plan_values) == 1 else "Multiple plans"
    context_display = f"{plan_display} · {period_display} · {price_display}"

    return {
        "rate.current_plan": _value(
            "configured",
            _account_label(snapshot_names),
            severity="success" if not unavailable else "warning",
            detail=plan_detail,
            freshness_at=now,
        ),
        "rate.current_period": _value(
            "current",
            period_display,
            severity="success" if not unavailable else "warning",
            detail=period_detail,
            freshness_at=now,
        ),
        "rate.current_price": _value(
            "current",
            price_display,
            severity="success" if not unavailable else "warning",
            detail=price_detail,
            freshness_at=now,
        ),
        "rate.current_context": _value(
            "current",
            context_display,
            severity="success" if not unavailable else "warning",
            detail=f"{plan_detail}; {period_detail}; {price_detail}",
            freshness_at=now,
        ),
    }


async def status_values(
    session: AsyncSession,
    *,
    settings: Settings,
    permissions: set[str] | frozenset[str],
    allowed_site_ids: set[str],
    all_sites: bool,
    site_id: str | None = None,
    device_id: str | None = None,
    requested_keys: set[str] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    values: dict[str, Any] = {}

    def permitted(key: str) -> bool:
        return INDICATOR_REGISTRY[key].permission_required in permissions and (
            requested_keys is None or key in requested_keys
        )

    if permitted("system.api_health"):
        values["system.api_health"] = _value(
            "healthy",
            "Healthy",
            severity="success",
            detail="Authenticated API request succeeded",
            freshness_at=now,
        )
    if permitted("system.database_health"):
        values["system.database_health"] = _value(
            "healthy",
            "Healthy",
            severity="success",
            detail="PostgreSQL query succeeded",
            freshness_at=now,
        )

    site_query = select(Site)
    if site_id:
        if not all_sites and site_id not in allowed_site_ids:
            raise ProblemError(404, "Site not found", "The site does not exist", "site_not_found")
        site_query = site_query.where(Site.id == site_id)
    elif not all_sites:
        site_query = site_query.where(Site.id.in_(allowed_site_ids))
    sites = list(await session.scalars(site_query.order_by(Site.name)))
    if site_id and not sites:
        raise ProblemError(404, "Site not found", "The site does not exist", "site_not_found")
    selected_site_ids = {item.id for item in sites}
    if permitted("site.current"):
        site_label = sites[0].name if len(sites) == 1 else f"{len(sites)} permitted sites"
        values["site.current"] = _value(
            "current", site_label, detail="Site selector remains available", freshness_at=None
        )

    device_query = select(Device)
    if device_id:
        device_query = device_query.where(Device.id == device_id)
    else:
        device_query = device_query.where(Device.lifecycle_status == "active")
    if selected_site_ids:
        device_query = device_query.where(Device.site_id.in_(selected_site_ids))
    elif site_id or not all_sites:
        device_query = device_query.where(Device.id == "")
    devices = list(await session.scalars(device_query))
    if device_id and not devices:
        raise ProblemError(404, "Device not found", "The device does not exist", "device_not_found")
    measurements, latest, _latest_readings = await load_latest_measurements(
        session, devices, settings, now=now
    )
    online_states = {
        "online_synchronized",
        "online_with_backlog",
        "online_push_only",
        "api_healthy_meter_failed",
        "api_healthy_storage_failed",
        "time_unsynchronized",
    }
    online = sum(item.status in online_states for item in devices)
    synchronized = sum(item.status == "online_synchronized" for item in devices)
    total = len(devices)
    offline = total - online
    newest_heartbeat = max((item.received_at for item in latest.values()), default=None)
    reporting = {
        item.id: measurements[item.id]
        for item in devices
        if measurements[item.id].freshness_state == "live"
        and measurements[item.id].power_watts is not None
    }
    aggregate_selection = await resolve_home_aggregate_devices(session, devices)
    included_devices = list(aggregate_selection.devices)
    contributing = {
        item.id: reporting[item.id] for item in included_devices if item.id in reporting
    }
    reporting_count = len(reporting)
    newest_measurement = max(
        (item.measured_at for item in contributing.values() if item.measured_at),
        default=None,
    )
    watts = (
        sum(
            (
                item.power_watts if item.power_watts is not None else Decimal("0")
                for item in contributing.values()
            ),
            Decimal("0"),
        )
        if contributing
        else None
    )
    pzem_failures = sum(not item.pzem_ok for item in latest.values())
    sd_failures = sum(not item.sd_ok for item in latest.values())
    time_trusted = sum(item.time_trusted for item in latest.values())
    backlog = sum(item.backlog_estimate for item in latest.values())
    rssi = [item.rssi_dbm for item in latest.values() if item.rssi_dbm is not None]
    if permitted("data.live_connection"):
        values["data.live_connection"] = _value(
            "healthy" if contributing else "attention" if total else "unavailable",
            "Live" if contributing else "Waiting" if total else "No sensors",
            severity="success" if contributing else "warning" if total else "unknown",
            detail=f"{reporting_count} of {total} devices reporting measurements",
            freshness_at=newest_measurement,
        )
    if permitted("data.current_power"):
        values["data.current_power"] = _value(
            "current" if watts is not None else "unavailable",
            f"{watts.quantize(Decimal('1'))} W" if watts is not None else "Unavailable",
            severity="info" if watts is not None else "unknown",
            detail=(
                "Latest valid contributing measurements"
                if watts is not None
                else "Waiting for a valid measurement"
            ),
            freshness_at=newest_measurement,
        )
    device_value_map = {
        "device.online_count": _value(
            "healthy" if online == total and total else "attention",
            str(online),
            severity="success" if online == total and total else "warning",
            detail=f"of {total} enrolled sensors",
            freshness_at=newest_heartbeat,
        ),
        "device.offline_count": _value(
            "healthy" if offline == 0 else "attention",
            str(offline),
            severity="success" if offline == 0 else "warning",
            detail="offline or stale",
            freshness_at=newest_heartbeat,
        ),
        "device.synchronized_count": _value(
            "healthy" if synchronized == total and total else "attention",
            f"{synchronized}/{total}",
            severity="success" if synchronized == total and total else "warning",
            detail="backlog clear",
            freshness_at=newest_heartbeat,
        ),
        "device.pzem_health": _value(
            "healthy" if pzem_failures == 0 else "failed",
            "Healthy" if pzem_failures == 0 else f"{pzem_failures} failed",
            severity="success" if pzem_failures == 0 else "critical",
            freshness_at=newest_heartbeat,
        ),
        "device.sd_health": _value(
            "healthy" if sd_failures == 0 else "failed",
            "Healthy" if sd_failures == 0 else f"{sd_failures} failed",
            severity="success" if sd_failures == 0 else "critical",
            freshness_at=newest_heartbeat,
        ),
        "device.sync_backlog": _value(
            "healthy" if backlog == 0 else "attention",
            str(backlog),
            severity="success" if backlog == 0 else "warning",
            detail="readings pending",
            freshness_at=newest_heartbeat,
        ),
        "device.time_sync": _value(
            "healthy" if time_trusted == total and total else "attention",
            f"{time_trusted}/{total}",
            severity="success" if time_trusted == total and total else "warning",
            detail="trusted clocks",
            freshness_at=newest_heartbeat,
        ),
        "device.wifi_signal": _value(
            "current",
            f"{round(sum(rssi) / len(rssi))} dBm" if rssi else "Unavailable",
            severity="info" if rssi else "unknown",
            detail="average current RSSI",
            freshness_at=newest_heartbeat,
        ),
        "device.heartbeat_freshness": _value(
            "current" if newest_heartbeat else "unknown",
            newest_heartbeat.isoformat() if newest_heartbeat else "Never",
            severity="info" if newest_heartbeat else "unknown",
            freshness_at=newest_heartbeat,
        ),
        "data.aggregate_coverage": _value(
            "healthy"
            if reporting_count == total and total
            else "partial"
            if total
            else "unavailable",
            f"{round((reporting_count / total) * 100)}%" if total else "Unavailable",
            severity=(
                "success"
                if reporting_count == total and total
                else "warning"
                if total
                else "unknown"
            ),
            detail=f"{reporting_count} of {total} reporting measurements",
            freshness_at=newest_measurement,
        ),
    }
    for key, value in device_value_map.items():
        if permitted(key):
            values[key] = value

    included_device_ids = [item.id for item in included_devices]
    summary_zone = ZoneInfo(sites[0].timezone) if len(sites) == 1 else UTC
    local_start = datetime.combine(
        now.astimezone(summary_zone).date(), datetime.min.time(), summary_zone
    ).astimezone(UTC)
    energy_wh: Decimal | None = None
    if included_device_ids and permitted("data.energy_today"):
        energy_wh = await session.scalar(
            select(func.sum(NormalizedInterval.selected_energy_wh)).where(
                NormalizedInterval.device_id.in_(included_device_ids),
                NormalizedInterval.interval_start >= local_start,
            )
        )
    if permitted("data.energy_today"):
        values["data.energy_today"] = _value(
            "current" if energy_wh is not None else "unavailable",
            f"{(Decimal(str(energy_wh)) / Decimal('1000')).quantize(Decimal('0.001'))} kWh"
            if energy_wh is not None
            else "Unavailable",
            severity="info" if energy_wh is not None else "unknown",
            detail="Since local midnight" if energy_wh is not None else "No readings today",
            freshness_at=newest_measurement,
        )
    if permitted("data.recent_peak"):
        values["data.recent_peak"] = _value(
            "current" if watts is not None else "unavailable",
            f"{watts.quantize(Decimal('1'))} W" if watts is not None else "Unavailable",
            severity="info" if watts is not None else "unknown",
            detail="Most recent aggregate live measurement",
            freshness_at=newest_measurement,
        )

    cost_keys = {"cost.today", "energy.billing_cycle", "cost.billing_cycle_estimate"}
    if any(permitted(key) for key in cost_keys):
        run_query = select(CostCalculationRun).where(CostCalculationRun.status == "completed")
        if selected_site_ids:
            run_query = run_query.join(
                AggregateSet, AggregateSet.id == CostCalculationRun.aggregate_set_id
            ).where(AggregateSet.site_id.in_(selected_site_ids))
        latest_run = await session.scalar(
            run_query.order_by(CostCalculationRun.completed_at.desc()).limit(1)
        )
        result_rows = (
            list(
                await session.scalars(
                    select(CostIntervalResult).where(CostIntervalResult.run_id == latest_run.id)
                )
            )
            if latest_run
            else []
        )
        energy_rows = [row for row in result_rows if row.component == "energy"]
        today_cost = sum(
            (row.unrounded_cost for row in energy_rows if row.interval_start >= local_start),
            Decimal("0"),
        )
        cycle_energy = sum((row.energy_kwh for row in energy_rows), Decimal("0"))
        cycle_cost = sum((row.unrounded_cost for row in result_rows), Decimal("0"))
        calculated_at = latest_run.completed_at if latest_run else None
        cost_values = {
            "cost.today": _value(
                "current" if latest_run else "unavailable",
                f"${today_cost.quantize(Decimal('0.01'))}" if latest_run else "Unavailable",
                severity="info" if latest_run else "unknown",
                detail="Estimated monitored energy cost; not a utility bill",
                freshness_at=calculated_at,
            ),
            "energy.billing_cycle": _value(
                "current" if latest_run else "unavailable",
                f"{cycle_energy.quantize(Decimal('0.001'))} kWh" if latest_run else "Unavailable",
                severity="info" if latest_run else "unknown",
                detail="Current calculation cycle",
                freshness_at=calculated_at,
            ),
            "cost.billing_cycle_estimate": _value(
                "current" if latest_run else "unavailable",
                f"${cycle_cost.quantize(Decimal('0.01'))}" if latest_run else "Unavailable",
                severity="info" if latest_run else "unknown",
                detail="Estimated monitored cost; not a utility bill",
                freshness_at=calculated_at,
            ),
        }
        for key, value in cost_values.items():
            if permitted(key):
                values[key] = value

    alert_value_keys = {
        "alerts.active_count",
        "alerts.critical_count",
        "alerts.warning_count",
        "alerts.enabled_rule_count",
        "alerts.disconnect_rule_state",
        "notifications.delivery_health",
    }
    active_alerts = 0
    alert_counts: dict[str, int] = {}
    enabled_rules = 0
    disconnect_rules = 0
    if any(permitted(key) for key in alert_value_keys):
        alert_query = (
            select(AlertInstance.severity, func.count(AlertInstance.id))
            .where(AlertInstance.status == "active")
            .group_by(AlertInstance.severity)
        )
        rule_query = select(AlertRule).where(AlertRule.enabled.is_(True))
        if site_id or not all_sites:
            alert_scope = or_(
                AlertInstance.site_id.is_(None),
                AlertInstance.site_id.in_(selected_site_ids),
            )
            rule_scope = or_(AlertRule.site_id.is_(None), AlertRule.site_id.in_(selected_site_ids))
            alert_query = alert_query.where(alert_scope)
            rule_query = rule_query.where(rule_scope)
        alert_counts = {
            str(severity): int(count)
            for severity, count in (await session.execute(alert_query)).all()
        }
        active_alerts = sum(alert_counts.values())
        rules = list(await session.scalars(rule_query))
        enabled_rules = len(rules)
        disconnect_rules = sum(rule.rule_type == "heartbeat_stale" for rule in rules)
    if permitted("alerts.active_count"):
        values["alerts.active_count"] = _value(
            "healthy" if active_alerts == 0 else "attention",
            str(active_alerts),
            severity="success" if active_alerts == 0 else "warning",
            detail="open alerts",
            freshness_at=now,
        )
    alert_summary_values = {
        "alerts.critical_count": _value(
            "healthy" if alert_counts.get("critical", 0) == 0 else "critical",
            str(alert_counts.get("critical", 0)),
            severity="success" if alert_counts.get("critical", 0) == 0 else "critical",
            detail="active critical alerts",
            freshness_at=now,
        ),
        "alerts.warning_count": _value(
            "healthy" if alert_counts.get("warning", 0) == 0 else "warning",
            str(alert_counts.get("warning", 0)),
            severity="success" if alert_counts.get("warning", 0) == 0 else "warning",
            detail="active warning alerts",
            freshness_at=now,
        ),
        "alerts.enabled_rule_count": _value(
            "configured" if enabled_rules else "attention",
            str(enabled_rules),
            severity="success" if enabled_rules else "warning",
            detail="enabled alert rules",
            freshness_at=now,
        ),
        "alerts.disconnect_rule_state": _value(
            "healthy" if disconnect_rules else "disabled",
            "On" if disconnect_rules else "Off",
            severity="success" if disconnect_rules else "warning",
            detail="signed-heartbeat disconnect rules",
            freshness_at=now,
        ),
    }
    for key, value in alert_summary_values.items():
        if permitted(key):
            values[key] = value

    worker = await session.scalar(
        select(WorkerState).order_by(WorkerState.last_loop_at.desc()).limit(1)
    )
    if permitted("system.worker_health"):
        worker_last_success = worker.last_success_at if worker else None
        if worker_last_success is not None and worker_last_success.tzinfo is None:
            worker_last_success = worker_last_success.replace(tzinfo=UTC)
        worker_stale = worker_last_success is None or worker_last_success < now - timedelta(
            seconds=45
        )
        worker_healthy = bool(worker and worker.status == "healthy" and not worker_stale)
        worker_status = "unknown" if worker is None else "stale" if worker_stale else worker.status
        values["system.worker_health"] = _value(
            worker_status,
            worker_status.replace("_", " ").title() if worker else "Unavailable",
            severity=("success" if worker_healthy else "critical" if worker else "warning"),
            detail="Background device and notification worker",
            freshness_at=worker.last_loop_at if worker else None,
        )

    rate_config = await session.get(RateSyncConfiguration, "default")
    rate_sources = list(await session.scalars(select(RateSource)))
    enabled_sources = [source for source in rate_sources if source.enabled]
    healthy_sources = [source for source in enabled_sources if source.consecutive_failures == 0]
    pending_candidates = int(
        await session.scalar(
            select(func.count(RateChangeCandidate.id)).where(
                RateChangeCandidate.status == "pending_review"
            )
        )
        or 0
    )
    current_rate_values = await _current_rate_values(
        session,
        selected_site_ids=selected_site_ids,
        devices=devices,
        now=now,
    )
    if "rate.current_context" not in current_rate_values:
        plan_value = current_rate_values["rate.current_plan"]
        period_value = current_rate_values["rate.current_period"]
        price_value = current_rate_values["rate.current_price"]
        current_rate_values["rate.current_context"] = _value(
            "unconfigured",
            "Not configured",
            severity="warning",
            detail="; ".join(
                str(value.get("detail"))
                for value in (plan_value, period_value, price_value)
                if value.get("detail")
            ),
        )
    rate_values = {
        **current_rate_values,
        "rate.source_health": _value(
            (
                "healthy"
                if rate_config
                and not rate_config.last_error
                and len(healthy_sources) == len(enabled_sources)
                and enabled_sources
                else "attention"
            ),
            f"{len(healthy_sources)}/{len(enabled_sources)} healthy",
            severity=(
                "success"
                if rate_config
                and not rate_config.last_error
                and len(healthy_sources) == len(enabled_sources)
                and enabled_sources
                else "warning"
            ),
            detail=(
                rate_config.last_error
                if rate_config and rate_config.last_error
                else "Approved enabled rate sources"
            ),
            freshness_at=rate_config.last_attempted_run if rate_config else None,
        ),
        "rate.update_pending": _value(
            "healthy" if pending_candidates == 0 else "attention",
            str(pending_candidates),
            severity="success" if pending_candidates == 0 else "warning",
            detail="candidates awaiting review",
        ),
        "rate.last_successful_check": _value(
            "current" if rate_config and rate_config.last_successful_run else "unknown",
            rate_config.last_successful_run.isoformat()
            if rate_config and rate_config.last_successful_run
            else "Never",
            freshness_at=rate_config.last_successful_run if rate_config else None,
        ),
        "rate.next_scheduled_check": _value(
            "scheduled" if rate_config and rate_config.next_scheduled_run else "unknown",
            rate_config.next_scheduled_run.isoformat()
            if rate_config and rate_config.next_scheduled_run
            else "Not scheduled",
            freshness_at=rate_config.next_scheduled_run if rate_config else None,
        ),
        "rate.review_policy": _value(
            "configured",
            (
                rate_config.approval_mode.replace("_", " ").title()
                if rate_config
                else "Manual review"
            ),
        ),
    }
    for key, value in rate_values.items():
        if permitted(key):
            values[key] = value

    latest_backup = (
        await session.scalar(select(BackupRun).order_by(BackupRun.started_at.desc()).limit(1))
        if (permitted("backup.last_result") or permitted("backup.verification"))
        else None
    )
    if permitted("backup.last_result"):
        values["backup.last_result"] = _value(
            latest_backup.status if latest_backup else "unknown",
            latest_backup.status.replace("_", " ").title() if latest_backup else "Never",
            severity="success"
            if latest_backup and latest_backup.status == "completed"
            else "warning",
            freshness_at=latest_backup.completed_at if latest_backup else None,
        )
    if permitted("backup.verification"):
        verified = bool(latest_backup and latest_backup.verified_at)
        values["backup.verification"] = _value(
            "healthy" if verified else "attention",
            "Verified" if verified else "Not verified",
            severity="success" if verified else "warning",
            freshness_at=latest_backup.verified_at if latest_backup else None,
        )

    latest_delivery = (
        await session.scalar(
            select(NotificationAttempt).order_by(NotificationAttempt.attempted_at.desc()).limit(1)
        )
        if permitted("notifications.delivery_health")
        else None
    )
    if permitted("notifications.delivery_health"):
        delivery_ok = bool(
            latest_delivery and latest_delivery.status in {"sent", "delivered", "success"}
        )
        values["notifications.delivery_health"] = _value(
            latest_delivery.status if latest_delivery else "unknown",
            latest_delivery.status.title() if latest_delivery else "No attempts",
            severity="success" if delivery_ok else "warning",
            freshness_at=latest_delivery.attempted_at if latest_delivery else None,
        )

    if permitted("enrollment.availability"):
        active_tokens = int(
            await session.scalar(
                select(func.count(EnrollmentToken.id)).where(
                    EnrollmentToken.expires_at > now,
                    EnrollmentToken.consumed_at.is_(None),
                    EnrollmentToken.revoked_at.is_(None),
                )
            )
            or 0
        )
        values["enrollment.availability"] = _value(
            "available" if active_tokens else "idle",
            str(active_tokens),
            severity="info",
            detail="unclaimed active tokens",
        )
    if permitted("firmware.update_state"):
        pending_firmware = int(
            await session.scalar(
                select(func.count(FirmwareDeployment.id)).where(
                    FirmwareDeployment.status.in_(
                        {"scheduled", "downloading", "installing", "validating"}
                    )
                )
            )
            or 0
        )
        values["firmware.update_state"] = _value(
            "healthy" if pending_firmware == 0 else "pending",
            str(pending_firmware),
            severity="info",
            detail="deployments in progress",
        )
    if permitted("topology.aggregate_overlap"):
        # Overlap confirmation is already enforced by the topology and History services.
        values["topology.aggregate_overlap"] = _value(
            "current",
            "See topology",
            detail="Explicit overlap warnings remain visible in affected workflows",
        )

    allowed_keys = {
        item.key
        for item in INDICATOR_DEFINITIONS
        if item.permission_required in permissions
        and (requested_keys is None or item.key in requested_keys)
    }
    return {
        "registry_version": REGISTRY_VERSION,
        "generated_at": now,
        "values": {key: value for key, value in values.items() if key in allowed_keys},
    }
