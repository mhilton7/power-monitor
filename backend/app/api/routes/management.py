from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import false, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.access import (
    active_admin_count,
    explicit_site_ids,
    permissions_for_roles,
    require_recent_reauthentication,
    revoke_user_sessions,
    user_role_names,
)
from app.api.deps import AppSettings, CsrfPrincipal, DbSession, Principal, Viewer, audit_event
from app.db.models import (
    AggregateMember,
    AggregateSet,
    AlertInstance,
    AlertRule,
    AuditEvent,
    BackgroundJob,
    BackupRun,
    Circuit,
    CostCalculationRun,
    CostIntervalResult,
    DashboardAppearance,
    Device,
    DeviceAddress,
    DeviceConfigVersion,
    DeviceCredential,
    DeviceEvent,
    DeviceHeartbeat,
    DeviceLifecycleEvent,
    EnrollmentToken,
    GeneratedReport,
    NormalizedInterval,
    NotificationAttempt,
    NotificationChannel,
    NotificationEvent,
    NotificationSuppression,
    RawReading,
    ReportDefinition,
    SequenceGap,
    Site,
    SyncCursor,
    User,
    UserRole,
    Utility,
    UtilityAccount,
    UtilityAccountSiteAssignment,
    new_uuid,
)
from app.history import MAX_HISTORY_BUCKETS, history_csv, query_history
from app.home_aggregate import resolve_home_aggregate_devices
from app.live_measurements import load_latest_measurements, log_measurement_decision
from app.network_policy import ensure_site_policies, policy_cidrs, policy_for_site, policy_summary
from app.notifications import NOTIFICATION_CATALOG, catalog_entry, load_notification_views
from app.problem import ProblemError
from app.schemas import (
    AggregateSetCreate,
    AlertAcknowledge,
    AlertRuleWrite,
    AlertSilence,
    BackupDeleteRequest,
    BackupReplaceAllRequest,
    BackupRequestCreate,
    BackupVerifyRequest,
    BackupView,
    CircuitCreate,
    CircuitView,
    CredentialRotationRequest,
    DashboardAppearanceView,
    DashboardAppearanceWrite,
    DeviceConfigCreate,
    DeviceListItem,
    DeviceMeasurementAssignmentView,
    DeviceMeasurementAssignmentWrite,
    DeviceUnclaimRequest,
    EnrollmentTokenCreate,
    EnrollmentTokenView,
    FleetSummary,
    HistoryPoint,
    HistoryQueryRequest,
    HistoryQueryResponse,
    HistoryResponse,
    MaintenanceWindow,
    NotificationChannelWrite,
    NotificationHistoryPage,
    NotificationPage,
    NotificationSuppressionItem,
    NotificationSuppressRequest,
    NotificationView,
    PasswordReset,
    ReportDefinitionWrite,
    SiteCreate,
    SiteView,
    StorageActionRequest,
    StoragePolicyWrite,
    UserCreate,
)
from app.security.browser import hash_password, password_is_strong
from app.security.protocol import SecretCipher
from app.topology import AggregateItem, overlap_warnings, would_create_cycle

router = APIRouter(prefix="/api/v1", tags=["management"])
ESTIMATE_DISCLOSURE = (
    "Estimate, not utility bill. Results depend on monitored coverage, configured rates, "
    "meter accuracy, provider adjustments, taxes, credits, and tariff changes."
)
DEFAULT_CHART_COLORS = {
    "chart_power_color": "#78DFBF",
    "chart_energy_color": "#78DFBF",
    "chart_cost_color": "#C9A7FF",
}


def _permission(principal: Principal, permission: str) -> None:
    if permission not in principal.permissions:
        raise ProblemError(
            403,
            "Permission denied",
            "Your account does not have the required permission",
            "forbidden",
            extra={"required_permission": permission},
        )


def _site_allowed(principal: Principal, site_id: str) -> None:
    if not principal.can_access_site(site_id):
        raise ProblemError(404, "Resource not found", "Resource does not exist", "resource_missing")


async def _dashboard_appearance(session: DbSession, *, lock: bool = False) -> DashboardAppearance:
    query = select(DashboardAppearance).where(DashboardAppearance.id == "current")
    if lock:
        query = query.with_for_update()
    appearance = await session.scalar(query)
    if appearance is None:
        appearance = DashboardAppearance(
            id="current",
            **DEFAULT_CHART_COLORS,
            revision=1,
            updated_at=datetime.now(UTC),
        )
        session.add(appearance)
        await session.flush()
    return appearance


@router.get("/appearance", response_model=DashboardAppearanceView)
async def get_dashboard_appearance(
    principal: Principal,
    session: DbSession,
) -> DashboardAppearance:
    # Every authenticated dashboard user receives the same published colors.
    return await _dashboard_appearance(session)


