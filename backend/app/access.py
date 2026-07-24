from __future__ import annotations

import re
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BrowserSession,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
    UserSite,
)
from app.problem import ProblemError


@dataclass(frozen=True)
class PermissionDefinition:
    code: str
    group: str
    label: str
    description: str
    high_risk: bool = False


def _permission(
    code: str, group: str, label: str, description: str, high_risk: bool = False
) -> PermissionDefinition:
    return PermissionDefinition(code, group, label, description, high_risk)


PERMISSION_DEFINITIONS = (
    _permission(
        "overview.view", "Dashboard and data", "View overview", "View dashboard summaries."
    ),
    _permission("usage.view", "Dashboard and data", "View usage", "View energy usage."),
    _permission("history.view", "Dashboard and data", "View history", "View historical readings."),
    _permission(
        "history.export",
        "Dashboard and data",
        "Export history",
        "Export permitted historical readings.",
    ),
    _permission("costs.view", "Dashboard and data", "View costs", "View calculated costs."),
    _permission(
        "costs.export", "Dashboard and data", "Export costs", "Export permitted cost data."
    ),
    _permission(
        "costs.recalculate",
        "Dashboard and data",
        "Recalculate costs",
        "Recalculate account costs and tier allocations.",
        True,
    ),
    _permission(
        "usage_imports.manage",
        "Dashboard and data",
        "Manage usage imports",
        "Preview and commit authoritative utility usage evidence.",
        True,
    ),
    _permission("sites.view", "Sites and devices", "View sites", "View assigned sites."),
    _permission(
        "sites.manage",
        "Sites and devices",
        "Manage sites",
        "Change site boundaries and utility accounts.",
        True,
    ),
    _permission(
        "utility_accounts.view",
        "Sites and devices",
        "View utility accounts",
        "View utility accounts for assigned sites.",
    ),
    _permission(
        "utility_accounts.manage",
        "Sites and devices",
        "Manage utility accounts",
        "Create, revise, and archive assigned-site utility accounts.",
        True,
    ),
    _permission(
        "network.view",
        "Sites and devices",
        "View sensor network policy",
        "View assigned-site sensor policies and observed addresses.",
    ),
    _permission(
        "network.manage",
        "Sites and devices",
        "Manage sensor network policy",
        "Change sensor network policies and CIDRs.",
        True,
    ),
    _permission(
        "topology.view", "Sites and devices", "View topology", "View assigned-site topology."
    ),
    _permission(
        "topology.manage", "Sites and devices", "Manage topology", "Change circuits and aggregates."
    ),
    _permission("devices.view", "Sites and devices", "View devices", "View assigned-site sensors."),
    _permission(
        "devices.manage",
        "Sites and devices",
        "Manage devices",
        "Change device settings and credentials.",
    ),
    _permission(
        "devices.remove",
        "Sites and devices",
        "Remove devices",
        "Decommission enrolled devices.",
        True,
    ),
    _permission(
        "enrollment.view", "Sites and devices", "View enrollment", "View sensor enrollment state."
    ),
    _permission(
        "enrollment.manage",
        "Sites and devices",
        "Manage enrollment",
        "Create and revoke enrollment tokens.",
        True,
    ),
    _permission(
        "firmware.view",
        "Sites and devices",
        "View firmware",
        "View firmware releases and deployments.",
    ),
    _permission(
        "firmware.manage",
        "Sites and devices",
        "Manage firmware",
        "Upload and deploy signed firmware.",
        True,
    ),
    _permission("rates.view", "Rates", "View rates", "View rate plans and assignments."),
    _permission(
        "rates.manage_custom",
        "Rates",
        "Manage custom rates",
        "Create and revise custom rate plans.",
    ),
    _permission(
        "rates.manage_sources",
        "Rates",
        "Manage rate sources",
        "Configure approved rate sources.",
        True,
    ),
    _permission(
        "rates.check_sources", "Rates", "Check rate sources", "Run approved source checks."
    ),
    _permission(
        "rates.review_candidates",
        "Rates",
        "Review rate candidates",
        "Review extracted rate candidates.",
    ),
    _permission(
        "rates.approve_candidates",
        "Rates",
        "Approve rate candidates",
        "Approve or reject rate candidates.",
        True,
    ),
    _permission(
        "rates.assign", "Rates", "Assign rates", "Assign effective rates to accounts.", True
    ),
    _permission("alerts.view", "Alerts", "View alerts", "View alerts for assigned sites."),
    _permission(
        "alerts.acknowledge", "Alerts", "Acknowledge alerts", "Acknowledge and silence alerts."
    ),
    _permission(
        "alerts.manage_rules", "Alerts", "Manage alert rules", "Create and change alert rules."
    ),
    _permission(
        "alerts.manage_delivery",
        "Alerts",
        "Manage alert delivery",
        "Configure notification delivery.",
        True,
    ),
    _permission(
        "backups.view", "Backups and logs", "View backups", "View backup and restore evidence."
    ),
    _permission("backups.create", "Backups and logs", "Create backups", "Request logical backups."),
    _permission(
        "backups.restore", "Backups and logs", "Restore backups", "Restore a verified backup.", True
    ),
    _permission(
        "logs.export", "Backups and logs", "Export logs", "Export redacted application logs.", True
    ),
    _permission("users.view", "Administration", "View users", "View users and effective access."),
    _permission(
        "users.manage",
        "Administration",
        "Manage users",
        "Change roles, site scope, status, and sessions.",
        True,
    ),
    _permission(
        "users.manage_protected",
        "Administration",
        "Manage protected administrators",
        "Perform protected administrator changes.",
        True,
    ),
    _permission("roles.view", "Administration", "View roles", "View built-in and custom roles."),
    _permission(
        "roles.manage", "Administration", "Manage roles", "Create and revise custom roles.", True
    ),
    _permission(
        "audit.view", "Administration", "View audit log", "View security and administration events."
    ),
    _permission("settings.view", "Administration", "View settings", "View system settings."),
    _permission(
        "settings.manage",
        "Administration",
        "Manage settings",
        "Change global system settings.",
        True,
    ),
    _permission(
        "interface_text.view",
        "Administration",
        "View interface text",
        "View interface text and revision history.",
    ),
    _permission(
        "interface_text.manage",
        "Administration",
        "Manage interface text",
        "Draft, publish, reset, and restore interface text.",
        True,
    ),
    _permission(
        "status_indicators.view",
        "Administration",
        "View status layouts",
        "View the registered indicators and the effective published layout.",
    ),
    _permission(
        "status_indicators.manage",
        "Administration",
        "Manage status layouts",
        "Draft, preview, publish, import, reset, and restore status layouts.",
        True,
    ),
)

