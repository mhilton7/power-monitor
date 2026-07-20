from __future__ import annotations

from typing import Any

import httpx
import pytest


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


@pytest.mark.asyncio
async def test_admin_can_create_and_deactivate_user(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "admin@example.com",
            "display_name": "Admin",
            "password": "Long-Production-Password-42!",
        },
    )
    assert bootstrap.status_code == 201

    created = await client.post(
        "/api/v1/users",
        headers=csrf(client),
        json={
            "email": "viewer@example.com",
            "display_name": "Viewer",
            "password": "Another-Production-Password-42!",
            "roles": ["viewer"],
        },
    )
    assert created.status_code == 201, created.text

    removed = await client.delete(f"/api/v1/users/{created.json()['id']}", headers=csrf(client))
    assert removed.status_code == 204, removed.text
    users = (await client.get("/api/v1/users")).json()
    target = next(user for user in users if user["id"] == created.json()["id"])
    assert target["is_active"] is False


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_self(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "admin@example.com",
            "display_name": "Admin",
            "password": "Long-Production-Password-42!",
        },
    )
    admin_id = bootstrap.json()["user"]["id"]
    removed = await client.delete(f"/api/v1/users/{admin_id}", headers=csrf(client))
    assert removed.status_code == 409
    assert removed.json()["code"] == "self_deactivation_forbidden"
