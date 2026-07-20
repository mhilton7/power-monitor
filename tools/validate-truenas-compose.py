#!/usr/bin/env python3
"""Fail-closed structural validator for the TrueNAS production Compose template."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REQUIRED_SERVICES = {
    "gateway",
    "frontend",
    "api",
    "worker",
    "migrate",
    "postgres",
    "backup",
}
REQUIRED_SECRETS = {
    "postgres_password",
    "database_url",
    "app_master_key",
    "session_pepper",
    "admin_setup_token",
    "backup_encryption_key",
    "tls_certificate",
    "tls_private_key",
}
EXPECTED_SERVICE_SECRETS = {
    "postgres": {"postgres_password"},
    "migrate": {"database_url"},
    "api": {"database_url", "app_master_key", "session_pepper", "admin_setup_token"},
    "worker": {"database_url", "app_master_key"},
    "frontend": set(),
    "gateway": {"tls_certificate", "tls_private_key"},
    "backup": {"postgres_password", "backup_encryption_key"},
}
EXPECTED_IDENTITIES = {
    "gateway": "10002:10002",
    "frontend": "101:101",
    "api": "10001:10001",
    "worker": "10001:10001",
    "migrate": "10001:10001",
    "postgres": "999:999",
    "backup": "10003:10003",
}
REQUIRED_DATASET_SUFFIXES = {
    "postgres",
    "backups",
    "firmware",
    "logs",
    "config",
    "caddy-data",
    "caddy-config",
    "rate-source-artifacts",
}
IMAGE_PATTERN = re.compile(
    r"^[a-z0-9.-]+(?::[0-9]+)?/[A-Za-z0-9._/-]+:(?!latest(?:@|$))[^@]+@sha256:([0-9a-f]{64})$"
)
OFFICIAL_IMAGE_PATTERN = re.compile(
    r"^[a-z0-9._/-]+:(?!latest(?:@|$))[^@]+@sha256:([0-9a-f]{64})$"
)
DATASET_PATTERN = re.compile(r"^/mnt/([^/]+)/Power/power-monitor/([^:]+)")
SENSITIVE_ENVIRONMENT = re.compile(
    r"(?:PASSWORD|SECRET|PEPPER|MASTER_KEY|SETUP_TOKEN)$"
)


def _mapping(value: Any, context: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{context} must be a mapping")
        return {}
    return value


def _sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _host_port(entry: Any) -> tuple[int | None, int | None]:
    if isinstance(entry, int):
        return None, entry
    if isinstance(entry, str):
        clean = entry.split("/", 1)[0]
        parts = clean.rsplit(":", 2)
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            return int(parts[0]), int(parts[1])
        if len(parts) == 3 and parts[-2].isdigit() and parts[-1].isdigit():
            return int(parts[-2]), int(parts[-1])
    if isinstance(entry, dict):
        published = entry.get("published")
        target = entry.get("target")
        return (int(published) if published is not None else None, int(target))
    return None, None


def validate_compose(
    document: Any,
    *,
    deployment: bool,
    expected_pool: str | None,
    gateway_port: int,
    allow_worker_net_raw: bool = False,
) -> list[str]:
    errors: list[str] = []
    root = _mapping(document, "Compose document", errors)
    services = _mapping(root.get("services"), "services", errors)
    missing = REQUIRED_SERVICES - services.keys()
    extra = services.keys() - REQUIRED_SERVICES
    if missing:
        errors.append(f"missing required services: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"unexpected production services: {', '.join(sorted(extra))}")

    networks = _mapping(root.get("networks"), "networks", errors)
    if set(networks) != {"public", "database"}:
        errors.append("networks must be exactly public and database")
    if not _mapping(networks.get("database"), "database network", errors).get(
        "internal"
    ):
        errors.append("database network must be marked internal")
    if _mapping(networks.get("public"), "public network", errors).get("internal"):
        errors.append("public network must permit normal bridge egress")

    discovered_suffixes: set[str] = set()
    discovered_pools: set[str] = set()
    zero_digest = "0" * 64
    for service_name in sorted(REQUIRED_SERVICES & services.keys()):
        service = _mapping(services[service_name], f"service {service_name}", errors)
        if "build" in service:
            errors.append(f"{service_name}: build directives are forbidden")
        image = service.get("image")
        if not isinstance(image, str):
            errors.append(f"{service_name}: image must be a string")
        else:
            match = IMAGE_PATTERN.fullmatch(image) or OFFICIAL_IMAGE_PATTERN.fullmatch(
                image
            )
            if not match:
                errors.append(
                    f"{service_name}: image must have a version tag and sha256 digest"
                )
            elif deployment and match.group(1) == zero_digest:
                errors.append(
                    f"{service_name}: all-zero placeholder digest is forbidden in deployment mode"
                )
            if ":latest" in image.lower():
                errors.append(f"{service_name}: latest tags are forbidden")
            if deployment and "REPLACE_" in image:
                errors.append(
                    f"{service_name}: registry owner placeholder is unresolved"
                )
        if service.get("platform") != "linux/amd64":
            errors.append(f"{service_name}: platform must be linux/amd64")
        if service.get("user") != EXPECTED_IDENTITIES[service_name]:
            errors.append(
                f"{service_name}: user must be numeric {EXPECTED_IDENTITIES[service_name]}"
            )
        if service.get("privileged"):
            errors.append(f"{service_name}: privileged mode is forbidden")
        if service.get("network_mode") == "host":
            errors.append(f"{service_name}: host networking is forbidden")
        if service.get("read_only") is not True:
            errors.append(f"{service_name}: root filesystem must be read-only")
        if "ALL" not in _sequence(service.get("cap_drop")):
            errors.append(f"{service_name}: cap_drop must include ALL")
        if "no-new-privileges:true" not in _sequence(service.get("security_opt")):
            errors.append(f"{service_name}: no-new-privileges is required")
        added = set(_sequence(service.get("cap_add")))
        expected_added = {"NET_BIND_SERVICE"} if service_name == "gateway" else set()
        if service_name == "worker" and allow_worker_net_raw:
            expected_added = {"NET_RAW"}
        if added != expected_added:
            errors.append(
                f"{service_name}: unexpected added capabilities {sorted(added)}"
            )
        if "restart" not in service:
            errors.append(f"{service_name}: restart policy is required")
        elif service_name == "migrate" and str(service.get("restart")).lower() != "no":
            errors.append("migrate: restart policy must be no")
        elif service_name != "migrate" and service.get("restart") != "unless-stopped":
            errors.append(f"{service_name}: restart policy must be unless-stopped")
        if service_name != "migrate" and not service.get("healthcheck"):
            errors.append(f"{service_name}: health check is required")
        if not service.get("tmpfs"):
            errors.append(f"{service_name}: at least one tmpfs mount is required")
        elif any(
            not str(item).startswith("/") for item in _sequence(service.get("tmpfs"))
        ):
            errors.append(
                f"{service_name}: every tmpfs entry must begin with an absolute path"
            )
        resource_limits = (
            _mapping(service.get("deploy"), f"{service_name}.deploy", errors)
            .get("resources", {})
            .get("limits", {})
        )
        if not resource_limits:
            errors.append(f"{service_name}: resource limits are required")
        elif not resource_limits.get("pids"):
            errors.append(f"{service_name}: PID resource limit is required")

        ports = _sequence(service.get("ports"))
        if service_name != "gateway" and ports:
            errors.append(f"{service_name}: only gateway may publish ports")
        for port in ports:
            published, target = _host_port(port)
            if service_name == "gateway" and (published, target) != (gateway_port, 443):
                errors.append(
                    f"gateway: expected only {gateway_port}:443/tcp, got {published}:{target}"
                )
        if service_name == "gateway" and len(ports) != 1:
            errors.append("gateway must publish exactly one port")

        environment = _mapping(
            service.get("environment", {}), f"{service_name}.environment", errors
        )
        service_secrets = set(_sequence(service.get("secrets")))
        if service_secrets != EXPECTED_SERVICE_SECRETS[service_name]:
            errors.append(
                f"{service_name}: secret mounts must be exactly "
                f"{sorted(EXPECTED_SERVICE_SECRETS[service_name])}"
            )
        for key, value in environment.items():
            if SENSITIVE_ENVIRONMENT.search(str(key)) and not str(key).endswith(
                "_FILE"
            ):
                errors.append(
                    f"{service_name}: sensitive environment variable {key} must use *_FILE"
                )
            if isinstance(value, str) and "CHANGE_ME" in value:
                errors.append(
                    f"{service_name}: unresolved environment placeholder in {key}"
                )
            if str(key).endswith("_FILE") and isinstance(value, str):
                mounted_name = value.removeprefix("/run/secrets/")
                if value == mounted_name or mounted_name not in service_secrets:
                    errors.append(
                        f"{service_name}: {key} must point to a mounted /run/secrets file"
                    )

        for volume in _sequence(service.get("volumes")):
            if not isinstance(volume, str):
                errors.append(
                    f"{service_name}: volumes must use auditable short bind syntax"
                )
                continue
            source = volume.split(":", 1)[0]
            if source.startswith("/mnt/"):
                match = DATASET_PATTERN.match(source)
                if not match:
                    errors.append(
                        f"{service_name}: invalid TrueNAS dataset path {source}"
                    )
                    continue
                pool, suffix = match.groups()
                discovered_pools.add(pool)
                discovered_suffixes.add(suffix.split("/", 1)[0])
                if deployment and pool == "POOL":
                    errors.append(f"{service_name}: POOL placeholder is unresolved")
                if expected_pool is not None and pool != expected_pool:
                    errors.append(
                        f"{service_name}: dataset pool {pool!r} does not match {expected_pool!r}"
                    )

    if REQUIRED_DATASET_SUFFIXES - discovered_suffixes:
        errors.append(
            "missing required dataset roots: "
            + ", ".join(sorted(REQUIRED_DATASET_SUFFIXES - discovered_suffixes))
        )
    if len(discovered_pools) != 1:
        errors.append(
            f"all bind mounts must use one pool; found {sorted(discovered_pools)}"
        )
    if deployment and expected_pool is None:
        errors.append(
            "deployment mode requires --pool to confirm the rendered pool name"
        )
    if expected_pool == "POOL":
        errors.append("--pool must be a real TrueNAS pool name, not POOL")

    postgres_networks = set(
        _sequence(
            _mapping(services.get("postgres"), "postgres", errors).get("networks")
        )
    )
    if postgres_networks != {"database"}:
        errors.append("postgres must attach only to the internal database network")
    for service_name in ("api", "worker"):
        attached = set(
            _sequence(
                _mapping(services.get(service_name), service_name, errors).get(
                    "networks"
                )
            )
        )
        if attached != {"public", "database"}:
            errors.append(f"{service_name} must attach to public and database networks")
    for service_name in ("frontend", "gateway"):
        attached = set(
            _sequence(
                _mapping(services.get(service_name), service_name, errors).get(
                    "networks"
                )
            )
        )
        if attached != {"public"}:
            errors.append(f"{service_name} must attach only to the public network")
    for service_name in ("migrate", "backup"):
        attached = set(
            _sequence(
                _mapping(services.get(service_name), service_name, errors).get(
                    "networks"
                )
            )
        )
        if attached != {"database"}:
            errors.append(f"{service_name} must attach only to the database network")

    migrate_depends = _mapping(
        _mapping(services.get("migrate"), "migrate", errors).get("depends_on"),
        "migrate.depends_on",
        errors,
    )
    if (
        _mapping(
            migrate_depends.get("postgres"), "migrate postgres dependency", errors
        ).get("condition")
        != "service_healthy"
    ):
        errors.append("migrate must wait for healthy postgres")
    for service_name in ("api", "worker", "backup"):
        depends = _mapping(
            _mapping(services.get(service_name), service_name, errors).get(
                "depends_on"
            ),
            f"{service_name}.depends_on",
            errors,
        )
        migrate_condition = _mapping(
            depends.get("migrate"), f"{service_name} migration dependency", errors
        ).get("condition")
        if migrate_condition != "service_completed_successfully":
            errors.append(f"{service_name} must wait for successful migration")

    top_secrets = _mapping(root.get("secrets"), "secrets", errors)
    if set(top_secrets) != REQUIRED_SECRETS:
        errors.append(
            "top-level secrets inventory does not match the required file-backed set"
        )
    for name, definition in top_secrets.items():
        path = _mapping(definition, f"secret {name}", errors).get("file")
        template_secret = isinstance(path, str) and path.startswith(
            "/mnt/POOL/Power/power-monitor/secrets/"
        )
        deployed_secret = (
            deployment and isinstance(path, str) and bool(DATASET_PATTERN.match(path))
        )
        if not template_secret and not deployed_secret:
            errors.append(
                f"secret {name}: file must be under the absolute secrets dataset"
            )
    return errors


def validate_icmp_overlay(document: Any) -> list[str]:
    errors: list[str] = []
    root = _mapping(document, "ICMP overlay", errors)
    if set(root) != {"services"}:
        errors.append("ICMP overlay may contain only services")
    services = _mapping(root.get("services"), "ICMP overlay services", errors)
    if set(services) != {"worker"}:
        errors.append("ICMP overlay may modify only worker")
    worker = _mapping(services.get("worker"), "ICMP overlay worker", errors)
    if set(worker) != {"cap_add"} or _sequence(worker.get("cap_add")) != ["NET_RAW"]:
        errors.append("ICMP overlay must add only NET_RAW to worker")
    return errors


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "compose", nargs="?", type=Path, default=Path("deploy/truenas/compose.yaml")
    )
    parser.add_argument(
        "--icmp-overlay", type=Path, default=Path("deploy/truenas/compose-icmp.yaml")
    )
    parser.add_argument(
        "--deployment", action="store_true", help="reject every checked-in placeholder"
    )
    parser.add_argument(
        "--pool", help="expected TrueNAS pool name (required with --deployment)"
    )
    parser.add_argument("--gateway-port", type=int, default=8443)
    parser.add_argument(
        "--icmp-enabled",
        action="store_true",
        help="allow the separately validated overlay's NET_RAW worker capability",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        errors = validate_compose(
            _load_yaml(args.compose),
            deployment=args.deployment,
            expected_pool=args.pool,
            gateway_port=args.gateway_port,
            allow_worker_net_raw=args.icmp_enabled,
        )
        errors.extend(validate_icmp_overlay(_load_yaml(args.icmp_overlay)))
    except (OSError, yaml.YAMLError) as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(
            f"TrueNAS Compose validation failed with {len(errors)} error(s)",
            file=sys.stderr,
        )
        return 1
    mode = "deployment" if args.deployment else "template"
    print(f"TrueNAS Compose validation passed ({mode} mode): {args.compose}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
