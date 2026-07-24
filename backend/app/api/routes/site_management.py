from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CsrfPrincipal, DbSession, Principal, audit_event
from app.db.models import (
    AlertInstance,
    AuditEvent,
    BackgroundJob,
    BillingCycle,
    Circuit,
    CostCalculationRun,
    Device,
    DeviceLifecycleEvent,
    DeviceSiteAssignment,
    EnrollmentToken,
    RawReading,
    SensorNetworkCidr,
    SensorNetworkPolicy,
    Site,
    User,
    UserSite,
    UtilityAccount,
    UtilityAccountSiteAssignment,
    UtilityBillImport,
)
from app.network_policy import ensure_site_policies, policy_cidrs, policy_summary
from app.problem import ProblemError
from app.schemas import (
    SiteAdminCreate,
    SiteAdminUpdate,
    SiteDependencyResolution,
    SiteLifecycleRequest,
    SiteRemoveRequest,
    SiteRestoreRequest,
)

router = APIRouter(prefix="/api/v1/admin/sites", tags=["site management"])


def _permission(principal: Principal, permission: str) -> None:
    if permission not in principal.permissions:
        raise ProblemError(
            403,
            "Permission denied",
            "Your account does not have the required site-management permission",
            "forbidden",
            extra={"required_permission": permission},
        )


def _site_allowed(principal: Principal, site_id: str) -> None:
    if not principal.can_access_site(site_id):
        raise ProblemError(404, "Site not found", "Site does not exist", "site_missing")


async def _site(
    session: AsyncSession, principal: Principal, site_id: str, *, lock: bool = False
) -> Site:
    _site_allowed(principal, site_id)
    query = select(Site).where(Site.id == site_id)
    if lock:
        query = query.with_for_update()
    value = await session.scalar(query)
    if value is None:
        raise ProblemError(404, "Site not found", "Site does not exist", "site_missing")
    return value


def _stale(site: Site, revision: int) -> None:
    if site.revision != revision:
        raise ProblemError(
            409,
            "Site changed",
            "Refresh the site details and review the latest revision",
            "stale_site_revision",
            extra={"current_revision": site.revision},
        )


async def _active_count(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(Site).where(Site.lifecycle_state == "active")
        )
        or 0
    )


async def _policy_details(session: AsyncSession, site: Site) -> list[dict[str, Any]]:
    policies = list(
        await session.scalars(
            select(SensorNetworkPolicy)
            .where(SensorNetworkPolicy.site_id == site.id)
            .order_by(SensorNetworkPolicy.direction)
        )
    )
    result: list[dict[str, Any]] = []
    for policy in policies:
        cidrs = await policy_cidrs(session, policy.id)
        result.append(
            {
                "id": policy.id,
                "direction": policy.direction,
                "mode": policy.mode,
                "revision": policy.revision,
                "summary": policy_summary(policy, sum(1 for item in cidrs if item.enabled)),
                "cidrs": [
                    {
                        "id": item.id,
                        "network": item.network,
                        "label": item.label,
                        "enabled": item.enabled,
                    }
                    for item in cidrs
                ],
            }
        )
    return result


