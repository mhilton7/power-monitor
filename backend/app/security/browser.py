from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import effective_permissions, explicit_site_ids, user_role_names
from app.db.models import BrowserSession, User

PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_is_strong(password: str) -> bool:
    character_classes = sum(
        (
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        )
    )
    return len(password) >= 14 and character_classes >= 3


def opaque_hash(value: str, pepper: str) -> str:
    if not pepper:
        raise RuntimeError("SESSION_PEPPER is required")
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class NewSession:
    row: BrowserSession
    token: str
    csrf_token: str


def create_session(
    *,
    user_id: str,
    pepper: str,
    duration_hours: int,
    source_ip: str | None,
    user_agent: str | None,
) -> NewSession:
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    row = BrowserSession(
        user_id=user_id,
        token_hash=opaque_hash(token, pepper),
        csrf_hash=opaque_hash(csrf, pepper),
        created_at=now,
        expires_at=now + timedelta(hours=duration_hours),
        last_seen_at=now,
        source_ip=source_ip,
        user_agent=(user_agent or "")[:512],
    )
    return NewSession(row=row, token=token, csrf_token=csrf)


@dataclass(frozen=True)
class SessionPrincipal:
    user: User
    session: BrowserSession
    roles: frozenset[str]
    permissions: frozenset[str]
    all_sites: bool
    site_ids: frozenset[str]

    def has_permission(self, code: str) -> bool:
        return code in self.permissions

    def can_access_site(self, site_id: str) -> bool:
        return self.all_sites or site_id in self.site_ids


async def authenticate_session(
    session: AsyncSession, token: str, pepper: str
) -> SessionPrincipal | None:
    now = datetime.now(UTC)
    token_hash = opaque_hash(token, pepper)
    browser_session = await session.scalar(
        select(BrowserSession).where(BrowserSession.token_hash == token_hash)
    )
    expires_at = browser_session.expires_at if browser_session is not None else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        browser_session is None
        or browser_session.revoked_at is not None
        or expires_at is None
        or expires_at <= now
    ):
        return None
    user = await session.get(User, browser_session.user_id)
    if user is None or not user.is_active:
        return None
    roles = await user_role_names(session, user.id)
    permissions = await effective_permissions(session, user.id)
    site_ids = await explicit_site_ids(session, user.id)
    browser_session.last_seen_at = now
    return SessionPrincipal(
        user=user,
        session=browser_session,
        roles=roles,
        permissions=permissions,
        all_sites=user.all_sites,
        site_ids=site_ids,
    )


def csrf_matches(session: BrowserSession, supplied: str | None, pepper: str) -> bool:
    if not supplied:
        return False
    return hmac.compare_digest(session.csrf_hash, opaque_hash(supplied, pepper))
