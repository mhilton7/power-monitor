from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.deps import CsrfPrincipal, DbSession, Principal, audit_event
from app.db.models import (
    InterfaceTextDraft,
    InterfaceTextRevision,
    InterfaceTextState,
)
from app.interface_text import (
    TEXT_CATALOG,
    catalog_payload,
    compiled_defaults,
    current_revision,
    current_text_payload,
    validate_text_values,
)
from app.problem import ProblemError
from app.schemas import (
    InterfaceTextDraftWrite,
    InterfaceTextImport,
    InterfaceTextPublish,
    InterfaceTextReset,
    InterfaceTextRestore,
)

router = APIRouter(tags=["interface text"])
_publish_attempts: dict[str, deque[float]] = defaultdict(deque)


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
    attempts = _publish_attempts[key]
    while attempts and attempts[0] < now - 300:
        attempts.popleft()
    if len(attempts) >= 20:
        raise ProblemError(
            429,
            "Too many publication requests",
            "Wait five minutes before trying another publication",
            "interface_text_publish_throttled",
        )
    attempts.append(now)


async def _audit_denial(
    session: DbSession,
    request: Request,
    principal: Principal,
    *,
    action: str,
    code: str,
    details: dict[str, object] | None = None,
) -> None:
    session.add(
        audit_event(
            action=action,
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="interface_text_revision",
            object_id="current",
            outcome="denied",
            details={"code": code, **(details or {})},
        )
    )
    await session.commit()


async def _state(session: DbSession, *, lock: bool = False) -> InterfaceTextState:
    query = select(InterfaceTextState).where(InterfaceTextState.id == "current")
    if lock:
        query = query.with_for_update()
    state = await session.scalar(query)
    if state is None:
        state = InterfaceTextState(
            id="current",
            current_revision_id=None,
            current_revision=0,
            updated_at=datetime.now(UTC),
        )
        session.add(state)
        await session.flush()
    return state


async def _publish_values(
    *,
    session: DbSession,
    principal: Principal,
    request: Request,
    values: dict[str, str],
    reason: str | None,
    action: str,
    expected_revision: int,
    restored_from_id: str | None = None,
) -> InterfaceTextRevision:
    state = await _state(session, lock=True)
    if state.current_revision != expected_revision:
        session.add(
            audit_event(
                action="interface_text.publication_conflict",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="interface_text_revision",
                object_id=str(expected_revision),
                outcome="denied",
                details={
                    "base_revision": expected_revision,
                    "current_revision": state.current_revision,
                },
            )
        )
        await session.commit()
        raise ProblemError(
            409,
            "Published text changed",
            "Reload and review the current revision before publishing",
            "interface_text_revision_conflict",
        )
    normalized = validate_text_values(values, complete=True)
    defaults = compiled_defaults()
    overrides = {key: value for key, value in normalized.items() if value != defaults[key]}
    revision_number = state.current_revision + 1
    revision = InterfaceTextRevision(
        revision=revision_number,
        values=overrides,
        created_by=principal.user.id,
        created_at=datetime.now(UTC),
        reason=reason,
        restored_from_id=restored_from_id,
    )
    session.add(revision)
    await session.flush()
    state.current_revision_id = revision.id
    state.current_revision = revision_number
    state.updated_at = datetime.now(UTC)
    session.add(
        audit_event(
            action=action,
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="interface_text_revision",
            object_id=revision.id,
            details={
                "revision": revision_number,
                "changed_keys": sorted(overrides),
                "reason": reason,
                "restored_from_id": restored_from_id,
            },
        )
    )
    return revision


def _revision_payload(revision: InterfaceTextRevision, *, include_values: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": revision.id,
        "revision": revision.revision,
        "created_by": revision.created_by,
        "created_at": revision.created_at,
        "reason": revision.reason,
        "restored_from_id": revision.restored_from_id,
        "changed_key_count": len(revision.values),
    }
    if include_values:
        values = compiled_defaults()
        values.update(revision.values)
        payload["values"] = values
        payload["overrides"] = revision.values
    return payload


@router.get("/api/v1/public/interface-text")
async def public_interface_text(request: Request, session: DbSession) -> Response:
    payload = await current_text_payload(session, public_only=True)
    etag = f'"interface-text-{payload["revision"]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304, headers={"ETag": etag, "Cache-Control": "public, max-age=60"}
        )
    return JSONResponse(
        payload,
        headers={
            "ETag": etag,
            "Cache-Control": "public, max-age=60, stale-while-revalidate=300",
        },
    )


@router.get("/api/v1/interface-text")
async def authenticated_interface_text(
    _principal: Principal, session: DbSession
) -> dict[str, object]:
    return await current_text_payload(session)


