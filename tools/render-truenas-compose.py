#!/usr/bin/env python3
"""Render the fail-closed TrueNAS template into a deployment-specific Compose file."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

POOL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
DATASET_RELATIVE_ROOT = "Power/power-monitor"


def _image_tag(image: str) -> str:
    reference = image.split("@", 1)[0]
    last_slash = reference.rfind("/")
    last_colon = reference.rfind(":")
    if last_colon <= last_slash:
        raise ValueError(f"application image has no version tag: {image}")
    return reference[last_colon + 1 :]


def _load_validator(root: Path) -> ModuleType:
    path = root / "tools" / "validate-truenas-compose.py"
    spec = importlib.util.spec_from_file_location(
        "power_monitor_truenas_validator", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load validator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(
    template: dict[str, Any],
    *,
    pool: str,
    gateway_port: int,
    site_address: str,
    public_origin: str,
    images: dict[str, str],
    enable_icmp: bool = False,
) -> dict[str, Any]:
    if not POOL_PATTERN.fullmatch(pool) or pool == "POOL":
        raise ValueError("pool must be a real TrueNAS pool name")
    if not 1 <= gateway_port <= 65535:
        raise ValueError("gateway port must be between 1 and 65535")
    services = template["services"]
    for service_name in ("api", "worker", "migrate"):
        services[service_name]["image"] = images["api"]
    for service_name in ("frontend", "backup", "postgres", "gateway"):
        services[service_name]["image"] = images[service_name]
    release_version = _image_tag(images["api"])
    for service_name in ("api", "worker"):
        services[service_name]["environment"]["POWER_MONITOR_VERSION"] = release_version
    template_root = f"/mnt/POOL/{DATASET_RELATIVE_ROOT}/"
    deployment_root = f"/mnt/{pool}/{DATASET_RELATIVE_ROOT}/"
    for service in services.values():
        service["volumes"] = [
            volume.replace(template_root, deployment_root)
            for volume in service.get("volumes", [])
        ]
    for definition in template["secrets"].values():
        definition["file"] = definition["file"].replace(template_root, deployment_root)
    services["gateway"]["ports"] = [f"{gateway_port}:443/tcp"]
    services["gateway"]["environment"]["POWER_MONITOR_SITE_ADDRESS"] = site_address
    services["api"]["environment"]["PUBLIC_ORIGIN"] = public_origin
    services["worker"]["environment"]["PUBLIC_ORIGIN"] = public_origin
    if enable_icmp:
        services["worker"]["cap_add"] = ["NET_RAW"]
    return template


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--template", type=Path, default=Path("deploy/truenas/compose.yaml")
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pool", required=True)
    parser.add_argument("--gateway-port", type=int, default=8443)
    parser.add_argument(
        "--site-address", required=True, help="Caddy HTTPS site address"
    )
    parser.add_argument(
        "--public-origin", required=True, help="browser origin including host port"
    )
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--backup-image", required=True)
    parser.add_argument("--postgres-image", required=True)
    parser.add_argument("--gateway-image", required=True)
    parser.add_argument(
        "--enable-icmp",
        action="store_true",
        help="apply the validated compose-icmp.yaml NET_RAW-only worker overlay",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.output.exists():
        print(f"render failed: output already exists: {args.output}", file=sys.stderr)
        return 1
    try:
        template = yaml.safe_load(args.template.read_text(encoding="utf-8"))
        rendered = render(
            template,
            pool=args.pool,
            gateway_port=args.gateway_port,
            site_address=args.site_address,
            public_origin=args.public_origin,
            enable_icmp=args.enable_icmp,
            images={
                "api": args.api_image,
                "frontend": args.frontend_image,
                "backup": args.backup_image,
                "postgres": args.postgres_image,
                "gateway": args.gateway_image,
            },
        )
        validator = _load_validator(root)
        errors = validator.validate_compose(
            rendered,
            deployment=True,
            expected_pool=args.pool,
            gateway_port=args.gateway_port,
            allow_worker_net_raw=args.enable_icmp,
        )
        if errors:
            raise ValueError("; ".join(errors))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            yaml.safe_dump(rendered, sort_keys=False), encoding="utf-8"
        )
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f"render failed: {exc}", file=sys.stderr)
        return 1
    print(f"rendered and deployment-validated: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
