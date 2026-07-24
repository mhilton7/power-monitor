from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.main import app


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    token = client.cookies.get("pm_csrf")
    assert token
    return {"X-CSRF-Token": token}


async def bootstrap(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "admin@example.com",
            "display_name": "Bootstrap Administrator",
            "password": "Production-Admin-Password-42!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_viewer(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/users",
        headers=csrf(client),
        json={
            "email": "viewer@example.com",
            "display_name": "Lifecycle Viewer",
            "password": "Production-Viewer-Password-42!",
            "roles": ["viewer"],
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.mark.asyncio
async def test_disable_is_reversible_and_distinct_from_remove(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap(api_client)
    user_id = await create_viewer(api_client)
    before = (await api_client.get(f"/api/v1/admin/users/{user_id}")).json()

    disabled = await api_client.post(
        f"/api/v1/admin/users/{user_id}/disable",
        headers=csrf(api_client),
        json={
            "reason": "Temporary leave",
            "expected_revision": before["access_revision"],
        },
    )
    assert disabled.status_code == 200, disabled.text
    disabled_user = disabled.json()["user"]
    assert disabled_user["status"] == "disabled"
    assert disabled_user["roles"] == ["viewer"]
    assert disabled_user["all_sites"] is True
    assert disabled_user["removed_at"] is None

    denied_login = await api_client.post(
        "/api/v1/auth/login",
        json={
            "email": "viewer@example.com",
            "password": "Production-Viewer-Password-42!",
        },
    )
    assert denied_login.status_code == 401

    enabled = await api_client.post(
        f"/api/v1/admin/users/{user_id}/enable",
        headers=csrf(api_client),
        json={
            "reason": "Returned",
            "expected_revision": disabled_user["access_revision"],
        },
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["user"]["status"] == "active"
    assert enabled.json()["user"]["roles"] == ["viewer"]


@pytest.mark.asyncio
async def test_remove_revokes_access_preserves_identity_and_restores_unassigned(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap(api_client)
    user_id = await create_viewer(api_client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as viewer_client:
        login = await viewer_client.post(
            "/api/v1/auth/login",
            json={
                "email": "viewer@example.com",
                "password": "Production-Viewer-Password-42!",
            },
        )
        assert login.status_code == 200

        before = (await api_client.get(f"/api/v1/admin/users/{user_id}")).json()
        mismatch = await api_client.post(
            f"/api/v1/admin/users/{user_id}/remove",
            headers=csrf(api_client),
            json={
                "reason": "Contract ended",
                "confirmation": "wrong@example.com",
                "expected_revision": before["access_revision"],
                "confirm_high_risk": True,
            },
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["code"] == "removal_confirmation_mismatch"

        not_reauthenticated = await api_client.post(
            f"/api/v1/admin/users/{user_id}/remove",
            headers=csrf(api_client),
            json={
                "reason": "Contract ended",
                "confirmation": "viewer@example.com",
                "expected_revision": before["access_revision"],
                "confirm_high_risk": True,
            },
        )
        assert not_reauthenticated.status_code == 428
        assert (
            await api_client.post(
                "/api/v1/auth/reauthenticate",
                headers=csrf(api_client),
                json={"password": "Production-Admin-Password-42!"},
            )
        ).status_code == 200

        removed = await api_client.post(
            f"/api/v1/admin/users/{user_id}/remove",
            headers=csrf(api_client),
            json={
                "reason": "Contract ended",
                "confirmation": "viewer@example.com",
                "expected_revision": before["access_revision"],
                "confirm_high_risk": True,
            },
        )
        assert removed.status_code == 200, removed.text
        removed_user = removed.json()["user"]
        assert removed.json()["sessions_revoked"] == 1
        assert removed_user["status"] == "removed"
        assert removed_user["roles"] == []
        assert removed_user["site_ids"] == []
        assert removed_user["all_sites"] is False
        assert removed_user["former_access"] == {
            "roles": ["viewer"],
            "all_sites": True,
            "site_ids": [],
        }
        assert (await viewer_client.get("/api/v1/auth/session")).json()["authenticated"] is False

    active_list = (await api_client.get("/api/v1/admin/users")).json()["users"]
    assert user_id not in {item["id"] for item in active_list}
    removed_list = (
        await api_client.get("/api/v1/admin/users", params={"status": "removed"})
    ).json()["users"]
    assert [item["id"] for item in removed_list] == [user_id]

    repeated = await api_client.post(
        f"/api/v1/admin/users/{user_id}/remove",
        headers=csrf(api_client),
        json={
            "reason": "Repeated request",
            "confirmation": "viewer@example.com",
            "expected_revision": before["access_revision"],
            "confirm_high_risk": True,
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["changed"] is False

    duplicate = await api_client.post(
        "/api/v1/users",
        headers=csrf(api_client),
        json={
            "email": "viewer@example.com",
            "display_name": "Duplicate Viewer",
            "password": "Production-Viewer-Password-84!",
            "roles": ["viewer"],
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "user_removed_restore_required"

    restore_without_confirmation = await api_client.post(
        f"/api/v1/admin/users/{user_id}/restore",
        headers=csrf(api_client),
        json={
            "reason": "Returning contractor",
            "expected_revision": removed_user["access_revision"],
        },
    )
    assert restore_without_confirmation.status_code == 409
    assert restore_without_confirmation.json()["code"] == "high_risk_confirmation_required"

    restored = await api_client.post(
        f"/api/v1/admin/users/{user_id}/restore",
        headers=csrf(api_client),
        json={
            "reason": "Returning contractor",
            "expected_revision": removed_user["access_revision"],
            "confirm_high_risk": True,
        },
    )
    assert restored.status_code == 200, restored.text
    restored_user = restored.json()["user"]
    assert restored_user["status"] == "disabled"
    assert restored_user["roles"] == []
    assert restored_user["site_ids"] == []
    assert restored_user["is_active"] is False

    no_access_enable = await api_client.post(
        f"/api/v1/admin/users/{user_id}/enable",
        headers=csrf(api_client),
        json={"expected_revision": restored_user["access_revision"]},
    )
    assert no_access_enable.status_code == 409
    assert no_access_enable.json()["code"] == "user_access_required"

    history = await api_client.get(f"/api/v1/admin/users/{user_id}/access-history")
    actions = {event["action"] for event in history.json()["events"]}
    assert {"user.removed", "user.restored"} <= actions


@pytest.mark.asyncio
async def test_protected_bootstrap_and_self_removal_are_rejected(
    api_client: httpx.AsyncClient,
) -> None:
    admin = await bootstrap(api_client)
    admin_id = str(admin["user"]["id"])
    detail = (await api_client.get(f"/api/v1/admin/users/{admin_id}")).json()
    assert detail["protected_account"] is True

    response = await api_client.post(
        f"/api/v1/admin/users/{admin_id}/remove",
        headers=csrf(api_client),
        json={
            "reason": "Unsafe request",
            "confirmation": "admin@example.com",
            "expected_revision": detail["access_revision"],
            "confirm_high_risk": True,
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "self_removal_forbidden"