async def _dependencies(session: AsyncSession, site: Site) -> dict[str, Any]:
    active_sensors = list(
        await session.scalars(
            select(Device)
            .where(Device.site_id == site.id, Device.lifecycle_status == "active")
            .order_by(Device.name)
        )
    )
    active_accounts = list(
        await session.scalars(
            select(UtilityAccount)
            .where(UtilityAccount.site_id == site.id, UtilityAccount.status == "active")
            .order_by(UtilityAccount.name)
        )
    )
    assigned_users = list(
        await session.execute(
            select(User.id, User.email, User.display_name)
            .join(UserSite, UserSite.user_id == User.id)
            .where(UserSite.site_id == site.id, User.lifecycle_state != "removed")
            .order_by(User.display_name)
        )
    )
    active_alerts = int(
        await session.scalar(
            select(func.count())
            .select_from(AlertInstance)
            .where(
                AlertInstance.site_id == site.id,
                AlertInstance.status.in_(("open", "acknowledged", "silenced")),
            )
        )
        or 0
    )
    circuit_count = int(
        await session.scalar(
            select(func.count()).select_from(Circuit).where(Circuit.site_id == site.id)
        )
        or 0
    )
    reading_count, first_reading, latest_reading = (
        await session.execute(
            select(
                func.count(RawReading.id),
                func.min(RawReading.interval_start),
                func.max(RawReading.interval_end),
            ).where(RawReading.site_id == site.id)
        )
    ).one()
    billing_cycle_count = int(
        await session.scalar(
            select(func.count())
            .select_from(BillingCycle)
            .join(
                UtilityAccount,
                UtilityAccount.id == BillingCycle.utility_account_id,
            )
            .where(UtilityAccount.site_id == site.id)
        )
        or 0
    )
    active_job_count = int(
        await session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .join(UtilityBillImport, UtilityBillImport.job_id == BackgroundJob.id)
            .join(
                UtilityAccount,
                UtilityAccount.id == UtilityBillImport.utility_account_id,
            )
            .where(
                UtilityAccount.site_id == site.id,
                BackgroundJob.status.in_(("queued", "running", "retrying")),
            )
        )
        or 0
    )
    active_job_count += int(
        await session.scalar(
            select(func.count())
            .select_from(CostCalculationRun)
            .join(
                UtilityAccount,
                UtilityAccount.id == CostCalculationRun.utility_account_id,
            )
            .where(
                UtilityAccount.site_id == site.id,
                CostCalculationRun.status.in_(("queued", "running", "recalculating")),
            )
        )
        or 0
    )
    active_tokens = sum(
        1
        for token in await session.scalars(
            select(EnrollmentToken).where(
                EnrollmentToken.consumed_at.is_(None),
                EnrollmentToken.revoked_at.is_(None),
            )
        )
        if token.preassignment.get("site_id") == site.id
    )
    blockers: list[dict[str, Any]] = []
    if site.is_default:
        blockers.append(
            {
                "code": "default_site",
                "message": "Select another active default site before removing this site.",
            }
        )
    if site.lifecycle_state == "active" and await _active_count(session) <= 1:
        blockers.append(
            {
                "code": "last_active_site",
                "message": "At least one active administrative site must remain.",
            }
        )
    if active_job_count:
        blockers.append(
            {
                "code": "active_jobs",
                "count": active_job_count,
                "message": "Wait for or safely cancel active imports and recalculations.",
            }
        )
    required_actions: list[dict[str, Any]] = []
    if active_sensors:
        required_actions.append(
            {
                "resource": "sensors",
                "count": len(active_sensors),
                "actions": ["archive", "transfer", "cancel"],
            }
        )
    if active_accounts:
        required_actions.append(
            {
                "resource": "utility_accounts",
                "count": len(active_accounts),
                "actions": ["archive", "transfer", "cancel"],
            }
        )
    if assigned_users:
        required_actions.append(
            {
                "resource": "user_access",
                "count": len(assigned_users),
                "actions": ["end_access", "cancel"],
            }
        )
    return {
        "site_id": site.id,
        "revision": site.revision,
        "state": site.lifecycle_state,
        "default_site": site.is_default,
        "active": {
            "sensors": [
                {
                    "id": item.id,
                    "name": item.name,
                    "status": item.status,
                    "latest_reading_at": item.last_seen_at,
                }
                for item in active_sensors
            ],
            "utility_accounts": [
                {"id": item.id, "name": item.nickname or item.name, "status": item.status}
                for item in active_accounts
            ],
            "users": [
                {"id": item.id, "email": item.email, "display_name": item.display_name}
                for item in assigned_users
            ],
            "alerts": active_alerts,
            "enrollment_tokens": active_tokens,
            "jobs": active_job_count,
        },
        "retained": {
            "raw_readings": int(reading_count or 0),
            "history_start": first_reading,
            "history_end": latest_reading,
            "circuits": circuit_count,
            "billing_cycles": billing_cycle_count,
            "alerts": active_alerts,
            "audit_history": True,
            "costs_and_rate_assignments": True,
        },
        "required_actions": required_actions,
        "blockers": blockers,
        "resolved": not required_actions and not blockers,
    }


