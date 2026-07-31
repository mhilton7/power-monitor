# ruff: noqa: E501
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AlertInstance,
    AlertRule,
    Device,
    NotificationAttempt,
    NotificationChannel,
    NotificationEvent,
    NotificationSuppression,
    Site,
    User,
)
from app.schemas import NotificationView


@dataclass(frozen=True)
class NotificationCatalogEntry:
    category: str
    severity: Literal["info", "warning", "error", "critical"]
    title: str
    summary: str
    impact: str
    remediation_summary: str
    remediation_steps: tuple[str, ...]
    automatic_recovery: str | None = None
    action_label: str | None = None
    action_target: str | None = None
    action_permissions: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    permanently_suppressible: bool = False


def _entry(
    category: str,
    severity: Literal["info", "warning", "error", "critical"],
    title: str,
    summary: str,
    impact: str,
    remediation_summary: str,
    *steps: str,
    automatic_recovery: str | None = None,
    action_label: str | None = None,
    action_target: str | None = None,
    action_permissions: tuple[str, ...] = (),
    required_evidence: tuple[str, ...] = (),
    permanently_suppressible: bool = False,
) -> NotificationCatalogEntry:
    return NotificationCatalogEntry(
        category=category,
        severity=severity,
        title=title,
        summary=summary,
        impact=impact,
        remediation_summary=remediation_summary,
        remediation_steps=steps,
        automatic_recovery=automatic_recovery,
        action_label=action_label,
        action_target=action_target,
        action_permissions=action_permissions,
        required_evidence=required_evidence,
        permanently_suppressible=permanently_suppressible,
    )


