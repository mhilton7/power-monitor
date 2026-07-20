from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import AuditEvent
from app.db.session import get_session
from app.problem import ProblemError
from app.security.browser import SessionPrincipal, authenticate_session, csrf_matches
from app.security.protocol import SecretCipher, VerifiedDevice, verify_device_request

DbSession = Annotated[AsyncSession, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


async def current_principal(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    token: Annotated[str | None, Cookie(alias="pm_session")] = None,
) -> SessionPrincipal:
    if not token:
        raise ProblemError(
            401, "Authentication required", "Sign in to continue", "not_authenticated"
        )
    principal = await authenticate_session(session, token, settings.session_pepper)
    if principal is None:
        raise ProblemError(401, "Session expired", "Sign in again", "session_expired")
    request.state.principal = principal
    return principal


Principal = Annotated[SessionPrincipal, Depends(current_principal)]


def require_roles(*allowed: str) -> Callable[..., Awaitable[SessionPrincipal]]:
    async def dependency(principal: Principal) -> SessionPrincipal:
        if not principal.roles.intersection(allowed):
            raise ProblemError(
                403, "Permission denied", "Your role cannot perform this action", "forbidden"
            )
        return principal

    return dependency


def require_permissions(
    *required: str, any_of: bool = False
) -> Callable[..., Awaitable[SessionPrincipal]]:
    async def dependency(principal: Principal) -> SessionPrincipal:
        matches = [permission in principal.permissions for permission in required]
        permitted = any(matches) if any_of else all(matches)
        if not permitted:
            raise ProblemError(
                403,
                "Permission denied",
                "Your account does not have the required permission",
                "forbidden",
                extra={"required_permissions": list(required)},
            )
        return principal

    return dependency


Admin = Annotated[SessionPrincipal, Depends(require_roles("admin"))]
Operator = Annotated[SessionPrincipal, Depends(require_roles("admin", "operator"))]
Viewer = Annotated[SessionPrincipal, Depends(require_permissions("overview.view"))]


async def require_csrf(
    principal: Principal,
    settings: AppSettings,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> SessionPrincipal:
    if not csrf_matches(principal.session, csrf_header, settings.session_pepper):
        raise ProblemError(403, "CSRF validation failed", "Refresh and retry", "csrf_failed")
    return principal


CsrfPrincipal = Annotated[SessionPrincipal, Depends(require_csrf)]


async def authenticated_device(
    request: Request, session: DbSession, settings: AppSettings
) -> VerifiedDevice:
    body = await request.body()
    headers = {key.lower(): value for key, value in request.headers.items()}
    query = request.url.query
    target = request.url.path + (f"?{query}" if query else "")
    return await verify_device_request(
        session=session,
        headers=headers,
        method=request.method,
        target=target,
        body=body,
        cipher=SecretCipher(settings.app_master_key),
        clock_window_seconds=settings.max_device_clock_skew_seconds,
    )


Verified = Annotated[VerifiedDevice, Depends(authenticated_device)]


def audit_event(
    *,
    action: str,
    actor_type: str,
    actor_id: str | None,
    request: Request | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    outcome: str = "success",
    details: dict[str, object] | None = None,
) -> AuditEvent:
    return AuditEvent(
        occurred_at=datetime.now(UTC),
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        source_ip=request.client.host if request and request.client else None,
        outcome=outcome,
        correlation_id=(getattr(request.state, "request_id", None) if request else None),
        details=details or {},
    )