async def _site_payload(session: AsyncSession, site: Site) -> dict[str, Any]:
    dependencies = await _dependencies(session, site)
    policies = await _policy_details(session, site)
    policy_warning = any(
        item["mode"].startswith("legacy") or item["mode"] == "deny_all" for item in policies
    )
    return {
        "id": site.id,
        "name": site.name,
        "code": site.code,
        "description": site.description,
        "location_label": site.location_label,
        "organization": site.organization,
        "timezone": site.timezone,
        "currency": site.currency,
        "locale": site.locale,
        "unit_system": site.unit_system,
        "lifecycle_state": site.lifecycle_state,
        "is_default": site.is_default,
        "revision": site.revision,
        "disabled_at": site.disabled_at,
        "removed_at": site.removed_at,
        "removed_by": site.removed_by,
        "removal_reason": site.removal_reason,
        "restored_at": site.restored_at,
        "created_at": site.created_at,
        "updated_at": site.updated_at,
        "sensor_count": len(dependencies["active"]["sensors"]),
        "utility_account_count": len(dependencies["active"]["utility_accounts"]),
        "assigned_user_count": len(dependencies["active"]["users"]),
        "active_alert_count": dependencies["active"]["alerts"],
        "latest_reading_at": dependencies["retained"]["history_end"],
        "configuration_health": "warning" if policy_warning else "ready",
        "network_policy_summary": " · ".join(item["summary"] for item in policies)
        or "Not initialized",
        "network_policies": policies,
        "dependencies": dependencies,
    }


@router.get("")
async def list_admin_sites(
    principal: Principal,
    session: DbSession,
    status: str = Query(default="current", pattern="^(current|active|disabled|removed|all)$"),
    search: str | None = Query(default=None, max_length=160),
) -> list[dict[str, Any]]:
    _permission(principal, "sites.view")
    query = select(Site)
    if not principal.all_sites:
        query = query.where(Site.id.in_(principal.site_ids))
    if status == "current":
        query = query.where(Site.lifecycle_state != "removed")
    elif status != "all":
        query = query.where(Site.lifecycle_state == status)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                Site.name.ilike(term),
                Site.code.ilike(term),
                Site.timezone.ilike(term),
                Site.location_label.ilike(term),
            )
        )
    sites = list(await session.scalars(query.order_by(Site.is_default.desc(), Site.name)))
    return [await _site_payload(session, item) for item in sites]