NOTIFICATION_CATALOG: dict[str, NotificationCatalogEntry] = {
    "heartbeat_stale": _entry(
        "connectivity",
        "error",
        "{resource} stopped reporting",
        "No signed heartbeat has been received from {resource} within the configured {threshold} second window.",
        "Live values may be stale. Readings retained on the sensor can synchronize when connectivity returns.",
        "Check sensor power and network access.",
        "Confirm the sensor has power.",
        "Review Wi-Fi signal, DNS, time synchronization, and the last connection error.",
        automatic_recovery="The sensor retries heartbeats and retained-reading uploads after connectivity returns.",
        action_label="Open sensor details",
        action_target="/settings/sensors",
        action_permissions=("devices.view",),
        required_evidence=("stale_after_seconds",),
    ),
    "device_api_unreachable": _entry(
        "connectivity",
        "error",
        "{resource} device API is unreachable",
        "The server could not contact {resource} at its most recently signed address.",
        "Server-initiated diagnostics and pull synchronization are unavailable; signed push ingestion can continue.",
        "Review the sensor network path.",
        "Confirm the signed heartbeat contains the current address.",
        "Check VLAN routing and the server-to-device network policy.",
        action_label="Review network policy",
        action_target="/settings/advanced/network",
        action_permissions=("network_policy.view",),
    ),
    "authentication_failure": _entry(
        "security",
        "critical",
        "Signed sensor authentication failed",
        "Power Monitor rejected one or more signed requests for {resource}.",
        "Untrusted requests are blocked and no rejected readings enter monitoring or history.",
        "Verify the enrolled sensor credential and clock.",
        "Confirm the sensor UUID matches its enrollment.",
        "Re-enroll only if credential recovery is required.",
        action_label="Review sensors",
        action_target="/settings/sensors",
        action_permissions=("devices.view",),
    ),
    "protocol_incompatible": _entry(
        "security",
        "critical",
        "{resource} uses an incompatible protocol",
        "The sensor protocol does not match pm-protocol/1.0.0.",
        "Heartbeats or readings cannot be safely interpreted until compatible firmware is installed.",
        "Install a compatible signed firmware release.",
        "Review the sensor firmware and protocol versions.",
        "Deploy a release compatible with pm-protocol/1.0.0.",
        action_label="Open firmware",
        action_target="/settings/advanced/firmware",
        action_permissions=("firmware.view",),
    ),
    "pzem_failure": _entry(
        "meter",
        "error",
        "{resource} cannot read the energy meter",
        "The sensor reports repeated PZEM measurement failures.",
        "Live electrical measurements and new energy readings are unavailable; stored history is preserved.",
        "Inspect the PZEM meter and serial connection.",
        "Check PZEM power and RX/TX wiring.",
        "Open the sensor local Diagnostics page and verify valid meter responses.",
        action_label="Open sensor details",
        action_target="/settings/sensors",
        action_permissions=("devices.view",),
    ),
    "no_valid_reading": _entry(
        "meter",
        "error",
        "{resource} has no valid electrical reading",
        "Recent measurement payloads did not contain a valid power reading.",
        "The sensor may remain connected while live totals and History exclude invalid readings.",
        "Review measurement validation evidence.",
        "Check PZEM communication and configured CT limits.",
        "Correct invalid voltage, current, frequency, or power-factor values.",
        action_label="Open sensor details",
        action_target="/settings/sensors",
        action_permissions=("devices.view",),
    ),
    "sd_failure": _entry(
        "storage",
        "critical",
        "{resource} cannot write history to microSD",
        "The sensor reports that durable local storage is unavailable or not writable.",
        "Live data may continue, but pending history may not survive a reboot.",
        "Inspect or replace the microSD card.",
        "Power down the sensor safely.",
        "Check card seating, filesystem health, free space, and write access.",
        action_label="Open sensor details",
        action_target="/settings/sensors",
        action_permissions=("devices.view",),
    ),
    "sync_backlog": _entry(
        "history",
        "warning",
        "{resource} has readings waiting to synchronize",
        "The sensor has {observed} retained readings that the server has not acknowledged.",
        "Live data may be current while History remains incomplete.",
        "Keep the sensor and server connected while backlog drains.",
        "Review the acknowledged and newest sequence values.",
        "Check connectivity if the backlog is not decreasing.",
        automatic_recovery="The sensor uploads retained readings in bounded batches after connectivity returns.",
        action_label="Open History",
        action_target="/history",
        action_permissions=("history.view",),
    ),
    "sequence_gap": _entry(
        "history",
        "error",
        "History gap detected for {resource}",
        "Power Monitor detected {observed} missing sequence records.",
        "History coverage is incomplete for the affected interval; gaps remain visible and are not replaced with zero.",
        "Allow the sensor to retry retained records.",
        "Keep the sensor online while synchronization retries.",
        "Inspect microSD health if the sequence remains unavailable.",
        automatic_recovery="The synchronization worker requests missing retained sequences when they remain available.",
        action_label="Open History",
        action_target="/history",
        action_permissions=("history.view",),
    ),
    "time_untrusted": _entry(
        "data_integrity",
        "error",
        "{resource} does not trust its clock",
        "The sensor reported untrusted measurement time.",
        "Measurements cannot be assigned safely to History or time-of-use billing periods.",
        "Restore sensor NTP synchronization.",
        "Check DNS and NTP access.",
        "Confirm the sensor timezone and certificate-validity clock.",
        action_label="Open sensor details",
        action_target="/settings/sensors",
        action_permissions=("devices.view",),
    ),
    "low_rssi": _entry(
        "connectivity",
        "warning",
        "{resource} has a weak Wi-Fi signal",
        "Current signal is {observed}; the recommended minimum is {expected}.",
        "Heartbeats and History synchronization may be delayed.",
        "Improve 2.4 GHz Wi-Fi coverage near the sensor.",
        "Move the access point closer or reduce obstructions.",
        "Add a nearby 2.4 GHz access point if needed.",
        action_label="Open sensor details",
        action_target="/settings/sensors",
        action_permissions=("devices.view",),
    ),
    "power_surge": _entry(
        "electrical",
        "critical",
        "Power exceeded the configured limit",
        "{resource} measured {observed}, above the configured {expected} limit.",
        "Sustained high demand may indicate an electrical load or configuration requiring prompt review.",
        "Inspect the active loads and measurement configuration.",
        "Turn off unexpected high-demand equipment when safe.",
        "Contact a qualified electrician for unexplained sustained demand.",
        action_label="Open Home",
        action_target="/",
        action_permissions=("overview.view",),
    ),
    "ct_utilization": _entry(
        "electrical",
        "warning",
        "{resource} is near its configured CT limit",
        "Measured current is approaching the configured current-transformer rating.",
        "Measurements may clip or become inaccurate above the configured CT range.",
        "Verify the CT rating and installation.",
        "Confirm the configured amp rating matches the installed CT.",
        "Use a correctly rated CT if the load regularly exceeds its range.",
        action_label="Open sensor details",
        action_target="/settings/sensors",
        action_permissions=("devices.view",),
    ),
    "voltage_frequency_range": _entry(
        "electrical",
        "error",
        "Voltage or frequency exceeded the configured range",
        "{resource} reported {observed}, outside the configured {expected} range.",
        "This may indicate utility variation, wiring problems, or an incorrect measurement configuration.",
        "Review the electrical reading and configured range.",
        "Confirm the measurement with an appropriate instrument.",
        "Contact a qualified electrician or utility for persistent unsafe readings.",
        action_label="Open sensor details",
        action_target="/settings/sensors",
        action_permissions=("devices.view",),
    ),
    "reboot_loop": _entry(
        "sensor",
        "error",
        "{resource} is restarting repeatedly",
        "The sensor restart count exceeded the configured stability threshold.",
        "Live monitoring and synchronization may be interrupted repeatedly.",
        "Review reset reason, power supply, and firmware diagnostics.",
        "Use a stable power supply.",
        "Inspect crash/reset evidence and installed firmware.",
        action_label="Open sensor details",
        action_target="/settings/sensors",
        action_permissions=("devices.view",),
    ),
    "firmware_failure": _entry(
        "firmware",
        "critical",
        "{resource} did not complete its firmware update",
        "The signed firmware deployment stopped before validation completed.",
        "The sensor continues running its prior signed firmware when rollback succeeds.",
        "Review the deployment stage and signed artifact.",
        "Confirm artifact checksum, signing key ID, and hardware target.",
        "Retry only after resolving the reported stage failure.",
        action_label="Open firmware",
        action_target="/settings/advanced/firmware",
        action_permissions=("firmware.view",),
    ),
    "firmware_failed": _entry(
        "firmware",
        "critical",
        "{resource} did not complete its firmware update",
        "The signed firmware deployment stopped before validation completed.",
        "The sensor continues running its prior signed firmware when rollback succeeds.",
        "Review the deployment stage and signed artifact.",
        "Confirm artifact checksum, signing key ID, and hardware target.",
        "Retry only after resolving the reported stage failure.",
        action_label="Open firmware",
        action_target="/settings/advanced/firmware",
        action_permissions=("firmware.view",),
    ),
    "server_failure": _entry(
        "server",
        "critical",
        "Power Monitor server health check failed",
        "A required server component is unavailable or unhealthy.",
        "Monitoring ingestion, History processing, or dashboard updates may be delayed.",
        "Review System Health and container status.",
        "Open System Health for the failing component and timestamp.",
        "Restore the unhealthy service without deleting data.",
        action_label="Open System Health",
        action_target="/settings/advanced/system-health",
        action_permissions=("system_health.view",),
    ),
    "worker_failure": _entry(
        "server",
        "critical",
        "Power Monitor worker is unhealthy",
        "The asynchronous worker has not completed a healthy loop within the configured threshold.",
        "History synchronization, alerts, backups, and background processing may be delayed.",
        "Review worker health and logs.",
        "Open System Health and application logs.",
        "Restart the worker only after preserving diagnostic evidence.",
        action_label="Open System Health",
        action_target="/settings/advanced/system-health",
        action_permissions=("system_health.view",),
    ),
    "backup_failure": _entry(
        "backup",
        "critical",
        "Backup verification failed",
        "The latest logical backup failed a required creation or verification stage.",
        "The affected backup is not eligible for Restore.",
        "Create and verify a replacement backup.",
        "Review the failed stage and safe error evidence.",
        "Do not delete the newest verified backup.",
        action_label="Open Backups",
        action_target="/settings/data",
        action_permissions=("backups.view",),
    ),
    "recommendation.smtp_not_configured": _entry(
        "delivery",
        "info",
        "Email notifications are not configured",
        "Power Monitor continues showing dashboard alerts, but it cannot send them by email.",
        "Dashboard monitoring and alerts continue normally; only optional email delivery is unavailable.",
        "Set up email if you want off-dashboard delivery.",
        "Add an SMTP channel and send a test email.",
        action_label="Set up email",
        action_target="/settings/notifications?setup=smtp",
        action_permissions=("alerts.manage_delivery",),
        permanently_suppressible=True,
    ),
    "notification_delivery_failed": _entry(
        "delivery",
        "error",
        "Email delivery failed",
        "{resource} could not deliver the related Power Monitor notification.",
        "The original alert remains available in the dashboard. Only external delivery was affected.",
        "Review the safe delivery stage and channel configuration.",
        "Confirm the SMTP host, port, and TLS mode.",
        "Verify the configured sender and recipients, then send a test email.",
        action_label="Review email delivery",
        action_target="/settings/notifications?setup=smtp",
        action_permissions=("alerts.manage_delivery",),
    ),
}

