from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_truenas_gate_prepares_and_exercises_rate_source_artifact_permissions() -> None:
    workflow = (ROOT / ".github/workflows/truenas-compose-integration.yml").read_text(
        encoding="utf-8"
    )

    assert "{backups,firmware,logs,config/reports,rate-source-artifacts,secrets" in workflow
    assert "10001:10001" in workflow
    assert "d:u:10003:rX" in workflow
    assert "Verify rate-source evidence permissions" in workflow
    assert "--reuid=10001" in workflow
    assert "--reuid=10003" in workflow


def test_truenas_gate_exports_the_caddy_ca_outside_the_caddy_bind_mount() -> None:
    workflow = (ROOT / ".github/workflows/truenas-compose-integration.yml").read_text(
        encoding="utf-8"
    )

    assert "--ca-certificate /tmp/power-monitor-root.crt" in workflow
    assert "--ca-certificate /mnt/pmci/Power/power-monitor/caddy-data" not in workflow


def test_publish_gate_rejects_version_reuse_before_build_or_publish() -> None:
    workflow = (ROOT / ".github/workflows/publish-images.yml").read_text(encoding="utf-8")

    preflight_at = workflow.index("  preflight:")
    source_validation_at = workflow.index("  source-validation:")
    frontend_validation_at = workflow.index("  frontend-validation:")
    build_validation_at = workflow.index("  build-validation:")
    publish_at = workflow.index("  publish:")

    assert (
        preflight_at
        < source_validation_at
        < frontend_validation_at
        < build_validation_at
        < publish_at
    )
    assert 'json.load(open("release/versions.json"' in workflow
    assert "does not match release/versions.json" in workflow
    assert "Reject an existing immutable release tag" in workflow
    assert "could not prove that immutable release tag is unused" in workflow
    assert "application source changed after the tested release commit" in workflow
    assert "--expected-commit '${{ needs.preflight.outputs.source_commit }}'" in workflow
    assert (
        "org.opencontainers.image.revision=${{ needs.preflight.outputs.source_commit }}" in workflow
    )
    assert (
        "needs: [preflight, source-validation, frontend-validation, build-validation]" in workflow
    )
    assert 'RUN_POSTGRES_INTEGRATION: "1"' in workflow
    assert 'RUN_HISTORY_PERFORMANCE: "1"' in workflow
    assert "push: false" in workflow[build_validation_at:publish_at]
    assert "push: true" in workflow[publish_at:]


def test_local_release_gates_regenerate_dependency_audits_before_evidence() -> None:
    powershell = (ROOT / "scripts/release.ps1").read_text(encoding="utf-8")
    shell = (ROOT / "scripts/release.sh").read_text(encoding="utf-8")

    for script in (powershell, shell):
        backend_audit_at = script.index("backend-audit.json")
        frontend_audit_at = script.index("frontend-audit.json")
        evidence_at = script.index("scripts/generate_release_reports.py")

        assert backend_audit_at < evidence_at
        assert frontend_audit_at < evidence_at
        assert "pip_audit" in script
        assert "audit --audit-level=high --json" in script
