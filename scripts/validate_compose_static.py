from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    document: dict[str, Any] = yaml.safe_load(
        (ROOT / "compose.yaml").read_text(encoding="utf-8")
    )
    services = document.get("services", {})
    required = {"postgres", "api", "worker", "frontend", "caddy", "backup"}
    if set(services) != required:
        raise AssertionError(f"unexpected services: {sorted(services)}")
    for name in ("postgres", "api", "worker", "frontend", "caddy"):
        if "healthcheck" not in services[name]:
            raise AssertionError(f"{name} has no healthcheck")
    for name in ("api", "worker", "frontend", "caddy", "backup"):
        service = services[name]
        if service.get("read_only") is not True:
            raise AssertionError(f"{name} must have a read-only root filesystem")
        if service.get("cap_drop") != ["ALL"]:
            raise AssertionError(f"{name} must drop Linux capabilities")
        if "no-new-privileges:true" not in service.get("security_opt", []):
            raise AssertionError(f"{name} must set no-new-privileges")
    for name, service in services.items():
        image = str(service.get("image", ""))
        if image.endswith(":latest") or (image and ":" not in image):
            raise AssertionError(f"{name} image is not version-pinned")
        build = service.get("build")
        if build and not (ROOT / str(build["dockerfile"])).is_file():
            raise AssertionError(f"{name} Dockerfile is missing")
    networks = document.get("networks", {})
    if networks.get("database", {}).get("internal") is not True:
        raise AssertionError("database network must be internal")
    if "device_access" not in services["worker"].get("networks", []):
        raise AssertionError("worker requires the isolated device-access network")
    if "device_access" in services["api"].get("networks", []):
        raise AssertionError("API must not have direct device-network access")
    print("Compose topology, build paths, healthchecks, pins, and hardening are valid")


if __name__ == "__main__":
    main()