for _rate_code, _rate_title, _rate_severity in (
    ("rate_check_succeeded", "Rate source check completed", "info"),
    ("rate_source_changed", "A managed rate source changed", "warning"),
    ("rate_candidate_pending", "A rate update is ready for review", "warning"),
    ("rate_candidate_validation_failed", "Rate candidate validation failed", "error"),
    ("rate_source_unavailable", "A managed rate source is unavailable", "error"),
    ("rate_candidate_approved", "Rate candidate approved", "info"),
    ("rate_candidate_rejected", "Rate candidate rejected", "info"),
    ("rate_parser_failed", "Rate source parsing failed", "error"),
    ("rate_source_conflict", "Managed rate sources disagree", "error"),
    ("rate_version_activated", "A rate version was activated", "info"),
    ("rate_version_auto_activated", "A verified rate version was automatically activated", "info"),
    ("rate_retroactive_activated", "A retroactive rate version was activated", "warning"),
    ("rate_estimates_recalculated", "Rate estimates were recalculated", "info"),
    ("rate_source_stale", "Managed rate evidence is stale", "warning"),
):
    NOTIFICATION_CATALOG[_rate_code] = _entry(
        "rate_source",
        _rate_severity,  # type: ignore[arg-type]
        _rate_title,
        f"Power Monitor recorded {_rate_title.lower()} for {{resource}}.",
        "Current and historical estimates retain their exact effective-dated rate evidence.",
        "Review the managed source evidence and candidate state.",
        "Open Detailed Rates and inspect source evidence, validation, and effective dates.",
        action_label="Open Detailed Rates",
        action_target="/billing?advanced=rates",
        action_permissions=("rates.view",),
    )

