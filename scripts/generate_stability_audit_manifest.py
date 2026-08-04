"""Generate the coordinated, tracked-file stability-audit manifest.

The manifest is intentionally derived from ``git ls-files`` so that release
evidence cannot silently omit a tracked first-party file.  It records the
validation method that applies to each file; ``--validated`` is used only
after the complete subsystem gates have passed.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
SENSOR_ROOT = SERVER_ROOT.parent / "power-monitor-sensor"
DEFAULT_OUTPUT = SERVER_ROOT / "docs" / "audits" / "TRACKED_FILE_AUDIT_MANIFEST.csv"


KNOWN_FINDINGS: dict[tuple[str, str], tuple[str, str]] = {
    ("server", "backend/app/history.py"): (
        "Baseline hydrated broad ORM rows and performed unrequested rate, tier, and series work.",
        "Use a metric-aware execution plan, narrow projections, bounded output, and indexed tier lookup.",
    ),
    ("server", "frontend-next/src/pages/history/HistoryPage.tsx"): (
        "Baseline request identity, cancellation, and event refresh behavior could duplicate expensive work.",
        "Stabilize query identity, propagate cancellation, coalesce active-site refreshes, and preserve pagination.",
    ),
    ("server", "backend/app/api/routes/firmware.py"): (
        "Baseline reconciliation was partly request-triggered and allowed stale nonterminal deployments.",
        "Use the centralized, monotonic lifecycle service and persisted terminalization timestamps.",
    ),
    ("server", "backend/app/api/routes/device_protocol.py"): (
        "Heartbeat/report races used split transition logic.",
        "Route transitions through the row-locked lifecycle service and retain attempt-scoped evidence.",
    ),
    ("server", "worker/app/main.py"): (
        "Baseline worker did not periodically reconcile stale firmware deployments.",
        "Run bounded, idempotent lifecycle reconciliation independently of dashboard traffic.",
    ),
    ("sensor", "include/app/TaskConfig.h"): (
        "Baseline permanent internal-DRAM stack reservations left a production sensor below unchanged TLS admission floors.",
        "Reclaim measured-safe stack reserve and prove at least 25 percent physical high-water margin.",
    ),
    ("sensor", "src/api/HttpApi.cpp"): (
        "Local health exposed a hysteretic cached TLS-ready value rather than the current heap snapshot.",
        "Calculate readiness from the exact current total/largest-block/integrity sample.",
    ),
    ("sensor", "src/ota/OtaService.cpp"): (
        "OTA durability, boot-selection verification, post-boot validation, and rollback outcomes require fail-closed evidence.",
        "Persist and read back typed recovery state, verify the target partition, and use bounded live validation and recovery outcomes.",
    ),
}


TEXT_EXTENSIONS = {
    "",
    ".c",
    ".cc",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".dockerfile",
    ".example",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".lock",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".sha256",
    ".sh",
    ".sql",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


def tracked_files(root: Path) -> list[str]:
    output = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return [
        line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()
    ]


def category(path: str) -> str:
    name = Path(path).name.lower()
    extension = Path(path).suffix.lower()
    if extension in {".png", ".jpg", ".jpeg", ".pdf"}:
        return "binary-reference"
    if extension in {".bin", ".elf", ".map"}:
        return "release-artifact"
    if name in {"package-lock.json", "uv.lock", "requirements.lock"}:
        return "dependency-lock"
    if extension in {".md", ".txt"} or path.startswith("docs/"):
        return "documentation"
    if "test" in path.lower() or path.startswith(("frontend-next/e2e/", "simulator/")):
        return "test-or-simulator"
    if extension in TEXT_EXTENSIONS:
        return "first-party-text"
    return "generated-or-special"


def subsystem(repository: str, path: str) -> str:
    normalized = path.lower()
    if repository == "sensor":
        for token, label in (
            ("src/ota/", "sensor-ota"),
            ("src/network/", "sensor-network-sync"),
            ("src/storage/", "sensor-storage"),
            ("src/api/", "sensor-local-web"),
            ("src/config/", "sensor-configuration"),
            ("src/provisioning/", "sensor-provisioning"),
            ("src/security/", "sensor-security"),
            ("web/", "sensor-local-web"),
            ("release/", "sensor-release"),
            ("tools/", "sensor-tooling"),
            ("test/", "sensor-tests"),
        ):
            if normalized.startswith(token):
                return label
        return "sensor-runtime"
    for token, label in (
        ("backend/alembic/", "database-migrations"),
        ("backend/app/history", "history-backend"),
        ("backend/app/firmware", "firmware-lifecycle"),
        ("backend/app/api/routes/firmware", "firmware-lifecycle"),
        ("backend/", "backend-api"),
        ("worker/", "background-worker"),
        ("frontend-next/", "production-frontend"),
        ("frontend/", "legacy-frontend"),
        ("deploy/", "deployment"),
        ("tools/", "deployment-tooling"),
        ("scripts/", "build-release-tooling"),
        ("shared/", "normative-contracts"),
        ("simulator/", "device-simulator"),
        ("release/", "server-release"),
        ("docs/", "documentation"),
    ):
        if normalized.startswith(token):
            return label
    return "repository-infrastructure"


def audit_method(file_category: str) -> str:
    if file_category == "binary-reference":
        return "inventory plus visual/content-specific regression gate"
    if file_category == "release-artifact":
        return "inventory plus SHA-256, metadata, layout, and provenance verification"
    if file_category == "dependency-lock":
        return "inventory plus pinned dependency/build reproduction"
    if file_category == "documentation":
        return "inventory plus instruction, link, safety, and provenance review"
    return (
        "inventory plus static analysis, compile/lint, and subsystem regression gates"
    )


def rows(validated: bool) -> list[list[str]]:
    manifest_rows: list[list[str]] = []
    for repository, root in (("server", SERVER_ROOT), ("sensor", SENSOR_ROOT)):
        for path in tracked_files(root):
            file_category = category(path)
            finding, required = KNOWN_FINDINGS.get(
                (repository, path),
                (
                    "No file-specific defect identified during subsystem audit.",
                    "None beyond applicable subsystem gates.",
                ),
            )
            status = (
                "reviewed-and-validated"
                if validated
                else "inventory-reviewed; final subsystem gate pending"
            )
            manifest_rows.append(
                [
                    repository,
                    path,
                    file_category,
                    subsystem(repository, path),
                    status,
                    audit_method(file_category),
                    finding,
                    required,
                ]
            )
    return manifest_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validated", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "repository",
                "path",
                "category",
                "subsystem",
                "audit_status",
                "audit_method",
                "relevant_findings",
                "changes_required",
            ]
        )
        writer.writerows(rows(args.validated))


if __name__ == "__main__":
    main()
