"""Fail-closed release source and toolchain checks.

The release scripts deliberately run this before any build or generated evidence.
That makes the commit recorded in images and evidence the same clean commit that
was tested. After evidence generation, only files under ``release/`` may differ.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

try:
    from scripts.generate_release_reports import (
        _node_toolchain,
        validate_release_inputs,
    )
except ModuleNotFoundError:  # direct ``python scripts/release_guard.py`` execution
    from generate_release_reports import _node_toolchain, validate_release_inputs

ROOT = Path(__file__).resolve().parents[1]
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _git(*args: str) -> str:
    return subprocess.check_output(  # noqa: S603 - fixed git release checks
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def head_commit() -> str:
    commit = _git("rev-parse", "HEAD").lower()
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ValueError("could not resolve a full Git commit for HEAD")
    return commit


def assert_expected_commit(expected: str) -> None:
    if not COMMIT_PATTERN.fullmatch(expected.lower()):
        raise ValueError("release commit must be a full 40-character SHA-1")
    actual = head_commit()
    if actual != expected.lower():
        raise ValueError(
            f"release commit {expected} does not match checked-out HEAD {actual}"
        )


def changed_paths() -> set[str]:
    commands = (
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    )
    paths: set[str] = set()
    for command in commands:
        paths.update(line for line in _git(*command).splitlines() if line)
    return paths


def assert_clean_source() -> None:
    changes = sorted(changed_paths())
    if changes:
        preview = "\n  ".join(changes[:20])
        suffix = "\n  ..." if len(changes) > 20 else ""
        raise ValueError(
            "release source must be a clean committed tree; found:\n  "
            f"{preview}{suffix}"
        )


def assert_only_release_outputs() -> None:
    unexpected = sorted(
        path for path in changed_paths() if not path.startswith("release/")
    )
    if unexpected:
        preview = "\n  ".join(unexpected[:20])
        suffix = "\n  ..." if len(unexpected) > 20 else ""
        raise ValueError(
            "tests or builds changed source outside release/; refusing to package:\n  "
            f"{preview}{suffix}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--release-commit", required=True)
    preflight.add_argument("--version", required=True)
    preflight.add_argument("--migration-revision", required=True)
    preflight.add_argument("--node-bin", required=True)
    preflight.add_argument("--npm-bin", required=True)
    post = subparsers.add_parser("post-generation")
    post.add_argument("--release-commit", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        assert_expected_commit(args.release_commit)
        if args.command == "preflight":
            assert_clean_source()
            validate_release_inputs(args.version, args.migration_revision)
            _node_toolchain(args.node_bin, args.npm_bin)
            protocol = (
                (ROOT / "shared" / "protocol-version.txt")
                .read_text(encoding="utf-8")
                .strip()
            )
            if protocol != "pm-protocol/1.0.0":
                raise ValueError(f"unexpected protocol identifier: {protocol!r}")
        else:
            assert_only_release_outputs()
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"release guard failed: {exc}", file=sys.stderr)
        return 1
    print(f"release guard passed: {args.command} commit={args.release_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