PERMISSION_CATALOG = {item.code: item for item in PERMISSION_DEFINITIONS}
ALL_PERMISSIONS = frozenset(PERMISSION_CATALOG)

VIEWER_PERMISSIONS = frozenset(
    {
        "overview.view",
        "usage.view",
        "history.view",
        "history.export",
        "costs.view",
        "costs.export",
        "sites.view",
        "topology.view",
        "devices.view",
        "rates.view",
        "alerts.view",
        "status_indicators.view",
    }
)
OPERATOR_PERMISSIONS = VIEWER_PERMISSIONS | {
    "topology.manage",
    "devices.manage",
    "enrollment.view",
    "enrollment.manage",
    "firmware.view",
    "alerts.acknowledge",
    "alerts.manage_rules",
}
RATE_MANAGER_PERMISSIONS = VIEWER_PERMISSIONS | {
    "rates.manage_custom",
    "rates.manage_sources",
    "rates.check_sources",
    "rates.review_candidates",
    "rates.approve_candidates",
    "rates.assign",
}
BUILTIN_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": ALL_PERMISSIONS,
    "operator": frozenset(OPERATOR_PERMISSIONS),
    "rate-manager": frozenset(RATE_MANAGER_PERMISSIONS),
    "viewer": VIEWER_PERMISSIONS,
}
BUILTIN_ROLE_NAMES = frozenset(BUILTIN_ROLE_PERMISSIONS)
BUILTIN_ROLE_LABELS = {
    "admin": "Administrator",
    "operator": "Operator",
    "rate-manager": "Rate Manager",
    "viewer": "Regular User / Read-Only Viewer",
}

