from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.access import (
    BUILTIN_ROLE_LABELS,
    PERMISSION_CATALOG,
    PERMISSION_DEPENDENCIES,
    active_recovery_administrator_count,
    custom_role_identifier,
    effective_permissions,
    explicit_site_ids,
    permission_catalog_payload,
    permissions_for_roles,
    replace_user_sites,
    require_recent_reauthentication,
    revoke_user_sessions,
    user_role_names,
    validate_permissions,
    validate_role_display_name,
)
from app.api.deps import CsrfPrincipal, DbSession, Principal, audit_event
from app.db.models import (
    AuditEvent,
    BrowserSession,
    Role,
    RolePermission,
    RoleRevision,
    Site,
    TotpCredential,
    User,
    UserRole,
)
from app.problem import ProblemError
from app.schemas import (
    RoleWrite,
    UserAccessUpdate,
    UserRemovalRequest,
    UserRestoreRequest,
    UserStatusChange,
)

router = APIRouter(prefix="/api/v1/admin", tags=["users and access"])
_high_risk_attempts: dict[str, deque[float]] = defaultdict(deque)


def _require(principal: Principal, permission: str) -> None:
    if permission not in principal.permissions:
        raise ProblemError(
            403,
            "Permission denied",
            "Your account does not have the required permission",
            "forbidden",
            extra={"required_permission": permission},
        )


def _rate_limit(principal: Principal, action: str) -> None:
    key = f"{principal.user.id}:{action}"
    now = time.monotonic()
    attempts = _high_risk_attempts[key]
    while attempts and attempts[0] < now - 300:
        attempts.popleft()
    if len(attempts) >= 20:
        raise ProblemError(
            429,
            "Too many protected changes",
            "Wait five minutes before trying another protected change",
            "protected_change_throttled",
        )
    attempts.append(now)


async def _role_permissions(session: DbSession, role_name: str) -> list[str]:
    return sorted(
        await session.scalars(
            select(RolePermission.permission_code).where(RolePermission.role_name == role_name)
        )
    )


async def _role_payload(session: DbSession, role: Role) -> dict[str, Any]:
    assigned_users = await session.scalar(
        select(func.count()).select_from(UserRole).where(UserRole.role_name == role.name)
    )
    return {
        "id": role.name,
        "display_name": role.display_name or BUILTIN_ROLE_LABELS.get(role.name, role.name),
        "description": role.description,
        "built_in": role.is_builtin,
        "archived": role.is_archived,
        "revision": role.revision,
        "permissions": await _role_permissions(session, role.name),
        "assigned_user_count": int(assigned_users or 0),
        "created_at": role.created_at,
        "updated_at": role.updated_at,
    }


async def _target_scope_allowed(session: DbSession, principal: Principal, user: User) -> bool:
    if principal.all_sites:
        return True
    all_sites = user.removed_all_sites if user.lifecycle_state == "removed" else user.all_sites
    site_ids = (
        set(user.removed_site_ids)
        if user.lifecycle_state == "removed"
        else set(await explicit_site_ids(session, user.id))
    )
    if all_sites:
        return False
    return site_ids.issubset(principal.site_ids)