@router.post("", status_code=201)
async def create_admin_site(
    payload: SiteAdminCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "sites.create")
    if not principal.all_sites:
        raise ProblemError(
            403,
            "Permission denied",
            "Creating a new site requires organization-wide administrative scope",
            "site_create_scope",
        )
    duplicate = await session.scalar(
        select(Site.id).where(or_(Site.name == payload.name, Site.code == payload.code))
    )
    if duplicate:
        raise ProblemError(
            409,
            "Site already exists",
            "Site display names and stable codes must be unique",
            "site_identity_conflict",
        )
    values = payload.model_dump(
        exclude={
            "initial_user_ids",
            "make_default",
            "network_policy_mode",
            "network_policy_id",
            "create_utility_account_after",
            "confirmation",
        }
    )
    site = Site(**values, lifecycle_state="active", revision=1)
    session.add(site)
    await session.flush()
    active_count = await _active_count(session)
    previous_default = await session.scalar(
        select(Site).where(Site.is_default.is_(True), Site.id != site.id).with_for_update()
    )
    if payload.make_default or active_count == 1:
        await session.execute(
            update(Site)
            .where(Site.is_default.is_(True), Site.id != site.id)
            .values(is_default=False, revision=Site.revision + 1)
        )
        site.is_default = True
    ingress, pull = await ensure_site_policies(session, site)
    if payload.network_policy_mode == "explicit":
        ingress.mode = "allow_all_private"
        ingress.migration_notice_pending = False
        ingress.migrated_from_legacy = False
        pull.mode = "deny_all"
        pull.migration_notice_pending = False
        pull.migrated_from_legacy = False
    elif payload.network_policy_mode == "existing" and payload.network_policy_id:
        source = await session.get(SensorNetworkPolicy, payload.network_policy_id)
        if source is None:
            raise ProblemError(
                422,
                "Network policy not found",
                "The selected network policy does not exist",
                "network_policy_missing",
            )
        target = ingress if source.direction == "device_ingress" else pull
        target.mode = source.mode
        target.migration_notice_pending = False
        target.migrated_from_legacy = False
        for item in await policy_cidrs(session, source.id):
            session.add(
                SensorNetworkCidr(
                    policy_id=target.id,
                    network=item.network,
                    label=f"Copied: {item.label}"[:120],
                    enabled=item.enabled,
                    revision=1,
                )
            )
    if payload.initial_user_ids:
        users = list(
            await session.scalars(select(User).where(User.id.in_(payload.initial_user_ids)))
        )
        if len(users) != len(set(payload.initial_user_ids)):
            raise ProblemError(
                422,
                "User assignment invalid",
                "One or more selected users do not exist",
                "site_user_missing",
            )
        for user in users:
            if user.lifecycle_state != "active":
                raise ProblemError(
                    409,
                    "User assignment invalid",
                    "Only active users may receive initial site access",
                    "site_user_inactive",
                )
            if not user.all_sites:
                session.add(UserSite(user_id=user.id, site_id=site.id))
    session.add(
        audit_event(
            action="site.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=site.id,
            details={
                "revision": 1,
                "code": site.code,
                "timezone": site.timezone,
                "currency": site.currency,
                "network_policy_mode": payload.network_policy_mode,
                "initial_user_count": len(payload.initial_user_ids),
                "default_site": site.is_default,
            },
        )
    )
    if site.is_default and previous_default is not None:
        session.add(
            audit_event(
                action="site.default_changed",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="site",
                object_id=site.id,
                details={
                    "revision": site.revision,
                    "previous_default_site_id": previous_default.id,
                    "new_default_site_id": site.id,
                    "reason": "Default selected during confirmed site creation",
                },
            )
        )
    await session.commit()
    await session.refresh(site)
    result = await _site_payload(session, site)
    result["next_step"] = (
        "create_utility_account" if payload.create_utility_account_after else "site_details"
    )
    return result


@router.get("/{site_id}")
async def get_admin_site(site_id: str, principal: Principal, session: DbSession) -> dict[str, Any]:
    _permission(principal, "sites.view")
    return await _site_payload(session, await _site(session, principal, site_id))


@router.put("/{site_id}")
async def update_admin_site(
    site_id: str,
    payload: SiteAdminUpdate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "sites.edit")
    site = await _site(session, principal, site_id, lock=True)
    if site.lifecycle_state == "removed":
        raise ProblemError(
            409,
            "Site is removed",
            "Restore the site before editing it",
            "site_removed",
        )
    _stale(site, payload.revision)
    changes = payload.model_dump(
        exclude={"revision", "timezone_change_confirmed", "reason"}, exclude_none=True
    )
    if "name" in changes and changes["name"] != site.name:
        duplicate = await session.scalar(
            select(Site.id).where(Site.name == changes["name"], Site.id != site.id)
        )
        if duplicate:
            raise ProblemError(
                409,
                "Site already exists",
                "Site display names must be unique",
                "site_identity_conflict",
            )
    if "timezone" in changes and changes["timezone"] != site.timezone:
        if not payload.timezone_change_confirmed:
            raise ProblemError(
                409,
                "Timezone confirmation required",
                "Changing timezone affects local display, TOU evaluation, billing "
                "boundaries, and unfinalized summaries",
                "site_timezone_confirmation_required",
            )
        session.add(
            audit_event(
                action="site.timezone_changed",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="site",
                object_id=site.id,
                details={
                    "revision": site.revision + 1,
                    "before": {"timezone": site.timezone},
                    "after": {"timezone": changes["timezone"]},
                    "raw_utc_readings_rewritten": False,
                    "finalized_reports_preserved": True,
                    "reason": payload.reason,
                },
            )
        )
    before = {key: getattr(site, key) for key in changes}
    for key, value in changes.items():
        setattr(site, key, value)
    site.revision += 1
    session.add(
        audit_event(
            action="site.edited",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=site.id,
            details={
                "revision": site.revision,
                "before": before,
                "after": changes,
                "reason": payload.reason,
            },
        )
    )
    await session.commit()
    await session.refresh(site)
    return await _site_payload(session, site)


