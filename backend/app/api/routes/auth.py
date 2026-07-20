from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque
from datetime import UTC, datetime

import pyotp
from fastapi import APIRouter, Cookie, Request, Response
from sqlalchemy import func, select

from app.api.deps import AppSettings, CsrfPrincipal, DbSession, audit_event
from app.db.models import BrowserSession, TotpCredential, User, UserRole
from app.problem import ProblemError
from app.schemas import BootstrapRequest, LoginRequest, SessionView, UserSummary
from app.security.browser import (
    authenticate_session,
    create_session,
    hash_password,
    password_is_strong,
    verify_password,
)
from app.security.protocol import SecretCipher
from app.services.bootstrap import ensure_default_reference_data, ensure_roles

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
_attempts: dict[str, deque[float]] = defaultdict(deque)


def _session_view(
    user: User, roles: list[str], browser_session: BrowserSession, csrf: str | None
) -> SessionView:
    return SessionView(
        authenticated=True,
        user=UserSummary(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            roles=roles,
        ),
        expires_at=browser_session.expires_at,
        csrf_token=csrf,
    )


def _set_session_cookies(
    response: Response, settings: AppSettings, token: str, csrf: str, expires_at: datetime
) -> None:
    max_age = max(1, int((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=max_age,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf,
        max_age=max_age,
        secure=settings.cookie_secure,
        httponly=False,
        samesite="strict",
        path="/",
    )


@router.post("/bootstrap", response_model=SessionView, status_code=201)
async def bootstrap(
    payload: BootstrapRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> SessionView:
    user_count = await session.scalar(select(func.count()).select_from(User))
    if user_count:
        raise ProblemError(
            409, "Already initialized", "Bootstrap is permanently closed", "bootstrap_closed"
        )
    if not settings.bootstrap_secret or not hmac.compare_digest(
        payload.bootstrap_secret, settings.bootstrap_secret
    ):
        session.add(
            audit_event(
                action="auth.bootstrap_failed",
                actor_type="anonymous",
                actor_id=None,
                request=request,
                outcome="denied",
            )
        )
        await session.commit()
        raise ProblemError(
            403, "Bootstrap denied", "The bootstrap secret is invalid", "bootstrap_denied"
        )
    if not password_is_strong(payload.password):
        raise ProblemError(
            422,
            "Weak password",
            "Use at least 14 characters and three character classes",
            "weak_password",
        )
    await ensure_roles(session)
    await ensure_default_reference_data(session, settings.default_site_name, settings)
    user = User(
        email=str(payload.email).lower(),
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        password_changed_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    session.add(UserRole(user_id=user.id, role_name="admin"))
    created = create_session(
        user_id=user.id,
        pepper=settings.session_pepper,
        duration_hours=settings.session_hours,
        source_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    session.add(created.row)
    session.add(
        audit_event(
            action="auth.bootstrap_completed",
            actor_type="user",
            actor_id=user.id,
            request=request,
            object_type="user",
            object_id=user.id,
        )
    )
    await session.commit()
    _set_session_cookies(
        response, settings, created.token, created.csrf_token, created.row.expires_at
    )
    return _session_view(user, ["admin"], created.row, created.csrf_token)


@router.post("/login", response_model=SessionView)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> SessionView:
    source = request.client.host if request.client else "unknown"
    key = f"{source}:{str(payload.email).lower()}"
    now_mono = time.monotonic()
    attempts = _attempts[key]
    while attempts and attempts[0] < now_mono - 300:
        attempts.popleft()
    if len(attempts) >= 8:
        raise ProblemError(
            429, "Too many attempts", "Try again after five minutes", "login_throttled"
        )
    user = await session.scalar(select(User).where(User.email == str(payload.email).lower()))
    if (
        user is None
        or not user.is_active
        or not verify_password(user.password_hash, payload.password)
    ):
        attempts.append(now_mono)
        session.add(
            audit_event(
                action="auth.login_failed",
                actor_type="anonymous",
                actor_id=None,
                request=request,
                outcome="denied",
                details={"email_fingerprint": str(payload.email).lower()[:3]},
            )
        )
        await session.commit()
        raise ProblemError(
            401, "Sign in failed", "Email or password is incorrect", "invalid_credentials"
        )
    totp = await session.get(TotpCredential, user.id)
    if totp is not None and totp.confirmed:
        if payload.totp_code is None:
            raise ProblemError(401, "TOTP required", "Enter your six-digit code", "totp_required")
        secret = SecretCipher(settings.app_master_key).decrypt(totp.encrypted_secret).decode()
        verifier = pyotp.TOTP(secret)
        if not verifier.verify(payload.totp_code, valid_window=1):
            attempts.append(now_mono)
            raise ProblemError(401, "Sign in failed", "TOTP code is invalid", "invalid_totp")
    attempts.clear()
    roles = list(
        await session.scalars(select(UserRole.role_name).where(UserRole.user_id == user.id))
    )
    created = create_session(
        user_id=user.id,
        pepper=settings.session_pepper,
        duration_hours=settings.session_hours,
        source_ip=source,
        user_agent=request.headers.get("user-agent"),
    )
    session.add(created.row)
    session.add(
        audit_event(
            action="auth.login",
            actor_type="user",
            actor_id=user.id,
            request=request,
            object_type="session",
            object_id=created.row.id,
        )
    )
    await session.commit()
    _set_session_cookies(
        response, settings, created.token, created.csrf_token, created.row.expires_at
    )
    return _session_view(user, roles, created.row, created.csrf_token)


@router.get("/session", response_model=SessionView)
async def session_status(
    session: DbSession,
    settings: AppSettings,
    token: str | None = Cookie(default=None, alias="pm_session"),
) -> SessionView:
    if token:
        principal = await authenticate_session(session, token, settings.session_pepper)
        if principal:
            return _session_view(
                principal.user, sorted(principal.roles), principal.session, csrf=None
            )
    count = await session.scalar(select(func.count()).select_from(User))
    return SessionView(authenticated=False, bootstrap_required=not bool(count))


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> Response:
    principal.session.revoked_at = datetime.now(UTC)
    session.add(
        audit_event(
            action="auth.logout",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="session",
            object_id=principal.session.id,
        )
    )
    await session.commit()
    response.delete_cookie(settings.cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    response.status_code = 204
    return response


@router.post("/totp/setup")
async def setup_totp(
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, str]:
    secret = pyotp.random_base32()
    protected = SecretCipher(settings.app_master_key).encrypt(secret.encode())
    existing = await session.get(TotpCredential, principal.user.id)
    if existing is None:
        session.add(TotpCredential(user_id=principal.user.id, encrypted_secret=protected))
    else:
        existing.encrypted_secret = protected
        existing.confirmed = False
    session.add(
        audit_event(
            action="auth.totp_setup_started",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
        )
    )
    await session.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(
        name=principal.user.email, issuer_name="Power Monitor Server"
    )
    return {"secret": secret, "provisioning_uri": uri}


@router.post("/totp/verify")
async def verify_totp_setup(
    code: str,
    principal: CsrfPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, bool]:
    credential = await session.get(TotpCredential, principal.user.id)
    if credential is None:
        raise ProblemError(409, "TOTP not started", "Start TOTP setup first", "totp_not_started")
    secret = SecretCipher(settings.app_master_key).decrypt(credential.encrypted_secret).decode()
    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        raise ProblemError(
            422, "Invalid code", "The verification code is not valid", "invalid_totp"
        )
    credential.confirmed = True
    await session.commit()
    return {"enabled": True}
