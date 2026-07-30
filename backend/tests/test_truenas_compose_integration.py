from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.integration
def test_full_truenas_compose_workflow() -> None:
    if os.getenv("RUN_TRUENAS_COMPOSE_INTEGRATION") != "1":
        pytest.skip("set RUN_TRUENAS_COMPOSE_INTEGRATION=1 with a rendered production stack")
    root = Path(__file__).resolve().parents[2]
    required = {
        "TRUENAS_COMPOSE_FILE": os.getenv("TRUENAS_COMPOSE_FILE"),
        "TRUENAS_BASE_URL": os.getenv("TRUENAS_BASE_URL"),
        "TRUENAS_CA_CERTIFICATE": os.getenv("TRUENAS_CA_CERTIFICATE"),
        "TRUENAS_SETUP_TOKEN_FILE": os.getenv("TRUENAS_SETUP_TOKEN_FILE"),
    }
    missing = [name for name, value in required.items() if not value]
    assert not missing, f"missing integration configuration: {', '.join(missing)}"
    command = [
        str(root / ".venv/Scripts/python.exe") if os.name == "nt" else "python3",
        str(root / "tools/test-truenas-workflow.py"),
        "--compose",
        required["TRUENAS_COMPOSE_FILE"],
        "--base-url",
        required["TRUENAS_BASE_URL"],
        "--ca-certificate",
        required["TRUENAS_CA_CERTIFICATE"],
        "--setup-token-file",
        required["TRUENAS_SETUP_TOKEN_FILE"],
        "--gateway-port",
        os.getenv("TRUENAS_GATEWAY_PORT", "8443"),
    ]
    desktop_root = os.getenv("TRUENAS_DOCKER_DESKTOP_HOST_ROOT")
    if desktop_root:
        command.extend(["--docker-desktop-host-root", desktop_root])
    desktop_project = os.getenv("TRUENAS_DOCKER_DESKTOP_PROJECT_NAME")
    if desktop_project:
        command.extend(["--docker-desktop-project-name", desktop_project])
    if os.getenv("TRUENAS_DOCKER_DESKTOP_LOCAL_APPLICATION_IMAGES") == "1":
        command.append("--docker-desktop-local-application-images")
    subprocess.run(  # noqa: S603
        command,
        cwd=root,
        check=True,
    )
