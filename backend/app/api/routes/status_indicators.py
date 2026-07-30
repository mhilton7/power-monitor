from __future__ import annotations

import copy
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.api.deps import AppSettings, CsrfPrincipal, DbSession, Principal, audit_event
from app.db.models import (
    Role,
    RolePermission,
    StatusLayoutDraft,
    StatusLayoutRevision,
    StatusLayoutState,
)
from app.problem import ProblemError
from app.schemas import (
    StatusLayoutDraftWrite,
    StatusLayoutImport,
    StatusLayoutPreview,
    StatusLayoutPublish,
    StatusLayoutReset,
    StatusLayoutRestore,
    StatusLayoutValidate,
)
from app.status_indicators import (
    BREAKPOINTS,
    INDICATOR_DEFINITIONS,
    INDICATOR_REGISTRY,
    LAYOUT_SCHEMA_VERSION,
    PAGES,
    REGISTRY_VERSION,
    ZONES,
    compiled_configuration,
    current_layout,
    default_item,
    materialize_configuration,
    registry_payload,
    repair_configuration,
    resolve_layout,
    status_values,
    validate_configuration,
)

router = APIRouter(tags=["status indicators"])
_mutation_attempts: dict[str, deque[float]] = defaultdict(deque)


def _require(principal: Principal, permission: str) -> None:
    if permission not in principal.permissions:
        raise ProblemError(
            403,
            "Permission denied",
            "Your account does not have the required status-indicator permission",
            "forbidden",
            extra={"required_permission": permission},
        )


def _rate_limit(principal: Principal, operation: str) -> None:
    now = time.monotonic()
    key = f"{principal.user.id}:{operation}"
    attempts = _mutation_attempts[key]
    while attempts and attempts[0] < now - 60:
        attempts.popleft()
    if len(attempts) >= 10:
        raise ProblemError(
            429,
            "Too many layout changes",
            "Wait before publishing, restoring, or importing again",
            "status_layout_rate_limited",
        )
    attempts.append(now)


async def _role_catalog(
    session: DbSession,
) -> tuple[set[str], dict[str, set[str]], list[dict[str, str]]]:
    roles = list(await session.scalars(select(Role).where(Role.is_archived.is_(False))))
    assignments = list(
        (
            await session.execute(select(RolePermission.role_name, RolePermission.permission_code))
        ).all()
    )
    permissions: dict[str, set[str]] = {role.name: set() for role in roles}
    for role_name, permission in assignments:
        permissions.setdefault(role_name, set()).add(permission)
    return (
        set(permissions),
        permissions,
        [{"id": role.name, "label": role.display_name or role.name} for role in roles],
    )


async def _state(session: DbSession, *, lock: bool = False) -> StatusLayoutState:
    query = select(StatusLayoutState).where(StatusLayoutState.id == "current")
    if lock:
        query = query.with_for_update()
    state = await session.scalar(query)
    if state is None:
        state = StatusLayoutState(
            id="current",
            current_revision_id=None,
            current_revision=0,
            updated_at=datetime.now(UTC),
        )
        session.add(state)
        await session.flush()
    return state


def _identity(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("indicator_key")),
        str(item.get("page", "*")),
        str(item.get("role", "*")),
        str(item.get("breakpoint", "default")),
    )


