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
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

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


def _application_image_tag(image: str) -> str:
    reference = image.split("@", 1)[0]
    last_slash = reference.rfind("/")
    last_colon = reference.rfind(":")
    if last_colon <= last_slash:
        raise WorkflowFailure(f"application image has no version tag: {image}")
    return reference[last_colon + 1 :]


def docker_desktop_runtime_compose(
    compose: Path,
    host_root: Path,
    *,
    project_name: str | None = None,
    local_application_images: bool = False,
) -> Path:
    """Translate TrueNAS datasets for a POSIX-compatible Docker Desktop gate."""
    document = yaml.safe_load(compose.read_text(encoding="utf-8"))
    prefix: str | None = None
    for definition in document["secrets"].values():
        source = str(definition["file"])
        marker = "/Power/power-monitor/"
        if marker in source:
            prefix = source.split(marker, 1)[0] + marker.rstrip("/")
            break
    if prefix is None or not prefix.startswith("/mnt/"):
        raise WorkflowFailure("could not identify the validated TrueNAS bind root")
    if project_name is not None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_name):
            raise WorkflowFailure(
                f"unsafe Docker Desktop project name: {project_name!r}"
            )
        document["name"] = project_name
    if local_application_images:
        release = _application_image_tag(str(document["services"]["api"]["image"]))
        document["services"]["api"]["image"] = f"power-monitor-api:{release}"
        document["services"]["worker"]["image"] = f"power-monitor-api:{release}"
        document["services"]["migrate"]["image"] = f"power-monitor-api:{release}"
        document["services"]["frontend"]["image"] = f"power-monitor-frontend:{release}"
        document["services"]["backup"]["image"] = f"power-monitor-backup:{release}"
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
        # Docker Desktop named volumes cannot reproduce the two-user TrueNAS
        # ACL on logs, so the integration-only volume permits both runtimes.
        "desktop_logs": (10001, 10001, "0777"),
        "desktop_rate-source-artifacts": (10001, 10001, "0775"),
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


async def _wait_for_verified_backup(
    client: httpx.AsyncClient,
    backup_id: str,
    *,
    timeout_seconds: int = 240,
) -> None:
    failure_states = {
        "backup_failed",
        "verification_failed",
        "restore_failed",
        "deletion_failed",
    }
    for _ in range(timeout_seconds):
        backups = _require_success(
            await client.get("/api/v1/backups"),
            "observe backup verification",
        ).json()
        backup = next((item for item in backups if item.get("id") == backup_id), None)
        if backup is None:
            raise WorkflowFailure(
                "queued backup disappeared from the visible inventory"
            )
        if backup.get("status") == "verified" and backup.get("verified_at"):
            return
        if backup.get("status") in failure_states:
            raise WorkflowFailure(
                f"backup {backup_id[:8]} failed in state {backup.get('status')}"
            )
        await asyncio.sleep(1)
    raise WorkflowFailure(f"backup {backup_id[:8]} did not verify before timeout")


async def _wait_for_backup_idle(
    client: httpx.AsyncClient,
    *,
    timeout_seconds: int = 300,
) -> list[dict[str, Any]]:
    incomplete_backup_statuses = {
        "queued",
        "creating",
        "verification_queued",
        "verifying",
        "restore_preflight",
        "restoring",
        "deleting",
    }
    for _ in range(timeout_seconds):
        jobs = _require_success(
            await client.get("/api/v1/backup-requests"),
            "observe backup job queue",
        ).json()
        backups = _require_success(
            await client.get("/api/v1/backups"),
            "observe backup inventory",
        ).json()
        if not any(
            item.get("status") in {"queued", "running"} for item in jobs
        ) and not any(
            item.get("status") in incomplete_backup_statuses for item in backups
        ):
            return backups
        await asyncio.sleep(1)
    raise WorkflowFailure("backup service did not become idle before timeout")


