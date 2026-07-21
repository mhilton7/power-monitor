#!/usr/bin/env python3
"""Exercise Status Indicators & Layout through a deployed HTTPS gateway."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx


class WorkflowFailure(RuntimeError):
    """A deployed status-layout acceptance check failed."""


def require(response: httpx.Response, operation: str) -> httpx.Response:
    if response.is_error:
        detail = response.text[:800].replace("\n", " ")
        raise WorkflowFailure(
            f"{operation} failed with HTTP {response.status_code}: {detail}"
        )
    return response


def global_item(configuration: dict[str, Any], indicator_key: str) -> dict[str, Any]:
    try:
        return next(
            item
            for item in configuration["items"]
            if item["indicator_key"] == indicator_key
            and item["page"] == "*"
            and item["role"] == "*"
            and item["breakpoint"] == "default"
        )
    except (KeyError, StopIteration) as exc:
        raise WorkflowFailure(f"compiled layout omitted {indicator_key}") from exc


async def exercise(args: argparse.Namespace) -> None:
    setup_token = args.setup_token_file.read_text(encoding="utf-8").strip()
    if not setup_token:
        raise WorkflowFailure("administrator setup token file is empty")
    async with httpx.AsyncClient(
        base_url=args.base_url,
        verify=str(args.ca_certificate),
        timeout=30,
    ) as client:
        ready = require(await client.get("/health/ready"), "readiness").json()
        if ready.get("checks", {}).get("migration") != "20260720_0006":
            raise WorkflowFailure(f"deployment is not at migration 0006: {ready}")
        bootstrap = require(
            await client.post(
                "/api/v1/auth/bootstrap",
                json={
                    "bootstrap_secret": setup_token,
                    "email": "status-layout-integration@example.com",
                    "display_name": "Status Layout Integration Administrator",
                    "password": "Status-Layout-Integration-Password-47!",
                },
            ),
            "bootstrap administrator",
        ).json()
        if not bootstrap.get("authenticated"):
            raise WorkflowFailure("bootstrap did not create an authenticated session")
        csrf = client.cookies.get("pm_csrf")
        if not csrf:
            raise WorkflowFailure("bootstrap did not establish a CSRF cookie")
        headers = {"X-CSRF-Token": csrf}

        registry = require(
            await client.get("/api/v1/status-indicators/registry"), "read registry"
        ).json()
        keys = [item["key"] for item in registry.get("indicators", [])]
        if len(keys) < 30 or len(keys) != len(set(keys)):
            raise WorkflowFailure(
                "deployed registry is incomplete or contains duplicates"
            )
        for required in (
            "alerts.active_count",
            "data.energy_today",
            "device.offline_count",
            "system.worker_health",
        ):
            if required not in keys:
                raise WorkflowFailure(f"deployed registry omitted {required}")

        draft = require(
            await client.get("/api/v1/admin/status-indicators/draft"), "read draft"
        ).json()
        configuration = draft["configuration"]
        global_item(configuration, "data.energy_today")["visible"] = False
        offline = global_item(configuration, "device.offline_count")
        offline["zone"] = "page_summary_strip"
        offline["order"] = 5
        saved = require(
            await client.put(
                "/api/v1/admin/status-indicators/draft",
                headers=headers,
                json={
                    "base_revision": draft["base_revision"],
                    "draft_revision": draft["draft_revision"],
                    "configuration": configuration,
                    "reason": "Deployed deterministic status-layout acceptance gate",
                },
            ),
            "save layout draft",
        ).json()
        require(
            await client.post(
                "/api/v1/admin/status-indicators/validate", headers=headers, json={}
            ),
            "validate layout draft",
        )
        empty_preview = require(
            await client.post(
                "/api/v1/admin/status-indicators/preview",
                headers=headers,
                json={
                    "page": "overview",
                    "role": "admin",
                    "breakpoint": "mobile",
                    "scenario": "empty_zone",
                },
            ),
            "preview empty-zone mobile layout",
        ).json()
        if any(not zone["items"] for zone in empty_preview["layout"]["zones"]):
            raise WorkflowFailure("empty preview emitted an empty semantic zone")
        require(
            await client.post(
                "/api/v1/admin/status-indicators/preview",
                headers=headers,
                json={
                    "page": "overview",
                    "role": "admin",
                    "breakpoint": "desktop",
                    "scenario": "all_defaults",
                },
            ),
            "preview current draft",
        )
        published = require(
            await client.post(
                "/api/v1/admin/status-indicators/publish",
                headers=headers,
                json={
                    "base_revision": saved["base_revision"],
                    "draft_revision": saved["draft_revision"],
                    "reason": "Publish deployed deterministic status-layout gate",
                    "confirm": True,
                    "confirm_critical": False,
                },
            ),
            "publish layout",
        ).json()
        if published["revision"] != 2:
            raise WorkflowFailure("clean deployment did not publish revision 2")

        resolved = require(
            await client.get(
                "/api/v1/status-indicators/layout?page=overview&breakpoint=desktop"
            ),
            "read published layout",
        ).json()
        rendered = {
            item["indicator_key"]: zone["key"]
            for zone in resolved["zones"]
            for item in zone["items"]
        }
        if "data.energy_today" in rendered:
            raise WorkflowFailure("disabled indicator remained rendered")
        if rendered.get("device.offline_count") != "page_summary_strip":
            raise WorkflowFailure(
                "moved indicator did not resolve to its published zone"
            )
        if any(not zone["items"] for zone in resolved["zones"]):
            raise WorkflowFailure("published layout emitted an empty semantic zone")

        revisions = require(
            await client.get("/api/v1/admin/status-indicators/revisions"),
            "list immutable revisions",
        ).json()["revisions"]
        revision_one = next(item for item in revisions if item["revision"] == 1)
        restored = require(
            await client.post(
                f"/api/v1/admin/status-indicators/revisions/{revision_one['id']}/restore",
                headers=headers,
                json={
                    "base_revision": 2,
                    "reason": "Restore compiled layout in deployed acceptance gate",
                    "confirm": True,
                    "confirm_critical": True,
                },
            ),
            "restore immutable revision",
        ).json()
        if (
            restored["revision"] != 3
            or restored["restored_from_id"] != revision_one["id"]
        ):
            raise WorkflowFailure("rollback did not create linked immutable revision 3")
        restored_layout = require(
            await client.get(
                "/api/v1/status-indicators/layout?page=overview&breakpoint=desktop"
            ),
            "read restored layout",
        ).json()
        restored_keys = {
            item["indicator_key"]
            for zone in restored_layout["zones"]
            for item in zone["items"]
        }
        if "data.energy_today" not in restored_keys:
            raise WorkflowFailure(
                "restored compiled layout did not re-enable Energy today"
            )

        actions = {
            item["action"]
            for item in require(
                await client.get("/api/v1/audit-events"), "read status-layout audit"
            ).json()
        }
        required_actions = {
            "status_layout.draft_saved",
            "status_layout.indicator_disabled",
            "status_layout.indicator_moved",
            "status_layout.draft_published",
            "status_layout.revision_restored",
        }
        if not required_actions <= actions:
            raise WorkflowFailure(
                f"audit log omitted actions: {sorted(required_actions - actions)}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ca-certificate", required=True, type=Path)
    parser.add_argument("--setup-token-file", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(exercise(args))
    except (OSError, ValueError, httpx.HTTPError, WorkflowFailure) as exc:
        print(f"Status-layout workflow failed: {exc}", file=sys.stderr)
        return 1
    print(
        "Status-layout workflow passed: registry>=30 draft=validated preview=responsive "
        "publish=revision-2 gap-free=verified rollback=revision-3 audit=verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