async def _user_payload(session: DbSession, user: User, *, detail: bool = False) -> dict[str, Any]:
    roles = sorted(await user_role_names(session, user.id))
    permissions = sorted(await effective_permissions(session, user.id))
    site_ids = sorted(await explicit_site_ids(session, user.id))
    sites = []
    if site_ids:
        sites = [
            {"id": site.id, "name": site.name}
            for site in await session.scalars(
                select(Site).where(Site.id.in_(site_ids)).order_by(Site.name)
            )
        ]
    active_sessions = int(
        await session.scalar(
            select(func.count())
            .select_from(BrowserSession)
            .where(
                BrowserSession.user_id == user.id,
                BrowserSession.revoked_at.is_(None),
                BrowserSession.expires_at > datetime.now(UTC),
            )
        )
        or 0
    )
    mfa = await session.get(TotpCredential, user.id)
    payload: dict[str, Any] = {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_active": user.is_active,
        "status": user.lifecycle_state,
        "roles": roles,
        "all_sites": user.all_sites,
        "sites": sites,
        "site_ids": site_ids,
        "permissions": permissions,
        "permission_count": len(permissions),
        "mfa_enabled": bool(mfa and mfa.confirmed),
        "last_login_at": user.last_login_at,
        "active_session_count": active_sessions,
        "created_at": user.created_at,
        "access_revision": user.access_revision,
        "protected_administrator": user.is_protected or "admin" in roles,
        "protected_account": user.is_protected,
        "removed_at": user.removed_at,
        "removed_by": user.removed_by,
        "removal_reason": user.removal_reason,
        "restored_at": user.restored_at,
        "restored_by": user.restored_by,
        "former_access": {
            "roles": list(user.removed_role_ids),
            "all_sites": user.removed_all_sites,
            "site_ids": list(user.removed_site_ids),
        }
        if user.lifecycle_state == "removed"
        else None,
    }
    if detail:
        payload["sessions"] = [
            {
                "id": item.id,
                "created_at": item.created_at,
                "last_seen_at": item.last_seen_at,
                "expires_at": item.expires_at,
                "source_ip": item.source_ip,
                "user_agent": item.user_agent,
            }
            for item in await session.scalars(
                select(BrowserSession)
                .where(
                    BrowserSession.user_id == user.id,
                    BrowserSession.revoked_at.is_(None),
                    BrowserSession.expires_at > datetime.now(UTC),
                )
                .order_by(BrowserSession.last_seen_at.desc())
            )
        ]
        payload["permission_sources"] = {
            role_name: await _role_permissions(session, role_name) for role_name in roles
        }
    return payload


async def _load_manageable_user(
    session: DbSession, principal: Principal, user_id: str, *, lock: bool = False
) -> User:
    query = select(User).where(User.id == user_id)
    if lock:
        query = query.with_for_update()
    user = await session.scalar(query)
    if user is None or not await _target_scope_allowed(session, principal, user):
        raise ProblemError(404, "User not found", "User does not exist", "user_missing")
    return user


async def _audit_denial(
    session: DbSession,
    request: Request,
    principal: Principal,
    *,
    action: str,
    object_type: str,
    object_id: str,
    code: str,
) -> None:
    session.add(
        audit_event(
            action=action,
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type=object_type,
            object_id=object_id,
            outcome="denied",
            details={"code": code},
        )
    )
    await session.commit()


@router.get("/permissions")
async def list_permissions(principal: Principal) -> dict[str, Any]:
    _require(principal, "roles.view")
    return {
        "permissions": permission_catalog_payload(),
        "dependencies": {
            code: sorted(required) for code, required in PERMISSION_DEPENDENCIES.items()
        },
    }


@router.get("/users")
async def list_managed_users(
    principal: Principal,
    session: DbSession,
    search: str | None = Query(default=None, max_length=160),
    status: str | None = Query(default=None, pattern="^(active|disabled|removed)$"),
    include_removed: bool = False,
    role: str | None = Query(default=None, max_length=32),
    site_id: str | None = Query(default=None, max_length=36),
    mfa_enabled: bool | None = None,
    protected: bool | None = None,
) -> dict[str, Any]:
    _require(principal, "users.view")
    query = select(User).order_by(User.display_name, User.email)
    if search:
        pattern = f"%{search.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(User.display_name).like(pattern),
                func.lower(User.email).like(pattern),
            )
        )
    if status:
        query = query.where(User.lifecycle_state == status)
    elif not include_removed:
        query = query.where(User.lifecycle_state != "removed")
    users = []
    for user in await session.scalars(query):
        if not await _target_scope_allowed(session, principal, user):
            continue
        payload = await _user_payload(session, user)
        if role and role not in payload["roles"]:
            continue
        if site_id and not payload["all_sites"] and site_id not in payload["site_ids"]:
            continue
        if mfa_enabled is not None and payload["mfa_enabled"] is not mfa_enabled:
            continue
        if protected is not None and payload["protected_administrator"] is not protected:
            continue
        users.append(payload)
    return {"users": users}