PERMISSION_DEPENDENCIES: dict[str, frozenset[str]] = {
    "users.manage": frozenset({"users.view"}),
    "users.manage_protected": frozenset({"users.manage", "users.view"}),
    "roles.manage": frozenset({"roles.view"}),
    "rates.manage_custom": frozenset({"rates.view"}),
    "rates.manage_sources": frozenset({"rates.view"}),
    "rates.check_sources": frozenset({"rates.manage_sources", "rates.view"}),
    "rates.review_candidates": frozenset({"rates.view"}),
    "rates.approve_candidates": frozenset({"rates.review_candidates", "rates.view"}),
    "rates.assign": frozenset({"rates.view"}),
    "costs.recalculate": frozenset({"costs.view"}),
    "usage_imports.manage": frozenset({"usage.view", "utility_accounts.view"}),
    "backups.restore": frozenset({"backups.view"}),
    "interface_text.manage": frozenset({"interface_text.view"}),
    "status_indicators.manage": frozenset({"status_indicators.view"}),
    "sites.manage": frozenset({"sites.view"}),
    "utility_accounts.view": frozenset({"sites.view"}),
    "utility_accounts.manage": frozenset({"utility_accounts.view", "sites.view"}),
    "network.view": frozenset({"sites.view"}),
    "network.manage": frozenset({"network.view", "sites.view"}),
    "topology.manage": frozenset({"topology.view"}),
    "devices.manage": frozenset({"devices.view"}),
    "devices.remove": frozenset({"devices.manage", "devices.view"}),
    "enrollment.manage": frozenset({"enrollment.view"}),
    "firmware.manage": frozenset({"firmware.view"}),
    "alerts.acknowledge": frozenset({"alerts.view"}),
    "alerts.manage_rules": frozenset({"alerts.view"}),
    "alerts.manage_delivery": frozenset({"alerts.view"}),
    "backups.create": frozenset({"backups.view"}),
}


def permission_catalog_payload() -> list[dict[str, object]]:
    return [asdict(item) for item in PERMISSION_DEFINITIONS]


def validate_permissions(codes: set[str]) -> None:
    unknown = sorted(codes - ALL_PERMISSIONS)
    if unknown:
        raise ProblemError(
            422,
            "Unknown permission",
            "One or more permission codes are not registered",
            "permission_unknown",
            extra={"permissions": unknown},
        )
    missing: dict[str, list[str]] = {}
    for code, required in PERMISSION_DEPENDENCIES.items():
        absent = sorted(required - codes)
        if code in codes and absent:
            missing[code] = absent
    if missing:
        raise ProblemError(
            422,
            "Permission dependencies missing",
            "Add the required view or parent permissions",
            "permission_dependencies_missing",
            extra={"dependencies": missing},
        )


async def ensure_access_catalog(session: AsyncSession) -> None:
    descriptions = {
        "admin": "Full system and security administration",
        "operator": "Devices, enrollment, alerts, firmware, and assigned-site operations",
        "rate-manager": "Create, review, approve, and assign rate plans",
        "viewer": "Read-only dashboard and permitted exports",
    }
    for definition in PERMISSION_DEFINITIONS:
        permission = await session.get(Permission, definition.code)
        if permission is None:
            session.add(
                Permission(
                    code=definition.code,
                    group_name=definition.group,
                    label=definition.label,
                    description=definition.description,
                    high_risk=definition.high_risk,
                )
            )
        else:
            permission.group_name = definition.group
            permission.label = definition.label
            permission.description = definition.description
            permission.high_risk = definition.high_risk
    for name, configured_permissions in BUILTIN_ROLE_PERMISSIONS.items():
        role = await session.get(Role, name)
        if role is None:
            role = Role(
                name=name,
                display_name=BUILTIN_ROLE_LABELS[name],
                description=descriptions[name],
                is_builtin=True,
            )
            session.add(role)
            await session.flush()
        else:
            role.display_name = BUILTIN_ROLE_LABELS[name]
            role.is_builtin = True
            role.is_archived = False
        existing = set(
            await session.scalars(
                select(RolePermission.permission_code).where(RolePermission.role_name == name)
            )
        )
        for permission_code in existing - configured_permissions:
            await session.execute(
                delete(RolePermission).where(
                    RolePermission.role_name == name,
                    RolePermission.permission_code == permission_code,
                )
            )
        for permission_code in configured_permissions - existing:
            session.add(RolePermission(role_name=name, permission_code=permission_code))


