#!/usr/bin/env python3
"""Recover an existing human account from a trusted server console.

This tool is intentionally not exposed through HTTP. Run it only inside the API
workload with direct TrueNAS administrator access.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import AuditEvent, BrowserSession, Role, User, UserRole, UserSite
from app.security.browser import hash_password, password_is_strong


def _database_url(path: Path | None) -> str:
    if path is None:
        return get_settings().database_url
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"database URL file is not readable: {path}") from exc
    if len(payload) > 65_536:
        raise ValueError("database URL file exceeds 64 KiB")
    try:
        value = payload.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError as exc:
        raise ValueError("database URL file is not UTF-8") from exc
    if not value or any(character in value for character in ("\n", "\r", "\x00")):
        raise ValueError("database URL file must contain exactly one non-empty line")
    return value


def _new_password() -> str:
    first = getpass.getpass("New administrator password (input hidden): ")
    second = getpass.getpass("Confirm administrator password (input hidden): ")
    if first != second:
        raise ValueError("password confirmation does not match")
    if not password_is_strong(first):
        raise ValueError("password must have 14 characters and three character classes")
    return first


async def recover(
    *, database_url: str, email: str, password: str | None, dry_run: bool
) -> tuple[str, int, list[str]]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            user = await session.scalar(
                select(User).where(func.lower(User.email) == email.lower())
            )
            if user is None:
                raise ValueError("no existing user matches that email address")
            if await session.get(Role, "admin") is None:
                raise ValueError(
                    "Administrator role is missing; apply migrations before recovery"
                )
            prior_roles = list(
                await session.scalars(
                    select(UserRole.role_name).where(UserRole.user_id == user.id)
                )
            )
            active_sessions = int(
                await session.scalar(
                    select(func.count())
                    .select_from(BrowserSession)
                    .where(
                        BrowserSession.user_id == user.id,
                        BrowserSession.revoked_at.is_(None),
                    )
                )
                or 0
            )
            if dry_run:
                return user.id, active_sessions, sorted(prior_roles)

            now = datetime.now(UTC)
            await session.execute(delete(UserRole).where(UserRole.user_id == user.id))
            session.add(UserRole(user_id=user.id, role_name="admin"))
            await session.execute(delete(UserSite).where(UserSite.user_id == user.id))
            user.is_active = True
            user.all_sites = True
            user.access_revision += 1
            if password is not None:
                user.password_hash = hash_password(password)
                user.password_changed_at = now
            sessions = list(
                await session.scalars(
                    select(BrowserSession).where(
                        BrowserSession.user_id == user.id,
                        BrowserSession.revoked_at.is_(None),
                    )
                )
            )
            for browser_session in sessions:
                browser_session.revoked_at = now
            session.add(
                AuditEvent(
                    occurred_at=now,
                    actor_type="system",
                    actor_id=None,
                    action="user.emergency_admin_recovered",
                    object_type="user",
                    object_id=user.id,
                    source_ip="local-console",
                    outcome="success",
                    correlation_id=f"emergency-recovery-{now.strftime('%Y%m%dT%H%M%SZ')}",
                    details={
                        "prior_roles": sorted(prior_roles),
                        "new_roles": ["admin"],
                        "all_sites": True,
                        "sessions_revoked": len(sessions),
                        "password_reset": password is not None,
                        "recovery_channel": "direct_server_console",
                    },
                )
            )
            await session.commit()
            return user.id, len(sessions), sorted(prior_roles)
    finally:
        await engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Promote and enable an existing account through the trusted server console."
    )
    parser.add_argument("--email", required=True, help="existing local account email")
    parser.add_argument(
        "--confirm",
        required=True,
        help="must exactly match --email to prevent accidental recovery",
    )
    parser.add_argument(
        "--database-url-file",
        type=Path,
        help="file containing the database URL; defaults to application *_FILE settings",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="prompt privately for a replacement password",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="verify the target and report impact without making changes",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    email = args.email.strip().lower()
    if not email or args.confirm.strip().lower() != email:
        print("recovery failed: --confirm must exactly match --email", file=sys.stderr)
        return 2
    try:
        password = _new_password() if args.reset_password else None
        user_id, sessions, prior_roles = asyncio.run(
            recover(
                database_url=_database_url(args.database_url_file),
                email=email,
                password=password,
                dry_run=args.dry_run,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"recovery failed: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(
            f"dry run: user {user_id} has roles {prior_roles}; "
            f"{sessions} active session(s) would be revoked"
        )
    else:
        print(
            f"recovered user {user_id} as an active all-site administrator; "
            f"revoked {sessions} session(s)"
        )
        print("sign in again and review the immutable audit event immediately")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