@router.get("/users/{user_id}")
async def get_managed_user(
    user_id: str, principal: Principal, session: DbSession
) -> dict[str, Any]:
    _require(principal, "users.view")
    return await _user_payload(
        session, await _load_manageable_user(session, principal, user_id), detail=True
    )


@router.put("/users/{user_id}/access")
async def update_user_access(
    user_id: str,
    payload: UserAccessUpdate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "users.manage")
    _rate_limit(principal, "user-access")
    user = await _load_manageable_user(session, principal, user_id, lock=True)
    if user.lifecycle_state == "removed":
        raise ProblemError(
            409,
            "User is removed",
            "Restore the user before assigning roles or site access",
            "user_removed",
        )
    if user.access_revision != payload.expected_revision:
        raise ProblemError(
            409,
            "User access changed",
            "Reload the user before applying your changes",
            "access_revision_conflict",
        )
    requested_roles = set(payload.role_ids)
    roles = list(
        await session.scalars(
            select(Role).where(Role.name.in_(requested_roles), Role.is_archived.is_(False))
        )
    )
    if len(roles) != len(requested_roles):
        raise ProblemError(422, "Invalid role", "A selected role is unavailable", "role_invalid")
    requested_permissions = await permissions_for_roles(session, requested_roles)
    if not requested_permissions.issubset(principal.permissions):
        await _audit_denial(
            session,
            request,
            principal,
            action="user.access_change_rejected",
            object_type="user",
            object_id=user.id,
            code="permission_delegation_forbidden",
        )
        raise ProblemError(
            403,
            "Privilege delegation denied",
            "You cannot grant permissions that you do not possess",
            "permission_delegation_forbidden",
        )
    requested_sites = set(payload.site_ids)
    if payload.all_sites and not principal.all_sites:
        raise ProblemError(
            403,
            "Site delegation denied",
            "You cannot grant all-site access",
            "site_delegation_forbidden",
        )
    if not payload.all_sites:
        if not principal.all_sites and not requested_sites.issubset(principal.site_ids):
            raise ProblemError(
                403,
                "Site delegation denied",
                "You cannot grant access to an unassigned site",
                "site_delegation_forbidden",
            )
        existing_sites = set(
            await session.scalars(select(Site.id).where(Site.id.in_(requested_sites)))
        )
        if existing_sites != requested_sites:
            raise ProblemError(
                403,
                "Site delegation denied",
                "One or more selected sites are unavailable",
                "site_delegation_forbidden",
            )
    prior_roles = set(await user_role_names(session, user.id))
    prior_permissions = await effective_permissions(session, user.id)
    prior_sites = set(await explicit_site_ids(session, user.id))
    prior_all_sites = user.all_sites
    removes_admin = "admin" in prior_roles and "admin" not in requested_roles
    if (
        removes_admin
        and await active_recovery_administrator_count(session, excluding_user_id=user.id, lock=True)
        == 0
    ):
        await _audit_denial(
            session,
            request,
            principal,
            action="user.access_change_rejected",
            object_type="user",
            object_id=user.id,
            code="last_admin_required",
        )
        raise ProblemError(
            409,
            "Last administrator is protected",
            "Create another active administrator before changing this account",
            "last_admin_required",
        )
    self_restriction = user.id == principal.user.id and (
        "users.manage" not in requested_permissions
        or "roles.manage" not in requested_permissions
        or (principal.all_sites and not payload.all_sites)
    )
    high_risk = (
        ("admin" in prior_roles) != ("admin" in requested_roles)
        or not requested_permissions.issubset(prior_permissions)
        or self_restriction
    )
    if "admin" in prior_roles and "users.manage_protected" not in principal.permissions:
        raise ProblemError(
            403,
            "Protected administrator",
            "Your account cannot change protected administrators",
            "protected_administrator_forbidden",
        )
    if high_risk:
        if not payload.confirm_high_risk:
            raise ProblemError(
                409,
                "Protected confirmation required",
                "Confirm the permission increase or administrator-role change",
                "high_risk_confirmation_required",
            )
        require_recent_reauthentication(principal.session.reauthenticated_at)
    if (
        self_restriction
        and await active_recovery_administrator_count(session, excluding_user_id=user.id, lock=True)
        == 0
    ):
        raise ProblemError(
            409,
            "Self-lockout prevented",
            "Another active administrator must retain user and role management",
            "self_lockout_prevented",
        )
    await session.execute(delete(UserRole).where(UserRole.user_id == user.id))
    for role_id in sorted(requested_roles):
        session.add(UserRole(user_id=user.id, role_name=role_id))
    await replace_user_sites(session, user.id, requested_sites if not payload.all_sites else set())
    user.all_sites = payload.all_sites
    user.access_revision += 1
    revoked = await revoke_user_sessions(session, user.id)
    session.add(
        audit_event(
            action="user.access_updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="user",
            object_id=user.id,
            details={
                "prior": {
                    "roles": sorted(prior_roles),
                    "all_sites": prior_all_sites,
                    "site_ids": sorted(prior_sites),
                    "permissions": sorted(prior_permissions),
                },
                "new": {
                    "roles": sorted(requested_roles),
                    "all_sites": payload.all_sites,
                    "site_ids": sorted(requested_sites),
                    "permissions": sorted(requested_permissions),
                },
                "sessions_revoked": revoked,
                "reason": payload.reason,
            },
        )
    )
    await session.commit()
    result = await _user_payload(session, user, detail=True)
    result["sessions_revoked"] = revoked
    return result