async def user_role_names(session: AsyncSession, user_id: str) -> frozenset[str]:
    return frozenset(
        await session.scalars(
            select(UserRole.role_name)
            .join(Role, Role.name == UserRole.role_name)
            .where(UserRole.user_id == user_id, Role.is_archived.is_(False))
        )
    )


async def permissions_for_roles(session: AsyncSession, role_names: set[str]) -> frozenset[str]:
    if "admin" in role_names:
        return ALL_PERMISSIONS
    if not role_names:
        return frozenset()
    stored = set(
        await session.scalars(
            select(RolePermission.permission_code)
            .join(Role, Role.name == RolePermission.role_name)
            .where(RolePermission.role_name.in_(role_names), Role.is_archived.is_(False))
        )
    )
    for role_name in role_names:
        stored.update(BUILTIN_ROLE_PERMISSIONS.get(role_name, ()))
    return frozenset(stored)


async def effective_permissions(session: AsyncSession, user_id: str) -> frozenset[str]:
    return await permissions_for_roles(session, set(await user_role_names(session, user_id)))


async def explicit_site_ids(session: AsyncSession, user_id: str) -> frozenset[str]:
    return frozenset(
        await session.scalars(select(UserSite.site_id).where(UserSite.user_id == user_id))
    )


def can_access_site(*, all_sites: bool, site_ids: frozenset[str], site_id: str) -> bool:
    return all_sites or site_id in site_ids


async def revoke_user_sessions(
    session: AsyncSession, user_id: str, *, exclude_session_id: str | None = None
) -> int:
    now = datetime.now(UTC)
    conditions = [BrowserSession.user_id == user_id, BrowserSession.revoked_at.is_(None)]
    if exclude_session_id:
        conditions.append(BrowserSession.id != exclude_session_id)
    count = await session.scalar(
        select(func.count()).select_from(BrowserSession).where(*conditions)
    )
    await session.execute(update(BrowserSession).where(*conditions).values(revoked_at=now))
    return int(count or 0)


async def replace_user_sites(session: AsyncSession, user_id: str, site_ids: set[str]) -> None:
    await session.execute(delete(UserSite).where(UserSite.user_id == user_id))
    for site_id in sorted(site_ids):
        session.add(UserSite(user_id=user_id, site_id=site_id))


def require_recent_reauthentication(reauthenticated_at: datetime | None) -> None:
    value = reauthenticated_at
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if value is None or value < datetime.now(UTC) - timedelta(minutes=5):
        raise ProblemError(
            428,
            "Reauthentication required",
            "Confirm your current password or MFA code before this protected change",
            "reauthentication_required",
        )


def custom_role_identifier() -> str:
    return f"custom-{secrets.token_hex(10)}"


def validate_role_display_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not 3 <= len(normalized) <= 120 or re.search(r"[\x00-\x1f\x7f]", normalized):
        raise ProblemError(
            422,
            "Invalid role name",
            "Role names must contain 3 to 120 printable characters",
            "role_name_invalid",
        )
    return normalized


async def active_admin_count(
    session: AsyncSession, *, excluding_user_id: str | None = None, lock: bool = False
) -> int:
    query = (
        select(UserRole.user_id)
        .join(User, User.id == UserRole.user_id)
        .where(User.is_active.is_(True), UserRole.role_name == "admin")
    )
    if excluding_user_id:
        query = query.where(User.id != excluding_user_id)
    if lock:
        query = query.with_for_update(of=UserRole)
    return len(list(await session.scalars(query)))