@router.get("/api/v1/admin/interface-text/catalog")
async def get_catalog(principal: Principal, session: DbSession) -> dict[str, object]:
    _require(principal, "interface_text.view")
    return await catalog_payload(session)


@router.get("/api/v1/admin/interface-text/draft")
async def get_draft(principal: Principal, session: DbSession) -> dict[str, Any]:
    _require(principal, "interface_text.view")
    current_number, _overrides, _revision = await current_revision(session)
    draft = await session.get(InterfaceTextDraft, "current")
    return {
        "exists": draft is not None,
        "base_revision": draft.base_revision if draft else current_number,
        "draft_revision": draft.revision if draft else 0,
        "previewed_revision": draft.previewed_revision if draft else None,
        "values": dict(draft.values) if draft else {},
        "reason": draft.reason if draft else None,
        "edited_by": draft.edited_by if draft else None,
        "updated_at": draft.updated_at if draft else None,
    }


@router.put("/api/v1/admin/interface-text/draft")
async def save_draft(
    payload: InterfaceTextDraftWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "interface_text.manage")
    current_number = (await _state(session, lock=True)).current_revision
    if payload.base_revision != current_number:
        session.add(
            audit_event(
                action="interface_text.draft_conflict",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="interface_text_draft",
                object_id="current",
                outcome="denied",
                details={
                    "base_revision": payload.base_revision,
                    "current_revision": current_number,
                },
            )
        )
        await session.commit()
        raise ProblemError(
            409,
            "Published text changed",
            "Reload and review the newer published revision before saving",
            "interface_text_revision_conflict",
        )
    try:
        values = validate_text_values(payload.values)
    except ProblemError as exc:
        session.add(
            audit_event(
                action="interface_text.validation_failed",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="interface_text_draft",
                object_id="current",
                outcome="denied",
                details={"code": exc.code},
            )
        )
        await session.commit()
        raise
    now = datetime.now(UTC)
    draft = await session.scalar(
        select(InterfaceTextDraft).where(InterfaceTextDraft.id == "current").with_for_update()
    )
    if draft is None:
        if payload.draft_revision not in {None, 0}:
            raise ProblemError(
                409,
                "Text draft changed",
                "Reload the draft before saving",
                "interface_text_draft_conflict",
            )
        draft = InterfaceTextDraft(
            id="current",
            base_revision=current_number,
            revision=1,
            previewed_revision=None,
            values=values,
            edited_by=principal.user.id,
            reason=payload.reason,
            created_at=now,
            updated_at=now,
        )
        session.add(draft)
    else:
        if payload.draft_revision != draft.revision:
            raise ProblemError(
                409,
                "Text draft changed",
                "Reload the draft before saving",
                "interface_text_draft_conflict",
            )
        draft.revision += 1
        draft.previewed_revision = None
        draft.values = values
        draft.edited_by = principal.user.id
        draft.reason = payload.reason
        draft.updated_at = now
    session.add(
        audit_event(
            action="interface_text.draft_saved",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="interface_text_draft",
            object_id="current",
            details={
                "keys": sorted(values),
                "draft_revision": draft.revision,
                "reason": payload.reason,
            },
        )
    )
    await session.commit()
    return {
        "base_revision": draft.base_revision,
        "draft_revision": draft.revision,
        "previewed_revision": draft.previewed_revision,
        "values": draft.values,
        "reason": draft.reason,
        "updated_at": draft.updated_at,
    }


