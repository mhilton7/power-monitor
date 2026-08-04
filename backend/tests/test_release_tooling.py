from __future__ import annotations

import hashlib
import importlib.util
import json
import uuid
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_script(filename: str, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "scripts" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_defaults_track_head_and_ota_evidence_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = _load_script("generate_release_reports.py", "release_reports_test")
    assert reports.DEFAULT_RELEASE_VERSION == "1.0.30"
    assert reports.DEFAULT_MIGRATION_REVISION == "20260803_0030"
    assert reports.OTA_MIGRATION_REVISIONS == (
        "20260802_0026",
        "20260803_0027",
        "20260803_0028",
        "20260803_0030",
    )
    release = tmp_path / "release"
    release.mkdir()
    (release / "migration-offline.sql").write_text("-- migration\n", encoding="utf-8")
    (release / "backend-audit.json").write_text(
        '{"dependencies": [], "fixes": []}\n', encoding="utf-8"
    )
    (release / "frontend-audit.json").write_text(
        '{"auditReportVersion": 2, "vulnerabilities": {}, "metadata": {'
        '"vulnerabilities": {"info": 0, "low": 0, "moderate": 0, '
        '"high": 0, "critical": 0, "total": 0}, '
        '"dependencies": {"total": 0}}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(reports, "RELEASE", release)
    monkeypatch.setattr(reports, "_release_commit", lambda expected=None: "a" * 40)
    monkeypatch.setattr(reports, "_node_toolchain", lambda *_: ("v24.4.0", "11.4.2"))

    reports.versions(
        "9.8.7",
        "20260803_0030",
        release_commit="a" * 40,
        node_bin="pinned-node",
        npm_bin="pinned-npm",
    )
    payload = json.loads((release / "versions.json").read_text(encoding="utf-8"))
    ota_paths = {item["path"] for item in payload["ota_v2"]["artifacts"]}
    assert any("20260802_0026" in path for path in ota_paths)
    assert any("20260803_0027" in path for path in ota_paths)
    assert any("20260803_0028" in path for path in ota_paths)
    assert any("20260803_0030" in path for path in ota_paths)
    assert not any("20260803_0029" in path for path in ota_paths)
    assert payload["migration"]["source"]["path"].startswith(
        "backend/alembic/versions/20260803_0030_"
    )
    assert payload["node"].startswith("v24.")
    assert payload["git_commit"] == "a" * 40
    assert payload["dependency_audits"]["backend"]["lock"]["path"] == ("backend/requirements.lock")
    assert payload["dependency_audits"]["frontend"]["report"]["path"] == (
        "release/frontend-audit.json"
    )


def test_canonical_offline_migration_renderer_reaches_head_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = _load_script("generate_release_reports.py", "release_offline_sql_test")
    release = tmp_path / "release"
    release.mkdir()
    monkeypatch.setattr(reports, "RELEASE", release)

    reports.migration_offline_sql("20260803_0030")
    first = (release / "migration-offline.sql").read_bytes()
    reports.migration_offline_sql("20260803_0030")
    second = (release / "migration-offline.sql").read_bytes()

    assert first == second
    sql = first.decode("utf-8")
    assert "-- Running upgrade 20260730_0021 -> 20260731_0022" in sql
    assert "Packaging compatibility rendering for the released data-dependent migration" in sql
    assert (
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "power-monitor:migration:20260731_0022:role-revision",
            )
        )
        in sql
    )
    assert (
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "power-monitor:migration:20260731_0022:audit-event",
            )
        )
        in sql
    )
    assert "role.builtin_migrated" in sql
    assert "UPDATE alembic_version SET version_num='20260731_0022'" in sql
    assert "UPDATE alembic_version SET version_num='20260803_0030'" in sql


def test_release_guard_rejects_dirty_source_and_nonrelease_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = _load_script("release_guard.py", "release_guard_test")
    monkeypatch.setattr(guard, "changed_paths", lambda: {"backend/app/main.py"})
    with pytest.raises(ValueError, match="clean committed tree"):
        guard.assert_clean_source()
    with pytest.raises(ValueError, match="outside release"):
        guard.assert_only_release_outputs()
    monkeypatch.setattr(
        guard,
        "changed_paths",
        lambda: {"release/versions.json", "release/power-monitor-server-9.8.7.tar.gz"},
    )
    guard.assert_only_release_outputs()


def test_node_24_is_required_for_release_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = _load_script("generate_release_reports.py", "release_node_test")
    monkeypatch.setattr(
        reports,
        "command_version",
        lambda command: "v26.0.0" if command[-1] == "--version" else "unavailable",
    )
    with pytest.raises(ValueError, match="Node.js v24.4.0"):
        reports._node_toolchain("node", "npm")

    def version(command: list[str]) -> str:
        return "v24.4.0" if command[0] == "node24" else "11.4.2"

    monkeypatch.setattr(reports, "command_version", version)
    assert reports._node_toolchain("node24", "npm24") == ("v24.4.0", "11.4.2")