@router.post("/{site_id}/set-default")
async def set_default_site(
    site_id: str,
    payload: SiteLifecycleRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "sites.set_default")
    site = await _site(session, principal, site_id, lock=True)
    if site.lifecycle_state != "active":
        raise ProblemError(
            409,
            "Active site required",
            "Enable the site before making it the default",
            "default_site_inactive",
        )
    if site.is_default:
        return await _site_payload(session, site)
    _stale(site, payload.revision)
    previous = await session.scalar(
        select(Site).where(Site.is_default.is_(True), Site.id != site.id).with_for_update()
    )
    await session.execute(
        update(Site)
        .where(Site.is_default.is_(True), Site.id != site.id)
        .values(is_default=False, revision=Site.revision + 1)
    )
    site.is_default = True
    site.revision += 1
    session.add(
        audit_event(
            action="site.default_changed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=site.id,
            details={
                "revision": site.revision,
                "previous_default_site_id": previous.id if previous else None,
                "new_default_site_id": site.id,
                "reason": payload.reason,
            },
        )
    )
    await session.commit()
    await session.refresh(site)
    return await _site_payload(session, site)


@router.post("/{site_id}/disable")
async def disable_site(
    site_id: str,
    payload: SiteLifecycleRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "sites.disable")
    site = await _site(session, principal, site_id, lock=True)
    if site.lifecycle_state == "disabled":
        return await _site_payload(session, site)
    if site.lifecycle_state == "removed":
        raise ProblemError(
            409, "Site is removed", "Restore the site before enabling it", "site_removed"
        )
    _stale(site, payload.revision)
    if site.is_default:
        session.add(
            audit_event(
                action="site.disable_blocked",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="site",
                object_id=site.id,
                outcome="blocked",
                details={"reason": "default_site", "revision": site.revision},
            )
        )
        await session.commit()
        raise ProblemError(
            409,
            "Default site cannot be disabled",
            "Set another active site as default first",
            "default_site_protected",
        )
    if await _active_count(session) <= 1:
        session.add(
            audit_event(
                action="site.disable_blocked",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="site",
                object_id=site.id,
                outcome="blocked",
                details={"reason": "last_active_site", "revision": site.revision},
            )
        )
        await session.commit()
        raise ProblemError(
            409,
            "Last active site cannot be disabled",
            "At least one active administrative site must remain",
            "last_active_site",
        )
    site.lifecycle_state = "disabled"
    site.disabled_at = datetime.now(UTC)
    site.disabled_by = principal.user.id
    site.revision += 1
    session.add(
        audit_event(
            action="site.disabled",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=site.id,
            details={
                "revision": site.revision,
                "reason": payload.reason,
                "ingestion_continues": True,
                "new_assignments_blocked": True,
            },
        )
    )
    await session.commit()
    await session.refresh(site)
    return await _site_payload(session, site)


@router.post("/{site_id}/enable")
async def enable_site(
    site_id: str,
    payload: SiteLifecycleRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "sites.disable")
    site = await _site(session, principal, site_id, lock=True)
    if site.lifecycle_state == "active":
        return await _site_payload(session, site)
    if site.lifecycle_state == "removed":
        raise ProblemError(
            409, "Site is removed", "Restore the site before enabling it", "site_removed"
        )
    _stale(site, payload.revision)
    site.lifecycle_state = "active"
    site.disabled_at = None
    site.disabled_by = None
    site.revision += 1
    session.add(
        audit_event(
            action="site.enabled",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=site.id,
            details={"revision": site.revision, "reason": payload.reason},
        )
    )
    await session.commit()
    await session.refresh(site)
    return await _site_payload(session, site)


@router.get("/{site_id}/dependencies")
async def site_dependencies(
    site_id: str, principal: Principal, session: DbSession
) -> dict[str, Any]:
    _permission(principal, "sites.remove")
    return await _dependencies(session, await _site(session, principal, site_id))