async def _set_user_active(
    *,
    active: bool,
    user_id: str,
    payload: UserStatusChange,
    request: Request,
    principal: Principal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "users.disable")
    _rate_limit(principal, "user-status")
    user = await _load_manageable_user(session, principal, user_id, lock=True)
    if user.lifecycle_state == "removed":
        raise ProblemError(
            409,
            "User is removed",
            "Restore the user before changing sign-in status",
            "user_removed",
        )
    if payload.expected_revision is not None and user.access_revision != payload.expected_revision:
        raise ProblemError(
            409,
            "User status changed",
            "Reload the user before applying your change",
            "access_revision_conflict",
        )
    roles = await user_role_names(session, user.id)
    if active and not roles:
        raise ProblemError(
            409,
            "User access is not assigned",
            "Assign at least one role before enabling sign-in",
            "user_access_required",
        )
    permissions = await effective_permissions(session, user.id)
    recovery_permissions = {
        "users.manage",
        "roles.manage",
        "users.disable",
        "users.remove",
        "users.restore",
    }
    recovery_administrator = (
        user.is_active and user.all_sites and recovery_permissions.issubset(permissions)
    )
    if (
        not active
        and recovery_administrator
        and await active_recovery_administrator_count(session, excluding_user_id=user.id, lock=True)
        == 0
    ):
        await _audit_denial(
            session,
            request,
            principal,
            action="user.disable_rejected",
            object_type="user",
            object_id=user.id,
            code="last_admin_required",
        )
        raise ProblemError(
            409,
            "Last administrator is protected",
            "Create another active administrator before disabling this account",
            "last_admin_required",
        )
    if not active and user.id == principal.user.id:
        raise ProblemError(
            409,
            "Self-disable prevented",
            "Use another administrator account to disable this user",
            "self_deactivation_forbidden",
        )
    if not active and user.is_protected:
        raise ProblemError(
            409,
            "Protected bootstrap account",
            "The protected bootstrap account cannot be disabled",
            "protected_account",
        )
    if not active and ("admin" in roles or recovery_administrator):
        if not payload.confirm_high_risk:
            raise ProblemError(
                409,
                "Protected confirmation required",
                "Confirm that this administrator should be disabled",
                "high_risk_confirmation_required",
            )
        require_recent_reauthentication(principal.session.reauthenticated_at)
    changed = user.is_active is not active or user.lifecycle_state != (
        "active" if active else "disabled"
    )
    user.is_active = active
    user.lifecycle_state = "active" if active else "disabled"
    revoked = await revoke_user_sessions(session, user.id) if not active else 0
    if changed:
        user.access_revision += 1
        session.add(
            audit_event(
                action="user.enabled" if active else "user.disabled",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="user",
                object_id=user.id,
                details={"reason": payload.reason, "sessions_revoked": revoked},
            )
        )
    await session.commit()
    return {
        "changed": changed,
        "sessions_revoked": revoked,
        "user": await _user_payload(session, user),
    }


