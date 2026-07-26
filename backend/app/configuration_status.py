from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BackupRun,
    Device,
    DeviceHeartbeat,
    NotificationChannel,
    RateAssignment,
    RatePlan,
    RateSource,
    RateVersion,
    Site,
    UtilityAccount,
)
from app.rates.assignments import assignment_state, conflicting_pairs
from app.rates.documents import validate_document
from app.rates.service import version_document
from app.schemas import ConfigurationAction, ConfigurationIssue, ConfigurationStatus


def _action(action_id: str, label: str, target: str) -> ConfigurationAction:
    return ConfigurationAction(id=action_id, label=label, target=target)


def _issue(
    *,
    issue_id: str,
    category: str,
    state: str,
    title: str,
    what: str,
    why: str,
    fix: str,
    blocking: bool,
    action: ConfigurationAction,
) -> ConfigurationIssue:
    return ConfigurationIssue.model_validate(
        {
            "id": issue_id,
            "category": category,
            "state": state,
            "title": title,
            "what_is_wrong": what,
            "why_it_matters": why,
            "how_to_fix": fix,
            "blocking": blocking,
            "action": action,
        }
    )


async def build_configuration_status(
    session: AsyncSession,
    *,
    site: Site,
    heartbeat_expectation_seconds: int,
) -> ConfigurationStatus:
    """Resolve one server-authoritative status for the Single Home UI."""

    now = datetime.now(UTC)
    issues: list[ConfigurationIssue] = []
    rate_ready = False
    account = await session.scalar(
        select(UtilityAccount)
        .where(UtilityAccount.site_id == site.id, UtilityAccount.status == "active")
        .order_by(UtilityAccount.created_at)
        .limit(1)
    )
    if account is None:
        issues.append(
            _issue(
                issue_id="electric-service.missing",
                category="electric_service",
                state="setup_needed",
                title="Electric service needs setup",
                what="This home has no active electric-service record.",
                why="A service is required to assign a rate plan and calculate billing costs.",
                fix="Create the electric service and confirm its billing-cycle day.",
                blocking=True,
                action=_action(
                    "electric_service.create",
                    "Set up electric service",
                    "/billing?configuration=electric-service",
                ),
            )
        )
    else:
        assignments = list(
            await session.scalars(
                select(RateAssignment)
                .where(
                    RateAssignment.utility_account_id == account.id,
                    RateAssignment.cancelled_at.is_(None),
                )
                .order_by(RateAssignment.effective_from, RateAssignment.created_at)
            )
        )
        conflicts = conflicting_pairs(assignments)
        current = [item for item in assignments if assignment_state(item, now) == "current"]
        scheduled = [item for item in assignments if assignment_state(item, now) == "scheduled"]
        if conflicts or len(current) > 1:
            issues.append(
                _issue(
                    issue_id="rate-assignment.conflict",
                    category="rate_plan",
                    state="error",
                    title="Current-plan assignments conflict",
                    what="More than one rate assignment covers the same effective instant.",
                    why=(
                        "Costs would be ambiguous until one assignment is selected "
                        "as authoritative."
                    ),
                    fix="Open Versions and complete the assignment repair workflow.",
                    blocking=True,
                    action=_action(
                        "rate_assignment.repair",
                        "Repair assignments",
                        "/billing?advanced=rates&tab=versions&action=repair",
                    ),
                )
            )
        elif not current:
            if scheduled:
                issues.append(
                    _issue(
                        issue_id="rate-assignment.not-yet-effective",
                        category="rate_plan",
                        state="partially_configured",
                        title="Rate plan is scheduled",
                        what=(
                            "A published plan is assigned, but its effective time has not arrived."
                        ),
                        why="Current cost estimates remain unavailable until that boundary.",
                        fix=(
                            "Wait for the scheduled boundary or replace it with a "
                            "plan effective now."
                        ),
                        blocking=True,
                        action=_action(
                            "rate_assignment.make_current",
                            "Review scheduled plan",
                            "/billing?advanced=rates&tab=versions",
                        ),
                    )
                )
            else:
                issues.append(
                    _issue(
                        issue_id="rate-assignment.missing",
                        category="rate_plan",
                        state="setup_needed",
                        title="Choose a current rate plan",
                        what="The electric service has no plan effective now.",
                        why=(
                            "Energy can still be measured, but current prices and "
                            "cost estimates cannot be calculated."
                        ),
                        fix="Choose a published version and use Make current.",
                        blocking=True,
                        action=_action(
                            "rate_assignment.make_current",
                            "Choose current plan",
                            "/billing?advanced=rates&tab=versions",
                        ),
                    )
                )
        else:
            assignment = current[0]
            version = await session.get(RateVersion, assignment.rate_version_id)
            plan = await session.get(RatePlan, version.rate_plan_id) if version else None
            invalid = (
                version is None
                or plan is None
                or version.status not in {"published", "active", "approved"}
                or plan.status in {"removed", "retired"}
            )
            if not invalid and version is not None:
                document = await version_document(session, version)
                invalid = not validate_document(document).valid
            if invalid:
                issues.append(
                    _issue(
                        issue_id="rate-assignment.invalid",
                        category="rate_plan",
                        state="error",
                        title="Current rate plan is invalid",
                        what=(
                            "The effective assignment references a missing, unpublished, "
                            "retired, or removed rate version."
                        ),
                        why=(
                            "The server cannot safely use that version for current "
                            "cost calculations."
                        ),
                        fix=(
                            "Review the draft, publish a complete version, then replace "
                            "the current assignment."
                        ),
                        blocking=True,
                        action=_action(
                            "rate_version.review",
                            "Review and publish",
                            "/billing?advanced=rates&tab=plans&action=validate",
                        ),
                    )
                )
            else:
                rate_ready = True

        if not account.generation_provider or len(account.currency) != 3 or not account.timezone:
            issues.append(
                _issue(
                    issue_id="electric-service.details-invalid",
                    category="electric_service",
                    state="attention_required",
                    title="Electric service details need correction",
                    what="The provider, currency, or timezone is missing or invalid.",
                    why="Rate periods and exact Decimal costs depend on this local context.",
                    fix="Review the Electric Service provider, currency, and timezone.",
                    blocking=True,
                    action=_action(
                        "electric_service.review",
                        "Review electric service",
                        "/billing?configuration=electric-service",
                    ),
                )
            )
        if not 1 <= account.billing_cycle_start_day <= 31:
            issues.append(
                _issue(
                    issue_id="billing-cycle.missing",
                    category="billing_cycle",
                    state="partially_configured",
                    title="Billing cycle is not configured",
                    what="The Electric Service does not have a valid billing-cycle day.",
                    why="Cycle progress, tiers, and projections need an exact cycle boundary.",
                    fix="Add the billing day or import and review an electric bill.",
                    blocking=True,
                    action=_action(
                        "billing_cycle.configure",
                        "Configure billing cycle",
                        "/billing?configuration=billing-cycle",
                    ),
                )
            )

    devices = list(
        await session.scalars(
            select(Device).where(Device.site_id == site.id, Device.lifecycle_status == "active")
        )
    )
    latest_heartbeat = await session.scalar(
        select(DeviceHeartbeat.received_at)
        .join(Device, Device.id == DeviceHeartbeat.device_id)
        .where(Device.site_id == site.id, Device.lifecycle_status == "active")
        .order_by(DeviceHeartbeat.received_at.desc())
        .limit(1)
    )
    if not devices:
        issues.append(
            _issue(
                issue_id="sensor.missing",
                category="sensor",
                state="waiting_for_data" if rate_ready else "setup_needed",
                title="Connect a sensor",
                what="No active ESP32 sensor is enrolled for this home.",
                why="Live power and history require signed readings from at least one sensor.",
                fix="Generate a short-lived enrollment code and claim the sensor.",
                blocking=True,
                action=_action("sensor.enroll", "Connect sensor", "/settings/sensors?action=add"),
            )
        )
    elif latest_heartbeat is None:
        issues.append(
            _issue(
                issue_id="sensor.waiting-for-first-heartbeat",
                category="sensor",
                state="waiting_for_data",
                title="Waiting for sensor data",
                what="A sensor is enrolled but has not sent its first valid signed heartbeat.",
                why="The server cannot display current power or synchronized history yet.",
                fix="Power the sensor and verify its local network and server address settings.",
                blocking=True,
                action=_action("sensor.review", "Review sensors", "/settings/sensors"),
            )
        )
    else:
        heartbeat = (
            latest_heartbeat.replace(tzinfo=UTC)
            if latest_heartbeat.tzinfo is None
            else latest_heartbeat
        )
        if heartbeat < now - timedelta(seconds=heartbeat_expectation_seconds * 4):
            issues.append(
                _issue(
                    issue_id="sensor.data-stale",
                    category="sensor",
                    state="attention_required",
                    title="Sensor data is stale",
                    what="The latest signed heartbeat is older than the configured health window.",
                    why="Live power and recent history may no longer represent the home.",
                    fix="Review the sensor connection and signed-heartbeat diagnostics.",
                    blocking=True,
                    action=_action("sensor.review", "Review sensors", "/settings/sensors"),
                )
            )

    enabled_channels = int(
        await session.scalar(
            select(func.count())
            .select_from(NotificationChannel)
            .where(NotificationChannel.enabled.is_(True))
        )
        or 0
    )
    if enabled_channels == 0:
        issues.append(
            _issue(
                issue_id="notification.channel-missing",
                category="notification",
                state="partially_configured",
                title="Notification delivery is not configured",
                what="No enabled notification channel is available.",
                why="On-screen alerts still work, but email notifications cannot be delivered.",
                fix="Add and test an SMTP notification channel.",
                blocking=False,
                action=_action(
                    "notification.configure",
                    "Configure notifications",
                    "/settings/notifications",
                ),
            )
        )

    verified_backup = await session.scalar(
        select(BackupRun.id)
        .where(BackupRun.status == "completed", BackupRun.verified_at.is_not(None))
        .order_by(BackupRun.verified_at.desc())
        .limit(1)
    )
    if verified_backup is None:
        issues.append(
            _issue(
                issue_id="backup.unverified",
                category="backup",
                state="attention_required",
                title="No verified backup is recorded",
                what="The server has not recorded a successfully verified logical backup.",
                why="A verified restore point reduces data-loss risk during upgrades or recovery.",
                fix="Run a backup and its automated restore verification.",
                blocking=False,
                action=_action("backup.review", "Review backups", "/settings/data"),
            )
        )

    failing_sources = int(
        await session.scalar(
            select(func.count())
            .select_from(RateSource)
            .where(RateSource.enabled.is_(True), RateSource.consecutive_failures > 0)
        )
        or 0
    )
    if failing_sources:
        issues.append(
            _issue(
                issue_id="rate-source.failures",
                category="rate_source",
                state="attention_required",
                title="A rate source check is failing",
                what=f"{failing_sources} enabled rate source(s) report consecutive failures.",
                why="Managed rate updates and evidence may become stale.",
                fix="Open Sources, review the per-source result, and retry the check.",
                blocking=False,
                action=_action(
                    "rate_source.review",
                    "Review rate sources",
                    "/billing?advanced=rates&tab=sources",
                ),
            )
        )

    blocking = [issue for issue in issues if issue.blocking]
    candidates = blocking or issues
    if not candidates:
        state = "ready"
    else:
        order = {
            "error": 5,
            "attention_required": 4,
            "setup_needed": 3,
            "partially_configured": 2,
            "waiting_for_data": 1,
        }
        state = max(candidates, key=lambda issue: order[issue.state]).state
    labels = {
        "ready": "Ready",
        "setup_needed": "Setup needed",
        "partially_configured": "Partially configured",
        "waiting_for_data": "Waiting for data",
        "attention_required": "Attention required",
        "error": "Error",
    }
    blocking_count = len(blocking)
    summary = (
        "Configuration is complete."
        if not issues
        else f"{blocking_count} blocking and {len(issues) - blocking_count} advisory issue"
        f"{'' if len(issues) == 1 else 's'}."
    )
    return ConfigurationStatus.model_validate(
        {
            "schema_version": "configuration-status/1.0",
            "home_id": site.id,
            "electric_service_id": account.id if account else None,
            "state": state,
            "label": labels[state],
            "summary": summary,
            "generated_at": now,
            "issues": issues,
        }
    )