@router.delete("/api/v1/admin/interface-text/draft", status_code=204)
async def discard_draft(
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> Response:
    _require(principal, "interface_text.manage")
    draft = await session.scalar(
        select(InterfaceTextDraft).where(InterfaceTextDraft.id == "current").with_for_update()
    )
    if draft is not None:
        await session.delete(draft)
        session.add(
            audit_event(
                action="interface_text.draft_discarded",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="interface_text_draft",
                object_id="current",
            )
        )
        await session.commit()
    return Response(status_code=204)


@router.post("/api/v1/admin/interface-text/preview")
async def preview_draft(
    request: Request, principal: CsrfPrincipal, session: DbSession
) -> dict[str, object]:
    _require(principal, "interface_text.manage")
    _revision_number, overrides, _revision = await current_revision(session)
    draft = await session.scalar(
        select(InterfaceTextDraft).where(InterfaceTextDraft.id == "current").with_for_update()
    )
    values = compiled_defaults()
    values.update(overrides)
    if draft:
        values.update(validate_text_values(dict(draft.values)))
        draft.previewed_revision = draft.revision
        session.add(
            audit_event(
                action="interface_text.previewed",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="interface_text_draft",
                object_id="current",
                details={"draft_revision": draft.revision, "keys": sorted(draft.values)},
            )
        )
        await session.commit()
    return {"draft_revision": draft.revision if draft else 0, "values": values}


@router.post("/api/v1/admin/interface-text/publish", status_code=201)
async def publish_draft(
    payload: InterfaceTextPublish,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "interface_text.manage")
    _rate_limit(principal, "publish")
    if not payload.confirm:
        raise ProblemError(
            409,
            "Publication confirmation required",
            "Review the preview and confirm publication",
            "interface_text_publish_confirmation_required",
        )
    await _state(session, lock=True)
    current_number, overrides, _current = await current_revision(session)
    draft = await session.scalar(
        select(InterfaceTextDraft).where(InterfaceTextDraft.id == "current").with_for_update()
    )
    if draft is None:
        raise ProblemError(
            409, "No text draft", "Save a draft before publishing", "interface_text_draft_missing"
        )
    if (
        payload.base_revision != current_number
        or draft.base_revision != current_number
        or payload.draft_revision != draft.revision
    ):
        await _audit_denial(
            session,
            request,
            principal,
            action="interface_text.publication_conflict",
            code="interface_text_revision_conflict",
            details={
                "base_revision": payload.base_revision,
                "current_revision": current_number,
                "draft_revision": payload.draft_revision,
                "current_draft_revision": draft.revision,
            },
        )
        raise ProblemError(
            409,
            "Published text changed",
            "Reload and review the current revision before publishing",
            "interface_text_revision_conflict",
        )
    if draft.previewed_revision != draft.revision:
        raise ProblemError(
            409,
            "Preview required",
            "Preview the current validated draft revision before publishing",
            "interface_text_preview_required",
        )
    values = compiled_defaults()
    values.update(overrides)
    values.update(draft.values)
    revision = await _publish_values(
        session=session,
        principal=principal,
        request=request,
        values=values,
        reason=payload.reason or draft.reason,
        action="interface_text.published",
        expected_revision=payload.base_revision,
    )
    await session.delete(draft)
    await session.commit()
    return _revision_payload(revision, include_values=True)


@router.post("/api/v1/admin/interface-text/reset", status_code=201)
async def reset_text(
    payload: InterfaceTextReset,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "interface_text.manage")
    _rate_limit(principal, "reset")
    await _state(session, lock=True)
    current_number, overrides, _current = await current_revision(session)
    if payload.base_revision != current_number:
        await _audit_denial(
            session,
            request,
            principal,
            action="interface_text.publication_conflict",
            code="interface_text_revision_conflict",
            details={
                "base_revision": payload.base_revision,
                "current_revision": current_number,
                "operation": "reset",
            },
        )
        raise ProblemError(
            409,
            "Published text changed",
            "Reload before restoring defaults",
            "interface_text_revision_conflict",
        )
    if payload.key and payload.key not in TEXT_CATALOG:
        raise ProblemError(
            422,
            "Unknown interface text key",
            "The field is not registered",
            "interface_text_key_unknown",
        )
    if payload.section and payload.section not in {item.section for item in TEXT_CATALOG.values()}:
        raise ProblemError(
            422,
            "Unknown interface text section",
            "The section is not registered",
            "interface_text_section_unknown",
        )
    values = compiled_defaults()
    values.update(overrides)
    if payload.key is not None:
        reset_keys = [payload.key]
    else:
        reset_keys = [
            key for key, definition in TEXT_CATALOG.items() if definition.section == payload.section
        ]
    defaults = compiled_defaults()
    for key in reset_keys:
        values[key] = defaults[key]
    revision = await _publish_values(
        session=session,
        principal=principal,
        request=request,
        values=values,
        reason=payload.reason,
        action="interface_text.defaults_restored",
        expected_revision=payload.base_revision,
    )
    draft = await session.get(InterfaceTextDraft, "current")
    if draft:
        await session.delete(draft)
    await session.commit()
    return _revision_payload(revision, include_values=True)


@router.get("/api/v1/admin/interface-text/revisions")
async def list_revisions(principal: Principal, session: DbSession) -> dict[str, Any]:
    _require(principal, "interface_text.view")
    revisions = list(
        await session.scalars(
            select(InterfaceTextRevision).order_by(InterfaceTextRevision.revision.desc()).limit(200)
        )
    )
    return {"revisions": [_revision_payload(item, include_values=False) for item in revisions]}


@router.get("/api/v1/admin/interface-text/revisions/{revision_id}")
async def get_revision(
    revision_id: str, principal: Principal, session: DbSession
) -> dict[str, Any]:
    _require(principal, "interface_text.view")
    revision = await session.get(InterfaceTextRevision, revision_id)
    if revision is None:
        raise ProblemError(
            404,
            "Revision not found",
            "Text revision does not exist",
            "interface_text_revision_missing",
        )
    return _revision_payload(revision, include_values=True)


@router.post("/api/v1/admin/interface-text/revisions/{revision_id}/restore", status_code=201)
async def restore_revision(
    revision_id: str,
    payload: InterfaceTextRestore,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "interface_text.manage")
    _rate_limit(principal, "restore")
    if not payload.confirm:
        raise ProblemError(
            409,
            "Restore confirmation required",
            "Confirm the selected revision",
            "interface_text_restore_confirmation_required",
        )
    await _state(session, lock=True)
    current_number, _overrides, _current = await current_revision(session)
    if payload.base_revision != current_number:
        await _audit_denial(
            session,
            request,
            principal,
            action="interface_text.publication_conflict",
            code="interface_text_revision_conflict",
            details={
                "base_revision": payload.base_revision,
                "current_revision": current_number,
                "operation": "restore",
            },
        )
        raise ProblemError(
            409,
            "Published text changed",
            "Reload before restoring",
            "interface_text_revision_conflict",
        )
    source = await session.get(InterfaceTextRevision, revision_id)
    if source is None:
        raise ProblemError(
            404,
            "Revision not found",
            "Text revision does not exist",
            "interface_text_revision_missing",
        )
    values = compiled_defaults()
    values.update(source.values)
    revision = await _publish_values(
        session=session,
        principal=principal,
        request=request,
        values=values,
        reason=payload.reason,
        action="interface_text.revision_restored",
        expected_revision=payload.base_revision,
        restored_from_id=source.id,
    )
    draft = await session.get(InterfaceTextDraft, "current")
    if draft:
        await session.delete(draft)
    await session.commit()
    return _revision_payload(revision, include_values=True)


@router.get("/api/v1/admin/interface-text/export")
async def export_text(
    request: Request,
    principal: Principal,
    session: DbSession,
    draft: bool = False,
) -> dict[str, object]:
    _require(principal, "interface_text.view")
    current_number, overrides, _current = await current_revision(session)
    values = dict(overrides)
    if draft:
        draft_row = await session.get(InterfaceTextDraft, "current")
        if draft_row:
            values.update(draft_row.values)
    session.add(
        audit_event(
            action="interface_text.exported",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="interface_text_revision",
            object_id=str(current_number),
            details={"draft": draft, "keys": sorted(values)},
        )
    )
    await session.commit()
    return {
        "schema_version": "power-monitor-interface-text/1.0",
        "base_revision": current_number,
        "values": values,
    }


@router.post("/api/v1/admin/interface-text/import")
async def import_text(
    payload: InterfaceTextImport,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> dict[str, Any]:
    _require(principal, "interface_text.manage")
    current_number = (await _state(session, lock=True)).current_revision
    if payload.base_revision != current_number:
        await _audit_denial(
            session,
            request,
            principal,
            action="interface_text.draft_conflict",
            code="interface_text_revision_conflict",
            details={
                "base_revision": payload.base_revision,
                "current_revision": current_number,
                "operation": "import",
            },
        )
        raise ProblemError(
            409,
            "Published text changed",
            "Reload before importing",
            "interface_text_revision_conflict",
        )
    try:
        values = validate_text_values(payload.values)
    except ProblemError as exc:
        session.add(
            audit_event(
                action="interface_text.validation_failed",
                actor_type="user",
                actor_id=principal.user.id,
                request=request,
                object_type="interface_text_draft",
                object_id="current",
                outcome="denied",
                details={"code": exc.code, "source": "import"},
            )
        )
        await session.commit()
        raise
    now = datetime.now(UTC)
    draft = await session.scalar(
        select(InterfaceTextDraft).where(InterfaceTextDraft.id == "current").with_for_update()
    )
    if draft is None:
        draft = InterfaceTextDraft(
            id="current",
            base_revision=current_number,
            revision=1,
            previewed_revision=None,
            values=values,
            edited_by=principal.user.id,
            reason=payload.reason,
            created_at=now,
            updated_at=now,
        )
        session.add(draft)
    else:
        draft.revision += 1
        draft.previewed_revision = None
        draft.values = values
        draft.edited_by = principal.user.id
        draft.reason = payload.reason
        draft.updated_at = now
    session.add(
        audit_event(
            action="interface_text.imported",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="interface_text_draft",
            object_id="current",
            details={"keys": sorted(values), "reason": payload.reason},
        )
    )
    await session.commit()
    return {
        "base_revision": draft.base_revision,
        "draft_revision": draft.revision,
        "previewed_revision": draft.previewed_revision,
        "values": draft.values,
    }