@router.post("/users/{user_id}/enable")
async def enable_user(
    user_id: str,
    payload: UserStatusChange,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    return await _set_user_active(
        active=True,
        user_id=user_id,
        payload=payload,
        request=request,
        principal=principal,
        session=session,
    )


@router.post("/users/{user_id}/disable")
async def disable_user(
    user_id: str,
    payload: UserStatusChange,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    return await _set_user_active(
        active=False,
        user_id=user_id,
        payload=payload,
        request=request,
        principal=principal,
        session=session,
    )


@router.post("/users/{user_id}/remove")
async def remove_user(
    user_id: str,
    payload: UserRemovalRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "users.remove")
    _rate_limit(principal, "user-remove")
    user = await _load_manageable_user(session, principal, user_id, lock=True)
    if user.lifecycle_state == "removed":
        return {
            "changed": False,
            "sessions_revoked": 0,
            "user": await _user_payload(session, user),
        }
    if user.access_revision != payload.expected_revision:
        raise ProblemError(
            409,
            "User access changed",
            "Reload the user before removing the account",
            "access_revision_conflict",
        )
    confirmation = payload.confirmation.strip()
    if confirmation.lower() != user.email.lower() and confirmation != user.id[-8:]:
        raise ProblemError(
            422,
            "Removal confirmation does not match",
            "Enter the user's exact email address or final eight ID characters",
            "removal_confirmation_mismatch",
        )
    if user.id == principal.user.id:
        await _audit_denial(
            session,
            request,
            principal,
            action="user.remove_rejected",
            object_type="user",
            object_id=user.id,
            code="self_removal_forbidden",
        )
        raise ProblemError(
            409,
            "Self-removal prevented",
            "Use another administrator account to remove this user",
            "self_removal_forbidden",
        )
    if user.is_protected:
        await _audit_denial(
            session,
            request,
            principal,
            action="user.remove_rejected",
            object_type="user",
            object_id=user.id,
            code="protected_account",
        )
        raise ProblemError(
            409,
            "Protected bootstrap account",
            "The protected bootstrap account cannot be removed",
            "protected_account",
        )
    roles = sorted(await user_role_names(session, user.id))
    permissions = sorted(await effective_permissions(session, user.id))
    site_ids = sorted(await explicit_site_ids(session, user.id))
    recovery_permissions = {
        "users.manage",
        "roles.manage",
        "users.disable",
        "users.remove",
        "users.restore",
    }
    recovery_administrator = (
        user.is_active and user.all_sites and recovery_permissions.issubset(permissions)
    )
    if (
        recovery_administrator
        and await active_recovery_administrator_count(session, excluding_user_id=user.id, lock=True)
        == 0
    ):
        await _audit_denial(
            session,
            request,
            principal,
            action="user.remove_rejected",
            object_type="user",
            object_id=user.id,
            code="last_admin_required",
        )
        raise ProblemError(
            409,
            "Last administrator is protected",
            "Create another active all-site administrator before removing this account",
            "last_admin_required",
        )
    if not payload.confirm_high_risk:
        raise ProblemError(
            409,
            "Protected confirmation required",
            "Confirm that this user should be removed",
            "high_risk_confirmation_required",
        )
    require_recent_reauthentication(principal.session.reauthenticated_at)

    revoked = await revoke_user_sessions(session, user.id)
    await session.execute(delete(UserRole).where(UserRole.user_id == user.id))
    await replace_user_sites(session, user.id, set())
    now = datetime.now(UTC)
    user.removed_role_ids = roles
    user.removed_site_ids = site_ids
    user.removed_all_sites = user.all_sites
    user.all_sites = False
    user.is_active = False
    user.lifecycle_state = "removed"
    user.removed_at = now
    user.removed_by = principal.user.id
    user.removal_reason = payload.reason
    user.restored_at = None
    user.restored_by = None
    user.access_revision += 1
    session.add(
        audit_event(
            action="user.removed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="user",
            object_id=user.id,
            details={
                "reason": payload.reason,
                "sessions_revoked": revoked,
                "former_roles": roles,
                "former_permissions": permissions,
                "former_all_sites": user.removed_all_sites,
                "former_site_ids": site_ids,
                "identity_preserved": True,
            },
        )
    )
    await session.commit()
    return {
        "changed": True,
        "sessions_revoked": revoked,
        "user": await _user_payload(session, user),
    }


@router.post("/users/{user_id}/restore")
async def restore_user(
    user_id: str,
    payload: UserRestoreRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "users.restore")
    _rate_limit(principal, "user-restore")
    user = await _load_manageable_user(session, principal, user_id, lock=True)
    if user.lifecycle_state != "removed":
        if user.restored_at is not None and user.removed_at is not None:
            return {
                "changed": False,
                "sessions_revoked": 0,
                "user": await _user_payload(session, user),
            }
        raise ProblemError(
            409,
            "User is not removed",
            "Only removed users can be restored",
            "user_not_removed",
        )
    if user.access_revision != payload.expected_revision:
        raise ProblemError(
            409,
            "User lifecycle changed",
            "Reload the user before restoring the account",
            "access_revision_conflict",
        )
    if not payload.confirm_high_risk:
        raise ProblemError(
            409,
            "Restore confirmation required",
            "Confirm that the retained identity should be restored in a disabled state",
            "high_risk_confirmation_required",
        )
    user.lifecycle_state = "disabled"
    user.is_active = False
    user.all_sites = False
    user.restored_at = datetime.now(UTC)
    user.restored_by = principal.user.id
    user.access_revision += 1
    session.add(
        audit_event(
            action="user.restored",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="user",
            object_id=user.id,
            details={
                "reason": payload.reason,
                "status": "disabled",
                "roles_restored": False,
                "sites_restored": False,
            },
        )
    )
    await session.commit()
    return {
        "changed": True,
        "sessions_revoked": 0,
        "user": await _user_payload(session, user),
    }


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_sessions(
    user_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, int]:
    _require(principal, "users.manage")
    user = await _load_manageable_user(session, principal, user_id)
    revoked = await revoke_user_sessions(session, user.id)
    session.add(
        audit_event(
            action="user.sessions_revoked",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="user",
            object_id=user.id,
            details={"sessions_revoked": revoked},
        )
    )
    await session.commit()
    return {"sessions_revoked": revoked}


@router.get("/users/{user_id}/access-history")
async def user_access_history(
    user_id: str, principal: Principal, session: DbSession
) -> dict[str, Any]:
    _require(principal, "users.view")
    user = await _load_manageable_user(session, principal, user_id)
    events = list(
        await session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.object_type == "user",
                AuditEvent.object_id == user.id,
                AuditEvent.action.in_(
                    {
                        "user.access_updated",
                        "user.enabled",
                        "user.disabled",
                        "user.removed",
                        "user.restored",
                        "user.sessions_revoked",
                        "user.access_change_rejected",
                        "user.disable_rejected",
                        "user.remove_rejected",
                    }
                ),
            )
            .order_by(AuditEvent.occurred_at.desc())
            .limit(200)
        )
    )
    return {
        "events": [
            {
                "id": event.id,
                "occurred_at": event.occurred_at,
                "actor_id": event.actor_id,
                "action": event.action,
                "outcome": event.outcome,
                "details": event.details,
            }
            for event in events
        ]
    }