@router.post("/{site_id}/transfer-resources")
async def resolve_site_dependencies(
    site_id: str,
    payload: SiteDependencyResolution,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "sites.transfer_resources")
    site = await _site(session, principal, site_id, lock=True)
    if site.lifecycle_state == "removed":
        raise ProblemError(
            409, "Site is removed", "Removed-site dependencies are immutable", "site_removed"
        )
    _stale(site, payload.revision)
    now = datetime.now(UTC)
    sensor_summary: list[dict[str, Any]] = []
    for resolution in payload.sensors:
        device = await session.get(Device, resolution.device_id)
        if device is None or device.site_id != site.id or device.lifecycle_status != "active":
            raise ProblemError(
                409,
                "Sensor dependency changed",
                "Refresh the dependency review before continuing",
                "site_sensor_dependency_changed",
            )
        if resolution.action == "archive":
            device.lifecycle_status = "decommissioned"
            device.lifecycle_generation += 1
            device.decommissioned_at = now
            device.decommissioned_by = principal.user.id
            device.decommission_reason = "site_removed"
            session.add(
                DeviceLifecycleEvent(
                    device_id=device.id,
                    generation=device.lifecycle_generation,
                    event_type="decommissioned",
                    occurred_at=now,
                    actor_id=principal.user.id,
                    reason="site_removed",
                    site_id=site.id,
                    circuit_id=device.circuit_id,
                    details={"site_removal_resolution": True},
                )
            )
            sensor_summary.append({"device_id": device.id, "action": "archived"})
            session.add(
                audit_event(
                    action="site.sensor_archived",
                    actor_type="user",
                    actor_id=principal.user.id,
                    request=request,
                    object_type="site",
                    object_id=site.id,
                    details={
                        "revision": site.revision + 1,
                        "device_id": device.id,
                        "history_preserved": True,
                        "reason": payload.reason,
                    },
                )
            )
        else:
            target = await session.get(Site, resolution.target_site_id)
            if (
                target is None
                or target.lifecycle_state != "active"
                or not principal.can_access_site(target.id)
            ):
                raise ProblemError(
                    409,
                    "Transfer target unavailable",
                    "Choose an authorized active destination site",
                    "site_transfer_target_unavailable",
                )
            current = await session.scalar(
                select(DeviceSiteAssignment).where(
                    DeviceSiteAssignment.device_id == device.id,
                    DeviceSiteAssignment.effective_to.is_(None),
                )
            )
            if current:
                current.effective_to = now
            session.add(
                DeviceSiteAssignment(
                    device_id=device.id,
                    site_id=target.id,
                    effective_from=now,
                    assigned_by=principal.user.id,
                    reason=payload.reason,
                    created_at=now,
                )
            )
            prior_site_id = device.site_id
            device.site_id = target.id
            device.circuit_id = None
            device.utility_account_id = None
            device.lifecycle_generation += 1
            session.add(
                DeviceLifecycleEvent(
                    device_id=device.id,
                    generation=device.lifecycle_generation,
                    event_type="site_transferred",
                    occurred_at=now,
                    actor_id=principal.user.id,
                    reason="site_transfer",
                    site_id=prior_site_id,
                    details={
                        "from_site_id": prior_site_id,
                        "to_site_id": target.id,
                        "raw_history_preserved": True,
                    },
                )
            )
            sensor_summary.append(
                {
                    "device_id": device.id,
                    "action": "transferred",
                    "target_site_id": target.id,
                }
            )
            session.add(
                audit_event(
                    action="site.sensor_transferred",
                    actor_type="user",
                    actor_id=principal.user.id,
                    request=request,
                    object_type="site",
                    object_id=site.id,
                    details={
                        "revision": site.revision + 1,
                        "device_id": device.id,
                        "from_site_id": prior_site_id,
                        "to_site_id": target.id,
                        "effective_at": now.isoformat(),
                        "history_preserved": True,
                        "reason": payload.reason,
                    },
                )
            )
    account_summary: list[dict[str, Any]] = []
    for account_resolution in payload.utility_accounts:
        account = await session.get(UtilityAccount, account_resolution.utility_account_id)
        if account is None or account.site_id != site.id or account.status != "active":
            raise ProblemError(
                409,
                "Account dependency changed",
                "Refresh the dependency review before continuing",
                "site_account_dependency_changed",
            )
        if account_resolution.action == "archive":
            account.status = "archived"
            account.archived_at = now
            account.archived_by = principal.user.id
            account.revision += 1
            account_summary.append({"utility_account_id": account.id, "action": "archived"})
            session.add(
                audit_event(
                    action="site.utility_account_archived",
                    actor_type="user",
                    actor_id=principal.user.id,
                    request=request,
                    object_type="site",
                    object_id=site.id,
                    details={
                        "revision": site.revision + 1,
                        "utility_account_id": account.id,
                        "history_preserved": True,
                        "reason": payload.reason,
                    },
                )
            )
        else:
            target = await session.get(Site, account_resolution.target_site_id)
            if (
                target is None
                or target.lifecycle_state != "active"
                or not principal.can_access_site(target.id)
            ):
                raise ProblemError(
                    409,
                    "Transfer target unavailable",
                    "Choose an authorized active destination site",
                    "site_transfer_target_unavailable",
                )
            current = await session.scalar(
                select(UtilityAccountSiteAssignment).where(
                    UtilityAccountSiteAssignment.utility_account_id == account.id,
                    UtilityAccountSiteAssignment.effective_to.is_(None),
                )
            )
            if current:
                current.effective_to = now
            session.add(
                UtilityAccountSiteAssignment(
                    utility_account_id=account.id,
                    site_id=target.id,
                    effective_from=now,
                    assigned_by=principal.user.id,
                    reason=payload.reason,
                    created_at=now,
                )
            )
            account.site_id = target.id
            account.revision += 1
            account_summary.append(
                {
                    "utility_account_id": account.id,
                    "action": "transferred",
                    "target_site_id": target.id,
                }
            )
            session.add(
                audit_event(
                    action="site.utility_account_transferred",
                    actor_type="user",
                    actor_id=principal.user.id,
                    request=request,
                    object_type="site",
                    object_id=site.id,
                    details={
                        "revision": site.revision + 1,
                        "utility_account_id": account.id,
                        "from_site_id": site.id,
                        "to_site_id": target.id,
                        "effective_at": now.isoformat(),
                        "history_preserved": True,
                        "reason": payload.reason,
                    },
                )
            )
    if payload.end_user_access_ids:
        assigned = set(
            await session.scalars(
                select(UserSite.user_id).where(
                    UserSite.site_id == site.id,
                    UserSite.user_id.in_(payload.end_user_access_ids),
                )
            )
        )
        if assigned != set(payload.end_user_access_ids):
            raise ProblemError(
                409,
                "User dependency changed",
                "Refresh the dependency review before continuing",
                "site_user_dependency_changed",
            )
        await session.execute(
            delete(UserSite).where(
                UserSite.site_id == site.id,
                UserSite.user_id.in_(payload.end_user_access_ids),
            )
        )
        session.add(
            audit_event(
                action="site.access_assignments_ended",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="site",
                object_id=site.id,
                details={
                    "revision": site.revision + 1,
                    "user_ids": payload.end_user_access_ids,
                    "users_deleted": False,
                    "reason": payload.reason,
                },
            )
        )
    site.revision += 1
    summary = {
        "revision": site.revision,
        "reason": payload.reason,
        "sensors": sensor_summary,
        "utility_accounts": account_summary,
        "ended_user_access_ids": payload.end_user_access_ids,
    }
    session.add(
        audit_event(
            action="site.dependencies_resolved",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=site.id,
            details=summary,
        )
    )
    await session.commit()
    await session.refresh(site)
    return {"site": await _site_payload(session, site), "resolution": summary}


