from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
DEFAULT_RELEASE_VERSION = "1.0.38"
DEFAULT_MIGRATION_REVISION = "20260806_0033"
CANONICAL_RELEASE_TEXT_SUFFIXES = frozenset(
    {".csv", ".json", ".md", ".sha256", ".sql", ".txt"}
)
SEMVER_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MIGRATION_PATTERN = re.compile(r"^[0-9]{8}_[0-9]{4}$")
OTA_V2_ARTIFACTS = (
    "shared/schemas/ota-manifest-v2.schema.json",
    "shared/schemas/ota-deployment-report-v2.schema.json",
    "shared/auth-test-vectors/ota-manifest-v2.json",
)
OTA_MIGRATION_REVISIONS = (
    "20260802_0026",
    "20260803_0027",
    "20260803_0028",
    "20260803_0030",
)
OFFLINE_COMPATIBILITY_REVISION = "20260731_0022"
OFFLINE_COMPATIBILITY_PARENT = "20260730_0021"
VIEWER_PERMISSIONS = (
    "alerts.view",
    "costs.view",
    "devices.view",
    "history.view",
    "overview.view",
    "rates.view",
    "sites.view",
    "status_indicators.view",
    "topology.view",
    "usage.view",
    "utility_accounts.view",
)


def runtime_packages() -> list[tuple[str, str]]:
    packages: dict[str, str] = {}
    for line in (
        (ROOT / "backend" / "requirements.lock")
        .read_text(encoding="utf-8")
        .splitlines()
    ):
        match = re.match(r"^([A-Za-z0-9_.-]+)==", line)
        if match:
            version = line.split("==", 1)[1].split()[0]
            packages[match.group(1)] = version
    return sorted(packages.items(), key=lambda item: item[0].lower())


