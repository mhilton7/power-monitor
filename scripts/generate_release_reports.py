from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"


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
        (ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
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


def versions() -> None:
    git = command_version(["git", "rev-parse", "HEAD"])
    payload = {
        "product": "Power Monitor Server",
        "version": "1.0.0",
        "protocol": (ROOT / "shared" / "protocol-version.txt").read_text().strip(),
        "migration_revision": "20260720_0003",
        "python": platform.python_version(),
        "node": command_version(["node", "--version"]),
        "npm": command_version(["npm.cmd" if os.name == "nt" else "npm", "--version"]),
        "git_commit": git,
        "images": {
            "api": "power-monitor-api:1.0.0",
            "frontend": "power-monitor-frontend:1.0.0",
            "backup": "power-monitor-backup:1.0.0",
            "postgres": "docker.io/library/postgres:17.5-bookworm",
            "caddy": "docker.io/library/caddy:2.10.0-alpine",
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


if __name__ == "__main__":
    RELEASE.mkdir(exist_ok=True)
    backend_licenses()
    frontend_licenses()
    versions()
    checksums()
