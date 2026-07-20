#!/usr/bin/env python3
"""Generate file-backed production secrets without printing their values."""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import sys
from contextlib import suppress
from pathlib import Path
from urllib.parse import quote

SECRET_NAMES = (
    "postgres_password",
    "database_url",
    "app_master_key",
    "session_pepper",
    "admin_setup_token",
    "backup_encryption_key",
    "tls.crt",
    "tls.key",
)


def _inside_git_worktree(path: Path) -> bool:
    return any((candidate / ".git").exists() for candidate in (path, *path.parents))


def _write_exclusive(path: Path, payload: str) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        os.write(descriptor, payload.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    with suppress(OSError):
        path.chmod(0o600)


def generate_secret_files(
    output: Path, *, permit_worktree: bool = False
) -> tuple[Path, ...]:
    output = output.expanduser().resolve()
    if _inside_git_worktree(output) and not permit_worktree:
        raise ValueError("refusing to generate secrets inside a Git worktree")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output}")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    with suppress(OSError):
        output.chmod(0o700)

    postgres_password = secrets.token_urlsafe(48)
    values = {
        "postgres_password": postgres_password + "\n",
        "database_url": (
            "postgresql+asyncpg://power_monitor:"
            f"{quote(postgres_password, safe='')}@postgres:5432/power_monitor\n"
        ),
        "app_master_key": base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
        + "\n",
        "session_pepper": secrets.token_urlsafe(48) + "\n",
        "admin_setup_token": secrets.token_urlsafe(32) + "\n",
        "backup_encryption_key": secrets.token_urlsafe(48) + "\n",
        "tls.crt": "",
        "tls.key": "",
    }
    created: list[Path] = []
    try:
        for name in SECRET_NAMES:
            path = output / name
            _write_exclusive(path, values[name])
            created.append(path)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return tuple(created)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate high-entropy Power Monitor Docker secret files."
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="empty private output directory (normally outside the source checkout)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        created = generate_secret_files(args.output)
    except (OSError, ValueError) as exc:
        print(f"secret generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"created {len(created)} protected files in {args.output.resolve()}")
    print(
        "secret values were not printed; transfer the directory through a protected channel"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
