#!/usr/bin/env python3
"""Run the release-gating multi-device workflow through the deployed gateway."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCKER_CLI = shutil.which("docker")
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from simulator.simulated_device.model import SimulatedDevice  # noqa: E402
from simulator.simulated_device.push import push_once  # noqa: E402

from app.security.protocol import PROTOCOL  # noqa: E402


class WorkflowFailure(RuntimeError):
    pass


def docker_desktop_runtime_compose(compose: Path, host_root: Path) -> Path:
    """Translate TrueNAS datasets for a POSIX-compatible Docker Desktop gate."""
    document = yaml.safe_load(compose.read_text(encoding="utf-8"))
    prefix: str | None = None
    for definition in document["secrets"].values():
        source = str(definition["file"])
        marker = "/apps/power-monitor/"
        if marker in source:
            prefix = source.split(marker, 1)[0] + marker.rstrip("/")
            break
    if prefix is None or not prefix.startswith("/mnt/"):
        raise WorkflowFailure("could not identify the validated TrueNAS bind root")
    host_root = host_root.resolve()
    if not host_root.is_dir():
        raise WorkflowFailure(
            f"Docker Desktop test host root does not exist: {host_root}"
        )

    runtime_volumes: dict[str, dict[str, Any]] = {}
    for service in document["services"].values():
        translated: list[Any] = []
        for volume in service.get("volumes", []):
            if not isinstance(volume, str) or not volume.startswith(prefix + "/"):
                translated.append(volume)
                continue
            source, target, *options = volume.split(":")
            relative = source.removeprefix(prefix).lstrip("/")
            if relative == "config/Caddyfile":
                runtime_volume: dict[str, Any] = {
                    "type": "bind",
                    "source": (host_root / relative).as_posix(),
                    "target": target,
                }
            else:
                volume_name = "desktop_" + relative.replace("/", "_")
                runtime_volumes[volume_name] = {}
                runtime_volume = {
                    "type": "volume",
                    "source": volume_name,
                    "target": target,
                    "volume": {"nocopy": True},
                }
            if "ro" in options:
                runtime_volume["read_only"] = True
            translated.append(runtime_volume)
        service["volumes"] = translated
    document["volumes"] = runtime_volumes
    for definition in document["secrets"].values():
        source = str(definition["file"])
        relative = source.removeprefix(prefix).lstrip("/")
        definition["file"] = (host_root / relative).as_posix()

    descriptor, runtime_name = tempfile.mkstemp(
        prefix="truenas-docker-desktop-", suffix=".yaml", dir=compose.parent
    )
    os.close(descriptor)
    runtime = Path(runtime_name)
    runtime.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return runtime


def prepare_docker_desktop_volumes(compose: Path) -> None:
    """Create clean test volumes with the production containers' numeric owners."""
    document = yaml.safe_load(compose.read_text(encoding="utf-8"))
    project = str(document.get("name", ""))
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise WorkflowFailure(f"unsafe or missing Compose project name: {project!r}")
    ownership = {
        "desktop_postgres": (999, 999, "0700"),
        "desktop_backups": (10003, 10003, "0770"),
        "desktop_firmware": (10001, 10001, "0775"),
        "desktop_config": (10001, 10001, "0775"),
        "desktop_config_reports": (10001, 10001, "0775"),
        "desktop_caddy-data": (10002, 10002, "0700"),
        "desktop_caddy-config": (10002, 10002, "0700"),
    }
    declared = set(document.get("volumes", {}))
    if declared != set(ownership):
        raise WorkflowFailure(f"unexpected Docker Desktop test volumes: {declared}")
    helper_image = str(document["services"]["postgres"]["image"])
    for key, (uid, gid, mode) in ownership.items():
        volume_name = f"{project}_{key}"
        exists = subprocess.run(  # noqa: S603
            [DOCKER_CLI or "docker", "volume", "inspect", volume_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if exists.returncode == 0:
            raise WorkflowFailure(
                f"Docker Desktop test volume is not clean: {volume_name}"
            )
        subprocess.run(  # noqa: S603
            [DOCKER_CLI or "docker", "volume", "create", volume_name],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(  # noqa: S603
            [
                DOCKER_CLI or "docker",
                "run",
                "--rm",
                "--user",
                "0:0",
                "--mount",
                f"type=volume,src={volume_name},dst=/target",
                helper_image,
                "sh",
                "-c",
                f"chown {uid}:{gid} /target && chmod {mode} /target",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )


def run_compose(compose: Path, *arguments: str, capture: bool = False) -> str:
    command = ["docker", "compose", "-f", str(compose), *arguments]
    result = subprocess.run(  # noqa: S603
        command,
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=capture,
    )
    if result.returncode:
        detail = (
            result.stderr.strip() if capture else f"exit status {result.returncode}"
        )
        raise WorkflowFailure(f"docker compose {' '.join(arguments)} failed: {detail}")
    return result.stdout.strip() if capture else ""


def inspect_json(container_id: str, template: str) -> Any:
    result = subprocess.run(  # noqa: S603
        [DOCKER_CLI or "docker", "inspect", "--format", template, container_id],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise WorkflowFailure(
            f"docker inspect failed for {container_id}: {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def export_internal_ca(compose: Path, destination: Path) -> None:
    gateway_id = run_compose(compose, "ps", "-q", "gateway", capture=True)
    if not gateway_id:
        raise WorkflowFailure("cannot export internal CA: gateway container is missing")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    result = subprocess.run(  # noqa: S603
        [
            DOCKER_CLI or "docker",
            "cp",
            f"{gateway_id}:/data/caddy/pki/authorities/local/root.crt",
            str(destination),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode or not destination.is_file():
        raise WorkflowFailure(f"internal CA export failed: {result.stderr.strip()}")


def assert_container_state(compose: Path, gateway_port: int) -> None:
    for service in ("postgres", "api", "worker", "frontend", "gateway", "backup"):
        container_id = run_compose(compose, "ps", "-q", service, capture=True)
        if not container_id:
            raise WorkflowFailure(f"{service} did not create a running container")
        health = inspect_json(container_id, "{{json .State.Health.Status}}")
        if health != "healthy":
            raise WorkflowFailure(f"{service} health is {health!r}, expected 'healthy'")
        ports = inspect_json(container_id, "{{json .NetworkSettings.Ports}}") or {}
        published = {
            container_port: bindings
            for container_port, bindings in ports.items()
            if bindings is not None
        }
        if service == "gateway":
            bindings = published.get("443/tcp")
            if len(published) != 1 or not bindings:
                raise WorkflowFailure(
                    f"gateway published unexpected ports: {published}"
                )
            if {int(item["HostPort"]) for item in bindings} != {gateway_port}:
                raise WorkflowFailure(
                    f"gateway did not publish host port {gateway_port}"
                )
        elif published:
            raise WorkflowFailure(f"{service} must not publish host ports: {published}")

    migrate_id = run_compose(compose, "ps", "-aq", "migrate", capture=True)
    if not migrate_id:
        raise WorkflowFailure("migration container is missing")
    state = inspect_json(migrate_id, "{{json .State}}")
    if state.get("Status") != "exited" or state.get("ExitCode") != 0:
        raise WorkflowFailure(f"migration did not complete successfully: {state}")


def _require_success(response: httpx.Response, operation: str) -> httpx.Response:
    if response.is_error:
        detail = response.text[:500].replace("\n", " ")
        raise WorkflowFailure(
            f"{operation} failed with HTTP {response.status_code}: {detail}"
        )
    return response


async def exercise_application(
    *, base_url: str, ca_certificate: Path, setup_token_file: Path, device_count: int
) -> tuple[int, int]:
    setup_token = setup_token_file.read_text(encoding="utf-8").strip()
    if not setup_token:
        raise WorkflowFailure("administrator setup token is empty")
    async with httpx.AsyncClient(
        base_url=base_url,
        verify=str(ca_certificate),
        timeout=30,
        follow_redirects=False,
    ) as client:
        session = _require_success(
            await client.get("/api/v1/auth/session"), "read initial session"
        ).json()
        if not session.get("bootstrap_required"):
            raise WorkflowFailure(
                "integration database is not clean: bootstrap is already closed"
            )
        _require_success(
            await client.post(
                "/api/v1/auth/bootstrap",
                json={
                    "bootstrap_secret": setup_token,
                    "email": "integration-admin@example.com",
                    "display_name": "Integration Administrator",
                    "password": "Integration-Only-Password-47!",
                },
            ),
            "first-run administrator setup",
        )
        csrf = client.cookies.get("pm_csrf")
        if not csrf:
            raise WorkflowFailure(
                "bootstrap did not establish a CSRF-protected session"
            )
        csrf_header = {"X-CSRF-Token": csrf}
        sites = _require_success(await client.get("/api/v1/sites"), "list sites").json()
        if len(sites) != 1:
            raise WorkflowFailure("bootstrap did not create exactly one default site")

        expected_readings = 0
        for index in range(device_count):
            enrollment = _require_success(
                await client.post(
                    "/api/v1/enrollment-tokens",
                    headers=csrf_header,
                    json={
                        "site_id": sites[0]["id"],
                        "name": f"TrueNAS Simulator {index + 1}",
                        "connection_mode": "push",
                    },
                ),
                f"create enrollment token {index + 1}",
            ).json()
            claim = _require_success(
                await client.post(
                    "/api/v1/device-enrollment/claim",
                    json={
                        "token": enrollment["token"],
                        "protocol_version": PROTOCOL,
                        "hardware_id": f"esp32s3-truenas-integration-{index:04d}",
                        "capabilities": {
                            "hardware_target": "esp32-s3-pzem004t-v4",
                            "pzem_model": "PZEM-004T V4.0",
                            "sd_present": True,
                            "sd_required": True,
                            "supported_endpoints": ["health", "readings"],
                        },
                    },
                ),
                f"claim simulated device {index + 1}",
            ).json()
            device = SimulatedDevice(
                index=index,
                device_id=claim["device_id"],
                secret=claim["enrollment_secret"],
            )
            start = datetime.now(UTC) - timedelta(hours=6)
            for offset in range(30):
                device.generate_reading(instant=start + timedelta(minutes=offset))
            result = await push_once(client, device)
            if result != {"heartbeat": 1, "accepted": 30}:
                raise WorkflowFailure(
                    f"simulated device {index + 1} backfill was incomplete"
                )
            expected_readings += 30

        devices = _require_success(
            await client.get("/api/v1/devices"), "list devices"
        ).json()
        if len(devices) != device_count:
            raise WorkflowFailure("not all simulated devices were enrolled")
        if not all(item.get("last_seen_at") for item in devices):
            raise WorkflowFailure(
                "signed heartbeats did not update current device state"
            )

        rate = _require_success(
            await client.post(
                "/api/v1/rates/preview",
                json={
                    "plan_code": "TOU-D-4-9PM",
                    "interval_start": "2026-07-20T22:30:00Z",
                    "interval_end": "2026-07-21T00:30:00Z",
                    "energy_kwh": "2",
                    "cost_scope": "full_account",
                    "baseline_allocation_kwh": "1",
                    "billing_days": 1,
                },
            ),
            "calculate SCE rate",
        ).json()
        if rate.get("plan_code") != "TOU-D-4-9PM" or float(rate["display_total"]) <= 0:
            raise WorkflowFailure("SCE rate calculation returned an invalid result")
        return device_count, expected_readings


def backup_and_restore(
    compose: Path, expected_devices: int, expected_readings: int
) -> None:
    backup_output = run_compose(
        compose,
        "run",
        "--rm",
        "backup",
        "/srv/scripts/backup-container.sh",
        capture=True,
    )
    backup_dir = backup_output.splitlines()[-1].strip()
    if not backup_dir.startswith("/data/backups/power-monitor-"):
        raise WorkflowFailure("backup service did not return a completed backup path")
    run_compose(
        compose,
        "run",
        "--rm",
        "backup",
        "/srv/scripts/verify-backup-container.sh",
        backup_dir,
    )
    restore_database = "power_monitor_integration_restore"
    run_compose(
        compose,
        "run",
        "--rm",
        "backup",
        "/srv/scripts/restore-container.sh",
        backup_dir,
        restore_database,
        "--yes",
    )
    query = (
        "SELECT (SELECT count(*) FROM devices),"
        "(SELECT count(*) FROM device_heartbeats),"
        "(SELECT count(*) FROM raw_readings),"
        "(SELECT count(*) FROM alembic_version);"
    )
    restored = run_compose(
        compose,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "power_monitor",
        "-d",
        restore_database,
        "-At",
        "-c",
        query,
        capture=True,
    ).splitlines()[-1]
    devices, heartbeats, readings, revisions = (
        int(value) for value in restored.split("|")
    )
    if devices != expected_devices or heartbeats < expected_devices:
        raise WorkflowFailure(
            "restored database is missing enrolled devices or heartbeats"
        )
    if readings != expected_readings or revisions != 1:
        raise WorkflowFailure(
            "restored database is missing historical readings or migration state"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ca-certificate", required=True, type=Path)
    parser.add_argument("--setup-token-file", required=True, type=Path)
    parser.add_argument("--gateway-port", type=int, default=8443)
    parser.add_argument("--device-count", type=int, default=3)
    parser.add_argument(
        "--keep", action="store_true", help="leave containers running after success"
    )
    parser.add_argument(
        "--keep-on-failure",
        action="store_true",
        help="leave containers running after failure",
    )
    parser.add_argument(
        "--docker-desktop-host-root",
        type=Path,
        help="test-only replacement for the already validated /mnt/POOL bind root",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if DOCKER_CLI is None:
        print("workflow failed: Docker CLI is not installed", file=sys.stderr)
        return 2
    compose = args.compose.resolve()
    runtime_compose: Path | None = None
    if args.docker_desktop_host_root is not None:
        try:
            runtime_compose = docker_desktop_runtime_compose(
                compose, args.docker_desktop_host_root
            )
            compose = runtime_compose
        except (OSError, KeyError, TypeError, WorkflowFailure, yaml.YAMLError) as exc:
            print(f"workflow failed: {exc}", file=sys.stderr)
            return 1
    succeeded = False
    try:
        run_compose(compose, "config", "--quiet")
        if runtime_compose is not None:
            prepare_docker_desktop_volumes(compose)
        run_compose(compose, "up", "-d", "--wait", "--wait-timeout", "300")
        assert_container_state(compose, args.gateway_port)
        export_internal_ca(compose, args.ca_certificate.resolve())
        devices, readings = asyncio.run(
            exercise_application(
                base_url=args.base_url,
                ca_certificate=args.ca_certificate.resolve(),
                setup_token_file=args.setup_token_file.resolve(),
                device_count=args.device_count,
            )
        )
        backup_and_restore(compose, devices, readings)
        succeeded = True
        print(
            f"TrueNAS workflow passed: services=7 devices={devices} readings={readings} "
            "backup=verified restore=verified ports=verified"
        )
        return 0
    except (OSError, ValueError, WorkflowFailure, httpx.HTTPError) as exc:
        print(f"workflow failed: {exc}", file=sys.stderr)
        return 1
    finally:
        should_keep = args.keep if succeeded else args.keep_on_failure
        if not should_keep:
            try:
                cleanup = ["down", "--remove-orphans"]
                if runtime_compose is not None:
                    cleanup.append("--volumes")
                run_compose(compose, *cleanup)
            except WorkflowFailure as exc:
                print(f"cleanup warning: {exc}", file=sys.stderr)
        if runtime_compose is not None and not (args.keep_on_failure and not succeeded):
            runtime_compose.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