@router.post("/{site_id}/remove")
async def remove_site(
    site_id: str,
    payload: SiteRemoveRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "sites.remove")
    site = await _site(session, principal, site_id, lock=True)
    if site.lifecycle_state == "removed":
        return await _site_payload(session, site)
    _stale(site, payload.revision)
    if payload.confirmation not in {site.name, site.code}:
        raise ProblemError(
            422,
            "Confirmation does not match",
            "Type the exact site name or stable code",
            "site_confirmation_mismatch",
        )
    if not payload.dependency_reviewed:
        raise ProblemError(
            409,
            "Dependency review required",
            "Review retained and active dependencies before removing the site",
            "site_dependency_review_required",
        )
    dependencies = await _dependencies(session, site)
    if dependencies["blockers"] or dependencies["required_actions"]:
        session.add(
            audit_event(
                action="site.removal_blocked",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="site",
                object_id=site.id,
                outcome="blocked",
                details={
                    "revision": site.revision,
                    "blockers": dependencies["blockers"],
                    "required_actions": dependencies["required_actions"],
                },
            )
        )
        await session.commit()
        raise ProblemError(
            409,
            "Site dependencies are unresolved",
            "Resolve active resources and protection rules before removal",
            "site_dependencies_unresolved",
            extra={"dependencies": dependencies},
        )
    now = datetime.now(UTC)
    session.add(
        audit_event(
            action="site.removal_initiated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=site.id,
            details={
                "revision": site.revision,
                "reason": payload.reason,
                "dependency_reviewed": True,
                "retained": dependencies["retained"],
            },
        )
    )
    active_tokens = list(
        await session.scalars(
            select(EnrollmentToken).where(
                EnrollmentToken.consumed_at.is_(None),
                EnrollmentToken.revoked_at.is_(None),
            )
        )
    )
    for token in active_tokens:
        if token.preassignment.get("site_id") == site.id:
            token.revoked_at = now
    site.lifecycle_state = "removed"
    site.is_default = False
    site.removed_at = now
    site.removed_by = principal.user.id
    site.removal_reason = payload.reason
    site.revision += 1
    session.add(
        audit_event(
            action="site.removed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=site.id,
            details={
                "revision": site.revision,
                "reason": payload.reason,
                "retained": dependencies["retained"],
                "stable_tombstone": True,
                "hard_deleted": False,
            },
        )
    )
    await session.commit()
    await session.refresh(site)
    return await _site_payload(session, site)