def backend_licenses() -> None:
    with (RELEASE / "backend-licenses.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["package", "version", "license_expression", "home_page"])
        for name, locked_version in runtime_packages():
            try:
                metadata = importlib.metadata.metadata(name)
            except importlib.metadata.PackageNotFoundError:
                writer.writerow(
                    [name, locked_version, "NOT_INSTALLED_ON_BUILD_PLATFORM", ""]
                )
                continue
            license_value = (
                metadata.get("License-Expression")
                or metadata.get("License")
                or "UNKNOWN"
            )
            writer.writerow(
                [
                    name,
                    importlib.metadata.version(name),
                    license_value,
                    metadata.get("Home-page", ""),
                ]
            )


def frontend_licenses() -> None:
    lock = json.loads(
        (ROOT / "frontend-next" / "package-lock.json").read_text(encoding="utf-8")
    )
    with (RELEASE / "frontend-licenses.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["package", "version", "license", "resolved"])
        for path, package in sorted(lock.get("packages", {}).items()):
            if not path.startswith("node_modules/"):
                continue
            writer.writerow(
                [
                    path.removeprefix("node_modules/"),
                    package.get("version", ""),
                    package.get("license", "UNKNOWN"),
                    package.get("resolved", ""),
                ]
            )


def command_version(command: list[str]) -> str:
    try:
        return subprocess.check_output(  # noqa: S603 - fixed release metadata commands
            command, text=True, stderr=subprocess.STDOUT
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unavailable"


def _release_commit(expected: str | None = None) -> str:
    commit = command_version(["git", "rev-parse", "HEAD"])
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("could not resolve the exact source commit")
    if expected is not None and commit != expected.lower():
        raise ValueError(
            f"release commit {expected!r} does not match checked-out HEAD {commit}"
        )
    return commit


def _node_toolchain(node_bin: str, npm_bin: str) -> tuple[str, str]:
    node = command_version([node_bin, "--version"])
    npm = command_version([npm_bin, "--version"])
    if node != "v24.4.0":
        raise ValueError(
            "release evidence requires pinned Node.js v24.4.0; "
            f"{node_bin!r} returned {node!r}"
        )
    if npm != "11.4.2":
        raise ValueError(
            f"release evidence requires pinned npm 11.4.2; {npm_bin!r} returned {npm!r}"
        )
    return node, npm


def _git_blob_bytes(relative_path: str, release_commit: str) -> bytes:
    """Read committed source bytes without worktree/filter conversion.

    Release archives are built from the immutable Git tree.  On Windows a
    checkout can contain CRLF bytes even when the committed blob contains LF,
    so hashing ``Path.read_bytes()`` would bind evidence to the checkout rather
    than to the source actually placed in the archive.
    """

    result = subprocess.run(  # noqa: S603 - commit and repository path are validated
        ["git", "cat-file", "blob", f"{release_commit}:{relative_path}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError(
            f"required release artifact is not present at {release_commit}: "
            f"{relative_path}"
        )
    return result.stdout


def _artifact_evidence(
    relative_path: str, *, release_commit: str | None = None
) -> dict[str, str]:
    path = ROOT / relative_path
    if not path.is_file():
        raise FileNotFoundError(
            f"required release artifact is missing: {relative_path}"
        )
    contents = (
        path.read_bytes()
        if release_commit is None
        else _git_blob_bytes(relative_path, release_commit)
    )
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(contents).hexdigest(),
    }


def _release_artifact_evidence(filename: str) -> dict[str, str]:
    path = RELEASE / filename
    if not path.is_file():
        raise FileNotFoundError(f"required release artifact is missing: {path}")
    return {
        "path": f"release/{filename}",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def canonicalize_release_text_artifacts() -> int:
    """Make generated evidence byte-identical on Windows and Linux.

    Git stores these tracked text artifacts with LF line endings.  Dependency
    audit tools can emit CRLF on Windows, so evidence must be normalized before
    its SHA-256 is recorded.  Otherwise a clean Linux checkout contains valid
    data whose checksum differs from the one generated on the release host.
    """

    normalized = 0
    for path in sorted(RELEASE.iterdir()):
        if (
            not path.is_file()
            or path.suffix.lower() not in CANONICAL_RELEASE_TEXT_SUFFIXES
        ):
            continue
        contents = path.read_bytes()
        canonical = (
            contents.removeprefix(b"\xef\xbb\xbf")
            .replace(b"\r\n", b"\n")
            .replace(b"\r", b"\n")
        )
        if canonical != contents:
            path.write_bytes(canonical)
            normalized += 1
    return normalized


def _migration_evidence(
    revision: str, *, release_commit: str | None = None
) -> dict[str, str]:
    matches = sorted(
        (ROOT / "backend" / "alembic" / "versions").glob(f"{revision}_*.py")
    )
    if len(matches) != 1:
        raise ValueError(
            f"migration revision {revision} must resolve to exactly one migration file"
        )
    relative_path = matches[0].relative_to(ROOT).as_posix()
    return _artifact_evidence(relative_path, release_commit=release_commit)


def _validate_migration_head(revision: str) -> None:
    result = subprocess.run(  # noqa: S603 - fixed repository release command
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ROOT / "backend" / "alembic.ini"),
            "heads",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    heads = set(re.findall(r"^([^\s]+)\s+\(head\)$", result.stdout, re.MULTILINE))
    if heads != {revision}:
        raise ValueError(
            f"configured migration revision {revision} does not match Alembic heads "
            f"{sorted(heads)}"
        )


def validate_release_inputs(version: str, migration_revision: str) -> None:
    if not SEMVER_PATTERN.fullmatch(version):
        raise ValueError("release version must be strict X.Y.Z semantic versioning")
    if not MIGRATION_PATTERN.fullmatch(migration_revision):
        raise ValueError("migration revision must use YYYYMMDD_NNNN")
    for relative_path in OTA_V2_ARTIFACTS:
        _artifact_evidence(relative_path)
    for revision in OTA_MIGRATION_REVISIONS:
        _migration_evidence(revision)
    _migration_evidence(migration_revision)
    _validate_migration_head(migration_revision)


def versions(
    version: str,
    migration_revision: str,
    *,
    release_commit: str | None = None,
    node_bin: str = "node",
    npm_bin: str = "npm",
) -> None:
    git = _release_commit(release_commit)
    node, npm = _node_toolchain(node_bin, npm_bin)
    payload = {
        "product": "Power Monitor Server",
        "version": version,
        "protocol": (ROOT / "shared" / "protocol-version.txt").read_text().strip(),
        "migration_revision": migration_revision,
        "migration": {
            "source": _migration_evidence(migration_revision, release_commit=git),
            "offline_sql": _release_artifact_evidence("migration-offline.sql"),
        },
        "python": platform.python_version(),
        "node": node,
        "npm": npm,
        "toolchains": {
            "release_host": {
                "python": platform.python_version(),
                "node": node,
                "npm": npm,
            },
            "container_builders": {
                "backend": "python:3.13.5-slim-bookworm",
                "frontend": "node:24.4.0-alpine",
                "frontend_runtime": "nginxinc/nginx-unprivileged:1.29.0-alpine",
                "backup": "postgres:17.5-bookworm",
            },
        },
        "git_commit": git,
        "dependency_audits": {
            "backend": {
                "lock": _artifact_evidence(
                    "backend/requirements.lock", release_commit=git
                ),
                "report": _release_artifact_evidence("backend-audit.json"),
                "format": "pip-audit/json",
            },
            "frontend": {
                "lock": _artifact_evidence(
                    "frontend-next/package-lock.json", release_commit=git
                ),
                "report": _release_artifact_evidence("frontend-audit.json"),
                "format": "npm-audit/json",
            },
        },
        "images": {
            "api": f"power-monitor-api:{version}",
            "frontend": f"power-monitor-frontend:{version}",
            "backup": f"power-monitor-backup:{version}",
            "postgres": "docker.io/library/postgres:17.5-bookworm",
            "caddy": "docker.io/library/caddy:2.10.0-alpine",
        },
        "ota_v2": {
            "protocol": "pm-ota-manifest/2",
            "authentication": "existing_device_hmac",
            "artifacts": [
                *[
                    _artifact_evidence(path, release_commit=git)
                    for path in OTA_V2_ARTIFACTS
                ],
                *[
                    _migration_evidence(revision, release_commit=git)
                    for revision in OTA_MIGRATION_REVISIONS
                ],
            ],
        },
        "truenas": {
            "target": "TrueNAS Community Edition 25.10 Apps / Install via YAML",
            "template": "deploy/truenas/compose.yaml",
            "required_platform": "linux/amd64",
            "image_policy": "version tag plus sha256 digest",
        },
    }
    (RELEASE / "versions.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def _alembic_offline_sql(target: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed repository release command
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ROOT / "backend" / "alembic.ini"),
            "upgrade",
            target,
            "--sql",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return "\n".join(line.rstrip() for line in result.stdout.splitlines()) + "\n"


def _strict_viewer_permissions_offline_sql() -> str:
    """Render released revision 0022 without reading query results in offline mode.

    Revision 20260731_0022 calls ``.mappings().one()`` on an Alembic mock
    connection. That is correct during an online migration but cannot be rendered
    by Alembic's ``--sql`` mode. Keep the released migration immutable and emit
    its equivalent PostgreSQL statements between the two Alembic-generated
    segments instead.
    """

    role_revision_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        "power-monitor:migration:20260731_0022:role-revision",
    )
    audit_event_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        "power-monitor:migration:20260731_0022:audit-event",
    )
    permissions = json.dumps(VIEWER_PERMISSIONS, separators=(",", ":"))
    details = json.dumps(
        {
            "removed_permissions": ["history.export", "costs.export"],
            "sessions_revoked": True,
        },
        separators=(",", ":"),
    )
    return f"""BEGIN;

-- Running upgrade {OFFLINE_COMPATIBILITY_PARENT} -> {OFFLINE_COMPATIBILITY_REVISION}
-- Packaging compatibility rendering for the released data-dependent migration.

DELETE FROM role_permissions
WHERE role_name = 'viewer'
AND permission_code IN ('history.export', 'costs.export');

UPDATE roles SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP
WHERE name = 'viewer';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM roles WHERE name = 'viewer') THEN
        RAISE EXCEPTION 'built-in viewer role is missing';
    END IF;
END $$;

INSERT INTO role_revisions
(id, role_name, revision, display_name, description, permissions,
 created_by, created_at, reason)
SELECT '{role_revision_id}', 'viewer', revision, display_name, description,
       CAST('{permissions}' AS JSON), NULL, CURRENT_TIMESTAMP,
       'Built-in Viewer restricted to safe Home, History, and Billing reads'
FROM roles WHERE name = 'viewer';

UPDATE users SET access_revision = access_revision + 1
WHERE id IN (SELECT user_id FROM user_roles WHERE role_name = 'viewer');

UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP
WHERE revoked_at IS NULL
AND user_id IN (SELECT user_id FROM user_roles WHERE role_name = 'viewer');

INSERT INTO audit_events
(id, occurred_at, actor_type, actor_id, action, object_type, object_id,
 source_ip, outcome, correlation_id, details)
VALUES ('{audit_event_id}', CURRENT_TIMESTAMP, 'system', NULL,
        'role.builtin_migrated', 'role', 'viewer', NULL, 'success', NULL,
        CAST('{details}' AS JSON));

UPDATE alembic_version SET version_num='{OFFLINE_COMPATIBILITY_REVISION}'
WHERE alembic_version.version_num = '{OFFLINE_COMPATIBILITY_PARENT}';

COMMIT;
"""


def migration_offline_sql(migration_revision: str) -> None:
    segments = (
        _alembic_offline_sql(OFFLINE_COMPATIBILITY_PARENT),
        _strict_viewer_permissions_offline_sql(),
        _alembic_offline_sql(f"{OFFLINE_COMPATIBILITY_REVISION}:{migration_revision}"),
    )
    normalized = "\n".join(segment.rstrip() for segment in segments) + "\n"
    (RELEASE / "migration-offline.sql").write_text(
        normalized, encoding="utf-8", newline="\n"
    )


def checksums() -> None:
    lines = []
    for path in sorted(RELEASE.iterdir()):
        if (
            not path.is_file()
            or path.name == "checksums.sha256"
            or path.name.endswith((".tar.gz", ".zip"))
        ):
            continue
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (RELEASE / "checksums.sha256").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate versioned server release evidence"
    )
    parser.add_argument(
        "--version",
        default=os.environ.get("RELEASE_VERSION", DEFAULT_RELEASE_VERSION),
    )
    parser.add_argument(
        "--migration-revision",
        default=os.environ.get("MIGRATION_REVISION", DEFAULT_MIGRATION_REVISION),
    )
    parser.add_argument(
        "--release-commit",
        default=os.environ.get("RELEASE_COMMIT"),
        help="exact clean source commit represented by this evidence",
    )
    parser.add_argument(
        "--node-bin",
        default=os.environ.get("NODE_BIN", "node"),
    )
    parser.add_argument(
        "--npm-bin",
        default=os.environ.get("NPM_BIN", "npm.cmd" if os.name == "nt" else "npm"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    validate_release_inputs(args.version, args.migration_revision)
    RELEASE.mkdir(exist_ok=True)
    backend_licenses()
    frontend_licenses()
    migration_offline_sql(args.migration_revision)
    canonicalize_release_text_artifacts()
    versions(
        args.version,
        args.migration_revision,
        release_commit=args.release_commit,
        node_bin=args.node_bin,
        npm_bin=args.npm_bin,
    )
    checksums()