@router.put("/appearance", response_model=DashboardAppearanceView)
async def update_dashboard_appearance(
    payload: DashboardAppearanceWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> DashboardAppearance:
    _permission(principal, "settings.manage")
    appearance = await _dashboard_appearance(session, lock=True)
    if payload.expected_revision != appearance.revision:
        raise ProblemError(
            409,
            "Appearance changed",
            "Reload the published chart colors before applying your changes",
            "appearance_revision_conflict",
            extra={"current_revision": appearance.revision},
        )
    before = {
        "power": appearance.chart_power_color,
        "energy": appearance.chart_energy_color,
        "cost": appearance.chart_cost_color,
    }
    appearance.chart_power_color = payload.chart_power_color
    appearance.chart_energy_color = payload.chart_energy_color
    appearance.chart_cost_color = payload.chart_cost_color
    appearance.revision += 1
    appearance.updated_by = principal.user.id
    appearance.updated_at = datetime.now(UTC)
    session.add(
        audit_event(
            action="appearance.chart_colors_published",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="dashboard_appearance",
            object_id=appearance.id,
            details={
                "before": before,
                "after": {
                    "power": appearance.chart_power_color,
                    "energy": appearance.chart_energy_color,
                    "cost": appearance.chart_cost_color,
                },
                "revision": appearance.revision,
            },
        )
    )
    await session.commit()
    await session.refresh(appearance)
    return appearance


@router.get("/sites", response_model=list[SiteView])
async def list_sites(principal: Viewer, session: DbSession) -> list[Site]:
    _permission(principal, "sites.view")
    query = select(Site).order_by(Site.name)
    if not principal.all_sites:
        query = query.where(Site.id.in_(principal.site_ids))
    if {"sites.edit", "sites.manage"}.intersection(principal.permissions):
        query = query.where(Site.lifecycle_state != "removed")
    else:
        query = query.where(Site.lifecycle_state == "active")
    return list(await session.scalars(query))


@router.post("/sites", response_model=SiteView, status_code=201)
async def create_site(
    payload: SiteCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> Site:
    _permission(principal, "sites.manage")
    site = Site(**payload.model_dump(exclude_none=True))
    session.add(site)
    await session.flush()
    if not await session.scalar(
        select(Site.id).where(Site.is_default.is_(True), Site.id != site.id)
    ):
        site.is_default = True
    await ensure_site_policies(session, site)
    session.add(
        audit_event(
            action="site.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=site.id,
        )
    )
    await session.commit()
    return site


@router.put("/sites/{site_id}", response_model=SiteView)
async def update_site(
    site_id: str,
    payload: SiteCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> Site:
    _permission(principal, "sites.manage")
    _site_allowed(principal, site_id)
    site = await session.get(Site, site_id)
    if site is None:
        raise ProblemError(404, "Site not found", "Site does not exist", "site_missing")
    for key, value in payload.model_dump(exclude_none=True).items():
        if key == "code" and value != site.code:
            raise ProblemError(
                409,
                "Stable site code",
                "Use the reviewed site-management workflow for identity changes",
                "site_code_immutable",
            )
        setattr(site, key, value)
    site.revision += 1
    session.add(
        audit_event(
            action="site.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=site.id,
        )
    )
    await session.commit()
    return site


@router.delete("/sites/{site_id}", status_code=204, response_class=Response)
async def delete_site(
    site_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> Response:
    _permission(principal, "sites.manage")
    _site_allowed(principal, site_id)
    site = await session.get(Site, site_id)
    if site is None:
        raise ProblemError(404, "Site not found", "Site does not exist", "site_missing")
    session.add(
        audit_event(
            action="site.hard_delete_blocked",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=site.id,
            outcome="blocked",
            details={"use": f"/api/v1/admin/sites/{site.id}/remove"},
        )
    )
    await session.commit()
    raise ProblemError(
        409,
        "Use safe site removal",
        "Review dependencies and use the soft-removal lifecycle endpoint",
        "site_soft_removal_required",
        extra={"remove_endpoint": f"/api/v1/admin/sites/{site.id}/remove"},
    )


@router.get("/utility-accounts")
async def list_utility_accounts(principal: Viewer, session: DbSession) -> list[dict[str, Any]]:
    _permission(principal, "utility_accounts.view")
    # Import locally so the management router and account/network router keep
    # independent module initialization while sharing one authoritative view.
    from app.api.routes.account_network import account_view

    query = select(UtilityAccount).order_by(UtilityAccount.name)
    if not principal.all_sites:
        query = query.where(UtilityAccount.site_id.in_(principal.site_ids))
    accounts = list(await session.scalars(query))
    return [await account_view(session, account) for account in accounts]


@router.post("/utility-accounts", status_code=201)
async def create_utility_account(
    payload: dict[str, Any],
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.manage")
    utility = await session.scalar(
        select(Utility).where(Utility.name == "Southern California Edison")
    )
    if utility is None:
        raise ProblemError(
            409, "Utility missing", "Reference data is not initialized", "utility_missing"
        )
    allowed = {
        "site_id",
        "name",
        "timezone",
        "currency",
        "billing_cycle_start_day",
        "baseline_allocation_kwh",
        "generation_provider",
        "active_rate_version_id",
    }
    values = {key: value for key, value in payload.items() if key in allowed}
    if not values.get("site_id") or not values.get("name"):
        raise ProblemError(
            422, "Invalid account", "site_id and name are required", "invalid_account"
        )
    _site_allowed(principal, str(values["site_id"]))
    site = await session.get(Site, str(values["site_id"]))
    if site is None or site.lifecycle_state != "active":
        raise ProblemError(
            409,
            "Active site required",
            "Enable the site before creating new utility-account assignments",
            "site_not_assignable",
        )
    account = UtilityAccount(utility_id=utility.id, **values)
    session.add(account)
    await session.flush()
    now = datetime.now(UTC)
    session.add(
        UtilityAccountSiteAssignment(
            utility_account_id=account.id,
            site_id=account.site_id,
            effective_from=now,
            assigned_by=principal.user.id,
            reason="Initial utility-account assignment",
            created_at=now,
        )
    )
    session.add(
        audit_event(
            action="utility_account.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_account",
            object_id=account.id,
        )
    )
    await session.commit()
    return {"id": account.id, "name": account.name, "cost_scope_default": "energy_only"}


@router.put("/utility-accounts/{account_id}")
async def update_utility_account(
    account_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "utility_accounts.manage")
    account = await session.get(UtilityAccount, account_id)
    if account is None:
        raise ProblemError(
            404, "Account not found", "Utility account does not exist", "account_missing"
        )
    _site_allowed(principal, account.site_id)
    if payload.get("site_id"):
        _site_allowed(principal, str(payload["site_id"]))
    allowed = {
        "site_id",
        "name",
        "timezone",
        "currency",
        "billing_cycle_start_day",
        "baseline_allocation_kwh",
        "generation_provider",
        "active_rate_version_id",
    }
    for key, value in payload.items():
        if key in allowed:
            setattr(account, key, value)
    session.add(
        audit_event(
            action="utility_account.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_account",
            object_id=account.id,
        )
    )
    await session.commit()
    return {"id": account.id, "name": account.name, "timezone": account.timezone}


@router.delete("/utility-accounts/{account_id}", status_code=204, response_class=Response)
async def delete_utility_account(
    account_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> Response:
    _permission(principal, "utility_accounts.manage")
    account = await session.get(UtilityAccount, account_id)
    if account is None:
        raise ProblemError(
            404, "Account not found", "Utility account does not exist", "account_missing"
        )
    _site_allowed(principal, account.site_id)
    in_use = int(
        await session.scalar(
            select(func.count()).select_from(Device).where(Device.utility_account_id == account.id)
        )
        or 0
    )
    if in_use:
        raise ProblemError(
            409,
            "Account is in use",
            "Reassign devices before deleting this utility account",
            "account_in_use",
        )
    session.add(
        audit_event(
            action="utility_account.deleted",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="utility_account",
            object_id=account.id,
        )
    )
    try:
        await session.delete(account)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemError(
            409,
            "Account is in use",
            "Remove billing and aggregate references first",
            "account_in_use",
        ) from exc
    return Response(status_code=204)


@router.get("/circuits", response_model=list[CircuitView])
async def list_circuits(
    principal: Viewer, session: DbSession, site_id: str | None = None
) -> list[Circuit]:
    _permission(principal, "topology.view")
    query = select(Circuit).order_by(Circuit.name)
    if site_id:
        _site_allowed(principal, site_id)
        query = query.where(Circuit.site_id == site_id)
    elif not principal.all_sites:
        query = query.where(Circuit.site_id.in_(principal.site_ids))
    return list(await session.scalars(query))


@router.post("/circuits", response_model=CircuitView, status_code=201)
async def create_circuit(
    payload: CircuitCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> Circuit:
    _permission(principal, "topology.manage")
    _site_allowed(principal, payload.site_id)
    site = await session.get(Site, payload.site_id)
    if site is None:
        raise ProblemError(
            404, "Site not found", "The selected site does not exist", "site_missing"
        )
    parents = {
        circuit.id: circuit.parent_id
        for circuit in await session.scalars(
            select(Circuit).where(Circuit.site_id == payload.site_id)
        )
    }
    if payload.parent_id and payload.parent_id not in parents:
        raise ProblemError(
            422, "Invalid parent", "Parent circuit is not in this site", "invalid_parent"
        )
    candidate_id = secrets.token_hex(18)
    if would_create_cycle(candidate_id, payload.parent_id, parents):
        raise ProblemError(
            422, "Circuit cycle", "Circuit hierarchy cannot contain a cycle", "circuit_cycle"
        )
    circuit = Circuit(**payload.model_dump())
    session.add(circuit)
    session.add(
        audit_event(
            action="circuit.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="circuit",
            object_id=circuit.id,
        )
    )
    await session.commit()
    return circuit


@router.put("/circuits/{circuit_id}", response_model=CircuitView)
async def update_circuit(
    circuit_id: str,
    payload: CircuitCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> Circuit:
    _permission(principal, "topology.manage")
    circuit = await session.get(Circuit, circuit_id)
    if circuit is None:
        raise ProblemError(404, "Circuit not found", "Circuit does not exist", "circuit_missing")
    _site_allowed(principal, circuit.site_id)
    _site_allowed(principal, payload.site_id)
    parents = {
        item.id: item.parent_id
        for item in await session.scalars(select(Circuit).where(Circuit.site_id == circuit.site_id))
    }
    if would_create_cycle(circuit_id, payload.parent_id, parents):
        raise ProblemError(
            422, "Circuit cycle", "Circuit hierarchy cannot contain a cycle", "circuit_cycle"
        )
    for key, value in payload.model_dump().items():
        setattr(circuit, key, value)
    session.add(
        audit_event(
            action="circuit.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="circuit",
            object_id=circuit.id,
        )
    )
    await session.commit()
    return circuit


@router.delete("/circuits/{circuit_id}", status_code=204, response_class=Response)
async def delete_circuit(
    circuit_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> Response:
    _permission(principal, "topology.manage")
    circuit = await session.get(Circuit, circuit_id)
    if circuit is None:
        raise ProblemError(404, "Circuit not found", "Circuit does not exist", "circuit_missing")
    _site_allowed(principal, circuit.site_id)
    child_count = int(
        await session.scalar(
            select(func.count()).select_from(Circuit).where(Circuit.parent_id == circuit.id)
        )
        or 0
    )
    device_count = int(
        await session.scalar(
            select(func.count()).select_from(Device).where(Device.circuit_id == circuit.id)
        )
        or 0
    )
    if child_count or device_count:
        raise ProblemError(
            409,
            "Circuit is in use",
            "Reassign child circuits and devices before deletion",
            "circuit_in_use",
        )
    session.add(
        audit_event(
            action="circuit.deleted",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="circuit",
            object_id=circuit.id,
        )
    )
    await session.delete(circuit)
    await session.commit()
    return Response(status_code=204)


@router.get("/aggregate-sets")
async def list_aggregate_sets(principal: Viewer, session: DbSession) -> list[dict[str, Any]]:
    _permission(principal, "topology.view")
    query = select(AggregateSet).order_by(AggregateSet.name)
    if not principal.all_sites:
        query = query.where(AggregateSet.site_id.in_(principal.site_ids))
    aggregates = list(await session.scalars(query))
    response: list[dict[str, Any]] = []
    for aggregate in aggregates:
        members = list(
            await session.scalars(
                select(AggregateMember).where(AggregateMember.aggregate_set_id == aggregate.id)
            )
        )
        response.append(
            {
                "id": aggregate.id,
                "site_id": aggregate.site_id,
                "name": aggregate.name,
                "cost_scope": aggregate.cost_scope,
                "is_default": aggregate.is_default,
                "members": [
                    {
                        "circuit_id": member.circuit_id,
                        "device_id": member.device_id,
                        "allocation_percent": member.allocation_percent,
                    }
                    for member in members
                ],
                "overlap_confirmed_at": aggregate.overlap_confirmed_at,
            }
        )
    return response


@router.post("/aggregate-sets", status_code=201)
async def create_aggregate_set(
    payload: AggregateSetCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "topology.manage")
    _site_allowed(principal, payload.site_id)
    circuits = {
        item.id: item
        for item in await session.scalars(select(Circuit).where(Circuit.site_id == payload.site_id))
    }
    items = [
        AggregateItem(
            member.circuit_id,
            circuits[member.circuit_id].measurement_role,
            circuits[member.circuit_id].split_phase_group,
        )
        for member in payload.members
        if member.circuit_id and member.circuit_id in circuits
    ]
    parents = {circuit.id: circuit.parent_id for circuit in circuits.values()}
    warnings = overlap_warnings(items, parents)
    if warnings and not payload.confirm_overlap:
        raise ProblemError(
            409,
            "Aggregate overlaps",
            "Administrator confirmation is required before enabling this total",
            "aggregate_overlap",
            {"warnings": warnings},
        )
    aggregate = AggregateSet(
        site_id=payload.site_id,
        utility_account_id=payload.utility_account_id,
        name=payload.name,
        cost_scope=payload.cost_scope,
        is_default=payload.is_default,
        overlap_confirmed_at=datetime.now(UTC) if warnings else None,
    )
    session.add(aggregate)
    await session.flush()
    for member in payload.members:
        session.add(
            AggregateMember(
                aggregate_set_id=aggregate.id,
                circuit_id=member.circuit_id,
                device_id=member.device_id,
                allocation_percent=member.allocation_percent,
            )
        )
    session.add(
        audit_event(
            action="aggregate.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="aggregate_set",
            object_id=aggregate.id,
            details={"warnings": warnings, "cost_scope": aggregate.cost_scope},
        )
    )
    await session.commit()
    return {"id": aggregate.id, "warnings": warnings, "cost_scope": aggregate.cost_scope}


@router.put("/aggregate-sets/{aggregate_id}")
async def update_aggregate_set(
    aggregate_id: str,
    payload: AggregateSetCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "topology.manage")
    aggregate = await session.get(AggregateSet, aggregate_id)
    if aggregate is None:
        raise ProblemError(
            404, "Aggregate not found", "Aggregate set does not exist", "aggregate_missing"
        )
    _site_allowed(principal, aggregate.site_id)
    _site_allowed(principal, payload.site_id)
    circuits = {
        item.id: item
        for item in await session.scalars(select(Circuit).where(Circuit.site_id == payload.site_id))
    }
    missing_circuits = [
        member.circuit_id
        for member in payload.members
        if member.circuit_id and member.circuit_id not in circuits
    ]
    if missing_circuits:
        raise ProblemError(
            422,
            "Invalid aggregate",
            "An aggregate circuit is not part of this site",
            "aggregate_member_invalid",
        )
    items = [
        AggregateItem(
            member.circuit_id,
            circuits[member.circuit_id].measurement_role,
            circuits[member.circuit_id].split_phase_group,
        )
        for member in payload.members
        if member.circuit_id
    ]
    warnings = overlap_warnings(items, {item.id: item.parent_id for item in circuits.values()})
    if warnings and not payload.confirm_overlap:
        raise ProblemError(
            409,
            "Aggregate overlaps",
            "Administrator confirmation is required before enabling this total",
            "aggregate_overlap",
            {"warnings": warnings},
        )
    for key in ("site_id", "utility_account_id", "name", "cost_scope", "is_default"):
        setattr(aggregate, key, getattr(payload, key))
    aggregate.overlap_confirmed_at = datetime.now(UTC) if warnings else None
    for stored_member in await session.scalars(
        select(AggregateMember).where(AggregateMember.aggregate_set_id == aggregate.id)
    ):
        await session.delete(stored_member)
    for payload_member in payload.members:
        session.add(
            AggregateMember(
                aggregate_set_id=aggregate.id,
                circuit_id=payload_member.circuit_id,
                device_id=payload_member.device_id,
                allocation_percent=payload_member.allocation_percent,
            )
        )
    session.add(
        audit_event(
            action="aggregate.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="aggregate_set",
            object_id=aggregate.id,
            details={"warnings": warnings, "cost_scope": aggregate.cost_scope},
        )
    )
    await session.commit()
    return {"id": aggregate.id, "warnings": warnings, "cost_scope": aggregate.cost_scope}


@router.delete("/aggregate-sets/{aggregate_id}", status_code=204, response_class=Response)
async def delete_aggregate_set(
    aggregate_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> Response:
    _permission(principal, "topology.manage")
    aggregate = await session.get(AggregateSet, aggregate_id)
    if aggregate is None:
        raise ProblemError(
            404, "Aggregate not found", "Aggregate set does not exist", "aggregate_missing"
        )
    _site_allowed(principal, aggregate.site_id)
    session.add(
        audit_event(
            action="aggregate.deleted",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="aggregate_set",
            object_id=aggregate.id,
        )
    )
    try:
        await session.delete(aggregate)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemError(
            409,
            "Aggregate is in use",
            "Historical cost runs retain this aggregate",
            "aggregate_in_use",
        ) from exc
    return Response(status_code=204)


@router.post("/enrollment-tokens", response_model=EnrollmentTokenView, status_code=201)
async def create_enrollment_token(
    payload: EnrollmentTokenCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> EnrollmentTokenView:
    _permission(principal, "enrollment.manage")
    if payload.site_id:
        _site_allowed(principal, payload.site_id)
        site = await session.get(Site, payload.site_id)
        if site is None:
            raise ProblemError(
                404, "Site not found", "The selected site does not exist", "site_missing"
            )
        if site.lifecycle_state != "active":
            raise ProblemError(
                409,
                "Active site required",
                "Enable the site before creating enrollment assignments",
                "site_not_assignable",
            )
        ingress_policy = await policy_for_site(session, site, "device_ingress")
        ingress_cidrs = await policy_cidrs(session, ingress_policy.id)
        if ingress_policy.mode == "deny_all":
            raise ProblemError(
                409,
                "Enrollment is locked down",
                "Change the site's sensor ingress policy before issuing a token",
                "enrollment_policy_deny_all",
            )
    now = datetime.now(UTC)
    plaintext = secrets.token_urlsafe(48)
    preassignment = payload.model_dump(
        mode="json", exclude={"expires_in_seconds"}, exclude_none=True
    )
    if payload.site_id:
        preassignment["effective_ingress_policy"] = policy_summary(
            ingress_policy, sum(1 for item in ingress_cidrs if item.enabled)
        )
    token = EnrollmentToken(
        token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        expires_at=now + timedelta(seconds=payload.expires_in_seconds),
        created_by=principal.user.id,
        created_at=now,
        preassignment=preassignment,
    )
    session.add(token)
    session.add(
        audit_event(
            action="enrollment_token.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="enrollment_token",
            object_id=token.id,
            details={"expires_at": token.expires_at.isoformat(), "preassignment": preassignment},
        )
    )
    await session.commit()
    return EnrollmentTokenView(
        id=token.id, token=plaintext, expires_at=token.expires_at, preassignment=preassignment
    )


@router.get("/enrollment-tokens")
async def list_enrollment_tokens(principal: Viewer, session: DbSession) -> list[dict[str, Any]]:
    _permission(principal, "enrollment.view")
    tokens = list(
        await session.scalars(
            select(EnrollmentToken).order_by(EnrollmentToken.created_at.desc()).limit(200)
        )
    )
    return [
        {
            "id": token.id,
            "expires_at": token.expires_at,
            "created_at": token.created_at,
            "consumed_at": token.consumed_at,
            "revoked_at": token.revoked_at,
            "preassignment": token.preassignment,
        }
        for token in tokens
        if not token.preassignment.get("site_id")
        or principal.can_access_site(str(token.preassignment["site_id"]))
    ]


@router.delete("/enrollment-tokens/{token_id}", status_code=204, response_class=Response)
async def revoke_enrollment_token(
    token_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> Response:
    _permission(principal, "enrollment.manage")
    token = await session.get(EnrollmentToken, token_id)
    if token is None:
        raise ProblemError(
            404, "Token not found", "Enrollment token does not exist", "token_missing"
        )
    if token.preassignment.get("site_id"):
        _site_allowed(principal, str(token.preassignment["site_id"]))
    token.revoked_at = datetime.now(UTC)
    session.add(
        audit_event(
            action="enrollment_token.revoked",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="enrollment_token",
            object_id=token.id,
        )
    )
    await session.commit()
    return Response(status_code=204)


async def _latest_heartbeat(session: DbSession, device_id: str) -> DeviceHeartbeat | None:
    heartbeat: DeviceHeartbeat | None = await session.scalar(
        select(DeviceHeartbeat)
        .where(DeviceHeartbeat.device_id == device_id)
        .order_by(DeviceHeartbeat.received_at.desc())
        .limit(1)
    )
    return heartbeat


@router.get("/devices", response_model=list[DeviceListItem])
async def list_devices(
    principal: Viewer,
    session: DbSession,
    settings: AppSettings,
    site_id: str | None = None,
    status: str | None = None,
    lifecycle: str = "active",
) -> list[dict[str, Any]]:
    _permission(principal, "devices.view")
    if lifecycle not in {"active", "decommissioned", "all"}:
        raise ProblemError(
            422,
            "Invalid lifecycle filter",
            "Use active, decommissioned, or all",
            "invalid_lifecycle_filter",
        )
    query = select(Device).order_by(Device.name)
    if lifecycle != "all":
        query = query.where(Device.lifecycle_status == lifecycle)
    if site_id:
        _site_allowed(principal, site_id)
        query = query.where(Device.site_id == site_id)
    elif not principal.all_sites:
        query = query.where(Device.site_id.in_(principal.site_ids))
    devices = list(await session.scalars(query))
    device_ids = [device.id for device in devices]
    measurements, heartbeats, _readings = await load_latest_measurements(session, devices, settings)
    site_ids = {device.site_id for device in devices}
    circuit_ids = {device.circuit_id for device in devices if device.circuit_id}
    sites = {
        item.id: item for item in await session.scalars(select(Site).where(Site.id.in_(site_ids)))
    }
    circuits = {
        item.id: item
        for item in await session.scalars(select(Circuit).where(Circuit.id.in_(circuit_ids)))
    }
    cursors = {
        item.device_id: item
        for item in await session.scalars(
            select(SyncCursor).where(SyncCursor.device_id.in_(device_ids))
        )
    }
    removed_events: dict[str, DeviceLifecycleEvent] = {}
    if device_ids:
        ranked_events = (
            select(
                DeviceLifecycleEvent,
                func.row_number()
                .over(
                    partition_by=DeviceLifecycleEvent.device_id,
                    order_by=(
                        DeviceLifecycleEvent.occurred_at.desc(),
                        DeviceLifecycleEvent.id.desc(),
                    ),
                )
                .label("latest_rank"),
            )
            .where(
                DeviceLifecycleEvent.device_id.in_(device_ids),
                DeviceLifecycleEvent.event_type == "decommissioned",
            )
            .subquery()
        )
        lifecycle_event = aliased(DeviceLifecycleEvent, ranked_events)
        removed_events = {
            item.device_id: item
            for item in await session.scalars(
                select(lifecycle_event).where(ranked_events.c.latest_rank == 1)
            )
        }
    removed_circuit_ids = {item.circuit_id for item in removed_events.values() if item.circuit_id}
    missing_removed_circuit_ids = removed_circuit_ids.difference(circuits)
    if missing_removed_circuit_ids:
        circuits.update(
            {
                item.id: item
                for item in await session.scalars(
                    select(Circuit).where(Circuit.id.in_(missing_removed_circuit_ids))
                )
            }
        )
    removed_user_ids = {device.decommissioned_by for device in devices if device.decommissioned_by}
    removed_users = {
        item.id: item
        for item in await session.scalars(select(User).where(User.id.in_(removed_user_ids)))
    }
    output: list[dict[str, Any]] = []
    for device in devices:
        heartbeat = heartbeats.get(device.id)
        measurement = measurements[device.id]
        cursor = cursors.get(device.id)
        site = sites.get(device.site_id)
        circuit = circuits.get(device.circuit_id) if device.circuit_id else None
        removed_event = removed_events.get(device.id)
        removed_circuit = (
            circuits.get(removed_event.circuit_id or "")
            if removed_event and removed_event.circuit_id
            else None
        )
        removed_by = removed_users.get(device.decommissioned_by or "")
        log_measurement_decision(measurement)
        response_status = (
            "decommissioned"
            if device.lifecycle_status == "decommissioned"
            else "revoked"
            if device.revoked_at
            else "offline"
            if measurement.heartbeat_freshness == "offline"
            else device.status
        )
        if status and response_status != status:
            continue
        output.append(
            {
                "id": device.id,
                "name": device.name,
                "site_id": device.site_id,
                "site_name": site.name if site else None,
                "utility_account_id": device.utility_account_id,
                "circuit_id": device.circuit_id,
                "circuit_name": circuit.name if circuit else None,
                "connection_mode": device.connection_mode,
                "measurement_role": device.measurement_role,
                "cost_scope": device.cost_scope,
                "included_in_default": device.include_in_default_site_total,
                "ct_rating_amps": device.ct_rating_amps,
                "status": response_status,
                "lifecycle_status": device.lifecycle_status,
                "decommissioned_at": device.decommissioned_at,
                "decommissioned_by": device.decommissioned_by,
                "decommissioned_by_name": removed_by.display_name if removed_by else None,
                "decommission_reason": device.decommission_reason,
                "removed_site_id": removed_event.site_id if removed_event else None,
                "removed_circuit_id": removed_event.circuit_id if removed_event else None,
                "removed_circuit_name": removed_circuit.name if removed_circuit else None,
                "retained_history": True,
                "re_enrollment_allowed": device.lifecycle_status == "decommissioned",
                "last_seen_at": device.last_seen_at,
                "firmware_version": device.firmware_version,
                "current_watts": measurement.power_watts,
                "voltage_volts": measurement.voltage_volts,
                "current_amps": measurement.current_amps,
                "frequency_hz": measurement.frequency_hz,
                "power_factor": measurement.power_factor,
                "latest_measurement_at": measurement.measured_at,
                "measurement_received_at": measurement.received_at,
                "measurement_sequence": measurement.sequence,
                "measurement_source": measurement.source,
                "measurement_freshness": measurement.freshness_state,
                "measurement_invalid_metrics": list(measurement.invalid_metrics),
                "heartbeat_received_at": measurement.heartbeat_received_at,
                "heartbeat_age_seconds": measurement.heartbeat_age_seconds,
                "heartbeat_freshness": measurement.heartbeat_freshness,
                "offline_after_seconds": measurement.offline_after_seconds,
                "previous_outage_reason": measurement.previous_outage_reason,
                "rssi_dbm": heartbeat.rssi_dbm if heartbeat else None,
                "pzem_ok": heartbeat.pzem_ok if heartbeat else None,
                "sd_ok": heartbeat.sd_ok if heartbeat else None,
                "time_trusted": heartbeat.time_trusted if heartbeat else None,
                "backlog": (
                    max(0, cursor.maximum_seen_sequence - cursor.highest_contiguous_sequence)
                    if cursor
                    else 0
                ),
            }
        )
    return output


@router.put(
    "/admin/devices/{device_id}/measurement-assignment",
    response_model=DeviceMeasurementAssignmentView,
)
async def update_device_measurement_assignment(
    device_id: str,
    payload: DeviceMeasurementAssignmentWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    """Assign an existing sensor to its audited circuit and utility-account context."""

    _permission(principal, "topology.manage")
    device = await session.get(Device, device_id)
    if device is None or device.lifecycle_status != "active":
        raise ProblemError(
            404,
            "Sensor not found",
            "The active sensor does not exist",
            "device_missing",
        )
    _site_allowed(principal, device.site_id)

    circuit: Circuit | None = None
    if payload.circuit_id is not None:
        circuit = await session.get(Circuit, payload.circuit_id)
        if circuit is None or circuit.site_id != device.site_id:
            raise ProblemError(
                422,
                "Circuit is not eligible",
                "Choose a circuit from the sensor's home",
                "device_circuit_site_mismatch",
            )

    account: UtilityAccount | None = None
    if payload.utility_account_id is not None:
        account = await session.get(UtilityAccount, payload.utility_account_id)
        if account is None or account.site_id != device.site_id or account.status != "active":
            raise ProblemError(
                422,
                "Electric service is not eligible",
                "Choose an active electric service from the sensor's home",
                "device_account_site_mismatch",
            )

    if payload.include_in_default_site_total and circuit is None:
        raise ProblemError(
            422,
            "Circuit assignment required",
            "Assign a circuit before including the sensor in the Home total",
            "device_default_total_requires_circuit",
        )
    if payload.include_in_default_site_total and circuit is not None:
        site_circuits = {
            item.id: item
            for item in await session.scalars(
                select(Circuit).where(Circuit.site_id == device.site_id)
            )
        }
        existing_included = list(
            await session.scalars(
                select(Device).where(
                    Device.site_id == device.site_id,
                    Device.lifecycle_status == "active",
                    Device.include_in_default_site_total.is_(True),
                    Device.circuit_id.is_not(None),
                    Device.id != device.id,
                )
            )
        )
        existing_circuit_ids = {
            item.circuit_id for item in existing_included if item.circuit_id is not None
        }
        topology_warnings: list[str] = []
        if circuit.id in existing_circuit_ids:
            topology_warnings.append(
                "Another sensor on this circuit is already included in the Home total."
            )
        items = [
            AggregateItem(
                circuit_id=item.id,
                role=item.measurement_role,
                split_phase_group=item.split_phase_group,
            )
            for item in site_circuits.values()
            if item.id in existing_circuit_ids or item.id == circuit.id
        ]
        topology_warnings.extend(
            warning
            for warning in overlap_warnings(
                items,
                {item.id: item.parent_id for item in site_circuits.values()},
            )
            if warning.startswith("Circuit ")
        )
        if topology_warnings:
            raise ProblemError(
                409,
                "Home total would overlap",
                "Exclude an overlapping parent, child, or duplicate sensor before "
                "including this measurement",
                "device_default_total_overlap",
                {"warnings": sorted(set(topology_warnings))},
            )

    previous = {
        "circuit_id": device.circuit_id,
        "utility_account_id": device.utility_account_id,
        "measurement_role": device.measurement_role,
        "included_in_default_site_total": device.include_in_default_site_total,
    }
    device.circuit_id = circuit.id if circuit else None
    device.utility_account_id = account.id if account else None
    if circuit is not None:
        device.measurement_role = circuit.measurement_role
    device.include_in_default_site_total = payload.include_in_default_site_total
    session.add(
        audit_event(
            action="device.measurement_assignment_changed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="device",
            object_id=device.id,
            details={
                "reason": payload.reason,
                "previous": previous,
                "current": {
                    "circuit_id": device.circuit_id,
                    "utility_account_id": device.utility_account_id,
                    "measurement_role": device.measurement_role,
                    "included_in_default_site_total": (device.include_in_default_site_total),
                },
                "historical_readings_preserved": True,
            },
        )
    )
    await session.commit()
    return {
        "device_id": device.id,
        "site_id": device.site_id,
        "circuit_id": device.circuit_id,
        "circuit_name": circuit.name if circuit else None,
        "utility_account_id": device.utility_account_id,
        "utility_account_name": account.name if account else None,
        "measurement_role": device.measurement_role,
        "cost_scope": device.cost_scope,
        "included_in_default_site_total": device.include_in_default_site_total,
    }


@router.get("/devices/{device_id}")
async def device_detail(
    device_id: str,
    principal: Viewer,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, Any]:
    _permission(principal, "devices.view")
    device = await session.get(Device, device_id)
    if device is None:
        raise ProblemError(404, "Device not found", "Device does not exist", "device_missing")
    _site_allowed(principal, device.site_id)
    heartbeat = await _latest_heartbeat(session, device.id)
    measurements, _heartbeats, _readings = await load_latest_measurements(
        session, [device], settings
    )
    measurement = measurements[device.id]
    cursor = await session.get(SyncCursor, device.id)
    addresses = list(
        await session.scalars(
            select(DeviceAddress)
            .where(DeviceAddress.device_id == device.id)
            .order_by(DeviceAddress.last_seen_at.desc())
        )
    )
    gaps = list(
        await session.scalars(
            select(SequenceGap)
            .where(SequenceGap.device_id == device.id, SequenceGap.resolved_at.is_(None))
            .order_by(SequenceGap.start_sequence)
        )
    )
    events = list(
        await session.scalars(
            select(DeviceEvent)
            .where(DeviceEvent.device_id == device.id)
            .order_by(DeviceEvent.occurred_at.desc())
            .limit(100)
        )
    )
    credentials = list(
        await session.scalars(
            select(DeviceCredential)
            .where(DeviceCredential.device_id == device.id)
            .order_by(DeviceCredential.created_at.desc())
        )
    )
    history_count, history_start, history_end = (
        await session.execute(
            select(
                func.count(RawReading.id),
                func.min(RawReading.interval_start),
                func.max(RawReading.interval_end),
            ).where(RawReading.device_id == device.id)
        )
    ).one()
    lifecycle_events = list(
        await session.scalars(
            select(DeviceLifecycleEvent)
            .where(DeviceLifecycleEvent.device_id == device.id)
            .order_by(DeviceLifecycleEvent.occurred_at.desc())
        )
    )
    site = await session.get(Site, device.site_id)
    circuit = await session.get(Circuit, device.circuit_id) if device.circuit_id else None
    return {
        "device": {
            "id": device.id,
            "hardware_id": device.hardware_id,
            "name": device.name,
            "site_id": device.site_id,
            "site_name": site.name if site else None,
            "circuit_id": device.circuit_id,
            "circuit_name": circuit.name if circuit else None,
            "connection_mode": device.connection_mode,
            "measurement_role": device.measurement_role,
            "cost_scope": device.cost_scope,
            "ct_rating_amps": device.ct_rating_amps,
            "protocol_version": device.protocol_version,
            "firmware_version": device.firmware_version,
            "status": (
                "decommissioned"
                if device.lifecycle_status == "decommissioned"
                else "revoked"
                if device.revoked_at
                else device.status
            ),
            "lifecycle_status": device.lifecycle_status,
            "decommissioned_at": device.decommissioned_at,
            "decommissioned_by": device.decommissioned_by,
            "decommission_reason": device.decommission_reason,
            "last_seen_at": device.last_seen_at,
            "desired_config_version": device.desired_config_version,
            "effective_config_version": device.effective_config_version,
        },
        "latest_heartbeat": heartbeat.payload if heartbeat else None,
        "latest_measurement": {
            "device_id": measurement.device_id,
            "site_id": measurement.site_id,
            "circuit_id": measurement.circuit_id,
            "measurement_role": measurement.measurement_role,
            "measured_at": measurement.measured_at,
            "received_at": measurement.received_at,
            "sequence": measurement.sequence,
            "power_watts": measurement.power_watts,
            "voltage_volts": measurement.voltage_volts,
            "current_amps": measurement.current_amps,
            "frequency_hz": measurement.frequency_hz,
            "power_factor": measurement.power_factor,
            "source": measurement.source,
            "freshness_state": measurement.freshness_state,
            "invalid_metrics": measurement.invalid_metrics,
        },
        "sync": {
            "highest_contiguous_sequence": cursor.highest_contiguous_sequence if cursor else 0,
            "maximum_seen_sequence": cursor.maximum_seen_sequence if cursor else 0,
            "gaps": [
                {
                    "start": gap.start_sequence,
                    "end": gap.end_sequence,
                    "permanent_loss": gap.permanent_loss,
                }
                for gap in gaps
            ],
        },
        "addresses": [
            {
                "host": address.host,
                "port": address.port,
                "scheme": address.scheme,
                "source": address.source,
                "last_seen_at": address.last_seen_at,
                "validation_error": address.validation_error,
            }
            for address in addresses
        ],
        "credential_fingerprints": [
            {
                "fingerprint": credential.fingerprint,
                "valid_from": credential.valid_from,
                "valid_until": credential.valid_until,
                "revoked_at": credential.revoked_at,
            }
            for credential in credentials
        ],
        "history": {
            "reading_count": int(history_count or 0),
            "earliest_reading_at": history_start,
            "latest_reading_at": history_end,
            "retained": True,
        },
        "lifecycle_events": [
            {
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
                "actor_id": event.actor_id,
                "reason": event.reason,
                "site_id": event.site_id,
                "circuit_id": event.circuit_id,
                "details": event.details,
            }
            for event in lifecycle_events
        ],
        "events": [
            {
                "event_id": event.event_id,
                "occurred_at": event.occurred_at,
                "category": event.category,
                "severity": event.severity,
                "evidence": event.evidence,
            }
            for event in events
        ],
    }


@router.post("/devices/{device_id}/credential-rotation")
async def rotate_credential(
    device_id: str,
    payload: CredentialRotationRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, Any]:
    _permission(principal, "devices.manage")
    device = await session.get(Device, device_id)
    if device is None or device.revoked_at:
        raise ProblemError(
            404, "Device not active", "Device cannot rotate credentials", "device_missing"
        )
    _site_allowed(principal, device.site_id)
    now = datetime.now(UTC)
    old_credentials = list(
        await session.scalars(
            select(DeviceCredential).where(
                DeviceCredential.device_id == device.id,
                DeviceCredential.revoked_at.is_(None),
                DeviceCredential.valid_until.is_(None),
            )
        )
    )
    for old in old_credentials:
        old.valid_until = now + timedelta(seconds=payload.overlap_seconds)
    secret_text = secrets.token_urlsafe(48)
    fingerprint = hashlib.sha256(secret_text.encode()).hexdigest()
    credential = DeviceCredential(
        device_id=device.id,
        encrypted_secret=SecretCipher(settings.app_master_key).encrypt(secret_text.encode()),
        fingerprint=fingerprint,
        valid_from=now,
        created_at=now,
    )
    session.add(credential)
    device.desired_config_version += 1
    session.add(
        DeviceConfigVersion(
            device_id=device.id,
            version=device.desired_config_version,
            desired_config={"credential_rotation_id": credential.id},
            config_hash=hashlib.sha256(fingerprint.encode()).hexdigest(),
            status="pending",
            created_by=principal.user.id,
            created_at=now,
        )
    )
    session.add(
        audit_event(
            action="device.credential_rotation_requested",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="device",
            object_id=device.id,
            details={"fingerprint": fingerprint, "overlap_seconds": payload.overlap_seconds},
        )
    )
    await session.commit()
    return {
        "fingerprint": fingerprint,
        "delivery": "signed device configuration",
        "secret_exposed": False,
    }


@router.post("/devices/{device_id}/revoke")
async def revoke_device(
    device_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, bool]:
    _permission(principal, "devices.remove")
    device = await session.get(Device, device_id)
    if device is None:
        raise ProblemError(404, "Device not found", "Device does not exist", "device_missing")
    _site_allowed(principal, device.site_id)
    already_decommissioned = device.lifecycle_status == "decommissioned"
    if not already_decommissioned:
        await _decommission_device(
            device=device,
            reason="other",
            request=request,
            principal=principal,
            session=session,
            audit_action="device.revoked",
        )
    await session.commit()
    return {"revoked": True, "already_decommissioned": already_decommissioned}


async def _decommission_device(
    *,
    device: Device,
    reason: str | None,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    audit_action: str = "device.unclaimed",
) -> None:
    now = datetime.now(UTC)
    prior_site_id = device.site_id
    prior_circuit_id = device.circuit_id
    device.revoked_at = now
    device.status = "decommissioned"
    device.lifecycle_status = "decommissioned"
    device.lifecycle_generation += 1
    device.decommissioned_at = now
    device.decommissioned_by = principal.user.id
    device.decommission_reason = reason
    device.circuit_id = None
    device.maintenance_until = None
    for credential in await session.scalars(
        select(DeviceCredential).where(DeviceCredential.device_id == device.id)
    ):
        credential.revoked_at = credential.revoked_at or now
        credential.valid_until = credential.valid_until or now
    for config in await session.scalars(
        select(DeviceConfigVersion).where(
            DeviceConfigVersion.device_id == device.id,
            DeviceConfigVersion.status == "pending",
        )
    ):
        config.status = "cancelled"
    for token in await session.scalars(
        select(EnrollmentToken).where(
            EnrollmentToken.consumed_at.is_(None), EnrollmentToken.revoked_at.is_(None)
        )
    ):
        if token.preassignment.get("device_id") == device.id:
            token.revoked_at = now
    history_count, history_start, history_end = (
        await session.execute(
            select(
                func.count(RawReading.id),
                func.min(RawReading.interval_start),
                func.max(RawReading.interval_end),
            ).where(RawReading.device_id == device.id)
        )
    ).one()
    session.add(
        DeviceLifecycleEvent(
            device_id=device.id,
            generation=device.lifecycle_generation,
            event_type="decommissioned",
            occurred_at=now,
            actor_id=principal.user.id,
            reason=reason,
            site_id=prior_site_id,
            circuit_id=prior_circuit_id,
            details={
                "retained_history": True,
                "reading_count": int(history_count or 0),
                "earliest_reading_at": history_start.isoformat() if history_start else None,
                "latest_reading_at": history_end.isoformat() if history_end else None,
            },
        )
    )
    session.add(
        audit_event(
            action=audit_action,
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="device",
            object_id=device.id,
            details={
                "reason": reason,
                "site_id": prior_site_id,
                "circuit_id": prior_circuit_id,
                "retained_history": True,
                "reading_count": int(history_count or 0),
                "correlation_id": request.state.request_id,
            },
        )
    )


@router.post("/admin/devices/{device_id}/unclaim")
async def unclaim_device(
    device_id: str,
    payload: DeviceUnclaimRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "devices.remove")
    device = await session.get(Device, device_id)
    if device is None:
        raise ProblemError(404, "Device not found", "Device does not exist", "device_missing")
    _site_allowed(principal, device.site_id)
    if payload.confirmation not in {device.name, device.id}:
        raise ProblemError(
            409,
            "Confirmation does not match",
            "Type the exact sensor name or immutable device ID",
            "device_confirmation_mismatch",
        )
    if device.lifecycle_status == "decommissioned":
        return {
            "device_id": device.id,
            "status": "decommissioned",
            "already_decommissioned": True,
            "decommissioned_at": device.decommissioned_at,
            "historical_data_retained": True,
        }
    claimed = await session.execute(
        update(Device)
        .where(Device.id == device_id, Device.lifecycle_status == "active")
        .values(lifecycle_status="decommissioning")
    )
    if claimed.rowcount != 1:
        await session.rollback()
        current = await session.get(Device, device_id)
        if current:
            return {
                "device_id": current.id,
                "status": (
                    current.lifecycle_status
                    if current.lifecycle_status != "active"
                    else "decommissioning"
                ),
                "already_decommissioned": True,
                "decommissioned_at": current.decommissioned_at,
                "historical_data_retained": True,
            }
        raise ProblemError(404, "Device not found", "Device does not exist", "device_missing")
    await session.refresh(device)
    await _decommission_device(
        device=device,
        reason=payload.reason,
        request=request,
        principal=principal,
        session=session,
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        current = await session.get(Device, device_id)
        if current is None or current.lifecycle_status != "decommissioned":
            raise
        return {
            "device_id": current.id,
            "status": "decommissioned",
            "already_decommissioned": True,
            "decommissioned_at": current.decommissioned_at,
            "historical_data_retained": True,
        }
    return {
        "device_id": device.id,
        "status": "decommissioned",
        "already_decommissioned": False,
        "decommissioned_at": device.decommissioned_at,
        "historical_data_retained": True,
    }


@router.post("/devices/{device_id}/maintenance")
async def set_device_maintenance(
    device_id: str,
    payload: MaintenanceWindow,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "devices.manage")
    now = datetime.now(UTC)
    if payload.until <= now or payload.until > now + timedelta(days=31):
        raise ProblemError(
            422,
            "Invalid maintenance window",
            "Maintenance must end within the next 31 days",
            "maintenance_window_invalid",
        )
    device = await session.get(Device, device_id)
    if device is None or device.revoked_at:
        raise ProblemError(404, "Device not found", "Device does not exist", "device_missing")
    _site_allowed(principal, device.site_id)
    device.maintenance_until = payload.until
    device.status = "maintenance"
    session.add(
        audit_event(
            action="device.maintenance_started",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="device",
            object_id=device.id,
            details={"until": payload.until.isoformat(), "note": payload.note},
        )
    )
    await session.commit()
    return {"device_id": device.id, "status": device.status, "maintenance_until": payload.until}


@router.delete("/devices/{device_id}/maintenance", status_code=204, response_class=Response)
async def clear_device_maintenance(
    device_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> Response:
    _permission(principal, "devices.manage")
    device = await session.get(Device, device_id)
    if device is None:
        raise ProblemError(404, "Device not found", "Device does not exist", "device_missing")
    _site_allowed(principal, device.site_id)
    device.maintenance_until = None
    device.status = "offline_last_known"
    session.add(
        audit_event(
            action="device.maintenance_ended",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="device",
            object_id=device.id,
        )
    )
    await session.commit()
    return Response(status_code=204)


@router.post("/devices/{device_id}/config", status_code=201)
async def create_device_config(
    device_id: str,
    payload: DeviceConfigCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "devices.manage")
    device = await session.get(Device, device_id)
    if device is None:
        raise ProblemError(404, "Device not found", "Device does not exist", "device_missing")
    _site_allowed(principal, device.site_id)
    forbidden = {key for key in payload.settings if "pin" in key.lower() or "gpio" in key.lower()}
    if forbidden:
        raise ProblemError(
            422,
            "Hardware pins are local-only",
            "Pin assignments cannot be edited remotely",
            "hardware_pin_forbidden",
        )
    if "ct_rating_amps" in payload.settings and not payload.acknowledge_ct_rating_change:
        raise ProblemError(
            409,
            "CT warning required",
            "Acknowledge the CT-rating calibration impact",
            "ct_ack_required",
        )
    if "network" in payload.settings:
        payload.settings["network_rollback_seconds"] = payload.network_rollback_seconds
    device.desired_config_version += 1
    canonical = json.dumps(payload.settings, sort_keys=True, separators=(",", ":"), default=str)
    version = DeviceConfigVersion(
        device_id=device.id,
        version=device.desired_config_version,
        desired_config=payload.settings,
        config_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        status="pending",
        created_by=principal.user.id,
        created_at=datetime.now(UTC),
    )
    session.add(version)
    session.add(
        audit_event(
            action="device.config_created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="device_config",
            object_id=version.id,
            details={"version": version.version, "keys": sorted(payload.settings)},
        )
    )
    await session.commit()
    return {"id": version.id, "version": version.version, "status": version.status}


async def _queue_storage_configuration(
    *,
    device: Device,
    settings: dict[str, Any],
    action: str,
    reason: str,
    request: Request,
    principal: Principal,
    session: DbSession,
) -> DeviceConfigVersion:
    versions = list(
        await session.scalars(
            select(DeviceConfigVersion)
            .where(DeviceConfigVersion.device_id == device.id)
            .order_by(DeviceConfigVersion.version.asc())
        )
    )
    merged: dict[str, Any] = {}
    for existing in versions:
        if isinstance(existing.desired_config, dict):
            merged.update(existing.desired_config)
    merged.update(settings)
    device.desired_config_version += 1
    canonical = json.dumps(merged, sort_keys=True, separators=(",", ":"), default=str)
    version = DeviceConfigVersion(
        device_id=device.id,
        version=device.desired_config_version,
        desired_config=merged,
        config_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        status="pending",
        created_by=principal.user.id,
        created_at=datetime.now(UTC),
    )
    session.add(version)
    session.add(
        audit_event(
            action=action,
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="device_storage",
            object_id=device.id,
            details={
                "reason": reason,
                "version": version.version,
                "keys": sorted(settings),
            },
        )
    )
    await session.commit()
    return version


@router.get("/devices/{device_id}/storage")
async def device_storage_status(
    device_id: str,
    principal: Viewer,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "storage.view")
    device = await session.get(Device, device_id)
    if device is None or device.revoked_at:
        raise ProblemError(404, "Device not found", "Device does not exist", "device_missing")
    _site_allowed(principal, device.site_id)
    heartbeat = await session.scalar(
        select(DeviceHeartbeat)
        .where(DeviceHeartbeat.device_id == device.id)
        .order_by(DeviceHeartbeat.received_at.desc())
        .limit(1)
    )
    latest_config = await session.scalar(
        select(DeviceConfigVersion)
        .where(DeviceConfigVersion.device_id == device.id)
        .order_by(DeviceConfigVersion.version.desc())
        .limit(1)
    )
    effective_config = await session.scalar(
        select(DeviceConfigVersion).where(
            DeviceConfigVersion.device_id == device.id,
            DeviceConfigVersion.version == device.effective_config_version,
        )
    )
    payload = (
        cast(dict[str, Any], heartbeat.payload)
        if heartbeat and isinstance(heartbeat.payload, dict)
        else {}
    )
    raw_subsystem = payload.get("sd")
    subsystem = cast(dict[str, Any], raw_subsystem) if isinstance(raw_subsystem, dict) else {}
    raw_details = subsystem.get("details")
    details = cast(dict[str, Any], raw_details) if isinstance(raw_details, dict) else {}
    policy_keys = (
        "retention_mode",
        "retention_days",
        "minimum_local_history_days",
        "storage_notice_percent",
        "storage_warning_percent",
        "storage_critical_percent",
        "storage_emergency_percent",
        "storage_emergency_reserve_bytes",
        "storage_cleanup_target_percent",
        "storage_cleanup_target_bytes",
        "event_retention_days",
    )
    heartbeat_effective = {key: details[key] for key in policy_keys if key in details}
    effective = dict(heartbeat_effective)
    if effective_config and isinstance(effective_config.desired_config, dict):
        effective.update(effective_config.desired_config)
    desired = dict(effective)
    if latest_config and isinstance(latest_config.desired_config, dict):
        desired.update(latest_config.desired_config)

    def detail_count(key: str) -> int:
        value = details.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    normalized_details = {
        **details,
        "oldest_stored_sequence": details.get("oldest_record_sequence"),
        "newest_stored_sequence": details.get("newest_record_sequence"),
        "server_event_ack_sequence": details.get("event_ack_sequence"),
        "unsynchronized_count": details.get("unacknowledged_record_count"),
        "eligible_reclaimable_bytes": details.get("reclaimable_bytes"),
        "blocked_unacknowledged_bytes": details.get("protected_unacknowledged_bytes"),
        "protected_bytes": detail_count("protected_unacknowledged_bytes")
        + detail_count("protected_untrusted_bytes"),
        "last_cleanup_bytes": details.get("last_cleanup_reclaimed_bytes"),
        "estimated_bytes_per_day": details.get("growth_bytes_per_day"),
        "dropped_interval_count": details.get("dropped_interval_count"),
        "first_dropped_interval_at": details.get("first_dropped_interval_at"),
        "last_dropped_interval_at": details.get("last_dropped_interval_at"),
    }
    return {
        "schema_version": "sensor-storage/1.0",
        "device_id": device.id,
        "device_name": device.name,
        "observed_at": heartbeat.received_at if heartbeat else None,
        "available": bool(heartbeat),
        "healthy": bool(subsystem.get("ok", False)),
        "status": subsystem.get("status", "waiting_for_heartbeat"),
        "details": normalized_details,
        "desired_policy": {key: desired[key] for key in policy_keys if key in desired},
        "effective_policy": {key: effective[key] for key in policy_keys if key in effective},
        "desired_config_version": device.desired_config_version,
        "effective_config_version": device.effective_config_version,
        "policy_pending": device.desired_config_version > device.effective_config_version,
    }


@router.put("/devices/{device_id}/storage/policy", status_code=202)
async def update_device_storage_policy(
    device_id: str,
    payload: StoragePolicyWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "storage.manage")
    device = await session.get(Device, device_id)
    if device is None or device.revoked_at:
        raise ProblemError(404, "Device not found", "Device does not exist", "device_missing")
    _site_allowed(principal, device.site_id)
    version = await _queue_storage_configuration(
        device=device,
        settings=payload.model_dump(exclude={"reason"}),
        action="device.storage_policy_requested",
        reason=payload.reason,
        request=request,
        principal=principal,
        session=session,
    )
    return {"device_id": device.id, "version": version.version, "status": "pending"}


@router.post("/devices/{device_id}/storage/cleanup", status_code=202)
async def request_device_storage_cleanup(
    device_id: str,
    payload: StorageActionRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "storage.manage")
    device = await session.get(Device, device_id)
    if device is None or device.revoked_at:
        raise ProblemError(404, "Device not found", "Device does not exist", "device_missing")
    _site_allowed(principal, device.site_id)
    request_id = new_uuid()
    version = await _queue_storage_configuration(
        device=device,
        settings={
            "storage_cleanup_request_id": request_id,
            "storage_cleanup_reason": payload.reason,
        },
        action="device.storage_cleanup_requested",
        reason=payload.reason,
        request=request,
        principal=principal,
        session=session,
    )
    return {
        "device_id": device.id,
        "request_id": request_id,
        "version": version.version,
        "status": "pending_delivery",
    }


@router.post("/devices/{device_id}/storage/prepare-removal", status_code=202)
async def request_device_storage_prepare_removal(
    device_id: str,
    payload: StorageActionRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "storage.manage")
    device = await session.get(Device, device_id)
    if device is None or device.revoked_at:
        raise ProblemError(404, "Device not found", "Device does not exist", "device_missing")
    _site_allowed(principal, device.site_id)
    if payload.confirmation not in {device.name, device.id}:
        raise ProblemError(
            409,
            "Confirmation does not match",
            "Type the exact sensor name or immutable device ID",
            "storage_prepare_confirmation_mismatch",
        )
    request_id = new_uuid()
    version = await _queue_storage_configuration(
        device=device,
        settings={"storage_prepare_removal_request_id": request_id},
        action="device.storage_prepare_removal_requested",
        reason=payload.reason,
        request=request,
        principal=principal,
        session=session,
    )
    return {
        "device_id": device.id,
        "request_id": request_id,
        "version": version.version,
        "status": "pending_delivery",
    }


@router.get("/readings/history", response_model=HistoryResponse)
async def history(
    principal: Viewer,
    session: DbSession,
    start: datetime,
    end: datetime,
    device_id: str | None = None,
    circuit_id: str | None = None,
    site_id: str | None = None,
    aggregate_set_id: str | None = None,
    resolution: str = Query(default="raw", pattern="^(raw|5-minute|hourly|daily)$"),
    limit: int = Query(default=5000, ge=1, le=20000),
    after_sequence: int = Query(default=0, ge=0),
) -> HistoryResponse:
    _permission(principal, "history.view")
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ProblemError(
            422, "Invalid range", "Use an increasing timezone-aware range", "invalid_range"
        )
    selectors = [device_id, circuit_id, site_id, aggregate_set_id]
    if sum(value is not None for value in selectors) != 1:
        raise ProblemError(
            422,
            "Invalid selection",
            "Select exactly one device, circuit, site, or aggregate set",
            "history_selection_invalid",
        )
    selected_device_ids: set[str] = set()
    if device_id:
        device = await session.get(Device, device_id)
        if device is None:
            raise ProblemError(
                404, "Resource not found", "Resource does not exist", "resource_missing"
            )
        _site_allowed(principal, device.site_id)
        selected_device_ids.add(device_id)
    elif circuit_id:
        circuit = await session.get(Circuit, circuit_id)
        if circuit is None:
            raise ProblemError(
                404, "Resource not found", "Resource does not exist", "resource_missing"
            )
        _site_allowed(principal, circuit.site_id)
        selected_device_ids.update(
            await session.scalars(select(Device.id).where(Device.circuit_id == circuit_id))
        )
    elif site_id:
        _site_allowed(principal, site_id)
        selected_device_ids.update(
            await session.scalars(select(Device.id).where(Device.site_id == site_id))
        )
    elif aggregate_set_id:
        aggregate = await session.get(AggregateSet, aggregate_set_id)
        if aggregate is None:
            raise ProblemError(
                404,
                "Aggregate not found",
                "Aggregate set does not exist",
                "aggregate_missing",
            )
        _site_allowed(principal, aggregate.site_id)
        members = await session.scalars(
            select(AggregateMember).where(AggregateMember.aggregate_set_id == aggregate.id)
        )
        for member in members:
            if member.device_id:
                selected_device_ids.add(member.device_id)
            elif member.circuit_id:
                selected_device_ids.update(
                    await session.scalars(
                        select(Device.id).where(
                            Device.site_id == aggregate.site_id,
                            Device.circuit_id == member.circuit_id,
                        )
                    )
                )
    if not selected_device_ids:
        return HistoryResponse(
            points=[], missing_ranges=[], coverage_percent=Decimal("0"), next_cursor=None
        )
    query = (
        select(RawReading, NormalizedInterval)
        .join(NormalizedInterval, NormalizedInterval.raw_reading_id == RawReading.id, isouter=True)
        .where(
            RawReading.device_id.in_(selected_device_ids),
            RawReading.interval_start >= start,
            RawReading.interval_start < end,
            RawReading.sequence > after_sequence,
        )
        .order_by(RawReading.interval_start, RawReading.device_id, RawReading.sequence)
        .limit(limit + 1)
    )
    rows = list((await session.execute(query)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    raw_points = [
        HistoryPoint(
            timestamp=raw.interval_start,
            power_w=raw.power_avg,
            energy_wh=normalized.selected_energy_wh
            if normalized
            else raw.device_interval_energy_wh,
            voltage_v=raw.voltage_avg,
            current_a=raw.current_avg,
            power_factor=raw.power_factor,
            frequency_hz=raw.frequency_hz,
            quality_flags=raw.quality_flags,
        )
        for raw, normalized in rows
    ]
    if resolution == "raw":
        points = raw_points
    else:
        seconds = {"5-minute": 300, "hourly": 3600, "daily": 86400}[resolution]
        grouped: dict[int, list[HistoryPoint]] = {}
        for point in raw_points:
            bucket = int(point.timestamp.timestamp()) // seconds * seconds
            grouped.setdefault(bucket, []).append(point)

        def average(values: list[Decimal]) -> Decimal | None:
            return sum(values, Decimal("0")) / Decimal(str(len(values))) if values else None

        points = []
        for bucket, bucket_points in sorted(grouped.items()):
            power_values = [point.power_w for point in bucket_points if point.power_w is not None]
            energy_values = [
                point.energy_wh for point in bucket_points if point.energy_wh is not None
            ]
            voltage_values = [
                point.voltage_v for point in bucket_points if point.voltage_v is not None
            ]
            current_values = [
                point.current_a for point in bucket_points if point.current_a is not None
            ]
            factor_values = [
                point.power_factor for point in bucket_points if point.power_factor is not None
            ]
            frequency_values = [
                point.frequency_hz for point in bucket_points if point.frequency_hz is not None
            ]
            points.append(
                HistoryPoint(
                    timestamp=datetime.fromtimestamp(bucket, UTC),
                    power_w=average(power_values),
                    energy_wh=sum(energy_values, Decimal("0")) if energy_values else None,
                    voltage_v=average(voltage_values),
                    current_a=average(current_values),
                    power_factor=average(factor_values),
                    frequency_hz=average(frequency_values),
                    quality_flags=sorted(
                        {flag for point in bucket_points for flag in point.quality_flags}
                    ),
                )
            )
    gaps = list(
        await session.scalars(
            select(SequenceGap).where(
                SequenceGap.device_id.in_(selected_device_ids),
                SequenceGap.resolved_at.is_(None),
            )
        )
    )
    duration = Decimal(str((end - start).total_seconds())) * Decimal(str(len(selected_device_ids)))
    covered = sum(
        (Decimal(str((raw.interval_end - raw.interval_start).total_seconds())) for raw, _ in rows),
        Decimal("0"),
    )
    coverage = (
        min(Decimal("100"), covered / duration * Decimal("100")) if duration else Decimal("0")
    )
    next_cursor = str(rows[-1][0].sequence) if has_more and rows else None
    return HistoryResponse(
        points=points,
        missing_ranges=[
            {
                "device_id": gap.device_id,
                "start_sequence": gap.start_sequence,
                "end_sequence": gap.end_sequence,
            }
            for gap in gaps
        ],
        coverage_percent=coverage,
        next_cursor=next_cursor,
    )


@router.post("/history/query", response_model=HistoryQueryResponse)
async def query_history_api(
    payload: HistoryQueryRequest,
    principal: CsrfPrincipal,
    session: DbSession,
) -> HistoryQueryResponse:
    """Query aligned, topology-safe electrical and estimated energy-cost history."""
    _permission(principal, "history.view")
    return await query_history(session, principal, payload)


@router.post("/history/export")
async def export_history_api(
    payload: HistoryQueryRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> Response:
    """Export the bounded server-authoritative history result with provenance."""
    _permission(principal, "history.export")
    export_payload = payload.model_copy(update={"page": 1, "page_size": MAX_HISTORY_BUCKETS})
    result = await query_history(session, principal, export_payload)
    session.add(
        audit_event(
            action="history.exported",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="site",
            object_id=result.scope.site_id,
            details={
                "scope_type": result.scope.type,
                "included_sensor_count": len(result.scope.included_device_ids),
                "bucket": result.bucket,
                "display_mode": result.display_mode,
                "start_utc": payload.start_utc.isoformat(),
                "end_utc": payload.end_utc.isoformat(),
            },
        )
    )
    await session.commit()
    return Response(
        content=history_csv(result),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=power-monitor-history.csv"},
    )


@router.get("/fleet/summary", response_model=FleetSummary)
async def fleet_summary(
    principal: Viewer,
    session: DbSession,
    settings: AppSettings,
    site_id: str | None = None,
) -> FleetSummary:
    _permission(principal, "overview.view")
    device_query = select(Device).where(Device.lifecycle_status == "active")
    if site_id:
        _site_allowed(principal, site_id)
        device_query = device_query.where(Device.site_id == site_id)
    elif not principal.all_sites:
        device_query = device_query.where(Device.site_id.in_(principal.site_ids))
    devices = list(await session.scalars(device_query))
    aggregate_selection = await resolve_home_aggregate_devices(session, devices)
    included_devices = list(aggregate_selection.devices)
    included_device_ids = [device.id for device in included_devices]
    now = datetime.now(UTC)
    summary_site = await session.get(Site, site_id) if site_id else None
    summary_zone = ZoneInfo(summary_site.timezone) if summary_site else UTC
    local_start = datetime.combine(
        now.astimezone(summary_zone).date(), datetime.min.time(), summary_zone
    ).astimezone(UTC)
    measurements, heartbeats, _readings = await load_latest_measurements(
        session, devices, settings, now=now
    )
    reporting = [item for item in measurements.values() if item.is_reporting]
    contributing = [
        measurements[device.id]
        for device in included_devices
        if measurements[device.id].is_reporting
    ]
    reporting_devices = len(reporting)
    current_load = (
        sum(
            (item.power_watts for item in contributing if item.power_watts is not None),
            Decimal("0"),
        )
        if contributing
        else None
    )
    latest_measurement_at = max(
        (item.measured_at for item in contributing if item.measured_at is not None),
        default=None,
    )
    latest_received_at = max(
        (item.received_at for item in contributing if item.received_at is not None),
        default=None,
    )
    latest_heartbeat_at = max(
        (
            heartbeat.received_at
            for heartbeat in heartbeats.values()
            if heartbeat.received_at is not None
        ),
        default=None,
    )
    summary_logger = structlog.get_logger(__name__)
    summary_logger.info(
        "HOME_AGGREGATE_SELECTED",
        selection_mode=aggregate_selection.mode,
        included_device_count=len(included_devices),
        topology_warnings=list(aggregate_selection.warnings),
    )
    included_id_set = set(included_device_ids)
    for measurement in measurements.values():
        log_measurement_decision(measurement)
        if not measurement.is_reporting:
            summary_logger.info(
                "DEVICE_NOT_COUNTED",
                device_id=measurement.device_id,
                site_id=measurement.site_id,
                freshness_state=measurement.freshness_state,
                reason=measurement.validation_reason or "not_live",
            )
        elif measurement.device_id not in included_id_set:
            summary_logger.info(
                "DEVICE_NOT_COUNTED",
                device_id=measurement.device_id,
                site_id=measurement.site_id,
                freshness_state=measurement.freshness_state,
                reason="topology_excluded",
            )
    energy_wh = await session.scalar(
        select(func.sum(NormalizedInterval.selected_energy_wh)).where(
            NormalizedInterval.device_id.in_(included_device_ids)
            if included_device_ids
            else false(),
            NormalizedInterval.interval_start >= local_start,
        )
    )
    run_query = select(CostCalculationRun).where(CostCalculationRun.status == "completed")
    if site_id:
        run_query = run_query.join(
            AggregateSet, AggregateSet.id == CostCalculationRun.aggregate_set_id
        ).where(AggregateSet.site_id == site_id)
    elif not principal.all_sites:
        run_query = run_query.join(
            AggregateSet, AggregateSet.id == CostCalculationRun.aggregate_set_id
        ).where(AggregateSet.site_id.in_(principal.site_ids))
    latest_run = await session.scalar(
        run_query.order_by(CostCalculationRun.completed_at.desc()).limit(1)
    )
    estimated_today = Decimal("0")
    billing_energy = Decimal("0")
    billing_cost = Decimal("0")
    current_bucket: str | None = None
    has_cost_data = False
    if latest_run:
        result_rows = list(
            await session.scalars(
                select(CostIntervalResult).where(CostIntervalResult.run_id == latest_run.id)
            )
        )
        estimated_today = sum(
            (item.unrounded_cost for item in result_rows if item.interval_start >= local_start),
            Decimal("0"),
        )
        billing_energy = sum((item.energy_kwh for item in result_rows), Decimal("0"))
        billing_cost = sum((item.unrounded_cost for item in result_rows), Decimal("0"))
        recent_energy = max(
            (item for item in result_rows if item.component == "energy"),
            key=lambda item: item.interval_start,
            default=None,
        )
        current_bucket = recent_energy.bucket if recent_energy else None
        has_cost_data = bool(result_rows)
    from app.api.routes.account_network import _account_assignments, _rate_context

    account_query = select(UtilityAccount).where(UtilityAccount.status == "active")
    if site_id:
        account_query = account_query.where(UtilityAccount.site_id == site_id)
    elif not principal.all_sites:
        account_query = account_query.where(UtilityAccount.site_id.in_(principal.site_ids))
    rate_contexts = [
        await _rate_context(session, account, await _account_assignments(session, account.id))
        for account in await session.scalars(account_query)
    ]
    effective_contexts = [
        context for context in rate_contexts if context["state"] == "rate_configured_effective"
    ]
    context_periods = {
        str(context["current_period"])
        for context in effective_contexts
        if context.get("current_period") is not None
    }
    context_plans = {str(context["current_plan"]) for context in effective_contexts}
    context_versions = {int(context["current_version"]) for context in effective_contexts}
    context_prices = {
        Decimal(str(context["current_price_per_kwh"]))
        for context in effective_contexts
        if context.get("current_price_per_kwh") is not None
    }
    if context_periods:
        current_bucket = (
            next(iter(context_periods)) if len(context_periods) == 1 else "Multiple periods"
        )
    alert_query = (
        select(func.count()).select_from(AlertInstance).where(AlertInstance.status == "active")
    )
    if site_id:
        alert_query = alert_query.where(AlertInstance.site_id == site_id)
    elif not principal.all_sites:
        alert_query = alert_query.where(
            or_(AlertInstance.site_id.is_(None), AlertInstance.site_id.in_(principal.site_ids))
        )
    alerts = await session.scalar(alert_query)
    online = sum(
        measurement.heartbeat_freshness == "online" for measurement in measurements.values()
    )
    synchronized = sum(
        device.status == "online_synchronized"
        and measurements[device.id].heartbeat_freshness == "online"
        for device in devices
    )
    has_energy_data = energy_wh is not None
    kwh = Decimal(str(energy_wh or 0)) / Decimal("1000")
    coverage_percent = (
        (Decimal(reporting_devices) / Decimal(len(devices)) * Decimal("100")) if devices else None
    )
    has_live_data = bool(contributing)
    structlog.get_logger(__name__).info(
        "FLEET_SUMMARY_CALCULATED",
        site_id=site_id,
        total_devices=len(devices),
        reporting_devices=reporting_devices,
        contributing_devices=len(contributing),
        current_load_w=current_load,
        latest_measurement_at=latest_measurement_at,
        has_live_data=has_live_data,
    )
    return FleetSummary(
        site_id=site_id,
        current_load_w=current_load,
        energy_today_kwh=kwh,
        estimated_cost_today=estimated_today,
        billing_cycle_energy_kwh=billing_energy,
        estimated_billing_cycle_cost=billing_cost,
        online_devices=online,
        synchronized_devices=synchronized,
        total_devices=len(devices),
        active_alerts=int(alerts or 0),
        current_tou_bucket=current_bucket,
        current_rate_plan=next(iter(context_plans)) if len(context_plans) == 1 else None,
        current_rate_version=next(iter(context_versions)) if len(context_versions) == 1 else None,
        current_rate_price_per_kwh=(
            next(iter(context_prices)) if len(context_prices) == 1 else None
        ),
        rate_configured=bool(effective_contexts),
        recent_peak_w=current_load,
        has_live_data=has_live_data,
        has_energy_data=has_energy_data,
        has_cost_data=has_cost_data,
        reporting_devices=reporting_devices,
        latest_data_at=latest_measurement_at,
        latest_measurement_at=latest_measurement_at,
        latest_received_at=latest_received_at,
        latest_heartbeat_at=latest_heartbeat_at,
        server_now=now,
        coverage_percent=coverage_percent,
        disclosure=ESTIMATE_DISCLOSURE,
    )


@router.get("/alerts")
async def list_alerts(
    principal: Principal,
    session: DbSession,
    status: str | None = None,
    site_id: str | None = None,
) -> list[dict[str, Any]]:
    _permission(principal, "alerts.view")
    if site_id:
        _site_allowed(principal, site_id)
    query = select(AlertInstance).order_by(AlertInstance.opened_at.desc()).limit(1000)
    if site_id:
        query = query.where(AlertInstance.site_id == site_id)
    elif not principal.all_sites:
        query = query.where(
            or_(AlertInstance.site_id.is_(None), AlertInstance.site_id.in_(principal.site_ids))
        )
    if status:
        query = query.where(AlertInstance.status == ("active" if status == "open" else status))
    alerts = list(await session.scalars(query))
    rule_ids = {item.rule_id for item in alerts}
    rules = (
        {
            rule.id: rule
            for rule in await session.scalars(select(AlertRule).where(AlertRule.id.in_(rule_ids)))
        }
        if rule_ids
        else {}
    )
    return [
        {
            "id": alert.id,
            "name": rules[alert.rule_id].name if alert.rule_id in rules else "Deleted rule",
            "rule_type": rules[alert.rule_id].rule_type if alert.rule_id in rules else "unknown",
            "status": alert.status,
            "severity": alert.severity,
            "device_id": alert.device_id,
            "site_id": alert.site_id,
            "opened_at": alert.opened_at,
            "acknowledged_at": alert.acknowledged_at,
            "resolved_at": alert.resolved_at,
            "evidence": alert.evidence,
        }
        for alert in alerts
    ]


@router.get("/notifications", response_model=NotificationPage)
async def list_notifications(
    principal: Principal,
    session: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    state: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    site_id: str | None = None,
) -> NotificationPage:
    _permission(principal, "alerts.view")
    if site_id:
        _site_allowed(principal, site_id)
    views = await load_notification_views(
        session,
        user_id=principal.user.id,
        permissions=set(principal.permissions),
        all_sites=principal.all_sites,
        site_ids=set(principal.site_ids),
        requested_site_id=site_id,
    )
    if state:
        views = [item for item in views if item.state == state]
    if severity:
        views = [item for item in views if item.severity == severity]
    if category:
        views = [item for item in views if item.category == category]
    total = len(views)
    start = (page - 1) * page_size
    return NotificationPage(
        items=views[start : start + page_size], page=page, page_size=page_size, total=total
    )


@router.get("/notifications/{notification_id}", response_model=NotificationView)
async def get_notification(
    notification_id: str,
    principal: Principal,
    session: DbSession,
) -> NotificationView:
    _permission(principal, "alerts.view")
    views = await load_notification_views(
        session,
        user_id=principal.user.id,
        permissions=set(principal.permissions),
        all_sites=principal.all_sites,
        site_ids=set(principal.site_ids),
    )
    selected = next((item for item in views if item.id == notification_id), None)
    if selected is None:
        raise ProblemError(
            404, "Notification not found", "Notification does not exist", "notification_missing"
        )
    return selected


@router.get("/alert-rules")
async def list_alert_rules(principal: Viewer, session: DbSession) -> list[dict[str, Any]]:
    _permission(principal, "alerts.view")
    query = select(AlertRule).order_by(AlertRule.name)
    if not principal.all_sites:
        query = query.where(
            or_(AlertRule.site_id.is_(None), AlertRule.site_id.in_(principal.site_ids))
        )
    rules = list(await session.scalars(query))
    return [
        {
            "id": rule.id,
            "name": rule.name,
            "rule_type": rule.rule_type,
            "severity": rule.severity,
            "enabled": rule.enabled,
            "site_id": rule.site_id,
            "device_id": rule.device_id,
            "debounce_seconds": rule.debounce_seconds,
            "resolve_seconds": rule.resolve_seconds,
            "configuration": rule.configuration,
        }
        for rule in rules
    ]


def _validate_alert_rule_configuration(payload: AlertRuleWrite) -> None:
    if payload.rule_type == "heartbeat_stale":
        stale_seconds = payload.configuration.get("stale_seconds", 30)
        if isinstance(stale_seconds, bool) or not isinstance(stale_seconds, int):
            raise ProblemError(
                422,
                "Invalid disconnect rule",
                "Disconnect delay must be a whole number of seconds",
                "alert_rule_config_invalid",
            )
        if not 15 <= stale_seconds <= 86400:
            raise ProblemError(
                422,
                "Invalid disconnect rule",
                "Disconnect delay must be between 15 seconds and 24 hours",
                "alert_rule_config_invalid",
            )
    elif payload.rule_type == "power_surge":
        threshold = payload.configuration.get("threshold_watts")
        try:
            threshold_watts = Decimal(str(threshold))
        except Exception as exc:
            raise ProblemError(
                422,
                "Invalid power surge rule",
                "Power surge threshold must be a number of watts",
                "alert_rule_config_invalid",
            ) from exc
        if not Decimal("1") <= threshold_watts <= Decimal("10000000"):
            raise ProblemError(
                422,
                "Invalid power surge rule",
                "Power surge threshold must be between 1 W and 10,000,000 W",
                "alert_rule_config_invalid",
            )


@router.post("/alert-rules", status_code=201)
async def create_alert_rule(
    payload: AlertRuleWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "alerts.manage_rules")
    _validate_alert_rule_configuration(payload)
    if payload.device_id:
        selected_device = await session.get(Device, payload.device_id)
        if selected_device is None:
            raise ProblemError(422, "Invalid rule", "Device does not exist", "device_missing")
        _site_allowed(principal, selected_device.site_id)
    if payload.site_id and await session.get(Site, payload.site_id) is None:
        raise ProblemError(422, "Invalid rule", "Site does not exist", "site_missing")
    if payload.site_id:
        _site_allowed(principal, payload.site_id)
    elif not payload.device_id and not principal.all_sites:
        raise ProblemError(
            403, "Global rule denied", "Select an assigned site", "site_scope_required"
        )
    rule = AlertRule(**payload.model_dump())
    session.add(rule)
    session.add(
        audit_event(
            action="alert_rule.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="alert_rule",
            object_id=rule.id,
        )
    )
    await session.commit()
    return {"id": rule.id, "name": rule.name, "enabled": rule.enabled}


@router.put("/alert-rules/{rule_id}")
async def update_alert_rule(
    rule_id: str,
    payload: AlertRuleWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "alerts.manage_rules")
    _validate_alert_rule_configuration(payload)
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise ProblemError(404, "Rule not found", "Alert rule does not exist", "rule_missing")
    if rule.site_id:
        _site_allowed(principal, rule.site_id)
    elif not principal.all_sites:
        raise ProblemError(404, "Rule not found", "Alert rule does not exist", "rule_missing")
    if payload.site_id:
        _site_allowed(principal, payload.site_id)
    for key, value in payload.model_dump().items():
        setattr(rule, key, value)
    session.add(
        audit_event(
            action="alert_rule.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="alert_rule",
            object_id=rule.id,
        )
    )
    await session.commit()
    return {"id": rule.id, "name": rule.name, "enabled": rule.enabled}


@router.delete("/alert-rules/{rule_id}", status_code=204, response_class=Response)
async def delete_alert_rule(
    rule_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> Response:
    _permission(principal, "alerts.manage_rules")
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise ProblemError(404, "Rule not found", "Alert rule does not exist", "rule_missing")
    if rule.site_id:
        _site_allowed(principal, rule.site_id)
    elif not principal.all_sites:
        raise ProblemError(404, "Rule not found", "Alert rule does not exist", "rule_missing")
    instance_count = int(
        await session.scalar(
            select(func.count()).select_from(AlertInstance).where(AlertInstance.rule_id == rule.id)
        )
        or 0
    )
    if instance_count:
        rule.enabled = False
        action = "alert_rule.disabled_with_history"
    else:
        await session.delete(rule)
        action = "alert_rule.deleted"
    session.add(
        audit_event(
            action=action,
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="alert_rule",
            object_id=rule.id,
        )
    )
    await session.commit()
    return Response(status_code=204)


@router.post("/notifications/{alert_id}/acknowledge")
@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    payload: AlertAcknowledge,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, str]:
    _permission(principal, "alerts.acknowledge")
    alert = await session.get(AlertInstance, alert_id)
    if alert is None:
        raise ProblemError(404, "Alert not found", "Alert does not exist", "alert_missing")
    if alert.site_id:
        _site_allowed(principal, alert.site_id)
    if alert.resolved_at or alert.status == "resolved":
        raise ProblemError(
            409,
            "Notification resolved",
            "Resolved notifications cannot be acknowledged",
            "notification_state_conflict",
        )
    rule = await session.get(AlertRule, alert.rule_id)
    if alert.acknowledged_at is None:
        now = datetime.now(UTC)
        alert.status = "acknowledged"
        alert.acknowledged_at = now
        alert.acknowledged_by = principal.user.id
        alert.evidence = {**alert.evidence, "acknowledgement_note": payload.note}
        session.add(
            NotificationEvent(
                notification_id=alert.id,
                event_type="acknowledged",
                occurred_at=now,
                actor_id=principal.user.id,
                site_id=alert.site_id,
                category=catalog_entry(rule.rule_type if rule else "unknown").category,
                severity=alert.severity,
                resource_type="sensor" if alert.device_id else "server",
                resource_id=alert.device_id or alert.site_id,
                details={"note": payload.note},
            )
        )
    session.add(
        audit_event(
            action="alert.acknowledged",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="alert",
            object_id=alert.id,
        )
    )
    await session.commit()
    return {"status": alert.status}


@router.post("/notifications/{alert_id}/silence")
@router.post("/alerts/{alert_id}/silence")
async def silence_alert(
    alert_id: str,
    payload: AlertSilence,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "alerts.acknowledge")
    if payload.until <= datetime.now(UTC):
        raise ProblemError(
            422, "Invalid silence", "Silence end must be in the future", "silence_invalid"
        )
    alert = await session.get(AlertInstance, alert_id)
    if alert is None:
        raise ProblemError(404, "Alert not found", "Alert does not exist", "alert_missing")
    if alert.site_id:
        _site_allowed(principal, alert.site_id)
    if alert.resolved_at or alert.status == "resolved":
        raise ProblemError(
            409,
            "Notification resolved",
            "Resolved notifications cannot be silenced",
            "notification_state_conflict",
        )
    now = datetime.now(UTC)
    alert.silenced_until = payload.until
    alert.silenced_at = now
    alert.silenced_by = principal.user.id
    alert.silence_note = payload.note
    rule = await session.get(AlertRule, alert.rule_id)
    session.add(
        NotificationEvent(
            notification_id=alert.id,
            event_type="silenced",
            occurred_at=now,
            actor_id=principal.user.id,
            site_id=alert.site_id,
            category=catalog_entry(rule.rule_type if rule else "unknown").category,
            severity=alert.severity,
            resource_type="sensor" if alert.device_id else "server",
            resource_id=alert.device_id or alert.site_id,
            details={"until": payload.until.isoformat(), "note": payload.note},
        )
    )
    session.add(
        audit_event(
            action="alert.silenced",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="alert",
            object_id=alert.id,
            details={"until": payload.until.isoformat()},
        )
    )
    await session.commit()
    return {"id": alert.id, "silenced_until": alert.silenced_until}


@router.delete("/notifications/{alert_id}/silence", status_code=204, response_class=Response)
async def end_notification_silence(
    alert_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> Response:
    _permission(principal, "alerts.acknowledge")
    alert = await session.get(AlertInstance, alert_id)
    if alert is None:
        raise ProblemError(
            404, "Notification not found", "Notification does not exist", "notification_missing"
        )
    if alert.site_id:
        _site_allowed(principal, alert.site_id)
    if alert.silenced_until is None:
        return Response(status_code=204)
    now = datetime.now(UTC)
    alert.silenced_until = None
    alert.silenced_at = None
    alert.silenced_by = None
    alert.silence_note = None
    rule = await session.get(AlertRule, alert.rule_id)
    session.add(
        NotificationEvent(
            notification_id=alert.id,
            event_type="silence_ended",
            occurred_at=now,
            actor_id=principal.user.id,
            site_id=alert.site_id,
            category=catalog_entry(rule.rule_type if rule else "unknown").category,
            severity=alert.severity,
            resource_type="sensor" if alert.device_id else "server",
            resource_id=alert.device_id or alert.site_id,
            details={},
        )
    )
    session.add(
        audit_event(
            action="notification.silence_ended",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="notification",
            object_id=alert.id,
        )
    )
    await session.commit()
    return Response(status_code=204)


def _recommendation_identity(notification_id: str) -> tuple[str, str]:
    prefix, separator, remainder = notification_id.partition(":")
    key, site_separator, site_id = remainder.rpartition(":")
    if prefix != "recommendation" or not separator or not site_separator:
        raise ProblemError(
            409,
            "Notification cannot be permanently ignored",
            "Operational and delivery failures may be acknowledged or temporarily "
            "silenced, but they cannot be permanently hidden",
            "notification_not_suppressible",
        )
    entry = NOTIFICATION_CATALOG.get(key)
    if entry is None or not entry.permanently_suppressible:
        raise ProblemError(
            409,
            "Recommendation cannot be permanently ignored",
            "This recommendation is not eligible for permanent suppression",
            "notification_not_suppressible",
        )
    return key, site_id


async def _suppress_recommendation(
    notification_id: str,
    payload: NotificationSuppressRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    *,
    event_type: str = "permanently_suppressed",
) -> NotificationSuppression:
    _permission(principal, "alerts.manage_delivery")
    if not payload.confirmed:
        raise ProblemError(
            422,
            "Confirmation required",
            "Confirm that dashboard alerts continue while optional external delivery remains off",
            "suppression_confirmation_required",
        )
    key, site_id = _recommendation_identity(notification_id)
    _site_allowed(principal, site_id)
    site = await session.get(Site, site_id)
    if site is None or site.lifecycle_state != "active":
        raise ProblemError(404, "Home not found", "Home does not exist", "site_missing")
    filters = [
        NotificationSuppression.suppression_key == key,
        NotificationSuppression.active.is_(True),
    ]
    if payload.scope == "user":
        filters.append(NotificationSuppression.user_id == principal.user.id)
    else:
        filters.append(NotificationSuppression.site_id == site_id)
    existing = await session.scalar(select(NotificationSuppression).where(*filters))
    if existing:
        return existing
    now = datetime.now(UTC)
    suppression = NotificationSuppression(
        suppression_key=key,
        category=NOTIFICATION_CATALOG[key].category,
        scope_type=payload.scope,
        user_id=principal.user.id if payload.scope == "user" else None,
        site_id=site_id if payload.scope == "home" else None,
        created_by=principal.user.id,
        created_at=now,
        reason=payload.reason or None,
        source_notification_id=notification_id,
        active=True,
        revision=1,
    )
    session.add(suppression)
    session.add(
        NotificationEvent(
            notification_id=notification_id,
            event_type=event_type,
            occurred_at=now,
            actor_id=principal.user.id,
            site_id=site_id,
            category=NOTIFICATION_CATALOG[key].category,
            severity=NOTIFICATION_CATALOG[key].severity,
            resource_type="home",
            resource_id=site_id,
            details={"scope": payload.scope, "reason": payload.reason},
        )
    )
    session.add(
        audit_event(
            action=f"recommendation.{event_type}",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="notification_suppression",
            object_id=suppression.id,
            details={"suppression_key": key, "scope": payload.scope, "site_id": site_id},
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(select(NotificationSuppression).where(*filters))
        if existing is None:
            raise
        return existing
    structlog.get_logger(__name__).info(
        f"recommendation.{event_type}",
        notification_id=notification_id,
        category=suppression.category,
        site_id=site_id,
        actor_id=principal.user.id,
        scope=payload.scope,
    )
    return suppression


@router.post("/notifications/{notification_id}/suppress", status_code=201)
async def suppress_notification(
    notification_id: str,
    payload: NotificationSuppressRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    suppression = await _suppress_recommendation(
        notification_id, payload, request, principal, session
    )
    return {"id": suppression.id, "active": suppression.active, "revision": suppression.revision}


@router.post("/notifications/{notification_id}/dismiss", status_code=201)
async def dismiss_notification(
    notification_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    views = await load_notification_views(
        session,
        user_id=principal.user.id,
        permissions=set(principal.permissions),
        all_sites=principal.all_sites,
        site_ids=set(principal.site_ids),
        include_dismissed=True,
    )
    selected = next((item for item in views if item.id == notification_id), None)
    if selected is None:
        raise ProblemError(
            404,
            "Notification not found",
            "Notification does not exist",
            "notification_missing",
        )
    if selected.kind == "setup_recommendation":
        suppression = await _suppress_recommendation(
            notification_id,
            NotificationSuppressRequest(
                scope="user",
                reason="Removed from the notification center",
                confirmed=True,
            ),
            request,
            principal,
            session,
            event_type="dismissed",
        )
        return {
            "id": suppression.id,
            "notification_id": notification_id,
            "dismissed": True,
        }

    required_permission = (
        "alerts.manage_delivery" if selected.kind == "delivery_issue" else "alerts.acknowledge"
    )
    _permission(principal, required_permission)
    now = datetime.now(UTC)
    latest_reactivation = await session.scalar(
        select(NotificationEvent.occurred_at)
        .where(
            NotificationEvent.notification_id == notification_id,
            NotificationEvent.event_type.in_(("opened", "reopened")),
        )
        .order_by(NotificationEvent.occurred_at.desc())
        .limit(1)
    )
    occurrence_started_at = selected.first_seen_at
    if latest_reactivation and latest_reactivation.tzinfo is None:
        latest_reactivation = latest_reactivation.replace(tzinfo=UTC)
    if latest_reactivation and latest_reactivation > occurrence_started_at:
        occurrence_started_at = latest_reactivation
    existing = await session.scalar(
        select(NotificationEvent)
        .where(
            NotificationEvent.notification_id == notification_id,
            NotificationEvent.event_type == "dismissed",
            NotificationEvent.actor_id == principal.user.id,
            NotificationEvent.occurred_at >= occurrence_started_at,
        )
        .order_by(NotificationEvent.occurred_at.desc())
        .limit(1)
    )
    if existing is not None:
        return {"id": existing.id, "notification_id": notification_id, "dismissed": True}
    event = NotificationEvent(
        notification_id=notification_id,
        event_type="dismissed",
        occurred_at=now,
        actor_id=principal.user.id,
        site_id=selected.affected_resource.id
        if selected.affected_resource and selected.affected_resource.type == "home"
        else None,
        category=selected.category,
        severity=selected.severity,
        resource_type=selected.affected_resource.type if selected.affected_resource else None,
        resource_id=selected.affected_resource.id if selected.affected_resource else None,
        details={
            "reason": "Removed from the notification center",
            "last_seen_at": selected.last_seen_at.isoformat(),
            "occurrence_started_at": occurrence_started_at.isoformat(),
            "monitoring_preserved": True,
        },
    )
    session.add(event)
    session.add(
        audit_event(
            action="notification.dismissed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="notification",
            object_id=notification_id,
            details={"monitoring_preserved": True},
        )
    )
    await session.commit()
    return {"id": event.id, "notification_id": notification_id, "dismissed": True}


@router.get("/notification-suppressions", response_model=list[NotificationSuppressionItem])
async def list_notification_suppressions(
    principal: Principal,
    session: DbSession,
    active_only: bool = True,
) -> list[NotificationSuppressionItem]:
    _permission(principal, "alerts.manage_delivery")
    query = select(NotificationSuppression).order_by(NotificationSuppression.created_at.desc())
    if active_only:
        query = query.where(NotificationSuppression.active.is_(True))
    scope = [NotificationSuppression.user_id == principal.user.id]
    if principal.all_sites:
        scope.append(NotificationSuppression.site_id.is_not(None))
    elif principal.site_ids:
        scope.append(NotificationSuppression.site_id.in_(principal.site_ids))
    records = list(await session.scalars(query.where(or_(*scope)).limit(1000)))
    actor_ids = {
        actor for item in records for actor in (item.created_by, item.restored_by) if actor
    }
    user_names = (
        {
            item.id: item.display_name
            for item in await session.scalars(select(User).where(User.id.in_(actor_ids)))
        }
        if actor_ids
        else {}
    )
    suppression_site_ids = {item.site_id for item in records if item.site_id}
    site_names = (
        {
            item.id: item.name
            for item in await session.scalars(select(Site).where(Site.id.in_(suppression_site_ids)))
        }
        if suppression_site_ids
        else {}
    )
    return [
        NotificationSuppressionItem(
            id=item.id,
            suppression_key=item.suppression_key,
            category=item.category,
            scope_type=item.scope_type,  # type: ignore[arg-type]
            scope_name=(
                user_names.get(item.user_id, "Your account")
                if item.user_id
                else "Your account"
                if item.scope_type == "user"
                else site_names.get(item.site_id, "Home")
                if item.site_id
                else "Home"
            ),
            created_by=user_names.get(item.created_by, "Authorized user"),
            created_at=item.created_at,
            reason=item.reason,
            source_notification_id=item.source_notification_id,
            active=item.active,
            restored_by=user_names.get(item.restored_by) if item.restored_by else None,
            restored_at=item.restored_at,
            revision=item.revision,
        )
        for item in records
    ]


@router.delete(
    "/notification-suppressions/{suppression_id}",
    status_code=204,
    response_class=Response,
)
async def restore_notification_suppression(
    suppression_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    expected_revision: int | None = Query(default=None, ge=1),
) -> Response:
    _permission(principal, "alerts.manage_delivery")
    suppression = await session.get(NotificationSuppression, suppression_id)
    if suppression is None:
        raise ProblemError(
            404,
            "Ignored recommendation not found",
            "Suppression does not exist",
            "suppression_missing",
        )
    if suppression.scope_type == "user" and suppression.user_id != principal.user.id:
        raise ProblemError(
            404,
            "Ignored recommendation not found",
            "Suppression does not exist",
            "suppression_missing",
        )
    if suppression.site_id:
        _site_allowed(principal, suppression.site_id)
    if expected_revision is not None and suppression.revision != expected_revision:
        raise ProblemError(
            409,
            "Suppression changed",
            "Refresh Ignored recommendations and try again",
            "revision_conflict",
        )
    if not suppression.active:
        return Response(status_code=204)
    now = datetime.now(UTC)
    suppression.active = False
    suppression.restored_by = principal.user.id
    suppression.restored_at = now
    suppression.revision += 1
    session.add(
        NotificationEvent(
            notification_id=suppression.source_notification_id,
            event_type="suppression_restored",
            occurred_at=now,
            actor_id=principal.user.id,
            site_id=suppression.site_id,
            category=suppression.category,
            severity="info",
            resource_type="home" if suppression.site_id else None,
            resource_id=suppression.site_id,
            details={"scope": suppression.scope_type},
        )
    )
    session.add(
        audit_event(
            action="recommendation.suppression_restored",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="notification_suppression",
            object_id=suppression.id,
            details={"suppression_key": suppression.suppression_key},
        )
    )
    await session.commit()
    structlog.get_logger(__name__).info(
        "recommendation.suppression_restored",
        notification_id=suppression.source_notification_id,
        category=suppression.category,
        site_id=suppression.site_id,
        actor_id=principal.user.id,
    )
    return Response(status_code=204)


@router.get("/notification-history", response_model=NotificationHistoryPage)
async def list_notification_history(
    principal: Principal,
    session: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    state: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    resource_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> NotificationHistoryPage:
    _permission(principal, "alerts.view")
    filters: list[Any] = []
    if not principal.all_sites:
        filters.append(
            or_(
                NotificationEvent.site_id.is_(None),
                NotificationEvent.site_id.in_(principal.site_ids),
            )
        )
    if state:
        filters.append(NotificationEvent.event_type == state)
    if severity:
        filters.append(NotificationEvent.severity == severity)
    if category:
        filters.append(NotificationEvent.category == category)
    if resource_id:
        filters.append(NotificationEvent.resource_id == resource_id)
    if date_from:
        filters.append(NotificationEvent.occurred_at >= date_from)
    if date_to:
        filters.append(NotificationEvent.occurred_at <= date_to)
    total = int(
        await session.scalar(select(func.count()).select_from(NotificationEvent).where(*filters))
        or 0
    )
    events = list(
        await session.scalars(
            select(NotificationEvent)
            .where(*filters)
            .order_by(NotificationEvent.occurred_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    actors = {item.actor_id for item in events if item.actor_id}
    actor_names = (
        {
            item.id: item.display_name
            for item in await session.scalars(select(User).where(User.id.in_(actors)))
        }
        if actors
        else {}
    )
    return NotificationHistoryPage.model_validate(
        {
            "items": [
                {
                    "id": item.id,
                    "notification_id": item.notification_id,
                    "event_type": item.event_type,
                    "occurred_at": item.occurred_at,
                    "actor_name": actor_names.get(item.actor_id) if item.actor_id else None,
                    "site_id": item.site_id,
                    "category": item.category,
                    "severity": item.severity,
                    "resource_type": item.resource_type,
                    "resource_id": item.resource_id,
                    "details": item.details,
                }
                for item in events
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
    )


def _valid_notification_address(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or any(character in value for character in ("\r", "\n", "\x00"))
    ):
        return False
    local, separator, domain = value.rpartition("@")
    return bool(separator and local and "." in domain and not domain.startswith("."))


def _validate_notification_configuration(
    payload: NotificationChannelWrite, configuration: dict[str, Any] | None = None
) -> None:
    config = configuration if configuration is not None else payload.configuration
    if payload.channel_type == "smtp":
        missing = [key for key in ("host", "port", "from", "recipients") if not config.get(key)]
        if missing:
            raise ProblemError(
                422,
                "Invalid SMTP channel",
                f"Missing SMTP settings: {', '.join(missing)}",
                "notification_config_invalid",
            )
        host = config.get("host")
        if (
            not isinstance(host, str)
            or not host.strip()
            or host != host.strip()
            or any(character in host for character in ("\r", "\n", "\x00"))
        ):
            raise ProblemError(
                422,
                "Invalid SMTP channel",
                "SMTP host must be a valid hostname or IP address",
                "notification_config_invalid",
            )
        port = config.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ProblemError(
                422,
                "Invalid SMTP channel",
                "SMTP port must be between 1 and 65535",
                "notification_config_invalid",
            )
        sender = config.get("from")
        recipients = config.get("recipients")
        if not _valid_notification_address(sender):
            raise ProblemError(
                422,
                "Invalid SMTP channel",
                "Sender must be a valid email address",
                "notification_config_invalid",
            )
        if (
            not isinstance(recipients, list)
            or not 1 <= len(recipients) <= 50
            or not all(_valid_notification_address(item) for item in recipients)
        ):
            raise ProblemError(
                422,
                "Invalid SMTP channel",
                "Provide between 1 and 50 valid recipient email addresses",
                "notification_config_invalid",
            )
        starttls = config.get("starttls", True)
        implicit_tls = config.get("implicit_tls", False)
        if not isinstance(starttls, bool) or not isinstance(implicit_tls, bool):
            raise ProblemError(
                422,
                "Invalid SMTP channel",
                "SMTP TLS settings must be boolean values",
                "notification_config_invalid",
            )
        if starttls and implicit_tls:
            raise ProblemError(
                422,
                "Invalid SMTP channel",
                "Choose STARTTLS or implicit TLS, not both",
                "notification_config_invalid",
            )
        username = config.get("username")
        password = config.get("password")
        if bool(username) != bool(password):
            raise ProblemError(
                422,
                "Invalid SMTP channel",
                "SMTP username and password must be configured together",
                "notification_config_invalid",
            )
        if username and not (starttls or implicit_tls):
            raise ProblemError(
                422,
                "Invalid SMTP channel",
                "Authenticated SMTP requires STARTTLS or implicit TLS",
                "notification_config_invalid",
            )
        event_types = config.get("event_types", [])
        if (
            not isinstance(event_types, list)
            or len(event_types) > 50
            or not all(
                isinstance(item, str)
                and item
                and len(item) <= 80
                and all(
                    character.islower() or character.isdigit() or character == "_"
                    for character in item
                )
                for item in event_types
            )
        ):
            raise ProblemError(
                422,
                "Invalid SMTP channel",
                "Notification event types must be lowercase identifiers",
                "notification_config_invalid",
            )
    elif payload.channel_type == "https_webhook":
        url = str(config.get("url", ""))
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ProblemError(
                422,
                "Invalid webhook channel",
                "Webhook must be an HTTPS URL without embedded credentials",
                "notification_config_invalid",
            )
        if parsed.hostname.rstrip(".").lower() in {"localhost", "localhost.localdomain"}:
            raise ProblemError(
                422,
                "Invalid webhook channel",
                "Loopback webhook targets are not permitted",
                "notification_config_invalid",
            )
    elif config:
        raise ProblemError(
            422,
            "Invalid in-app channel",
            "In-app channels do not accept configuration",
            "notification_config_invalid",
        )


def _redacted_channel(channel: NotificationChannel, cipher: SecretCipher) -> dict[str, Any]:
    try:
        config = json.loads(cipher.decrypt(channel.encrypted_config))
    except (RuntimeError, ValueError, json.JSONDecodeError):
        config = {}
    target: dict[str, Any] = {}
    if channel.channel_type == "smtp":
        target = {
            "host": config.get("host"),
            "port": config.get("port"),
            "from": config.get("from"),
            "recipient_count": len(config.get("recipients", [])),
            "starttls": bool(config.get("starttls", True)),
            "implicit_tls": bool(config.get("implicit_tls", False)),
            "authentication_configured": bool(config.get("username")),
            "event_types": list(config.get("event_types", [])),
        }
    elif channel.channel_type == "https_webhook":
        parsed = urlsplit(str(config.get("url", "")))
        target = {"host": parsed.hostname, "path_configured": bool(parsed.path)}
    return {
        "id": channel.id,
        "name": channel.name,
        "channel_type": channel.channel_type,
        "enabled": channel.enabled,
        "target": target,
        "secrets_redacted": True,
    }


@router.get("/notification-channels")
async def list_notification_channels(
    principal: Principal, session: DbSession, settings: AppSettings
) -> list[dict[str, Any]]:
    _permission(principal, "alerts.manage_delivery")
    cipher = SecretCipher(settings.app_master_key)
    channels = list(
        await session.scalars(select(NotificationChannel).order_by(NotificationChannel.name))
    )
    return [_redacted_channel(channel, cipher) for channel in channels]


@router.post("/notification-channels", status_code=201)
async def create_notification_channel(
    payload: NotificationChannelWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, Any]:
    _permission(principal, "alerts.manage_delivery")
    _validate_notification_configuration(payload)
    protected = SecretCipher(settings.app_master_key).encrypt(
        json.dumps(payload.configuration, sort_keys=True, separators=(",", ":")).encode()
    )
    channel = NotificationChannel(
        name=payload.name,
        channel_type=payload.channel_type,
        enabled=payload.enabled,
        encrypted_config=protected,
    )
    session.add(channel)
    session.add(
        audit_event(
            action="notification_channel.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="notification_channel",
            object_id=channel.id,
            details={"channel_type": channel.channel_type},
        )
    )
    await session.commit()
    return _redacted_channel(channel, SecretCipher(settings.app_master_key))


@router.put("/notification-channels/{channel_id}")
async def update_notification_channel(
    channel_id: str,
    payload: NotificationChannelWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, Any]:
    _permission(principal, "alerts.manage_delivery")
    channel = await session.get(NotificationChannel, channel_id)
    if channel is None:
        raise ProblemError(
            404, "Channel not found", "Notification channel does not exist", "channel_missing"
        )
    configuration = dict(payload.configuration)
    if channel.channel_type == "smtp" and payload.channel_type == "smtp":
        try:
            existing = json.loads(
                SecretCipher(settings.app_master_key).decrypt(channel.encrypted_config)
            )
        except (RuntimeError, ValueError, json.JSONDecodeError):
            existing = {}
        if not configuration.get("password") and existing.get("password"):
            configuration["password"] = existing["password"]
        if not configuration.get("username") and existing.get("username"):
            configuration["username"] = existing["username"]
    _validate_notification_configuration(payload, configuration)
    channel.name = payload.name
    channel.channel_type = payload.channel_type
    channel.enabled = payload.enabled
    channel.encrypted_config = SecretCipher(settings.app_master_key).encrypt(
        json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    )
    session.add(
        audit_event(
            action="notification_channel.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="notification_channel",
            object_id=channel.id,
        )
    )
    await session.commit()
    return _redacted_channel(channel, SecretCipher(settings.app_master_key))


@router.delete("/notification-channels/{channel_id}", status_code=204, response_class=Response)
async def disable_notification_channel(
    channel_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> Response:
    _permission(principal, "alerts.manage_delivery")
    channel = await session.get(NotificationChannel, channel_id)
    if channel is None:
        raise ProblemError(
            404, "Channel not found", "Notification channel does not exist", "channel_missing"
        )
    channel.enabled = False
    session.add(
        audit_event(
            action="notification_channel.disabled",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="notification_channel",
            object_id=channel.id,
        )
    )
    await session.commit()
    return Response(status_code=204)


@router.post("/notification-channels/{channel_id}/test", status_code=202)
async def test_notification_channel(
    channel_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "alerts.manage_delivery")
    channel = await session.get(NotificationChannel, channel_id)
    if channel is None or not channel.enabled:
        raise ProblemError(
            404, "Channel unavailable", "Notification channel is not enabled", "channel_missing"
        )
    attempt = NotificationAttempt(
        alert_instance_id=None,
        channel_id=channel.id,
        attempted_at=datetime.now(UTC),
        queued_at=datetime.now(UTC),
        status="queued",
        attempt_number=0,
        response_summary=None,
        next_attempt_at=datetime.now(UTC),
        is_test=True,
    )
    session.add(attempt)
    session.add(
        audit_event(
            action="notification_channel.test_queued",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="notification_channel",
            object_id=channel.id,
        )
    )
    await session.commit()
    return {"attempt_id": attempt.id, "status": attempt.status}


@router.get("/notification-attempts")
async def list_notification_attempts(
    principal: Principal,
    session: DbSession,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    _permission(principal, "alerts.manage_delivery")
    attempts = list(
        await session.scalars(
            select(NotificationAttempt)
            .order_by(NotificationAttempt.attempted_at.desc())
            .limit(limit)
        )
    )
    return [
        {
            "id": item.id,
            "alert_instance_id": item.alert_instance_id,
            "channel_id": item.channel_id,
            "attempted_at": item.attempted_at,
            "queued_at": item.queued_at,
            "started_at": item.started_at,
            "completed_at": item.completed_at,
            "status": item.status,
            "attempt_number": item.attempt_number,
            "response_summary": item.response_summary,
            "safe_error_code": item.safe_error_code,
            "safe_error_summary": item.safe_error_summary,
            "next_attempt_at": item.next_attempt_at,
            "is_test": item.is_test,
        }
        for item in attempts
    ]


async def _target_user_scope_allowed(session: DbSession, principal: Principal, user: User) -> bool:
    if principal.all_sites:
        return True
    if user.all_sites:
        return False
    return set(await explicit_site_ids(session, user.id)).issubset(principal.site_ids)


@router.get("/users")
async def list_users(principal: Principal, session: DbSession) -> list[dict[str, Any]]:
    _permission(principal, "users.view")
    users = list(
        await session.scalars(
            select(User).where(User.lifecycle_state != "removed").order_by(User.email)
        )
    )
    output: list[dict[str, Any]] = []
    for user in users:
        if not principal.all_sites and (
            user.all_sites
            or not set(await explicit_site_ids(session, user.id)).issubset(principal.site_ids)
        ):
            continue
        roles = list(
            await session.scalars(select(UserRole.role_name).where(UserRole.user_id == user.id))
        )
        output.append(
            {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name,
                "is_active": user.is_active,
                "roles": roles,
            }
        )
    return output


@router.post("/users", status_code=201)
async def create_user(
    payload: UserCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "users.manage")
    if not password_is_strong(payload.password):
        raise ProblemError(
            422,
            "Weak password",
            "Use at least 14 characters and three character classes",
            "weak_password",
        )
    email = str(payload.email).lower()
    existing_user = await session.scalar(select(User).where(func.lower(User.email) == email))
    if existing_user:
        if existing_user.lifecycle_state == "removed":
            raise ProblemError(
                409,
                "Removed user already exists",
                "Restore the removed identity instead of creating a duplicate account",
                "user_removed_restore_required",
            )
        raise ProblemError(
            409,
            "User already exists",
            "An account with this email address already exists",
            "user_email_exists",
        )
    user = User(
        email=email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        password_changed_at=datetime.now(UTC),
        lifecycle_state="active",
    )
    session.add(user)
    await session.flush()
    effective_roles: set[str] = set(payload.roles)
    if "rate-manager" in effective_roles:
        effective_roles.add("viewer")
    if "admin" in effective_roles:
        _permission(principal, "users.manage_protected")
        if not payload.confirm_high_risk:
            raise ProblemError(
                409,
                "Protected confirmation required",
                "Confirm creation of the protected administrator",
                "protected_confirmation_required",
            )
        require_recent_reauthentication(principal.session.reauthenticated_at)
    requested_permissions = await permissions_for_roles(session, effective_roles)
    if not requested_permissions.issubset(principal.permissions):
        raise ProblemError(
            403,
            "Privilege delegation denied",
            "You cannot grant permissions that you do not possess",
            "permission_delegation_forbidden",
        )
    user.all_sites = principal.all_sites
    for role in sorted(effective_roles):
        session.add(UserRole(user_id=user.id, role_name=role))
    session.add(
        audit_event(
            action="user.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="user",
            object_id=user.id,
            details={"roles": sorted(effective_roles)},
        )
    )
    await session.commit()
    return {"id": user.id, "email": user.email, "roles": sorted(effective_roles)}


@router.post("/users/{user_id}/password-reset")
async def admin_password_reset(
    user_id: str,
    payload: PasswordReset,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, bool]:
    _permission(principal, "users.manage")
    if not password_is_strong(payload.new_password):
        raise ProblemError(
            422,
            "Weak password",
            "Use at least 14 characters and three character classes",
            "weak_password",
        )
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise ProblemError(404, "User not found", "User does not exist", "user_missing")
    if user.lifecycle_state == "removed":
        raise ProblemError(
            409,
            "User is removed",
            "Restore the user before resetting the password",
            "user_removed",
        )
    if not await _target_user_scope_allowed(session, principal, user):
        raise ProblemError(404, "User not found", "User does not exist", "user_missing")
    if "admin" in await user_role_names(session, user.id):
        _permission(principal, "users.manage_protected")
        require_recent_reauthentication(principal.session.reauthenticated_at)
    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = datetime.now(UTC)
    user.access_revision += 1
    revoked = await revoke_user_sessions(session, user.id)
    session.add(
        audit_event(
            action="user.password_reset",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="user",
            object_id=user.id,
        )
    )
    await session.commit()
    return {"reset": True, "sessions_revoked": revoked > 0}


@router.delete("/users/{user_id}", status_code=204)
async def deactivate_user(
    user_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> Response:
    """Disable a user while retaining their audit and ownership history."""
    _permission(principal, "users.disable")
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise ProblemError(404, "User not found", "User does not exist", "user_missing")
    if not await _target_user_scope_allowed(session, principal, user):
        raise ProblemError(404, "User not found", "User does not exist", "user_missing")
    if user.id == principal.user.id:
        raise ProblemError(
            409,
            "Cannot remove your own account",
            "Another administrator must remove this account",
            "self_deactivation_forbidden",
        )
    if not user.is_active:
        return Response(status_code=204)
    if user.is_protected:
        raise ProblemError(
            409,
            "Protected bootstrap account",
            "The protected bootstrap account cannot be disabled",
            "protected_account",
        )

    is_admin = "admin" in await user_role_names(session, user.id)
    if is_admin:
        _permission(principal, "users.manage_protected")
        require_recent_reauthentication(principal.session.reauthenticated_at)
        if await active_admin_count(session, excluding_user_id=user.id, lock=True) == 0:
            raise ProblemError(
                409,
                "Last administrator cannot be removed",
                "Create another administrator before removing this account",
                "last_admin_required",
            )

    user.is_active = False
    user.lifecycle_state = "disabled"
    user.access_revision += 1
    await revoke_user_sessions(session, user.id)
    session.add(
        audit_event(
            action="user.deactivated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="user",
            object_id=user.id,
        )
    )
    await session.commit()
    return Response(status_code=204)


@router.get("/audit-events")
async def list_audit_events(
    principal: Principal,
    session: DbSession,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    _permission(principal, "audit.view")
    events = list(
        await session.scalars(
            select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(limit)
        )
    )
    return [
        {
            "id": event.id,
            "occurred_at": event.occurred_at,
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "action": event.action,
            "object_type": event.object_type,
            "object_id": event.object_id,
            "outcome": event.outcome,
            "details": event.details,
        }
        for event in events
    ]


BACKUP_JOB_TYPES = (
    "backup_create",
    "backup_verify",
    "backup_restore_preflight",
    "backup_delete",
    "backup_replace_all",
)
ACTIVE_BACKUP_JOB_STATUSES = ("queued", "running")
BACKUP_IDENTIFIER_PATTERN = re.compile(r"^power-monitor-\d{8}T\d{6}Z(?:-[0-9a-f]{8})?$")


def _backup_payload(run: BackupRun) -> BackupView:
    return BackupView(
        id=run.id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        status=run.status,
        manifest_hash=run.manifest_hash,
        verified_at=run.verified_at,
        verification_details=dict(run.verification_details or {}),
        size_bytes=run.size_bytes,
        encrypted=run.encrypted,
        verification_started_at=run.verification_started_at,
        verification_completed_at=run.verification_completed_at,
        verification_attempt_count=run.verification_attempt_count,
        failed_stage=run.failed_stage,
        safe_error_code=run.safe_error_code,
        safe_error_summary=run.safe_error_summary,
        exit_code=run.exit_code,
        deleted_at=run.deleted_at,
        deletion_reason=run.deletion_reason,
        artifact_removal_result=run.artifact_removal_result,
        pre_deletion_status=run.pre_deletion_status,
        replaced_by_backup_id=run.replaced_by_backup_id,
    )


async def _active_backup_job(session: DbSession) -> BackgroundJob | None:
    return await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type.in_(BACKUP_JOB_TYPES),
            BackgroundJob.status.in_(ACTIVE_BACKUP_JOB_STATUSES),
        )
        .order_by(BackgroundJob.requested_at)
        .limit(1)
    )


def _backup_operation_in_progress(job: BackgroundJob) -> ProblemError:
    return ProblemError(
        409,
        "Backup operation already in progress",
        "Wait for the active backup operation to finish before starting another",
        "backup_operation_active",
        extra={
            "job_id": job.id,
            "backup_id": dict(job.progress or {}).get("backup_run_id"),
        },
    )


@router.get("/backups", response_model=list[BackupView])
async def list_backups(principal: Principal, session: DbSession) -> list[BackupView]:
    _permission(principal, "backups.view")
    runs = list(
        await session.scalars(
            select(BackupRun)
            .where(BackupRun.deleted_at.is_(None))
            .order_by(BackupRun.started_at.desc())
            .limit(100)
        )
    )
    return [_backup_payload(run) for run in runs]


def _backup_request_payload(job: BackgroundJob) -> dict[str, Any]:
    result = dict(job.result or {})
    progress = dict(job.progress or {})
    return {
        "id": job.id,
        "operation": {
            "backup_create": "create",
            "backup_verify": "verify",
            "backup_restore_preflight": "restore_preflight",
            "backup_delete": "delete",
            "backup_replace_all": "replace_all",
        }.get(job.job_type, job.job_type),
        "status": job.status,
        "requested_at": job.requested_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "backup_id": result.get("backup_run_id") or progress.get("backup_run_id"),
        "verified": bool(result.get("verified", False)),
        "maintenance_required": bool(result.get("maintenance_required", False)),
        "error_code": job.error_code,
        "error_detail": job.error_detail,
        "progress": progress,
        "result": result,
    }


@router.get("/backup-requests")
async def list_backup_requests(principal: Principal, session: DbSession) -> list[dict[str, Any]]:
    _permission(principal, "backups.view")
    requests = list(
        await session.scalars(
            select(BackgroundJob)
            .where(BackgroundJob.job_type.in_(BACKUP_JOB_TYPES))
            .order_by(BackgroundJob.requested_at.desc())
            .limit(100)
        )
    )
    return [_backup_request_payload(item) for item in requests]


@router.post("/backup-requests", status_code=202)
async def create_backup_request(
    payload: BackupRequestCreate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    permission = "backups.create" if payload.operation == "create" else "backups.restore"
    _permission(principal, permission)
    operation_job_type = (
        "backup_create" if payload.operation == "create" else "backup_restore_preflight"
    )
    existing = await session.scalar(
        select(BackgroundJob).where(
            BackgroundJob.job_type == operation_job_type,
            BackgroundJob.idempotency_key == payload.idempotency_key,
        )
    )
    if existing is not None:
        return _backup_request_payload(existing)
    backup: BackupRun | None = None
    if payload.operation == "restore_preflight":
        backup = await session.scalar(
            select(BackupRun).where(BackupRun.id == payload.backup_id).with_for_update()
        )
        if backup is None:
            raise ProblemError(
                404, "Backup not found", "The selected backup does not exist", "backup_missing"
            )
        if backup.status != "verified" or backup.verified_at is None:
            raise ProblemError(
                409,
                "Verified backup required",
                "Only a completed, automatically restored and verified backup can be selected",
                "backup_not_verified",
            )
    correlation_digest = hashlib.sha256(payload.idempotency_key.encode()).hexdigest()[:48]
    correlation_id = f"backup:{principal.user.id}:{correlation_digest}"
    active = await _active_backup_job(session)
    if active is not None:
        raise _backup_operation_in_progress(active)
    now = datetime.now(UTC)
    if payload.operation == "create":
        backup = BackupRun(
            id=new_uuid(),
            started_at=now,
            status="queued",
            path=None,
            manifest_hash=None,
            verified_at=None,
            verification_details={},
            requested_by=principal.user.id,
            trigger_type="manual",
            encrypted=False,
            verification_attempt_count=0,
            updated_at=now,
        )
        session.add(backup)
    else:
        assert backup is not None
        backup.status = "restore_preflight"
        backup.updated_at = now
    job = BackgroundJob(
        id=new_uuid(),
        job_type=operation_job_type,
        status="queued",
        requested_by=principal.user.id,
        requested_at=now,
        scheduled_for=now,
        correlation_id=correlation_id,
        dedupe_key="backup:global",
        idempotency_key=payload.idempotency_key,
        trigger_type="manual",
        progress={"backup_run_id": backup.id},
        result={},
    )
    session.add(job)
    session.add(
        audit_event(
            action=f"backup.{payload.operation}_requested",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="backup_request",
            object_id=job.id,
            details={"backup_id": backup.id},
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.job_type == operation_job_type,
                BackgroundJob.idempotency_key == payload.idempotency_key,
            )
        )
        if existing is not None:
            return _backup_request_payload(existing)
        active = await _active_backup_job(session)
        if active is not None:
            raise _backup_operation_in_progress(active) from None
        raise
    return _backup_request_payload(job)


@router.get("/backups/replace-all-preview")
async def replace_all_backups_preview(
    principal: Principal,
    session: DbSession,
) -> dict[str, int]:
    _permission(principal, "backups.view")
    if "admin" not in principal.roles:
        raise ProblemError(
            403,
            "Owner permission required",
            "Only the local owner can replace every backup",
            "backup_replace_all_owner_required",
        )
    rows = list(await session.scalars(select(BackupRun).where(BackupRun.deleted_at.is_(None))))
    verified = sum(1 for row in rows if row.status == "verified" and row.verified_at is not None)
    incomplete_states = {
        "queued",
        "creating",
        "verification_queued",
        "verifying",
        "restore_preflight",
        "restoring",
        "deleting",
    }
    incomplete = sum(1 for row in rows if row.status in incomplete_states)
    unverified = len(rows) - incomplete - verified
    storage_bytes = sum(int(row.size_bytes or row.original_size_bytes or 0) for row in rows)
    return {
        "existing_backup_count": len(rows),
        "existing_storage_bytes": storage_bytes,
        "incomplete_backup_count": incomplete,
        "unverified_backup_count": unverified,
        "verified_backup_count": verified,
        "estimated_reclaim_bytes": storage_bytes,
    }


@router.post("/backups/replace-all", status_code=202)
async def replace_all_backups(
    payload: BackupReplaceAllRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "backups.create")
    _permission(principal, "backups.delete")
    if "admin" not in principal.roles:
        raise ProblemError(
            403,
            "Owner permission required",
            "Only the local owner can replace every backup",
            "backup_replace_all_owner_required",
        )
    existing = await session.scalar(
        select(BackgroundJob).where(
            BackgroundJob.job_type == "backup_replace_all",
            BackgroundJob.idempotency_key == payload.idempotency_key,
        )
    )
    if existing is not None:
        return _backup_request_payload(existing)
    active = await _active_backup_job(session)
    if active is not None:
        raise _backup_operation_in_progress(active)
    now = datetime.now(UTC)
    inventory = list(
        await session.scalars(
            select(BackupRun).where(BackupRun.deleted_at.is_(None)).with_for_update()
        )
    )
    blocked_states = {
        "queued",
        "creating",
        "verification_queued",
        "verifying",
        "restore_preflight",
        "restoring",
        "deleting",
    }
    blocked = [item for item in inventory if item.status in blocked_states]
    if blocked:
        raise ProblemError(
            409,
            "Backup operation cannot start",
            "An existing backup or restore operation must finish before replacement",
            "backup_replace_all_incompatible_state",
            extra={"backup_ids": [item.id for item in blocked]},
        )
    replacement = BackupRun(
        id=new_uuid(),
        started_at=now,
        status="queued",
        path=None,
        manifest_hash=None,
        verified_at=None,
        verification_details={},
        requested_by=principal.user.id,
        trigger_type="replace_all",
        encrypted=False,
        verification_attempt_count=0,
        updated_at=now,
    )
    storage_bytes = sum(int(item.size_bytes or item.original_size_bytes or 0) for item in inventory)
    progress = {
        "stage": "preparing",
        "message": "Preparing replacement backup",
        "backup_run_id": replacement.id,
        "existing_backup_count": len(inventory),
        "existing_storage_bytes": storage_bytes,
        "verified_backup_count": sum(
            1 for item in inventory if item.status == "verified" and item.verified_at is not None
        ),
        "unverified_backup_count": sum(
            1 for item in inventory if item.status != "verified" or item.verified_at is None
        ),
        "incomplete_backup_count": 0,
        "estimated_reclaim_bytes": storage_bytes,
        "old_backup_ids": [item.id for item in inventory],
    }
    job = BackgroundJob(
        id=new_uuid(),
        job_type="backup_replace_all",
        status="queued",
        requested_by=principal.user.id,
        requested_at=now,
        scheduled_for=now,
        correlation_id=f"backup-replace-all:{replacement.id}",
        dedupe_key="backup:global",
        idempotency_key=payload.idempotency_key,
        trigger_type="manual",
        progress=progress,
        result={},
    )
    session.add_all([replacement, job])
    session.add(
        audit_event(
            action="backup.replace_all_requested",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="backup_request",
            object_id=job.id,
            details={
                "replacement_backup_id": replacement.id,
                "existing_backup_count": len(inventory),
                "existing_storage_bytes": storage_bytes,
            },
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.job_type == "backup_replace_all",
                BackgroundJob.idempotency_key == payload.idempotency_key,
            )
        )
        if existing is not None:
            return _backup_request_payload(existing)
        active = await _active_backup_job(session)
        if active is not None:
            raise _backup_operation_in_progress(active) from None
        raise
    return _backup_request_payload(job)


@router.post("/backups/{backup_id}/verify", response_model=BackupView, status_code=202)
async def verify_backup(
    backup_id: str,
    payload: BackupVerifyRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> BackupView:
    _permission(principal, "backups.verify")
    backup = await session.scalar(
        select(BackupRun).where(BackupRun.id == backup_id).with_for_update()
    )
    if backup is None:
        raise ProblemError(
            404, "Backup not found", "The selected backup does not exist", "backup_missing"
        )
    existing = await session.scalar(
        select(BackgroundJob).where(
            BackgroundJob.job_type == "backup_verify",
            BackgroundJob.idempotency_key == payload.idempotency_key,
        )
    )
    if existing is not None:
        return _backup_payload(backup)
    if backup.status == "verified" and backup.verified_at is not None:
        return _backup_payload(backup)
    if backup.status not in {"completed_unverified", "verification_failed"}:
        raise ProblemError(
            409,
            "Backup cannot be verified",
            f"A backup in the {backup.status} state cannot start verification",
            "backup_verify_transition_invalid",
        )
    if backup.path is None or not BACKUP_IDENTIFIER_PATTERN.fullmatch(Path(backup.path).name):
        raise ProblemError(
            409,
            "Backup artifact is not addressable",
            "The backup record does not contain a safe local artifact identifier",
            "backup_artifact_identifier_invalid",
        )
    active = await _active_backup_job(session)
    if active is not None:
        raise _backup_operation_in_progress(active)
    now = datetime.now(UTC)
    backup.status = "verification_queued"
    backup.updated_at = now
    job = BackgroundJob(
        id=new_uuid(),
        job_type="backup_verify",
        status="queued",
        requested_by=principal.user.id,
        requested_at=now,
        scheduled_for=now,
        correlation_id=f"backup-verify:{backup.id}:{backup.verification_attempt_count + 1}",
        dedupe_key="backup:global",
        idempotency_key=payload.idempotency_key,
        trigger_type="manual",
        progress={"backup_run_id": backup.id},
        result={},
    )
    session.add(job)
    session.add(
        audit_event(
            action="backup.verification_requested",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="backup_run",
            object_id=backup.id,
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        active = await _active_backup_job(session)
        if active is not None:
            raise _backup_operation_in_progress(active) from None
        raise
    return _backup_payload(backup)


@router.delete("/backups/{backup_id}", response_model=BackupView, status_code=202)
async def delete_backup(
    backup_id: str,
    payload: BackupDeleteRequest,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> BackupView:
    _permission(principal, "backups.delete")
    backup = await session.scalar(
        select(BackupRun).where(BackupRun.id == backup_id).with_for_update()
    )
    if backup is None:
        raise ProblemError(
            404, "Backup not found", "The selected backup does not exist", "backup_missing"
        )
    if payload.backup_id_confirmation not in {backup.id, backup.id[:8]}:
        raise ProblemError(
            422,
            "Backup confirmation does not match",
            "Enter the first eight characters of the backup ID exactly",
            "backup_confirmation_invalid",
        )
    eligible = {
        "verified",
        "completed_unverified",
        "verification_failed",
        "backup_failed",
        "deletion_failed",
    }
    if backup.status not in eligible:
        raise ProblemError(
            409,
            "Backup cannot be deleted",
            f"A backup in the {backup.status} state cannot be deleted",
            "backup_delete_transition_invalid",
        )
    if backup.verified_at is not None:
        verified_count = await session.scalar(
            select(func.count())
            .select_from(BackupRun)
            .where(BackupRun.verified_at.is_not(None), BackupRun.deleted_at.is_(None))
        )
        if int(verified_count or 0) <= 1:
            raise ProblemError(
                409,
                "Last verified backup is protected",
                "Create and verify another backup before deleting this one",
                "last_verified_backup_protected",
            )
    active = await _active_backup_job(session)
    if active is not None:
        raise _backup_operation_in_progress(active)
    now = datetime.now(UTC)
    backup.status = "deleting"
    backup.deleted_by = principal.user.id
    backup.deletion_reason = payload.reason
    backup.original_size_bytes = backup.size_bytes
    backup.updated_at = now
    attempt = int(
        await session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(
                BackgroundJob.job_type == "backup_delete",
                BackgroundJob.correlation_id.like(f"backup-delete:{backup.id}:%"),
            )
        )
        or 0
    )
    job = BackgroundJob(
        id=new_uuid(),
        job_type="backup_delete",
        status="queued",
        requested_by=principal.user.id,
        requested_at=now,
        scheduled_for=now,
        correlation_id=f"backup-delete:{backup.id}:{attempt + 1}",
        dedupe_key="backup:global",
        idempotency_key=f"delete:{backup.id}:{attempt + 1}",
        trigger_type="manual",
        progress={"backup_run_id": backup.id},
        result={},
    )
    session.add(job)
    session.add(
        audit_event(
            action="backup.deletion_requested",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="backup_run",
            object_id=backup.id,
            details={"reason": payload.reason},
        )
    )
    await session.commit()
    return _backup_payload(backup)


@router.get("/reports")
async def list_reports(principal: Viewer, session: DbSession) -> list[dict[str, Any]]:
    _permission(principal, "history.export")
    query = select(GeneratedReport).order_by(GeneratedReport.created_at.desc()).limit(100)
    if not principal.all_sites:
        query = query.where(GeneratedReport.requested_by == principal.user.id)
    reports = list(await session.scalars(query))
    return [
        {
            "id": report.id,
            "status": report.status,
            "created_at": report.created_at,
            "expires_at": report.expires_at,
            "data_coverage": report.data_coverage,
        }
        for report in reports
    ]


@router.get("/report-definitions")
async def list_report_definitions(principal: Viewer, session: DbSession) -> list[dict[str, Any]]:
    _permission(principal, "history.export")
    definitions = list(
        await session.scalars(select(ReportDefinition).order_by(ReportDefinition.name))
    )
    return [
        {
            "id": item.id,
            "name": item.name,
            "report_type": item.report_type,
            "configuration": item.configuration,
            "created_by": item.created_by,
        }
        for item in definitions
    ]


@router.post("/report-definitions", status_code=201)
async def create_report_definition(
    payload: ReportDefinitionWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "history.export")
    definition = ReportDefinition(**payload.model_dump(), created_by=principal.user.id)
    session.add(definition)
    session.add(
        audit_event(
            action="report_definition.created",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="report_definition",
            object_id=definition.id,
        )
    )
    await session.commit()
    return {"id": definition.id, "name": definition.name, "report_type": definition.report_type}


@router.put("/report-definitions/{definition_id}")
async def update_report_definition(
    definition_id: str,
    payload: ReportDefinitionWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "history.export")
    definition = await session.get(ReportDefinition, definition_id)
    if definition is None:
        raise ProblemError(
            404, "Definition not found", "Report definition does not exist", "report_missing"
        )
    for key, value in payload.model_dump().items():
        setattr(definition, key, value)
    session.add(
        audit_event(
            action="report_definition.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="report_definition",
            object_id=definition.id,
        )
    )
    await session.commit()
    return {"id": definition.id, "name": definition.name, "report_type": definition.report_type}


@router.delete("/report-definitions/{definition_id}", status_code=204, response_class=Response)
async def delete_report_definition(
    definition_id: str, request: Request, principal: CsrfPrincipal, session: DbSession
) -> Response:
    _permission(principal, "history.export")
    definition = await session.get(ReportDefinition, definition_id)
    if definition is None:
        raise ProblemError(
            404, "Definition not found", "Report definition does not exist", "report_missing"
        )
    session.add(
        audit_event(
            action="report_definition.deleted",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="report_definition",
            object_id=definition.id,
        )
    )
    await session.delete(definition)
    await session.commit()
    return Response(status_code=204)


@router.post("/report-definitions/{definition_id}/generate", status_code=202)
async def queue_report(
    definition_id: str,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _permission(principal, "history.export")
    definition = await session.get(ReportDefinition, definition_id)
    if definition is None:
        raise ProblemError(
            404, "Definition not found", "Report definition does not exist", "report_missing"
        )
    report = GeneratedReport(
        definition_id=definition.id,
        requested_by=principal.user.id,
        status="queued",
        file_path=None,
        data_coverage={},
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    session.add(report)
    session.add(
        audit_event(
            action="report.queued",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="generated_report",
            object_id=report.id,
            details={"definition_id": definition.id},
        )
    )
    await session.commit()
    return {"id": report.id, "status": report.status}


@router.get("/reports/{report_id}/download", response_class=FileResponse)
async def download_report(
    report_id: str, principal: Principal, session: DbSession, settings: AppSettings
) -> FileResponse:
    _permission(principal, "history.export")
    report = await session.get(GeneratedReport, report_id)
    if report is None or report.status != "completed" or not report.file_path:
        raise ProblemError(404, "Report unavailable", "Report is not ready", "report_unavailable")
    if not principal.all_sites and report.requested_by != principal.user.id:
        raise ProblemError(404, "Report unavailable", "Report is not ready", "report_unavailable")
    root = settings.report_path.resolve()
    path = Path(report.file_path).resolve()
    if root not in path.parents or not path.is_file():
        raise ProblemError(
            404, "Report unavailable", "Report file is missing", "report_unavailable"
        )
    return FileResponse(path, media_type="application/json", filename=path.name)
