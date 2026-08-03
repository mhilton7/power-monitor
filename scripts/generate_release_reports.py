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
DEFAULT_RELEASE_VERSION = "1.0.28"
DEFAULT_MIGRATION_REVISION = "20260802_0026"
SEMVER_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MIGRATION_PATTERN = re.compile(r"^[0-9]{8}_[0-9]{4}$")
OTA_V2_ARTIFACTS = (
    "shared/schemas/ota-manifest-v2.schema.json",
    "shared/schemas/ota-deployment-report-v2.schema.json",
    "shared/auth-test-vectors/ota-manifest-v2.json",
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


def _artifact_evidence(relative_path: str) -> dict[str, str]:
    path = ROOT / relative_path
    if not path.is_file():
        raise FileNotFoundError(
            f"required release artifact is missing: {relative_path}"
        )
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _release_artifact_evidence(filename: str) -> dict[str, str]:
    path = RELEASE / filename
    if not path.is_file():
        raise FileNotFoundError(f"required release artifact is missing: {path}")
    return {
        "path": f"release/{filename}",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _migration_evidence(revision: str) -> dict[str, str]:
    matches = sorted(
        (ROOT / "backend" / "alembic" / "versions").glob(f"{revision}_*.py")
    )
    if len(matches) != 1:
        raise ValueError(
            f"migration revision {revision} must resolve to exactly one migration file"
        )
    relative_path = matches[0].relative_to(ROOT).as_posix()
    return _artifact_evidence(relative_path)


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
    _migration_evidence(migration_revision)
    _validate_migration_head(migration_revision)


def versions(version: str, migration_revision: str) -> None:
    git = command_version(["git", "rev-parse", "HEAD"])
    payload = {
        "product": "Power Monitor Server",
        "version": version,
        "protocol": (ROOT / "shared" / "protocol-version.txt").read_text().strip(),
        "migration_revision": migration_revision,
        "migration": {
            "source": _migration_evidence(migration_revision),
            "offline_sql": _release_artifact_evidence("migration-offline.sql"),
        },
        "python": platform.python_version(),
        "node": command_version(["node", "--version"]),
        "npm": command_version(["npm.cmd" if os.name == "nt" else "npm", "--version"]),
        "git_commit": git,
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
                *[_artifact_evidence(path) for path in OTA_V2_ARTIFACTS],
                _migration_evidence(migration_revision),
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
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
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
    (RELEASE / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    validate_release_inputs(args.version, args.migration_revision)
    RELEASE.mkdir(exist_ok=True)
    backend_licenses()
    frontend_licenses()
    migration_offline_sql(args.migration_revision)
    versions(args.version, args.migration_revision)
    checksums()
