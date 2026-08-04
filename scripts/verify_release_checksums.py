"""Verify release hashes, source provenance, protocol, and migration evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path
from pathlib import PurePosixPath
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


def _parse_inventory(contents: str, *, label: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, line in enumerate(contents.splitlines(), start=1):
        if not line:
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or not SHA256_PATTERN.fullmatch(parts[0]):
            raise AssertionError(f"invalid {label} checksum line {line_number}")
        digest, name = parts
        if name in entries:
            raise AssertionError(f"duplicate {label} checksum entry: {name}")
        entries[name] = digest
    if not entries:
        raise AssertionError(f"{label} checksum inventory is empty")
    return entries


def _inventory(release: Path) -> dict[str, str]:
    return _parse_inventory(
        (release / "checksums.sha256").read_text(encoding="utf-8"),
        label="release",
    )


def _safe_archive_source_path(relative: str) -> PurePosixPath:
    path = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != relative
    ):
        raise AssertionError(f"unsafe archived source evidence path: {relative!r}")
    return path


def _archive_file_bytes(
    archive: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    name: str,
) -> bytes:
    member = members.get(name)
    if member is None or not member.isfile():
        raise AssertionError(f"missing regular archive member: {name}")
    handle = archive.extractfile(member)
    if handle is None:
        raise AssertionError(f"could not read archive member: {name}")
    with handle:
        return handle.read()


def _verify_embedded_archive(
    *,
    release: Path,
    archive_path: Path,
    entries: dict[str, str],
    metadata: dict[str, Any],
) -> None:
    version = metadata["version"]
    commit = metadata["git_commit"]
    archive_name = archive_path.name
    prefix = f"power-monitor-server-{version}/"
    release_prefix = f"{prefix}release/"

    try:
        archive_context = tarfile.open(archive_path, mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise AssertionError(f"invalid release archive: {exc}") from exc

    with archive_context as archive:
        member_list = archive.getmembers()
        names = [member.name for member in member_list]
        if len(names) != len(set(names)):
            raise AssertionError("release archive contains duplicate members")
        archive_root = prefix.removesuffix("/")
        if any(name != archive_root and not name.startswith(prefix) for name in names):
            raise AssertionError(
                "release archive contains a member outside its version prefix"
            )
        members = {member.name: member for member in member_list}

        archived_commit = archive.pax_headers.get("comment")
        if archived_commit != commit:
            raise AssertionError(
                "release archive source commit mismatch: "
                f"metadata={commit}, archive={archived_commit}"
            )

        embedded_versions = _archive_file_bytes(
            archive, members, f"{release_prefix}versions.json"
        )
        if embedded_versions != (release / "versions.json").read_bytes():
            raise AssertionError(
                "embedded versions.json does not match release provenance"
            )

        embedded_inventory_bytes = _archive_file_bytes(
            archive, members, f"{release_prefix}checksums.sha256"
        )
        try:
            embedded_inventory_text = embedded_inventory_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AssertionError("embedded checksum inventory is not UTF-8") from exc
        embedded_entries = _parse_inventory(
            embedded_inventory_text,
            label="embedded release",
        )
        if any(name.endswith((".tar.gz", ".zip")) for name in embedded_entries):
            raise AssertionError(
                "embedded checksum inventory references a release archive"
            )
        expected_embedded = {
            name: digest for name, digest in entries.items() if name != archive_name
        }
        if embedded_entries != expected_embedded:
            raise AssertionError(
                "embedded checksum inventory does not match the external release inventory"
            )

        expected_release_files = set(embedded_entries) | {"checksums.sha256"}
        archived_release_files = {
            member.name.removeprefix(release_prefix)
            for member in member_list
            if member.isfile() and member.name.startswith(release_prefix)
        }
        if archived_release_files != expected_release_files:
            raise AssertionError(
                "release archive contains unverified or missing release evidence files"
            )

        for name, expected_digest in embedded_entries.items():
            actual = hashlib.sha256(
                _archive_file_bytes(archive, members, f"{release_prefix}{name}")
            ).hexdigest()
            if actual != expected_digest:
                raise AssertionError(f"embedded release checksum mismatch: {name}")

        source_evidence: list[tuple[str, Any]] = [
            ("migration", metadata.get("migration", {}).get("source")),
            (
                "backend dependency lock",
                metadata.get("dependency_audits", {}).get("backend", {}).get("lock"),
            ),
            (
                "frontend dependency lock",
                metadata.get("dependency_audits", {}).get("frontend", {}).get("lock"),
            ),
        ]
        ota = metadata.get("ota_v2", {})
        ota_artifacts = ota.get("artifacts") if isinstance(ota, dict) else None
        if not isinstance(ota_artifacts, list):
            raise AssertionError("versions.json is missing OTA source evidence")
        source_evidence.extend(
            (f"OTA source artifact {index}", evidence)
            for index, evidence in enumerate(ota_artifacts, start=1)
        )
        for label, evidence in source_evidence:
            if not isinstance(evidence, dict):
                raise AssertionError(f"missing {label} evidence")
            relative = evidence.get("path")
            digest = evidence.get("sha256")
            if not isinstance(relative, str) or not isinstance(digest, str):
                raise AssertionError(f"invalid {label} evidence")
            source_path = _safe_archive_source_path(relative)
            actual = hashlib.sha256(
                _archive_file_bytes(
                    archive, members, f"{prefix}{source_path.as_posix()}"
                )
            ).hexdigest()
            if not SHA256_PATTERN.fullmatch(digest) or actual != digest:
                raise AssertionError(
                    f"archived {label} source hash mismatch: {relative}"
                )

        protocol = (
            _archive_file_bytes(
                archive, members, f"{prefix}shared/protocol-version.txt"
            )
            .decode("utf-8")
            .strip()
        )
        if protocol != PROTOCOL:
            raise AssertionError(f"archived protocol mismatch: {protocol!r}")


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
        archive_path = _safe_release_path(release, archive_name)
        _verify_embedded_archive(
            release=release,
            archive_path=archive_path,
            entries=entries,
            metadata=metadata,
        )
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