NOTIFICATION_CATALOG["device_address_outside_policy"] = _entry(
    "security",
    "error",
    "{resource} reported an address outside network policy",
    "The signed sensor address is not allowed by the configured server-pull policy.",
    "Server pull and device diagnostics are blocked; signed device ingress remains independently authenticated.",
    "Review the sensor address and explicit network policy.",
    "Test the address against the Home network policy.",
    "Add only the required trusted private CIDR when appropriate.",
    action_label="Review network policy",
    action_target="/settings/advanced/network",
    action_permissions=("network_policy.view",),
)


_ALIASES = {
    "api_unreachable": "device_api_unreachable",
    "reading_stale": "no_valid_reading",
    "voltage_range": "voltage_frequency_range",
    "frequency_range": "voltage_frequency_range",
    "ct_limit_80": "ct_utilization",
    "ct_limit_90": "ct_utilization",
}

_SECRET_MARKERS = (
    "password",
    "secret",
    "token",
    "signature",
    "credential",
    "cookie",
    "private_key",
)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def catalog_entry(code: str, severity: str = "warning") -> NotificationCatalogEntry:
    normalized = _ALIASES.get(code, code)
    if normalized in NOTIFICATION_CATALOG:
        return NOTIFICATION_CATALOG[normalized]
    safe_severity = cast(
        Literal["info", "warning", "error", "critical"],
        severity if severity in {"info", "warning", "error", "critical"} else "warning",
    )
    title = normalized.replace("_", " ").strip().capitalize() or "Operational alert"
    return _entry(
        "system",
        safe_severity,
        title,
        f"Power Monitor detected {title.lower()} for {{resource}}.",
        "The affected feature may be delayed or unavailable until the condition clears.",
        "Review the evidence and affected system area.",
        "Open System Health for current status and safe diagnostics.",
        action_label="Open System Health",
        action_target="/settings/advanced/system-health",
        action_permissions=("system_health.view",),
    )


