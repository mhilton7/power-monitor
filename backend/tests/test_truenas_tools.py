from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_tool(filename: str, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "tools" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_truenas_template_and_icmp_overlay_are_valid() -> None:
    validator = _load_tool("validate-truenas-compose.py", "truenas_validator")
    compose = yaml.safe_load((ROOT / "deploy/truenas/compose.yaml").read_text(encoding="utf-8"))
    overlay = yaml.safe_load(
        (ROOT / "deploy/truenas/compose-icmp.yaml").read_text(encoding="utf-8")
    )

    assert (
        validator.validate_compose(compose, deployment=False, expected_pool=None, gateway_port=8443)
        == []
    )
    assert validator.validate_icmp_overlay(overlay) == []


def test_deployment_validation_rejects_fail_closed_placeholders() -> None:
    validator = _load_tool("validate-truenas-compose.py", "truenas_validator_deployment")
    compose = yaml.safe_load((ROOT / "deploy/truenas/compose.yaml").read_text(encoding="utf-8"))
    compose["services"]["api"]["image"] = compose["services"]["api"]["image"].replace(
        "mhilton7", "REPLACE_WITH_GHCR_OWNER"
    )

    errors = validator.validate_compose(
        compose, deployment=True, expected_pool="Apps", gateway_port=8443
    )

    assert any("placeholder digest" in error for error in errors)
    assert any("POOL placeholder" in error for error in errors)
    assert any("registry owner placeholder" in error for error in errors)


def test_secret_generator_produces_high_entropy_file_inventory(tmp_path: Path) -> None:
    generator = _load_tool("generate-secrets.py", "truenas_secret_generator")
    output = tmp_path / "secrets"

    created = generator.generate_secret_files(output, permit_worktree=True)

    assert {path.name for path in created} == set(generator.SECRET_NAMES)
    assert len((output / "postgres_password").read_text(encoding="utf-8").strip()) >= 64
    assert len((output / "app_master_key").read_text(encoding="utf-8").strip()) == 44
    assert (output / "app_master_key").read_bytes().endswith(b"=\n")
    assert b"\r" not in (output / "app_master_key").read_bytes()
    assert "@postgres:5432/power_monitor" in (output / "database_url").read_text(encoding="utf-8")
    assert (output / "tls.crt").read_bytes() == b""
    assert (output / "tls.key").read_bytes() == b""


def test_renderer_creates_a_deployment_valid_document() -> None:
    renderer = _load_tool("render-truenas-compose.py", "truenas_renderer")
    validator = _load_tool("validate-truenas-compose.py", "rendered_truenas_validator")
    template = yaml.safe_load((ROOT / "deploy/truenas/compose.yaml").read_text(encoding="utf-8"))
    digest = "1" * 64
    rendered = renderer.render(
        template,
        pool="Apps",
        gateway_port=9443,
        site_address="https://127.0.0.1",
        public_origin="https://127.0.0.1:9443",
        images={
            "api": f"ghcr.io/example/power-monitor-api:1.0.0@sha256:{digest}",
            "frontend": f"ghcr.io/example/power-monitor-frontend:1.0.0@sha256:{digest}",
            "backup": f"ghcr.io/example/power-monitor-backup:1.0.0@sha256:{digest}",
            "postgres": f"docker.io/library/postgres:17.5-bookworm@sha256:{digest}",
            "gateway": f"docker.io/library/caddy:2.10.0-alpine@sha256:{digest}",
        },
    )

    expected_root = "/mnt/Apps/Power/power-monitor/"
    host_paths = [
        volume.split(":", 1)[0]
        for service in rendered["services"].values()
        for volume in service.get("volumes", [])
    ] + [secret["file"] for secret in rendered["secrets"].values()]
    assert host_paths
    assert all(path.startswith(expected_root) for path in host_paths)

    assert (
        validator.validate_compose(
            rendered, deployment=True, expected_pool="Apps", gateway_port=9443
        )
        == []
    )