@router.post("/{site_id}/restore")
async def restore_site(
    site_id: str,
    payload: SiteRestoreRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "sites.restore")
    site = await _site(session, principal, site_id, lock=True)
    if site.lifecycle_state == "disabled" and site.restored_at:
        return await _site_payload(session, site)
    if site.lifecycle_state != "removed":
        raise ProblemError(
            409, "Site is not removed", "Only removed sites can be restored", "site_not_removed"
        )
    _stale(site, payload.revision)
    if not payload.confirm_high_risk:
        raise ProblemError(
            409,
            "High-risk confirmation required",
            "Review users, policies, sensors, accounts, and rates before restoration",
            "site_restore_confirmation_required",
        )
    site.lifecycle_state = "disabled"
    site.is_default = False
    site.disabled_at = datetime.now(UTC)
    site.disabled_by = principal.user.id
    site.restored_at = site.disabled_at
    site.restored_by = principal.user.id
    site.revision += 1
    session.add(
        audit_event(
            action="site.restored",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=site.id,
            details={
                "revision": site.revision,
                "reason": payload.reason,
                "restored_state": "disabled",
                "default_site": False,
                "sensors_reactivated": False,
                "accounts_reactivated": False,
                "access_assignments_restored": False,
                "review_required": [
                    "users",
                    "network_policy",
                    "sensors",
                    "utility_accounts",
                    "rates",
                ],
            },
        )
    )
    await session.commit()
    await session.refresh(site)
    return await _site_payload(session, site)


@router.get("/{site_id}/audit")
async def site_audit(
    site_id: str, principal: Principal, session: DbSession
) -> list[dict[str, Any]]:
    _permission(principal, "sites.view_audit")
    await _site(session, principal, site_id)
    events = list(
        await session.scalars(
            select(AuditEvent)
            .where(AuditEvent.object_type == "site", AuditEvent.object_id == site_id)
            .order_by(AuditEvent.occurred_at.desc())
            .limit(500)
        )
    )
    return [
        {
            "id": item.id,
            "occurred_at": item.occurred_at,
            "actor_type": item.actor_type,
            "actor_id": item.actor_id,
            "action": item.action,
            "outcome": item.outcome,
            "correlation_id": item.correlation_id,
            "details": item.details,
        }
        for item in events
    ]