def _safe_text(value: Any) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _evidence_rows(evidence: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key, value in sorted(evidence.items()):
        if any(marker in key.lower() for marker in _SECRET_MARKERS):
            continue
        if key in {"status_before_resolve", "resolve_observed_at", "acknowledgement_note"}:
            continue
        if isinstance(value, dict | list):
            value = ", ".join(map(str, value)) if isinstance(value, list) else "Recorded"
        status = "error" if any(word in key for word in ("failure", "error", "missing")) else None
        rows.append(
            {
                "label": key.replace("_", " ").capitalize(),
                "value": _safe_text(value),
                **({"status": status} if status else {}),
            }
        )
    return rows[:24]


def _observed_expected(
    code: str, evidence: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if code == "heartbeat_stale":
        last_seen = evidence.get("last_seen_at")
        threshold = evidence.get("stale_after_seconds")
        observed_metric: dict[str, Any] = {
            "label": "Last signed heartbeat",
            "value": _safe_text(last_seen),
        }
        if isinstance(last_seen, str) and last_seen not in {"", "Unavailable"}:
            observed_metric["recorded_at"] = last_seen
        return (
            observed_metric,
            {
                "label": "Heartbeat interval",
                "operator": "within",
                "value": _safe_text(threshold),
                "unit": "seconds",
            },
        )
    if code == "low_rssi":
        return (
            {"label": "Wi-Fi signal", "value": _safe_text(evidence.get("rssi_dbm")), "unit": "dBm"},
            {"label": "Recommended minimum", "operator": "at least", "value": "-70", "unit": "dBm"},
        )
    if code == "power_surge":
        return (
            {
                "label": "Current power",
                "value": _safe_text(evidence.get("current_watts")),
                "unit": "W",
            },
            {
                "label": "Configured maximum",
                "operator": "below",
                "value": _safe_text(evidence.get("threshold_watts")),
                "unit": "W",
            },
        )
    if code == "sync_backlog":
        pending = evidence.get("pending_readings", evidence.get("backlog_count"))
        return (
            {"label": "Pending readings", "value": _safe_text(pending)},
            {"label": "Expected backlog", "operator": "equals", "value": "0"},
        )
    if code == "sequence_gap":
        count = evidence.get("missing_records", evidence.get("open_gap_count"))
        return (
            {"label": "Missing records", "value": _safe_text(count)},
            {"label": "Expected missing records", "operator": "equals", "value": "0"},
        )
    if code in {"voltage_frequency_range", "voltage_range", "frequency_range"}:
        observed = evidence.get("observed", evidence.get("voltage", evidence.get("frequency_hz")))
        expected = evidence.get(
            "configured_range", evidence.get("maximum", evidence.get("minimum"))
        )
        return (
            {"label": "Observed reading", "value": _safe_text(observed)},
            {"label": "Configured range", "operator": "within", "value": _safe_text(expected)},
        )
    return None, None


def _notification_state(alert: AlertInstance, now: datetime) -> str:
    if alert.resolved_at or alert.status == "resolved":
        return "resolved"
    if alert.silenced_until and _aware(alert.silenced_until) > now:
        return "silenced"
    if alert.acknowledged_at or alert.status == "acknowledged":
        return "acknowledged"
    return "open"


async def load_notification_views(
    session: AsyncSession,
    *,
    user_id: str,
    permissions: set[str],
    all_sites: bool,
    site_ids: set[str],
    requested_site_id: str | None = None,
    include_resolved: bool = True,
    include_dismissed: bool = False,
) -> list[NotificationView]:
    now = datetime.now(UTC)
    alert_query = select(AlertInstance).order_by(AlertInstance.last_seen_at.desc()).limit(1000)
    if not all_sites:
        alert_query = alert_query.where(
            or_(AlertInstance.site_id.is_(None), AlertInstance.site_id.in_(site_ids))
        )
    if requested_site_id:
        alert_query = alert_query.where(
            or_(AlertInstance.site_id.is_(None), AlertInstance.site_id == requested_site_id)
        )
    if not include_resolved:
        alert_query = alert_query.where(AlertInstance.status != "resolved")
    alerts = list(await session.scalars(alert_query))
    dismissed_events = list(
        await session.scalars(
            select(NotificationEvent)
            .where(
                NotificationEvent.actor_id == user_id,
                NotificationEvent.event_type == "dismissed",
            )
            .order_by(NotificationEvent.occurred_at.desc())
            .limit(2000)
        )
    )
    dismissed_at: dict[str, datetime] = {}
    for event in dismissed_events:
        dismissed_at.setdefault(event.notification_id, _aware(event.occurred_at))
    rule_ids = {item.rule_id for item in alerts}
    device_ids = {item.device_id for item in alerts if item.device_id}
    site_id_values = {item.site_id for item in alerts if item.site_id}
    rules = (
        {
            item.id: item
            for item in await session.scalars(select(AlertRule).where(AlertRule.id.in_(rule_ids)))
        }
        if rule_ids
        else {}
    )
    devices = (
        {
            item.id: item
            for item in await session.scalars(select(Device).where(Device.id.in_(device_ids)))
        }
        if device_ids
        else {}
    )
    sites = (
        {
            item.id: item
            for item in await session.scalars(select(Site).where(Site.id.in_(site_id_values)))
        }
        if site_id_values
        else {}
    )
    actor_ids = {
        actor for item in alerts for actor in (item.acknowledged_by, item.silenced_by) if actor
    }
    users = (
        {
            item.id: item.display_name
            for item in await session.scalars(select(User).where(User.id.in_(actor_ids)))
        }
        if actor_ids
        else {}
    )
    alert_ids = {item.id for item in alerts}
    attempts = (
        list(
            await session.scalars(
                select(NotificationAttempt)
                .where(NotificationAttempt.alert_instance_id.in_(alert_ids))
                .order_by(NotificationAttempt.attempted_at.desc())
            )
        )
        if alert_ids
        else []
    )
    latest_attempt: dict[str, NotificationAttempt] = {}
    channel_ids: set[str] = set()
    for attempt in attempts:
        if attempt.alert_instance_id and attempt.alert_instance_id not in latest_attempt:
            latest_attempt[attempt.alert_instance_id] = attempt
            channel_ids.add(attempt.channel_id)
    channels = (
        {
            item.id: item.name
            for item in await session.scalars(
                select(NotificationChannel).where(NotificationChannel.id.in_(channel_ids))
            )
        }
        if channel_ids
        else {}
    )

    output: list[NotificationView] = []
    for alert in alerts:
        rule = rules.get(alert.rule_id)
        code = rule.rule_type if rule else "unknown"
        entry = catalog_entry(code, alert.severity)
        resource_type = "sensor" if alert.device_id else ("home" if alert.site_id else "server")
        resource_name = (
            devices[alert.device_id].name
            if alert.device_id in devices
            else sites[alert.site_id].name
            if alert.site_id in sites
            else "Power Monitor server"
        )
        evidence = dict(alert.evidence or {})
        observed, expected = _observed_expected(code, evidence)
        observed_text = observed["value"] if observed else "the reported condition"
        expected_text = expected["value"] if expected else "configured health criteria"
        first_seen = _aware(alert.opened_at)
        last_seen = _aware(alert.last_seen_at or alert.opened_at)
        state = _notification_state(alert, now)
        latest = latest_attempt.get(alert.id)
        action = None
        if (
            entry.action_label
            and entry.action_target
            and set(entry.action_permissions).issubset(permissions)
        ):
            action = {
                "label": entry.action_label,
                "target": entry.action_target,
                "required_permissions": list(entry.action_permissions),
            }
        output.append(
            NotificationView.model_validate(
                {
                    "id": alert.id,
                    "code": code,
                    "kind": "operational_alert",
                    "category": entry.category,
                    "severity": alert.severity,
                    "state": state,
                    "title": entry.title.format(resource=resource_name),
                    "summary": entry.summary.format(
                        resource=resource_name,
                        threshold=evidence.get("stale_after_seconds", "configured"),
                        observed=observed_text,
                        expected=expected_text,
                    ),
                    "affected_resource": {
                        "type": resource_type,
                        "id": alert.device_id or alert.site_id,
                        "name": resource_name,
                    },
                    "first_seen_at": first_seen,
                    "last_seen_at": last_seen,
                    "resolved_at": _aware(alert.resolved_at) if alert.resolved_at else None,
                    "occurrence_count": max(1, alert.occurrence_count or 1),
                    "duration_seconds": max(
                        0,
                        int(
                            (
                                (_aware(alert.resolved_at) if alert.resolved_at else now)
                                - first_seen
                            ).total_seconds()
                        ),
                    ),
                    "observed": observed,
                    "expected": expected,
                    "cause": {
                        "code": code,
                        "explanation": f"The {code.replace('_', ' ')} rule is currently satisfied by authoritative monitoring evidence.",
                    },
                    "evidence": _evidence_rows(evidence),
                    "impact": entry.impact,
                    "remediation": {
                        "summary": entry.remediation_summary,
                        "steps": list(entry.remediation_steps),
                        "automatic_recovery": entry.automatic_recovery,
                        "action": action,
                    },
                    "acknowledgement": (
                        {
                            "acknowledged_at": alert.acknowledged_at,
                            "acknowledged_by": (
                                users.get(alert.acknowledged_by, "Authorized user")
                                if alert.acknowledged_by
                                else "Authorized user"
                            ),
                            "note": evidence.get("acknowledgement_note"),
                        }
                        if alert.acknowledged_at
                        else None
                    ),
                    "silence": (
                        {
                            "silenced_until": _aware(alert.silenced_until),
                            "silenced_by": (
                                users.get(alert.silenced_by, "Authorized user")
                                if alert.silenced_by
                                else "Authorized user"
                            ),
                            "note": alert.silence_note,
                        }
                        if alert.silenced_until and _aware(alert.silenced_until) > now
                        else None
                    ),
                    "delivery": (
                        {
                            "attempted": True,
                            "channel_name": channels.get(latest.channel_id),
                            "last_attempt_at": latest.completed_at or latest.attempted_at,
                            "last_outcome": latest.status,
                            "retry_at": latest.next_attempt_at,
                            "safe_error_code": latest.safe_error_code,
                            "safe_error_summary": latest.safe_error_summary
                            or latest.response_summary,
                        }
                        if latest
                        else {"attempted": False}
                    ),
                    "suppression": {
                        "dismissible": "alerts.acknowledge" in permissions,
                        "permanently_suppressible": False,
                        "currently_suppressed": False,
                        "allowed_scopes": [],
                    },
                }
            )
        )

        if latest and latest.status in {"failed", "retry_scheduled"}:
            delivery_entry = NOTIFICATION_CATALOG["notification_delivery_failed"]
            channel_name = channels.get(latest.channel_id, "Notification channel")
            delivery_action = None
            if set(delivery_entry.action_permissions).issubset(permissions):
                delivery_action = {
                    "label": delivery_entry.action_label,
                    "target": delivery_entry.action_target,
                    "required_permissions": list(delivery_entry.action_permissions),
                }
            attempted_at = _aware(latest.started_at or latest.queued_at or latest.attempted_at)
            completed_at = _aware(latest.completed_at or latest.attempted_at)
            output.append(
                NotificationView.model_validate(
                    {
                        "id": f"delivery:{latest.id}",
                        "code": "notification_delivery_failed",
                        "kind": "delivery_issue",
                        "category": "delivery",
                        "severity": "error",
                        "state": "open",
                        "title": delivery_entry.title,
                        "summary": delivery_entry.summary.format(resource=channel_name),
                        "affected_resource": {
                            "type": "notification_channel",
                            "id": latest.channel_id,
                            "name": channel_name,
                        },
                        "first_seen_at": attempted_at,
                        "last_seen_at": completed_at,
                        "occurrence_count": max(1, latest.attempt_number),
                        "duration_seconds": max(
                            0, int((completed_at - attempted_at).total_seconds())
                        ),
                        "cause": {
                            "code": latest.safe_error_code or "delivery_failed",
                            "explanation": latest.safe_error_summary
                            or latest.response_summary
                            or "The notification channel did not accept the message.",
                        },
                        "evidence": [
                            {"label": "Channel", "value": channel_name},
                            {
                                "label": "Related alert",
                                "value": entry.title.format(resource=resource_name),
                            },
                            {"label": "Attempt", "value": str(latest.attempt_number)},
                            {"label": "Outcome", "value": latest.status, "status": "error"},
                        ],
                        "impact": delivery_entry.impact,
                        "remediation": {
                            "summary": delivery_entry.remediation_summary,
                            "steps": list(delivery_entry.remediation_steps),
                            "action": delivery_action,
                        },
                        "delivery": {
                            "attempted": True,
                            "channel_name": channel_name,
                            "last_attempt_at": completed_at,
                            "last_outcome": latest.status,
                            "retry_at": latest.next_attempt_at,
                            "safe_error_code": latest.safe_error_code,
                            "safe_error_summary": latest.safe_error_summary
                            or latest.response_summary,
                        },
                        "suppression": {
                            "dismissible": "alerts.manage_delivery" in permissions,
                            "permanently_suppressible": False,
                            "currently_suppressed": False,
                            "allowed_scopes": [],
                        },
                    }
                )
            )

    if "alerts.manage_delivery" in permissions:
        channel_count = len(
            list(
                await session.scalars(
                    select(NotificationChannel.id).where(NotificationChannel.enabled.is_(True))
                )
            )
        )
        if channel_count == 0:
            site_query = (
                select(Site)
                .where(Site.lifecycle_state == "active")
                .order_by(Site.is_default.desc(), Site.name)
            )
            if requested_site_id:
                site_query = site_query.where(Site.id == requested_site_id)
            elif not all_sites:
                site_query = site_query.where(Site.id.in_(site_ids))
            site = await session.scalar(site_query.limit(1))
            if site:
                key = "recommendation.smtp_not_configured"
                suppressed = await session.scalar(
                    select(NotificationSuppression.id).where(
                        NotificationSuppression.suppression_key == key,
                        NotificationSuppression.active.is_(True),
                        or_(
                            NotificationSuppression.user_id == user_id,
                            NotificationSuppression.site_id == site.id,
                        ),
                    )
                )
                if suppressed is None:
                    entry = NOTIFICATION_CATALOG[key]
                    site_created_at = _aware(site.created_at)
                    output.append(
                        NotificationView.model_validate(
                            {
                                "id": f"recommendation:{key}:{site.id}",
                                "code": key,
                                "kind": "setup_recommendation",
                                "category": entry.category,
                                "severity": entry.severity,
                                "state": "open",
                                "title": entry.title,
                                "summary": entry.summary,
                                "affected_resource": {
                                    "type": "home",
                                    "id": site.id,
                                    "name": site.name,
                                },
                                "first_seen_at": site_created_at,
                                "last_seen_at": now,
                                "occurrence_count": 1,
                                "duration_seconds": max(
                                    0, int((now - site_created_at).total_seconds())
                                ),
                                "evidence": [
                                    {
                                        "label": "Dashboard alerts",
                                        "value": "Available",
                                        "status": "normal",
                                    },
                                    {
                                        "label": "Email delivery",
                                        "value": "Not configured",
                                        "status": "warning",
                                    },
                                ],
                                "impact": entry.impact,
                                "remediation": {
                                    "summary": entry.remediation_summary,
                                    "steps": list(entry.remediation_steps),
                                    "action": {
                                        "label": entry.action_label,
                                        "target": entry.action_target,
                                        "required_permissions": list(entry.action_permissions),
                                    },
                                },
                                "delivery": {"attempted": False},
                                "suppression": {
                                    "dismissible": True,
                                    "permanently_suppressible": True,
                                    "suppression_key": key,
                                    "currently_suppressed": False,
                                    "allowed_scopes": ["user", "home"],
                                },
                            }
                        )
                    )

    if not include_dismissed:
        output = [
            item
            for item in output
            if dismissed_at.get(item.id) is None
            or dismissed_at[item.id] < _aware(item.last_seen_at)
        ]

    severity_order = {"critical": 4, "error": 3, "warning": 2, "info": 1}
    kind_order = {"operational_alert": 3, "delivery_issue": 2, "setup_recommendation": 1}
    output.sort(
        key=lambda item: (
            item.state != "resolved",
            kind_order[item.kind],
            severity_order[item.severity],
            item.state == "open",
            item.last_seen_at,
        ),
        reverse=True,
    )
    return output