def _changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    old = {_identity(item): item for item in materialize_configuration(before)["items"]}
    new = {_identity(item): item for item in materialize_configuration(after)["items"]}
    changes: list[dict[str, Any]] = []
    for identity in sorted(set(old) | set(new)):
        previous = old.get(identity)
        current = new.get(identity)
        if previous == current:
            continue
        changed_fields = sorted(
            field
            for field in set(previous or {}) | set(current or {})
            if (previous or {}).get(field) != (current or {}).get(field)
        )
        auditable_fields = {
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
        changes.append(
            {
                "indicator_key": identity[0],
                "page": identity[1],
                "role": identity[2],
                "breakpoint": identity[3],
                "changed_fields": changed_fields,
                "before": {
                    field: (previous or {}).get(field)
                    for field in changed_fields
                    if field in auditable_fields
                },
                "after": {
                    field: (current or {}).get(field)
                    for field in changed_fields
                    if field in auditable_fields
                },
            }
        )
    return changes


def _critical_hidden(configuration: dict[str, Any]) -> list[dict[str, str]]:
    hidden: list[dict[str, str]] = []
    for item in materialize_configuration(configuration)["items"]:
        if item.get("visible") is False:
            definition = INDICATOR_REGISTRY.get(str(item.get("indicator_key")))
            if definition and definition.critical_fallback:
                hidden.append(
                    {
                        "indicator_key": definition.key,
                        "fallback": definition.critical_fallback,
                        "page": str(item.get("page", "*")),
                        "role": str(item.get("role", "*")),
                        "breakpoint": str(item.get("breakpoint", "default")),
                    }
                )
    return hidden


def _revision_payload(
    revision: StatusLayoutRevision, *, include_configuration: bool
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": revision.id,
        "revision": revision.revision,
        "registry_version": revision.registry_version,
        "created_by": revision.created_by,
        "created_at": revision.created_at,
        "reason": revision.reason,
        "restored_from_id": revision.restored_from_id,
    }
    if include_configuration:
        payload["configuration"] = materialize_configuration(dict(revision.configuration))
    return payload


async def _validate(
    session: DbSession, configuration: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    roles, role_permissions, _role_payload = await _role_catalog(session)
    return validate_configuration(
        materialize_configuration(configuration),
        roles=roles,
        role_permissions=role_permissions,
    )


async def _audit_validation_failure(
    session: DbSession,
    principal: Principal,
    request: Request,
    error: ProblemError,
) -> None:
    session.add(
        audit_event(
            action="status_layout.validation_failed",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="status_layout",
            outcome="failure",
            details={"code": error.code},
        )
    )
    await session.commit()


async def _save_draft(
    *,
    session: DbSession,
    principal: Principal,
    request: Request,
    configuration: dict[str, Any],
    base_revision: int,
    draft_revision: int | None,
    reason: str | None,
    action: str,
) -> StatusLayoutDraft:
    state = await _state(session, lock=True)
    if state.current_revision != base_revision:
        raise ProblemError(
            409,
            "Published layout changed",
            "Reload or rebase this draft before saving",
            "status_layout_revision_conflict",
            extra={"current_revision": state.current_revision},
        )
    normalized, warnings = await _validate(session, configuration)
    _revision_number, published, _revision = await current_layout(session)
    existing = await session.scalar(
        select(StatusLayoutDraft).where(StatusLayoutDraft.id == "current").with_for_update()
    )
    if existing is None:
        if draft_revision not in {None, 0}:
            raise ProblemError(
                409,
                "Draft changed",
                "Reload the status layout draft",
                "status_layout_draft_conflict",
            )
        now = datetime.now(UTC)
        existing = StatusLayoutDraft(
            id="current",
            base_revision=base_revision,
            revision=1,
            previewed_revision=None,
            registry_version=REGISTRY_VERSION,
            configuration=normalized,
            edited_by=principal.user.id,
            reason=reason,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
        prior_configuration = published
    else:
        if draft_revision is not None and existing.revision != draft_revision:
            raise ProblemError(
                409,
                "Draft changed",
                "Another administrator changed this draft; reload before saving",
                "status_layout_draft_conflict",
                extra={"current_draft_revision": existing.revision},
            )
        prior_configuration = dict(existing.configuration)
        existing.revision += 1
        existing.previewed_revision = None
        existing.registry_version = REGISTRY_VERSION
        existing.configuration = normalized
        existing.edited_by = principal.user.id
        existing.reason = reason
        existing.updated_at = datetime.now(UTC)
    changes = _changes(prior_configuration, normalized)
    session.add(
        audit_event(
            action=action,
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="status_layout_draft",
            object_id=existing.id,
            details={
                "base_revision": base_revision,
                "draft_revision": existing.revision,
                "changed_indicators": sorted({item["indicator_key"] for item in changes}),
                "change_count": len(changes),
                "warnings": [item["code"] for item in warnings],
                "reason": reason,
            },
        )
    )
    content_fields = {
        "show_icon",
        "show_label",
        "show_value",
        "show_freshness",
        "show_severity",
        "show_tooltip",
    }
    for change in changes:
        fields = set(change["changed_fields"])
        item_actions: list[str] = []
        if "visible" in fields:
            item_actions.append(
                "status_layout.indicator_enabled"
                if change["after"].get("visible", True)
                else "status_layout.indicator_disabled"
            )
        if "zone" in fields:
            item_actions.append("status_layout.indicator_moved")
        if "order" in fields:
            item_actions.append("status_layout.indicator_reordered")
        if "density" in fields:
            item_actions.append("status_layout.indicator_density_changed")
        if fields & content_fields:
            item_actions.append("status_layout.indicator_content_changed")
        if not item_actions and change["role"] != "*":
            item_actions.append("status_layout.role_override_changed")
        if not item_actions and change["page"] != "*":
            item_actions.append("status_layout.page_override_changed")
        if not item_actions and change["breakpoint"] != "default":
            item_actions.append("status_layout.breakpoint_override_changed")
        for item_action in item_actions:
            session.add(
                audit_event(
                    action=item_action,
                    actor_type="user",
                    actor_id=principal.user.id,
                    request=request,
                    object_type="status_indicator",
                    object_id=change["indicator_key"],
                    details={
                        **change,
                        "draft_revision": existing.revision,
                        "reason": reason,
                    },
                )
            )
    await session.commit()
    return existing


def _draft_payload(
    draft: StatusLayoutDraft | None, current_revision: int, current: dict[str, Any]
) -> dict[str, Any]:
    return {
        "exists": draft is not None,
        "base_revision": draft.base_revision if draft else current_revision,
        "draft_revision": draft.revision if draft else 0,
        "previewed_revision": draft.previewed_revision if draft else None,
        "configuration": materialize_configuration(dict(draft.configuration) if draft else current),
        "edited_by": draft.edited_by if draft else None,
        "reason": draft.reason if draft else None,
        "updated_at": draft.updated_at if draft else None,
        "critical_hidden": _critical_hidden(dict(draft.configuration) if draft else current),
    }


@router.get("/api/v1/status-indicators/registry")
async def get_registry(principal: Principal) -> dict[str, Any]:
    _require(principal, "status_indicators.view")
    return {
        "registry_version": REGISTRY_VERSION,
        "indicators": registry_payload(permissions=principal.permissions),
        "zones": list(ZONES),
        "pages": list(PAGES),
        "breakpoints": list(BREAKPOINTS[1:]),
    }


@router.get("/api/v1/status-indicators/layout")
async def get_resolved_layout(
    principal: Principal,
    session: DbSession,
    page: str = "overview",
    breakpoint: str = "desktop",
) -> dict[str, Any]:
    _require(principal, "status_indicators.view")
    if breakpoint not in BREAKPOINTS[1:]:
        raise ProblemError(
            422,
            "Invalid breakpoint",
            "Use desktop, tablet, or mobile",
            "status_layout_breakpoint_invalid",
        )
    revision, configuration, _current = await current_layout(session)
    return resolve_layout(
        configuration,
        page=page,
        roles=set(principal.roles),
        permissions=principal.permissions,
        breakpoint=breakpoint,  # type: ignore[arg-type]
        revision=revision,
    )


@router.get("/api/v1/status-indicators/values")
async def get_status_values(
    principal: Principal,
    session: DbSession,
    settings: AppSettings,
    site_id: str | None = None,
    device_id: str | None = None,
    keys: str | None = None,
) -> dict[str, Any]:
    _require(principal, "status_indicators.view")
    return await status_values(
        session,
        settings=settings,
        permissions=principal.permissions,
        allowed_site_ids=set(principal.site_ids),
        all_sites=principal.all_sites,
        site_id=site_id,
        device_id=device_id,
        requested_keys={key for key in (keys or "").split(",") if key}
        if keys is not None
        else None,
    )


@router.get("/api/v1/admin/status-indicators/catalog")
async def get_admin_catalog(principal: Principal, session: DbSession) -> dict[str, Any]:
    _require(principal, "status_indicators.view")
    revision, configuration, _current = await current_layout(session)
    _roles, _role_permissions, role_payload = await _role_catalog(session)
    configured_keys = {
        item.get("indicator_key")
        for item in configuration.get("items", [])
        if isinstance(item, dict)
    }
    return {
        "registry_version": REGISTRY_VERSION,
        "schema_version": LAYOUT_SCHEMA_VERSION,
        "published_revision": revision,
        "indicators": registry_payload(),
        "zones": list(ZONES),
        "pages": list(PAGES),
        "breakpoints": list(BREAKPOINTS[1:]),
        "roles": role_payload,
        "new_indicator_keys": [
            item.key
            for item in INDICATOR_DEFINITIONS
            if revision > 1 and item.key not in configured_keys
        ],
        "excluded_status_surfaces": [
            {
                "surface": "record_row_status",
                "reason": (
                    "Alert, backup, enrollment, user, firmware, rate-version, and export "
                    "row states stay attached to their records."
                ),
            },
            {
                "surface": "functional_feedback",
                "reason": (
                    "Validation, authentication, access-denied, destructive confirmation, "
                    "chart missing-data, and inline operation errors are mandatory feedback."
                ),
            },
            {
                "surface": "site_selector",
                "reason": (
                    "The site selector is a data-scope control; hiding site.current never "
                    "removes authorization scope controls."
                ),
            },
        ],
    }


@router.get("/api/v1/admin/status-indicators/draft")
async def get_draft(principal: Principal, session: DbSession) -> dict[str, Any]:
    _require(principal, "status_indicators.view")
    revision, current, _row = await current_layout(session)
    draft = await session.get(StatusLayoutDraft, "current")
    return _draft_payload(draft, revision, current)


@router.put("/api/v1/admin/status-indicators/draft")
async def save_draft(
    payload: StatusLayoutDraftWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "status_indicators.manage")
    try:
        draft = await _save_draft(
            session=session,
            principal=principal,
            request=request,
            configuration=payload.configuration,
            base_revision=payload.base_revision,
            draft_revision=payload.draft_revision,
            reason=payload.reason,
            action="status_layout.draft_saved",
        )
    except ProblemError as error:
        if error.status == 422:
            await _audit_validation_failure(session, principal, request, error)
        raise
    return _draft_payload(draft, draft.base_revision, dict(draft.configuration))


@router.delete("/api/v1/admin/status-indicators/draft", status_code=204)
async def discard_draft(request: Request, principal: CsrfPrincipal, session: DbSession) -> Response:
    _require(principal, "status_indicators.manage")
    draft = await session.get(StatusLayoutDraft, "current")
    if draft:
        await session.delete(draft)
        session.add(
            audit_event(
                action="status_layout.draft_discarded",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="status_layout_draft",
                object_id=draft.id,
                details={"draft_revision": draft.revision},
            )
        )
        await session.commit()
    return Response(status_code=204)


@router.post("/api/v1/admin/status-indicators/validate")
async def validate_layout(
    payload: StatusLayoutValidate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "status_indicators.manage")
    draft = await session.get(StatusLayoutDraft, "current")
    configuration = payload.configuration or (
        dict(draft.configuration) if draft else (await current_layout(session))[1]
    )
    try:
        normalized, warnings = await _validate(session, configuration)
    except ProblemError as error:
        await _audit_validation_failure(session, principal, request, error)
        raise
    return {
        "valid": True,
        "registry_version": REGISTRY_VERSION,
        "item_count": len(normalized["items"]),
        "warnings": warnings,
        "critical_hidden": _critical_hidden(normalized),
    }


@router.post("/api/v1/admin/status-indicators/repair")
async def repair_layout(
    payload: StatusLayoutValidate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    """Return a reviewable, deterministic repair without publishing it."""

    _require(principal, "status_indicators.manage")
    draft = await session.get(StatusLayoutDraft, "current")
    configuration = payload.configuration or (
        dict(draft.configuration) if draft else (await current_layout(session))[1]
    )
    repaired, repairs = repair_configuration(configuration)
    normalized, warnings = await _validate(session, repaired)
    session.add(
        audit_event(
            action="status_layout.repair_generated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="status_layout",
            details={
                "repair_count": len(repairs),
                "metric_identities": sorted(
                    {
                        str(item["metric_identity"])
                        for item in repairs
                        if item.get("metric_identity")
                    }
                ),
            },
        )
    )
    await session.commit()
    return {
        "configuration": normalized,
        "repairs": repairs,
        "warnings": warnings,
        "message": (
            "Recommended placements are ready to review. Save and preview before publishing."
        ),
    }


def _scenario(configuration: dict[str, Any], scenario: str, page: str) -> dict[str, Any]:
    result = copy.deepcopy(materialize_configuration(configuration))
    globals_for_page = [
        item
        for item in result["items"]
        if item.get("page") == "*"
        and item.get("role") == "*"
        and item.get("breakpoint") == "default"
        and (
            INDICATOR_REGISTRY[item["indicator_key"]].global_shell_support
            or page in INDICATOR_REGISTRY[item["indicator_key"]].supported_pages
        )
    ]
    if scenario in {"one_disabled", "two_disabled"}:
        for item in globals_for_page[: 1 if scenario == "one_disabled" else 2]:
            item["visible"] = False
    elif scenario == "one_only":
        for index, item in enumerate(globals_for_page):
            item["visible"] = index == 0
    elif scenario == "empty_zone" and globals_for_page:
        target = globals_for_page[0].get("zone")
        for item in globals_for_page:
            if item.get("zone") == target:
                item["visible"] = False
    elif scenario == "many":
        for item in globals_for_page:
            item["visible"] = True
    return result


@router.post("/api/v1/admin/status-indicators/preview")
async def preview_layout(
    payload: StatusLayoutPreview,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, Any]:
    _require(principal, "status_indicators.manage")
    roles, role_permissions, _role_payload = await _role_catalog(session)
    if payload.role not in roles:
        raise ProblemError(
            422, "Unknown role", "Choose an existing role", "status_layout_role_unknown"
        )
    draft = await session.get(StatusLayoutDraft, "current")
    configuration = payload.configuration or (
        dict(draft.configuration) if draft else (await current_layout(session))[1]
    )
    normalized, warnings = validate_configuration(
        materialize_configuration(configuration),
        roles=roles,
        role_permissions=role_permissions,
    )
    scenario_configuration = _scenario(normalized, payload.scenario, payload.page)
    resolved = resolve_layout(
        scenario_configuration,
        page=payload.page,
        roles={payload.role},
        permissions=role_permissions[payload.role],
        breakpoint=payload.breakpoint,
        revision=draft.base_revision if draft else (await current_layout(session))[0],
    )
    real_values = await status_values(
        session,
        settings=settings,
        permissions=role_permissions[payload.role],
        allowed_site_ids=set(principal.site_ids),
        all_sites=principal.all_sites,
    )
    values = copy.deepcopy(real_values["values"])
    if payload.scenario in {"warning", "critical"}:
        first_key = next(
            (item["indicator_key"] for zone in resolved["zones"] for item in zone["items"]),
            None,
        )
        if first_key:
            values[first_key] = {
                "status": payload.scenario,
                "severity": payload.scenario,
                "display_value": payload.scenario.title(),
                "detail": "Deterministic preview state",
                "freshness_at": datetime.now(UTC),
            }
    if payload.scenario == "long_label":
        for zone in resolved["zones"]:
            if zone["items"]:
                preview_item = zone["items"][0]
                preview_item["show_label"] = True
                preview_item["density"] = "standard"
                preview_item["definition"]["default_label"] = (
                    "A deliberately long translated indicator label that must wrap without overflow"
                )
                break
    saved_draft_preview = bool(
        draft
        and normalized == materialize_configuration(dict(draft.configuration))
        and payload.scenario == "all_defaults"
    )
    if draft and saved_draft_preview:
        draft.previewed_revision = draft.revision
        draft.updated_at = datetime.now(UTC)
        session.add(
            audit_event(
                action="status_layout.draft_previewed",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="status_layout_draft",
                object_id=draft.id,
                details={
                    "draft_revision": draft.revision,
                    "page": payload.page,
                    "role": payload.role,
                    "breakpoint": payload.breakpoint,
                    "scenario": payload.scenario,
                },
            )
        )
        await session.commit()
    return {"layout": resolved, "values": values, "warnings": warnings}


@router.post("/api/v1/admin/status-indicators/publish", status_code=201)
async def publish_layout(
    payload: StatusLayoutPublish,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "status_indicators.manage")
    _rate_limit(principal, "publish")
    if not payload.confirm:
        raise ProblemError(
            409,
            "Publication confirmation required",
            "Review the difference summary and confirm publication",
            "status_layout_publish_confirmation_required",
        )
    state = await _state(session, lock=True)
    if state.current_revision != payload.base_revision:
        session.add(
            audit_event(
                action="status_layout.stale_publish_rejected",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="status_layout",
                outcome="failure",
                details={
                    "expected_revision": payload.base_revision,
                    "current_revision": state.current_revision,
                },
            )
        )
        await session.commit()
        raise ProblemError(
            409,
            "Published layout changed",
            "Reload or rebase this draft before publishing",
            "status_layout_revision_conflict",
            extra={"current_revision": state.current_revision},
        )
    draft = await session.scalar(
        select(StatusLayoutDraft).where(StatusLayoutDraft.id == "current").with_for_update()
    )
    if draft is None:
        raise ProblemError(
            409,
            "No layout draft",
            "Save and preview a draft before publishing",
            "status_layout_draft_missing",
        )
    if draft.base_revision != payload.base_revision or draft.revision != payload.draft_revision:
        raise ProblemError(
            409,
            "Draft changed",
            "Reload and preview the current draft",
            "status_layout_draft_conflict",
            extra={"current_draft_revision": draft.revision},
        )
    if draft.previewed_revision != draft.revision:
        raise ProblemError(
            409,
            "Preview required",
            "Preview the current draft before publishing",
            "status_layout_preview_required",
        )
    normalized, warnings = await _validate(session, dict(draft.configuration))
    critical = _critical_hidden(normalized)
    if critical and not payload.confirm_critical:
        raise ProblemError(
            409,
            "Critical indicator confirmation required",
            "Confirm that critical states remain visible in their fallback workflows",
            "status_layout_critical_confirmation_required",
            extra={"indicators": critical},
        )
    _revision_number, before, _row = await current_layout(session)
    revision = StatusLayoutRevision(
        revision=state.current_revision + 1,
        registry_version=REGISTRY_VERSION,
        configuration=normalized,
        created_by=principal.user.id,
        created_at=datetime.now(UTC),
        reason=payload.reason or draft.reason,
        restored_from_id=None,
    )
    session.add(revision)
    await session.flush()
    state.current_revision_id = revision.id
    state.current_revision = revision.revision
    state.updated_at = datetime.now(UTC)
    changes = _changes(before, normalized)
    session.add(
        audit_event(
            action="status_layout.draft_published",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="status_layout_revision",
            object_id=revision.id,
            details={
                "revision": revision.revision,
                "base_revision": payload.base_revision,
                "changed_indicators": sorted({item["indicator_key"] for item in changes}),
                "change_count": len(changes),
                "warnings": [item["code"] for item in warnings],
                "reason": revision.reason,
            },
        )
    )
    content_fields = {
        "show_icon",
        "show_label",
        "show_value",
        "show_freshness",
        "show_severity",
        "show_tooltip",
    }
    for change in changes:
        fields = set(change["changed_fields"])
        actions: list[str] = []
        if "visible" in fields:
            actions.append(
                "status_layout.indicator_enabled"
                if change["after"].get("visible", True)
                else "status_layout.indicator_disabled"
            )
        if "zone" in fields:
            actions.append("status_layout.indicator_moved")
        if "order" in fields:
            actions.append("status_layout.indicator_reordered")
        if "density" in fields:
            actions.append("status_layout.indicator_density_changed")
        if fields & content_fields:
            actions.append("status_layout.indicator_content_changed")
        for action in actions or ["status_layout.indicator_override_changed"]:
            session.add(
                audit_event(
                    action=action,
                    actor_type="user",
                    actor_id=principal.user.id,
                    request=request,
                    object_type="status_indicator",
                    object_id=change["indicator_key"],
                    details={
                        "revision": revision.revision,
                        "page": change["page"],
                        "role": change["role"],
                        "breakpoint": change["breakpoint"],
                        "changed_fields": change["changed_fields"],
                        "before": change["before"],
                        "after": change["after"],
                        "reason": revision.reason,
                    },
                )
            )
    for item in critical:
        session.add(
            audit_event(
                action="status_layout.critical_indicator_hidden",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="status_indicator",
                object_id=item["indicator_key"],
                details={
                    "revision": revision.revision,
                    "fallback": item["fallback"],
                    "page": item["page"],
                    "role": item["role"],
                    "breakpoint": item["breakpoint"],
                    "reason": revision.reason,
                },
            )
        )
    await session.delete(draft)
    await session.commit()
    return {
        **_revision_payload(revision, include_configuration=True),
        "changes": changes,
        "critical_hidden": critical,
    }


@router.post("/api/v1/admin/status-indicators/reset")
async def reset_layout(
    payload: StatusLayoutReset,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "status_indicators.manage")
    draft = await session.get(StatusLayoutDraft, "current")
    current_revision, current, _row = await current_layout(session)
    if payload.base_revision != current_revision:
        raise ProblemError(
            409,
            "Published layout changed",
            "Reload before restoring defaults",
            "status_layout_revision_conflict",
        )
    configuration = materialize_configuration(dict(draft.configuration) if draft else current)
    defaults = compiled_configuration()
    if payload.scope == "all":
        configuration = defaults
    elif payload.scope == "indicator":
        if payload.indicator_key not in INDICATOR_REGISTRY:
            raise ProblemError(
                422,
                "Unknown indicator",
                "Choose a registered indicator",
                "status_indicator_unknown",
            )
        configuration["items"] = [
            item
            for item in configuration["items"]
            if item["indicator_key"] != payload.indicator_key
        ]
        configuration["items"].append(default_item(INDICATOR_REGISTRY[payload.indicator_key]))
    elif payload.scope == "zone":
        if payload.zone not in ZONES:
            raise ProblemError(
                422,
                "Unknown zone",
                "Choose a registered semantic zone",
                "status_indicator_zone_unsupported",
            )
        default_by_key = {item["indicator_key"]: item for item in defaults["items"]}
        configuration["items"] = [
            default_by_key.get(item["indicator_key"], item)
            if item.get("zone") == payload.zone
            and item.get("page") == "*"
            and item.get("role") == "*"
            else item
            for item in configuration["items"]
        ]
    else:
        if payload.page not in PAGES:
            raise ProblemError(
                422, "Unknown page", "Choose a registered page", "status_indicator_page_unsupported"
            )
        configuration["items"] = [
            item for item in configuration["items"] if item.get("page") != payload.page
        ]
    saved = await _save_draft(
        session=session,
        principal=principal,
        request=request,
        configuration=configuration,
        base_revision=current_revision,
        draft_revision=payload.draft_revision,
        reason=payload.reason,
        action="status_layout.defaults_restored",
    )
    return _draft_payload(saved, saved.base_revision, dict(saved.configuration))


@router.get("/api/v1/admin/status-indicators/revisions")
async def list_revisions(principal: Principal, session: DbSession) -> dict[str, Any]:
    _require(principal, "status_indicators.view")
    rows = list(
        await session.scalars(
            select(StatusLayoutRevision).order_by(StatusLayoutRevision.revision.desc()).limit(200)
        )
    )
    return {"revisions": [_revision_payload(item, include_configuration=False) for item in rows]}


@router.get("/api/v1/admin/status-indicators/revisions/{revision_id}")
async def get_revision(
    revision_id: str, principal: Principal, session: DbSession
) -> dict[str, Any]:
    _require(principal, "status_indicators.view")
    revision = await session.get(StatusLayoutRevision, revision_id)
    if revision is None:
        raise ProblemError(
            404,
            "Revision not found",
            "The status layout revision does not exist",
            "status_layout_revision_missing",
        )
    return _revision_payload(revision, include_configuration=True)


@router.post("/api/v1/admin/status-indicators/revisions/{revision_id}/restore", status_code=201)
async def restore_revision(
    revision_id: str,
    payload: StatusLayoutRestore,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "status_indicators.manage")
    _rate_limit(principal, "restore")
    if not payload.confirm:
        raise ProblemError(
            409,
            "Restore confirmation required",
            "Confirm creation of a restored immutable revision",
            "status_layout_restore_confirmation_required",
        )
    state = await _state(session, lock=True)
    if state.current_revision != payload.base_revision:
        raise ProblemError(
            409,
            "Published layout changed",
            "Reload before restoring",
            "status_layout_revision_conflict",
        )
    source = await session.get(StatusLayoutRevision, revision_id)
    if source is None:
        raise ProblemError(
            404,
            "Revision not found",
            "The status layout revision does not exist",
            "status_layout_revision_missing",
        )
    normalized, _warnings = await _validate(session, dict(source.configuration))
    critical = _critical_hidden(normalized)
    if critical and not payload.confirm_critical:
        raise ProblemError(
            409,
            "Critical indicator confirmation required",
            "Confirm the restored critical-indicator visibility",
            "status_layout_critical_confirmation_required",
            extra={"indicators": critical},
        )
    restored = StatusLayoutRevision(
        revision=state.current_revision + 1,
        registry_version=REGISTRY_VERSION,
        configuration=normalized,
        created_by=principal.user.id,
        created_at=datetime.now(UTC),
        reason=payload.reason,
        restored_from_id=source.id,
    )
    session.add(restored)
    await session.flush()
    state.current_revision_id = restored.id
    state.current_revision = restored.revision
    state.updated_at = datetime.now(UTC)
    draft = await session.get(StatusLayoutDraft, "current")
    if draft:
        await session.delete(draft)
    session.add(
        audit_event(
            action="status_layout.revision_restored",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="status_layout_revision",
            object_id=restored.id,
            details={
                "revision": restored.revision,
                "restored_from_id": source.id,
                "reason": payload.reason,
            },
        )
    )
    await session.commit()
    return _revision_payload(restored, include_configuration=True)


@router.get("/api/v1/admin/status-indicators/export")
async def export_layout(
    request: Request,
    principal: Principal,
    session: DbSession,
    draft: bool = False,
) -> dict[str, Any]:
    _require(principal, "status_indicators.view")
    revision, configuration, _row = await current_layout(session)
    draft_row = await session.get(StatusLayoutDraft, "current") if draft else None
    if draft_row:
        configuration = materialize_configuration(dict(draft_row.configuration))
    session.add(
        audit_event(
            action="status_layout.exported",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="status_layout",
            object_id=str(revision),
            details={"draft": draft, "registry_version": REGISTRY_VERSION},
        )
    )
    await session.commit()
    return {
        "schema_version": LAYOUT_SCHEMA_VERSION,
        "registry_version": REGISTRY_VERSION,
        "published_revision": revision,
        "configuration": configuration,
    }


@router.post("/api/v1/admin/status-indicators/import")
async def import_layout(
    payload: StatusLayoutImport,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "status_indicators.manage")
    _rate_limit(principal, "import")
    if payload.registry_version != REGISTRY_VERSION:
        raise ProblemError(
            422,
            "Registry version mismatch",
            "Import must target the current indicator registry",
            "status_layout_registry_invalid",
        )
    configuration = dict(payload.configuration)
    configuration["schema_version"] = payload.schema_version
    configuration["registry_version"] = payload.registry_version
    try:
        saved = await _save_draft(
            session=session,
            principal=principal,
            request=request,
            configuration=configuration,
            base_revision=payload.base_revision,
            draft_revision=None,
            reason=payload.reason,
            action="status_layout.imported",
        )
    except ProblemError as error:
        if error.status == 422:
            await _audit_validation_failure(session, principal, request, error)
        raise
    return {
        **_draft_payload(saved, saved.base_revision, dict(saved.configuration)),
        "requires_preview": True,
        "difference_summary": _changes(
            (await current_layout(session))[1], dict(saved.configuration)
        ),
    }