def test_checksum_verifier_binds_metadata_to_head_commit_and_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier = _load_script("verify_release_checksums.py", "release_verify_test")
    root = tmp_path / "root"
    release = root / "release"
    migration_dir = root / "backend" / "alembic" / "versions"
    backend_dir = root / "backend"
    frontend_dir = root / "frontend-next"
    protocol_dir = root / "shared"
    release.mkdir(parents=True)
    migration_dir.mkdir(parents=True)
    protocol_dir.mkdir(parents=True)
    frontend_dir.mkdir(parents=True)
    requirements = backend_dir / "requirements.lock"
    requirements.write_text("example==1.2.3\n", encoding="utf-8")
    package_lock = frontend_dir / "package-lock.json"
    package_lock.write_text(
        '{"lockfileVersion": 3, "packages": {'
        '"": {"name": "frontend"}, "node_modules/example": {"version": "1.0.0"}}}\n',
        encoding="utf-8",
    )
    backend_audit = release / "backend-audit.json"
    backend_audit.write_text(
        '{"dependencies": [{"name": "example", "version": "1.2.3", "vulns": []}]}\n',
        encoding="utf-8",
    )
    frontend_audit = release / "frontend-audit.json"
    frontend_audit.write_text(
        '{"auditReportVersion": 2, "vulnerabilities": {}, "metadata": {'
        '"vulnerabilities": {"info": 0, "low": 0, "moderate": 0, '
        '"high": 0, "critical": 0, "total": 0}, '
        '"dependencies": {"total": 1}}}\n',
        encoding="utf-8",
    )
    (protocol_dir / "protocol-version.txt").write_text("pm-protocol/1.0.0\n", encoding="utf-8")
    migration = migration_dir / "20260803_0030_ota.py"
    migration.write_text("revision = '20260803_0030'\n", encoding="utf-8")
    offline = release / "migration-offline.sql"
    offline.write_text("-- exact offline migration\n", encoding="utf-8")
    archive = release / "power-monitor-server-9.8.7.tar.gz"
    archive.write_bytes(b"exact source archive")
    metadata = {
        "version": "9.8.7",
        "protocol": "pm-protocol/1.0.0",
        "migration_revision": "20260803_0030",
        "git_commit": "a" * 40,
        "migration": {
            "source": {
                "path": migration.relative_to(root).as_posix(),
                "sha256": _digest(migration),
            },
            "offline_sql": {
                "path": "release/migration-offline.sql",
                "sha256": _digest(offline),
            },
        },
        "dependency_audits": {
            "backend": {
                "format": "pip-audit/json",
                "lock": {
                    "path": "backend/requirements.lock",
                    "sha256": _digest(requirements),
                },
                "report": {
                    "path": "release/backend-audit.json",
                    "sha256": _digest(backend_audit),
                },
            },
            "frontend": {
                "format": "npm-audit/json",
                "lock": {
                    "path": "frontend-next/package-lock.json",
                    "sha256": _digest(package_lock),
                },
                "report": {
                    "path": "release/frontend-audit.json",
                    "sha256": _digest(frontend_audit),
                },
            },
        },
    }
    versions = release / "versions.json"
    versions.write_text(json.dumps(metadata), encoding="utf-8")
    checksums = release / "checksums.sha256"
    checksums.write_text(
        "\n".join(
            (
                f"{_digest(offline)}  migration-offline.sql",
                f"{_digest(backend_audit)}  backend-audit.json",
                f"{_digest(frontend_audit)}  frontend-audit.json",
                f"{_digest(versions)}  versions.json",
                f"{_digest(archive)}  {archive.name}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(verifier, "_alembic_heads", lambda _: {"20260803_0030"})
    assert (
        verifier.verify_release(
            root=root,
            release=release,
            expected_version="9.8.7",
            expected_migration="20260803_0030",
            expected_commit="a" * 40,
            require_archive=True,
        )
        == 5
    )
    archive.unlink()
    assert verifier.verify_release(root=root, release=release) == 4
    with pytest.raises(AssertionError, match="missing release artifact"):
        verifier.verify_release(root=root, release=release, require_archive=True)

    # A stale audit cannot be made valid merely by recomputing its own report
    # hash and the release inventory. Its exact audited package set must still
    # agree with the source-frozen lockfile.
    backend_audit.write_text(
        '{"dependencies": [{"name": "example", "version": "1.2.2", "vulns": []}]}\n',
        encoding="utf-8",
    )
    metadata["dependency_audits"]["backend"]["report"]["sha256"] = _digest(backend_audit)
    versions.write_text(json.dumps(metadata), encoding="utf-8")
    lines = checksums.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.endswith("  backend-audit.json"):
            lines[index] = f"{_digest(backend_audit)}  backend-audit.json"
        elif line.endswith("  versions.json"):
            lines[index] = f"{_digest(versions)}  versions.json"
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="does not match the applicable locked"):
        verifier.verify_release(root=root, release=release)

    backend_audit.write_text(
        '{"dependencies": [{"name": "example", "version": "1.2.3", "vulns": []}]}\n',
        encoding="utf-8",
    )
    metadata["dependency_audits"]["backend"]["report"]["sha256"] = _digest(backend_audit)
    metadata["migration_revision"] = "20260803_0028"
    versions.write_text(json.dumps(metadata), encoding="utf-8")
    # The metadata mutation is rejected even if an attacker also updates its
    # inventory entry, because it no longer matches the repository head.
    lines = checksums.read_text(encoding="utf-8").splitlines()
    backend_line = next(
        index for index, line in enumerate(lines) if line.endswith("  backend-audit.json")
    )
    lines[backend_line] = f"{_digest(backend_audit)}  backend-audit.json"
    versions_line = next(
        index for index, line in enumerate(lines) if line.endswith("  versions.json")
    )
    lines[versions_line] = f"{_digest(versions)}  versions.json"
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="not the single Alembic head"):
        verifier.verify_release(root=root, release=release)
