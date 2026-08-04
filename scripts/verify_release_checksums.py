"""Verify release hashes, source provenance, protocol, and migration evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from packaging.markers import InvalidMarker, Marker

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
PROTOCOL = "pm-protocol/1.0.0"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REQUIREMENT_PATTERN = re.compile(
    r"^([A-Za-z0-9_.-]+)==([^ ;\\]+)(?:\s*;\s*(.*?))?\s*\\?$"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_release_path(release: Path, name: str) -> Path:
    if not name or Path(name).name != name:
        raise AssertionError(f"unsafe release artifact name: {name!r}")
    path = (release / name).resolve()
    if path.parent != release.resolve() or not path.is_file():
        raise AssertionError(f"unsafe or missing release artifact: {name}")
    return path


def _inventory(release: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    inventory_path = release / "checksums.sha256"
    for line_number, line in enumerate(
        inventory_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line:
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or not SHA256_PATTERN.fullmatch(parts[0]):
            raise AssertionError(f"invalid checksum line {line_number}")
        digest, name = parts
        if name in entries:
            raise AssertionError(f"duplicate checksum entry: {name}")
        entries[name] = digest
    if not entries:
        raise AssertionError("release checksum inventory is empty")
    return entries


def _alembic_heads(root: Path) -> set[str]:
    result = subprocess.run(  # noqa: S603 - fixed repository validation command
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(root / "backend" / "alembic.ini"),
            "heads",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(re.findall(r"^([^\s]+)\s+\(head\)$", result.stdout, re.MULTILINE))


def _source_evidence(root: Path, evidence: Any, label: str) -> None:
    if not isinstance(evidence, dict):
        raise AssertionError(f"missing {label} evidence")
    relative = evidence.get("path")
    digest = evidence.get("sha256")
    if not isinstance(relative, str) or not isinstance(digest, str):
        raise AssertionError(f"invalid {label} evidence")
    path = (root / relative).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        raise AssertionError(f"unsafe or missing {label} source: {relative}")
    if not SHA256_PATTERN.fullmatch(digest) or _sha256(path) != digest:
        raise AssertionError(f"{label} source hash mismatch: {relative}")


def _release_evidence(release: Path, evidence: Any, label: str) -> Path:
    if not isinstance(evidence, dict):
        raise AssertionError(f"missing {label} evidence")
    relative = evidence.get("path")
    digest = evidence.get("sha256")
    if not isinstance(relative, str) or not isinstance(digest, str):
        raise AssertionError(f"invalid {label} evidence")
    path = _safe_release_path(release, Path(relative).name)
    if relative != f"release/{path.name}":
        raise AssertionError(f"unsafe {label} release path: {relative}")
    if not SHA256_PATTERN.fullmatch(digest) or _sha256(path) != digest:
        raise AssertionError(f"{label} report hash mismatch: {relative}")
    return path


def _normalized_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _applicable_locked_requirements(path: Path) -> dict[str, str]:
    requirements: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = REQUIREMENT_PATTERN.fullmatch(raw_line.strip())
        if match is None:
            continue
        name, version, marker_text = match.groups()
        if marker_text:
            try:
                if not Marker(marker_text.rstrip(" \\")).evaluate():
                    continue
            except InvalidMarker as exc:
                raise AssertionError(
                    f"invalid requirement environment marker for {name}"
                ) from exc
        normalized = _normalized_package(name)
        if normalized in requirements:
            raise AssertionError(f"duplicate locked requirement: {name}")
        requirements[normalized] = version
    if not requirements:
        raise AssertionError("backend requirements lock has no pinned packages")
    return requirements


def _verify_dependency_audits(
    root: Path, release: Path, metadata: dict[str, Any]
) -> None:
    audits = metadata.get("dependency_audits")
    if not isinstance(audits, dict) or set(audits) != {"backend", "frontend"}:
        raise AssertionError("versions.json is missing exact dependency audit evidence")

    backend = audits["backend"]
    frontend = audits["frontend"]
    if not isinstance(backend, dict) or backend.get("format") != "pip-audit/json":
        raise AssertionError("invalid backend dependency audit evidence")
    if not isinstance(frontend, dict) or frontend.get("format") != "npm-audit/json":
        raise AssertionError("invalid frontend dependency audit evidence")

    _source_evidence(root, backend.get("lock"), "backend dependency lock")
    _source_evidence(root, frontend.get("lock"), "frontend dependency lock")
    backend_report = _release_evidence(
        release, backend.get("report"), "backend dependency audit"
    )
    frontend_report = _release_evidence(
        release, frontend.get("report"), "frontend dependency audit"
    )

    try:
        backend_payload = json.loads(backend_report.read_text(encoding="utf-8"))
        frontend_payload = json.loads(frontend_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"invalid dependency audit JSON: {exc}") from exc

    dependencies = backend_payload.get("dependencies")
    if not isinstance(dependencies, list):
        raise AssertionError("backend dependency audit has no dependency list")
    audited: dict[str, str] = {}
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise AssertionError("backend dependency audit entry is invalid")
        name = dependency.get("name")
        version = dependency.get("version")
        vulnerabilities = dependency.get("vulns")
        if not isinstance(name, str) or not isinstance(version, str):
            raise AssertionError("backend dependency audit identity is invalid")
        if vulnerabilities != []:
            raise AssertionError(f"backend dependency has vulnerabilities: {name}")
        normalized = _normalized_package(name)
        if normalized in audited:
            raise AssertionError(f"duplicate backend dependency audit entry: {name}")
        audited[normalized] = version
    expected = _applicable_locked_requirements(root / "backend/requirements.lock")
    if audited != expected:
        raise AssertionError(
            "backend dependency audit does not match the applicable locked packages"
        )

    vulnerabilities = frontend_payload.get("vulnerabilities")
    metadata_block = frontend_payload.get("metadata")
    vulnerability_totals = (
        metadata_block.get("vulnerabilities")
        if isinstance(metadata_block, dict)
        else None
    )
    dependency_totals = (
        metadata_block.get("dependencies") if isinstance(metadata_block, dict) else None
    )
    if (
        frontend_payload.get("auditReportVersion") != 2
        or vulnerabilities != {}
        or not isinstance(vulnerability_totals, dict)
        or not isinstance(dependency_totals, dict)
    ):
        raise AssertionError("frontend dependency audit is incomplete")
    severities = ("info", "low", "moderate", "high", "critical", "total")
    if any(vulnerability_totals.get(severity) != 0 for severity in severities):
        raise AssertionError("frontend dependency audit reports vulnerabilities")
    try:
        package_lock = json.loads(
            (root / "frontend-next/package-lock.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"invalid frontend dependency lock JSON: {exc}") from exc
    packages = package_lock.get("packages")
    if not isinstance(packages, dict) or "" not in packages:
        raise AssertionError("frontend dependency lock has no root package")
    expected_dependency_total = len(packages) - 1
    if dependency_totals.get("total") != expected_dependency_total:
        raise AssertionError(
            "frontend dependency audit does not match the locked package count"
        )


def verify_release(
    *,
    root: Path = ROOT,
    release: Path = RELEASE,
    expected_version: str | None = None,
    expected_migration: str | None = None,
    expected_commit: str | None = None,
    require_archive: bool = False,
) -> int:
    entries = _inventory(release)
    checked = 0
    deferred_archives: set[str] = set()
    for name, expected in entries.items():
        candidate = release / name
        if (
            not candidate.is_file()
            and not require_archive
            and name.endswith((".tar.gz", ".zip"))
        ):
            # Source archives are intentionally Git-ignored. Their committed
            # digest remains part of the evidence commit and is verified by the
            # local release gate before that commit is created.
            deferred_archives.add(name)
            continue
        actual = _sha256(_safe_release_path(release, name))
        if actual != expected:
            raise AssertionError(f"checksum mismatch: {name}")
        checked += 1

    metadata_path = _safe_release_path(release, "versions.json")
    if "versions.json" not in entries:
        raise AssertionError("versions.json is missing from checksums.sha256")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    version = metadata.get("version")
    migration = metadata.get("migration_revision")
    commit = metadata.get("git_commit")
    protocol = metadata.get("protocol")
    if expected_version is not None and version != expected_version:
        raise AssertionError(
            f"release version mismatch: expected {expected_version}, found {version}"
        )
    if expected_migration is not None and migration != expected_migration:
        raise AssertionError(
            f"migration mismatch: expected {expected_migration}, found {migration}"
        )
    if expected_commit is not None and commit != expected_commit.lower():
        raise AssertionError(
            f"source commit mismatch: expected {expected_commit}, found {commit}"
        )
    if not isinstance(commit, str) or not COMMIT_PATTERN.fullmatch(commit):
        raise AssertionError("versions.json contains an invalid source commit")
    repository_protocol = (
        (root / "shared" / "protocol-version.txt").read_text(encoding="utf-8").strip()
    )
    if protocol != PROTOCOL or repository_protocol != PROTOCOL:
        raise AssertionError(
            f"protocol mismatch: metadata={protocol!r} repository={repository_protocol!r}"
        )
    if not isinstance(migration, str) or _alembic_heads(root) != {migration}:
        raise AssertionError(
            f"versions.json migration {migration!r} is not the single Alembic head"
        )
    migration_evidence = metadata.get("migration")
    if not isinstance(migration_evidence, dict):
        raise AssertionError("versions.json is missing migration evidence")
    _source_evidence(root, migration_evidence.get("source"), "migration")
    source_path = str(migration_evidence["source"]["path"])
    if not Path(source_path).name.startswith(f"{migration}_"):
        raise AssertionError("migration evidence does not identify the configured head")
    offline_evidence = migration_evidence.get("offline_sql")
    if not isinstance(offline_evidence, dict):
        raise AssertionError("versions.json is missing offline SQL evidence")
    offline_name = Path(str(offline_evidence.get("path", ""))).name
    offline_path = _safe_release_path(release, offline_name)
    if _sha256(offline_path) != offline_evidence.get("sha256"):
        raise AssertionError("offline migration SQL evidence hash mismatch")

    _verify_dependency_audits(root, release, metadata)

    if require_archive:
        archive_name = f"power-monitor-server-{version}.tar.gz"
        if archive_name not in entries:
            raise AssertionError(f"release archive checksum is missing: {archive_name}")
        _safe_release_path(release, archive_name)
    elif deferred_archives and deferred_archives != {
        f"power-monitor-server-{version}.tar.gz"
    }:
        raise AssertionError(
            f"unexpected missing release archives: {sorted(deferred_archives)}"
        )
    return checked


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-migration")
    parser.add_argument("--expected-commit")
    parser.add_argument("--require-archive", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    checked = verify_release(
        expected_version=args.expected_version,
        expected_migration=args.expected_migration,
        expected_commit=args.expected_commit,
        require_archive=args.require_archive,
    )
    print(f"Verified {checked} release artifacts and release provenance")


if __name__ == "__main__":
    main()