async def replace_all_backups(
    client: httpx.AsyncClient,
    csrf_header: dict[str, str],
) -> str:
    inventory = await _wait_for_backup_idle(client)
    old_ids = [str(item.get("id", "")) for item in inventory]
    verified = [
        item
        for item in inventory
        if item.get("status") == "verified" and item.get("verified_at")
    ]
    for index in range(max(0, 2 - len(verified))):
        created = _require_success(
            await client.post(
                "/api/v1/backup-requests",
                headers=csrf_header,
                json={
                    "operation": "create",
                    "idempotency_key": f"truenas-replace-all-prerequisite-{index + 1}",
                },
            ),
            f"create prerequisite backup {index + 1}",
        ).json()
        backup_id = str(created.get("backup_id", ""))
        try:
            UUID(backup_id)
        except ValueError as exc:
            raise WorkflowFailure("backup request returned an invalid UUID") from exc
        await _wait_for_verified_backup(client, backup_id)
        await _wait_for_backup_idle(client)
        old_ids.append(backup_id)

    preview = _require_success(
        await client.get("/api/v1/backups/replace-all-preview"),
        "inventory backups before replacement",
    ).json()
    if preview.get("existing_backup_count", 0) < 2:
        raise WorkflowFailure(
            "replace-all preview did not inventory at least two backups"
        )
    if preview.get("verified_backup_count") != preview.get("existing_backup_count"):
        raise WorkflowFailure("replace-all preview included a non-verified backup")

    requested = _require_success(
        await client.post(
            "/api/v1/backups/replace-all",
            headers=csrf_header,
            json={
                "confirmation": "REPLACE ALL BACKUPS",
                "idempotency_key": "truenas-replace-all-final",
            },
        ),
        "replace all backups",
    ).json()
    job_id = str(requested.get("id", ""))
    replacement_id = str(requested.get("backup_id", ""))
    try:
        UUID(job_id)
        UUID(replacement_id)
    except ValueError as exc:
        raise WorkflowFailure("replace-all request returned an invalid UUID") from exc

    for _ in range(300):
        jobs = _require_success(
            await client.get("/api/v1/backup-requests"),
            "observe backup replacement",
        ).json()
        job = next((item for item in jobs if item.get("id") == job_id), None)
        if job is None:
            raise WorkflowFailure(
                "replace-all job disappeared from the request history"
            )
        if job.get("status") == "failed":
            raise WorkflowFailure(
                f"replace-all failed: {job.get('error_code') or 'unknown error'}"
            )
        if job.get("status") == "completed":
            result = job.get("result") or {}
            if not result.get("verified") or not result.get("cleanup_complete"):
                raise WorkflowFailure(
                    "replace-all completed without passing final checks"
                )
            break
        await asyncio.sleep(1)
    else:
        raise WorkflowFailure("replace-all did not complete before timeout")

    backups = _require_success(
        await client.get("/api/v1/backups"),
        "verify final backup inventory",
    ).json()
    if len(backups) != 1:
        raise WorkflowFailure("replace-all did not leave exactly one visible backup")
    final = backups[0]
    if (
        final.get("id") != replacement_id
        or final.get("status") != "verified"
        or not final.get("verified_at")
    ):
        raise WorkflowFailure("replace-all did not preserve its verified replacement")
    if replacement_id in old_ids:
        raise WorkflowFailure(
            "replace-all reused an old backup instead of creating a replacement"
        )
    print(
        "Backup replacement evidence: "
        f"existing_count={preview.get('existing_backup_count')} "
        f"existing_bytes={preview.get('existing_storage_bytes')} "
        f"replacement_id={replacement_id} "
        f"deleted_count={result.get('deleted_backup_count')} "
        f"reclaimed_bytes={result.get('reclaimed_bytes')} "
        f"remaining_count={result.get('remaining_backup_count')} "
        "checksums=verified temporary_restore=verified"
    )
    return replacement_id