@router.get("/roles")
async def list_roles(principal: Principal, session: DbSession) -> dict[str, Any]:
    _require(principal, "roles.view")
    roles = list(
        await session.scalars(select(Role).order_by(Role.is_builtin.desc(), Role.display_name))
    )
    return {"roles": [await _role_payload(session, role) for role in roles]}


@router.post("/roles", status_code=201)
async def create_role(
    payload: RoleWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "roles.manage")
    _rate_limit(principal, "role-write")
    display_name = validate_role_display_name(payload.display_name)
    permissions = set(payload.permissions)
    validate_permissions(permissions)
    if not permissions.issubset(principal.permissions):
        raise ProblemError(
            403,
            "Privilege delegation denied",
            "You cannot grant permissions that you do not possess",
            "permission_delegation_forbidden",
        )
    high_risk = any(PERMISSION_CATALOG[code].high_risk for code in permissions)
    if high_risk:
        if not payload.confirm_high_risk:
            raise ProblemError(
                409,
                "Protected confirmation required",
                "Confirm creation of a role with high-risk permissions",
                "high_risk_confirmation_required",
            )
        require_recent_reauthentication(principal.session.reauthenticated_at)
    if await session.scalar(
        select(Role.name).where(func.lower(Role.display_name) == display_name.lower())
    ):
        raise ProblemError(
            409, "Role already exists", "Choose a unique role name", "role_name_exists"
        )
    now = datetime.now(UTC)
    role = Role(
        name=custom_role_identifier(),
        display_name=display_name,
        description=payload.description.strip(),
        is_builtin=False,
        is_archived=False,
        revision=1,
        created_by=principal.user.id,
        updated_by=principal.user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(role)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemError(
            409, "Role already exists", "Choose a unique role name", "role_name_exists"
        ) from exc
    for permission in sorted(permissions):
        session.add(RolePermission(role_name=role.name, permission_code=permission))
    session.add(
        RoleRevision(
            role_name=role.name,
            revision=1,
            display_name=role.display_name,
            description=role.description,
            permissions=sorted(permissions),
            created_by=principal.user.id,
            created_at=now,
            reason=payload.reason,
        )
    )
    session.add(
        audit_event(
            action="role.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="role",
            object_id=role.name,
            details={"permissions": sorted(permissions), "reason": payload.reason},
        )
    )
    await session.commit()
    return await _role_payload(session, role)


@router.get("/roles/{role_id}")
async def get_role(role_id: str, principal: Principal, session: DbSession) -> dict[str, Any]:
    _require(principal, "roles.view")
    role = await session.get(Role, role_id)
    if role is None:
        raise ProblemError(404, "Role not found", "Role does not exist", "role_missing")
    return await _role_payload(session, role)


@router.put("/roles/{role_id}")
async def update_role(
    role_id: str,
    payload: RoleWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "roles.manage")
    _rate_limit(principal, "role-write")
    role = await session.scalar(select(Role).where(Role.name == role_id).with_for_update())
    if role is None:
        raise ProblemError(404, "Role not found", "Role does not exist", "role_missing")
    if role.is_builtin:
        raise ProblemError(
            409,
            "Built-in role is immutable",
            "Clone the built-in role before changing its permissions",
            "builtin_role_immutable",
        )
    if role.is_archived:
        raise ProblemError(
            409, "Role is archived", "Archived roles cannot be edited", "role_archived"
        )
    if payload.expected_revision != role.revision:
        raise ProblemError(
            409, "Role changed", "Reload the role before applying changes", "role_revision_conflict"
        )
    permissions = set(payload.permissions)
    validate_permissions(permissions)
    if not permissions.issubset(principal.permissions):
        raise ProblemError(
            403,
            "Privilege delegation denied",
            "You cannot grant permissions that you do not possess",
            "permission_delegation_forbidden",
        )
    prior_permissions = set(await _role_permissions(session, role.name))
    high_risk = any(PERMISSION_CATALOG[code].high_risk for code in permissions ^ prior_permissions)
    if high_risk:
        if not payload.confirm_high_risk:
            raise ProblemError(
                409,
                "Protected confirmation required",
                "Confirm this material role change",
                "high_risk_confirmation_required",
            )
        require_recent_reauthentication(principal.session.reauthenticated_at)
    display_name = validate_role_display_name(payload.display_name)
    duplicate = await session.scalar(
        select(Role.name).where(
            func.lower(Role.display_name) == display_name.lower(), Role.name != role.name
        )
    )
    if duplicate:
        raise ProblemError(
            409, "Role already exists", "Choose a unique role name", "role_name_exists"
        )
    impacted_users = list(
        await session.scalars(select(UserRole.user_id).where(UserRole.role_name == role.name))
    )
    role.display_name = display_name
    role.description = payload.description.strip()
    role.revision += 1
    role.updated_by = principal.user.id
    role.updated_at = datetime.now(UTC)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemError(
            409, "Role already exists", "Choose a unique role name", "role_name_exists"
        ) from exc
    await session.execute(delete(RolePermission).where(RolePermission.role_name == role.name))
    for permission in sorted(permissions):
        session.add(RolePermission(role_name=role.name, permission_code=permission))
    session.add(
        RoleRevision(
            role_name=role.name,
            revision=role.revision,
            display_name=role.display_name,
            description=role.description,
            permissions=sorted(permissions),
            created_by=principal.user.id,
            created_at=datetime.now(UTC),
            reason=payload.reason,
        )
    )
    revoked = 0
    for affected_user_id in impacted_users:
        revoked += await revoke_user_sessions(session, affected_user_id)
        affected = await session.get(User, affected_user_id)
        if affected:
            affected.access_revision += 1
    session.add(
        audit_event(
            action="role.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="role",
            object_id=role.name,
            details={
                "prior_permissions": sorted(prior_permissions),
                "permissions": sorted(permissions),
                "impacted_users": len(impacted_users),
                "sessions_revoked": revoked,
                "reason": payload.reason,
            },
        )
    )
    await session.commit()
    result = await _role_payload(session, role)
    result["sessions_revoked"] = revoked
    return result


@router.post("/roles/{role_id}/clone", status_code=201)
async def clone_role(
    role_id: str,
    payload: RoleWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    source = await session.get(Role, role_id)
    if source is None:
        raise ProblemError(404, "Role not found", "Role does not exist", "role_missing")
    source_permissions = await _role_permissions(session, source.name)
    clone_payload = payload.model_copy(
        update={"permissions": payload.permissions or source_permissions, "expected_revision": None}
    )
    return await create_role(clone_payload, request, principal, session)


@router.post("/roles/{role_id}/archive")
async def archive_role(
    role_id: str,
    payload: UserStatusChange,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, bool]:
    _require(principal, "roles.manage")
    role = await session.scalar(select(Role).where(Role.name == role_id).with_for_update())
    if role is None:
        raise ProblemError(404, "Role not found", "Role does not exist", "role_missing")
    if role.is_builtin:
        raise ProblemError(
            409,
            "Built-in role is protected",
            "Built-in roles cannot be archived",
            "builtin_role_immutable",
        )
    assigned = await session.scalar(
        select(func.count()).select_from(UserRole).where(UserRole.role_name == role.name)
    )
    if assigned:
        raise ProblemError(
            409,
            "Role is assigned",
            "Reassign all users before archiving this role",
            "role_in_use",
        )
    changed = not role.is_archived
    role.is_archived = True
    role.revision += 1
    role.updated_by = principal.user.id
    role.updated_at = datetime.now(UTC)
    if changed:
        session.add(
            audit_event(
                action="role.archived",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="role",
                object_id=role.name,
                details={"reason": payload.reason},
            )
        )
    await session.commit()
    return {"archived": True, "changed": changed}


@router.get("/roles/{role_id}/revisions")
async def role_revisions(role_id: str, principal: Principal, session: DbSession) -> dict[str, Any]:
    _require(principal, "roles.view")
    if await session.get(Role, role_id) is None:
        raise ProblemError(404, "Role not found", "Role does not exist", "role_missing")
    revisions = list(
        await session.scalars(
            select(RoleRevision)
            .where(RoleRevision.role_name == role_id)
            .order_by(RoleRevision.revision.desc())
        )
    )
    return {
        "revisions": [
            {
                "id": item.id,
                "revision": item.revision,
                "display_name": item.display_name,
                "description": item.description,
                "permissions": item.permissions,
                "created_by": item.created_by,
                "created_at": item.created_at,
                "reason": item.reason,
            }
            for item in revisions
        ]
    }