async def exercise_application(
    *, base_url: str, ca_certificate: Path, setup_token_file: Path, device_count: int
) -> tuple[int, int, int, int, str]:
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
        health_response = _require_success(
            await client.get(
                "/api/v1/system/health",
                headers={"X-Power-Monitor-Frontend-Version": "1.0.0"},
            ),
            "read owner System Health",
        )
        health = health_response.json()
        if health.get("schema_version") != "system-health/1.0":
            raise WorkflowFailure("System Health returned an incompatible schema")
        if {item.get("key") for item in health.get("components", [])} != {
            "api",
            "database",
            "worker",
            "storage",
            "backups",
            "live_data",
            "rate_engine",
        }:
            raise WorkflowFailure(
                "System Health omitted one or more required components"
            )
        safe_health = health_response.text.lower()
        if any(
            forbidden in safe_health
            for forbidden in (
                "database_url",
                "app_master_key",
                "session_pepper",
                "/data/",
            )
        ):
            raise WorkflowFailure(
                "System Health exposed a secret name or sensitive path"
            )
        sites = _require_success(await client.get("/api/v1/sites"), "list sites").json()
        if len(sites) != 1:
            raise WorkflowFailure("bootstrap did not create exactly one default site")
        site = sites[0]

        plans = _require_success(
            await client.get("/api/v1/rates/plans"), "list published rates"
        ).json()
        rate_version = next(
            (
                version
                for plan in plans
                for version in plan.get("versions", [])
                if (
                    version.get("publication_status") == "published"
                    or version.get("status") in {"active", "approved"}
                )
            ),
            None,
        )
        if rate_version is None:
            raise WorkflowFailure("no assignable rate version was seeded")
        account_payload = {
            "nickname": "TrueNAS deployment gate",
            "account_number_suffix": "0042",
            "utility_provider": "sce",
            "generation_provider": "sce",
            "provider_mode": "sce_bundled",
            "billing_cycle_start_day": 17,
            "currency": "USD",
            "service_class": "Residential",
            "rate_assignment": {
                "rate_version_id": rate_version["id"],
                "effective_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                "assignment_reason": "Digest-pinned deployment acceptance",
            },
            "cost_scope": "energy_only",
            "adjustments": [],
            "confirmation": True,
        }
        for index, name in enumerate(("Main electric", "Detached building")):
            account = _require_success(
                await client.post(
                    f"/api/v1/admin/sites/{site['id']}/utility-accounts",
                    headers=csrf_header,
                    json={**account_payload, "name": name},
                ),
                f"create utility account {index + 1}",
            ).json()
            context = account.get("rate_context", {})
            if context.get("state") != "rate_configured_effective":
                raise WorkflowFailure("utility account lacks an effective rate context")
            if not context.get("current_period") or not context.get(
                "current_price_per_kwh"
            ):
                raise WorkflowFailure("current period or price did not resolve")
        utility_accounts = _require_success(
            await client.get(f"/api/v1/admin/sites/{site['id']}/utility-accounts"),
            "list utility accounts",
        ).json()
        if len(utility_accounts) != 2:
            raise WorkflowFailure("multiple utility accounts were not persisted")
        readiness = _require_success(
            await client.get(f"/api/v1/sites/{site['id']}/setup-readiness"),
            "read setup readiness",
        ).json()
        if (
            readiness.get("rate_and_cost", {}).get("state")
            != "rate_configured_effective"
        ):
            raise WorkflowFailure("site readiness did not report the effective rate")

        enabled_test_mode = _require_success(
            await client.post(
                "/api/v1/test-mode/enable",
                headers=csrf_header,
                json={
                    "sensor_count": 3,
                    "load_profile": "variable_household",
                    "base_load_w": "800",
                    "variation_percent": "10",
                    "offline_sensor_indexes": [2],
                    "sample_interval_seconds": 1,
                    "expires_in_minutes": 15,
                    "cost_preview_enabled": False,
                    "idempotency_key": "truenas-test-mode-enable",
                },
            ),
            "enable isolated Sensor Test Mode",
        ).json()
        if (
            enabled_test_mode.get("source_type") != "simulated"
            or enabled_test_mode.get("environment") != "test_mode"
            or enabled_test_mode.get("sensor_count") != 3
            or enabled_test_mode.get("online_sensors") != 2
        ):
            raise WorkflowFailure("Sensor Test Mode returned an invalid isolated state")
        original_test_sensors = _require_success(
            await client.get("/api/v1/test-mode/sensors"),
            "list simulated sensors",
        ).json()
        if len(original_test_sensors) != 3:
            raise WorkflowFailure(
                "Sensor Test Mode did not create three simulated sensors"
            )
        original_ids = [sensor["id"] for sensor in original_test_sensors]
        updated_test_mode = _require_success(
            await client.put(
                "/api/v1/test-mode",
                headers=csrf_header,
                json={
                    "sensor_count": 5,
                    "offline_sensor_indexes": [2],
                    "cost_preview_enabled": True,
                    "idempotency_key": "truenas-test-mode-update",
                },
            ),
            "update isolated Sensor Test Mode",
        ).json()
        if (
            updated_test_mode.get("sensor_count") != 5
            or updated_test_mode.get("online_sensors") != 4
            or not updated_test_mode.get("cost_preview", {}).get("available")
        ):
            raise WorkflowFailure(
                "Sensor Test Mode update or temporary cost preview failed"
            )
        resized_test_sensors = _require_success(
            await client.get("/api/v1/test-mode/sensors"),
            "list resized simulated sensors",
        ).json()
        if [sensor["id"] for sensor in resized_test_sensors[:3]] != original_ids:
            raise WorkflowFailure(
                "Sensor Test Mode did not preserve stable simulated IDs"
            )
        if not _require_success(
            await client.get("/api/v1/test-mode/history"),
            "read isolated simulated history",
        ).json():
            raise WorkflowFailure("Sensor Test Mode did not produce session history")
        disabled_test_mode = _require_success(
            await client.post(
                "/api/v1/test-mode/disable",
                headers=csrf_header,
                json={"idempotency_key": "truenas-test-mode-disable"},
            ),
            "disable isolated Sensor Test Mode",
        ).json()
        if disabled_test_mode.get("enabled") or disabled_test_mode.get("sensor_count"):
            raise WorkflowFailure("Sensor Test Mode did not clean up its session")
        if (await client.get("/api/v1/test-mode/history")).status_code != 409:
            raise WorkflowFailure("Sensor Test Mode history remained after cleanup")
        if _require_success(
            await client.get("/api/v1/devices"),
            "verify Test Mode did not create real devices",
        ).json():
            raise WorkflowFailure("Sensor Test Mode contaminated the real device list")

        policies = _require_success(
            await client.get("/api/v1/admin/network/policies"),
            "list sensor network policies",
        ).json()
        ingress = next(
            item for item in policies if item["direction"] == "device_ingress"
        )
        pull = next(item for item in policies if item["direction"] == "server_pull")
        if ingress["mode"] != "legacy_authenticated_any" or not ingress.get(
            "migration_notice_pending"
        ):
            raise WorkflowFailure(
                "legacy ingress behavior was not preserved for review"
            )
        if pull["mode"] != "deny_all":
            raise WorkflowFailure("legacy empty pull CIDRs did not migrate to deny-all")
        _require_success(
            await client.post(
                "/api/v1/admin/network/cidrs",
                headers=csrf_header,
                json={
                    "policy_id": pull["id"],
                    "network": "192.168.50.42/24",
                    "label": "TrueNAS sensor VLAN",
                },
            ),
            "add canonical sensor CIDR",
        )
        pull = _require_success(
            await client.get(f"/api/v1/admin/network/policies/{pull['id']}"),
            "reload pull policy",
        ).json()
        pull = _require_success(
            await client.put(
                f"/api/v1/admin/network/policies/{pull['id']}",
                headers=csrf_header,
                json={
                    "revision": pull["revision"],
                    "mode": "allow_listed_private",
                    "reason": "Digest-pinned deployment acceptance",
                },
            ),
            "activate listed private sensor network",
        ).json()
        for address, expected in (
            ("192.168.50.99", True),
            ("192.168.60.99", False),
        ):
            result = _require_success(
                await client.post(
                    "/api/v1/admin/network/test-address",
                    headers=csrf_header,
                    json={"policy_id": pull["id"], "address": address},
                ),
                f"evaluate sensor address {address}",
            ).json()
            if result.get("allowed") is not expected:
                raise WorkflowFailure(
                    f"sensor address {address} did not match the explicit policy"
                )

        expected_readings = 0
        for index in range(device_count):
            enrollment = _require_success(
                await client.post(
                    "/api/v1/enrollment-tokens",
                    headers=csrf_header,
                    json={
                        "site_id": site["id"],
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
        history = _require_success(
            await client.post(
                "/api/v1/history/query",
                headers=csrf_header,
                json={
                    "scope": {"type": "device", "device_id": devices[0]["id"]},
                    "display_mode": "combined",
                    "metrics": ["power_w", "energy_kwh"],
                    "start_utc": (datetime.now(UTC) - timedelta(hours=8)).isoformat(),
                    "end_utc": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
                    "bucket": "1h",
                    "timezone": site["timezone"],
                },
            ),
            "query durable device History",
        ).json()
        history_energy = history.get("summary", {}).get("energy_kwh")
        if (
            not history.get("combined")
            or history_energy is None
            or Decimal(str(history_energy)) <= 0
        ):
            raise WorkflowFailure(
                "durable simulated readings did not appear in History"
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
        replacement_backup_id = await replace_all_backups(client, csrf_header)
        return (
            device_count,
            expected_readings,
            len(utility_accounts),
            1,
            replacement_backup_id,
        )


def backup_and_restore(
    compose: Path,
    expected_devices: int,
    expected_readings: int,
    expected_accounts: int,
    expected_cidrs: int,
    backup_run_id: str,
) -> None:
    try:
        UUID(backup_run_id)
    except ValueError as exc:
        raise WorkflowFailure("replacement backup has an invalid UUID") from exc
    restore_database = "power_monitor_integration_restore"
    run_compose(
        compose,
        "run",
        "--rm",
        "backup",
        "/srv/scripts/restore-container.sh",
        backup_run_id,
        restore_database,
        "--yes",
    )
    query = (
        "SELECT (SELECT count(*) FROM devices),"
        "(SELECT count(*) FROM device_heartbeats),"
        "(SELECT count(*) FROM raw_readings),"
        "(SELECT count(*) FROM normalized_intervals),"
        "(SELECT count(*) FROM alembic_version),"
        "(SELECT count(*) FROM utility_accounts),"
        "(SELECT count(*) FROM sensor_network_cidrs);"
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
    devices, heartbeats, readings, normalized, revisions, accounts, cidrs = (
        int(value) for value in restored.split("|")
    )
    if devices != expected_devices or heartbeats < expected_devices:
        raise WorkflowFailure(
            "restored database is missing enrolled devices or heartbeats"
        )
    if (
        readings != expected_readings
        or normalized != expected_readings
        or revisions != 1
    ):
        raise WorkflowFailure(
            "restored database is missing raw/normalized History or migration state"
        )
    if accounts != expected_accounts or cidrs != expected_cidrs:
        raise WorkflowFailure(
            "restored database is missing utility accounts or network policy rules"
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
    parser.add_argument(
        "--docker-desktop-project-name",
        help="test-only isolated Compose project name",
    )
    parser.add_argument(
        "--docker-desktop-local-application-images",
        action="store_true",
        help="test-only use of same-version locally built application images",
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
                compose,
                args.docker_desktop_host_root,
                project_name=args.docker_desktop_project_name,
                local_application_images=args.docker_desktop_local_application_images,
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
        devices, readings, accounts, cidrs, backup_run_id = asyncio.run(
            exercise_application(
                base_url=args.base_url,
                ca_certificate=args.ca_certificate.resolve(),
                setup_token_file=args.setup_token_file.resolve(),
                device_count=args.device_count,
            )
        )
        backup_and_restore(
            compose,
            devices,
            readings,
            accounts,
            cidrs,
            backup_run_id,
        )
        succeeded = True
        print(
            f"TrueNAS workflow passed: services=7 devices={devices} readings={readings} "
            f"utility_accounts={accounts} network_cidrs={cidrs} "
            "backup=replace-all-verified restore=verified ports=verified"
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
