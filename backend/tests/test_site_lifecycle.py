from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Device,
    DeviceSiteAssignment,
    Site,
    User,
    UserSite,
    Utility,
    UtilityAccount,
    UtilityAccountSiteAssignment,
)


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


async def bootstrap(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "admin@example.com",
            "display_name": "Admin",
            "password": "Long-Production-Password-42!",
        },
    )
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_site_lifecycle_is_soft_audited_and_selector_safe(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    await bootstrap(client)
    headers = csrf(client)

    initial_response = await client.get("/api/v1/admin/sites")
    assert initial_response.status_code == 200
    initial = initial_response.json()
    assert len(initial) == 1
    original = initial[0]
    assert original["is_default"] is True
    assert original["lifecycle_state"] == "active"

    created_response = await client.post(
        "/api/v1/admin/sites",
        headers=headers,
        json={
            "name": "Warehouse",
            "code": "warehouse",
            "description": "Secondary monitored building",
            "timezone": "America/Denver",
            "currency": "USD",
            "locale": "en-US",
            "unit_system": "imperial",
            "network_policy_mode": "explicit",
            "confirmation": True,
        },
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["is_default"] is False
    assert created["network_policies"]
    assert created["dependencies"]["retained"]["raw_readings"] == 0

    duplicate = await client.post(
        "/api/v1/admin/sites",
        headers=headers,
        json={
            "name": "Different warehouse name",
            "code": "warehouse",
            "timezone": "America/Los_Angeles",
            "currency": "USD",
            "locale": "en-US",
            "unit_system": "imperial",
            "network_policy_mode": "inherit",
            "confirmation": True,
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "site_identity_conflict"

    stale = await client.put(
        f"/api/v1/admin/sites/{created['id']}",
        headers=headers,
        json={
            "revision": created["revision"] + 1,
            "name": "Warehouse stale",
            "reason": "Test optimistic concurrency",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_site_revision"

    timezone_unconfirmed = await client.put(
        f"/api/v1/admin/sites/{created['id']}",
        headers=headers,
        json={
            "revision": created["revision"],
            "timezone": "America/Chicago",
            "timezone_change_confirmed": False,
            "reason": "Test timezone protection",
        },
    )
    assert timezone_unconfirmed.status_code == 409
    assert timezone_unconfirmed.json()["code"] == "site_timezone_confirmation_required"

    default_response = await client.post(
        f"/api/v1/admin/sites/{created['id']}/set-default",
        headers=headers,
        json={"revision": created["revision"], "reason": "Test default selection"},
    )
    assert default_response.status_code == 200, default_response.text
    created = default_response.json()
    assert created["is_default"] is True

    blocked_default = await client.post(
        f"/api/v1/admin/sites/{created['id']}/disable",
        headers=headers,
        json={"revision": created["revision"], "reason": "Test protected default"},
    )
    assert blocked_default.status_code == 409
    assert blocked_default.json()["code"] == "default_site_protected"

    original_current = (await client.get(f"/api/v1/admin/sites/{original['id']}")).json()
    original_default = await client.post(
        f"/api/v1/admin/sites/{original['id']}/set-default",
        headers=headers,
        json={"revision": original_current["revision"], "reason": "Restore original default"},
    )
    assert original_default.status_code == 200, original_default.text
    created = (await client.get(f"/api/v1/admin/sites/{created['id']}")).json()

    disabled = await client.post(
        f"/api/v1/admin/sites/{created['id']}/disable",
        headers=headers,
        json={"revision": created["revision"], "reason": "Seasonal site closure"},
    )
    assert disabled.status_code == 200, disabled.text
    created = disabled.json()
    assert created["lifecycle_state"] == "disabled"

    enabled = await client.post(
        f"/api/v1/admin/sites/{created['id']}/enable",
        headers=headers,
        json={"revision": created["revision"], "reason": "Reopen monitored site"},
    )
    assert enabled.status_code == 200, enabled.text
    created = enabled.json()
    assert created["lifecycle_state"] == "active"

    dependencies = await client.get(f"/api/v1/admin/sites/{created['id']}/dependencies")
    assert dependencies.status_code == 200
    assert dependencies.json()["blockers"] == []
    assert dependencies.json()["required_actions"] == []

    removed = await client.post(
        f"/api/v1/admin/sites/{created['id']}/remove",
        headers=headers,
        json={
            "revision": created["revision"],
            "reason": "Retire the secondary site",
            "confirmation": "warehouse",
            "dependency_reviewed": True,
        },
    )
    assert removed.status_code == 200, removed.text
    created = removed.json()
    assert created["lifecycle_state"] == "removed"
    assert created["dependencies"]["retained"]["raw_readings"] == 0

    selector_sites = (await client.get("/api/v1/sites")).json()
    assert all(site["id"] != created["id"] for site in selector_sites)
    removed_sites = (await client.get("/api/v1/admin/sites?status=removed")).json()
    assert [site["id"] for site in removed_sites] == [created["id"]]

    restored = await client.post(
        f"/api/v1/admin/sites/{created['id']}/restore",
        headers=headers,
        json={
            "revision": created["revision"],
            "reason": "Restore identity for administrator review",
            "confirm_high_risk": True,
        },
    )
    assert restored.status_code == 200, restored.text
    restored_site = restored.json()
    assert restored_site["lifecycle_state"] == "disabled"
    assert restored_site["is_default"] is False

    audit = await client.get(f"/api/v1/admin/sites/{created['id']}/audit")
    assert audit.status_code == 200
    actions = {event["action"] for event in audit.json()}
    assert {
        "site.created",
        "site.default_changed",
        "site.disable_blocked",
        "site.disabled",
        "site.enabled",
        "site.removal_initiated",
        "site.removed",
        "site.restored",
    } <= actions


@pytest.mark.asyncio
async def test_legacy_delete_never_hard_deletes_site(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    await bootstrap(client)
    site = (await client.get("/api/v1/sites")).json()[0]
    response = await client.delete(f"/api/v1/sites/{site['id']}", headers=csrf(client))
    assert response.status_code == 409
    assert response.json()["code"] == "site_soft_removal_required"
    assert (await client.get(f"/api/v1/admin/sites/{site['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_site_dependency_transfer_archive_and_access_end_preserve_identity(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client: httpx.AsyncClient = api_client
    await bootstrap(client)
    headers = csrf(client)

    async def create_site(name: str, code: str) -> dict[str, Any]:
        response = await client.post(
            "/api/v1/admin/sites",
            headers=headers,
            json={
                "name": name,
                "code": code,
                "timezone": "America/Los_Angeles",
                "currency": "USD",
                "locale": "en-US",
                "unit_system": "imperial",
                "network_policy_mode": "inherit",
                "confirmation": True,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    source = await create_site("Retiring lab", "retiring-lab")
    destination = await create_site("New lab", "new-lab")
    now = datetime.now(UTC)
    async with session_factory_fixture() as session:
        utility = await session.scalar(
            select(Utility).where(Utility.name == "Southern California Edison")
        )
        assert utility is not None
        device = Device(
            site_id=source["id"],
            hardware_id="site-transfer-device",
            name="Transfer test sensor",
            connection_mode="push",
            measurement_role="submeter",
            cost_scope="energy_only",
            ct_rating_amps=Decimal("100"),
        )
        account = UtilityAccount(
            site_id=source["id"],
            utility_id=utility.id,
            name="Archived source account",
            timezone="America/Los_Angeles",
            currency="USD",
        )
        scoped_user = User(
            email="site-user@example.com",
            display_name="Site User",
            password_hash="not-used-in-this-test",
            all_sites=False,
        )
        session.add_all((device, account, scoped_user))
        await session.flush()
        session.add_all(
            (
                DeviceSiteAssignment(
                    device_id=device.id,
                    site_id=source["id"],
                    effective_from=now,
                    created_at=now,
                    reason="Initial test assignment",
                ),
                UtilityAccountSiteAssignment(
                    utility_account_id=account.id,
                    site_id=source["id"],
                    effective_from=now,
                    created_at=now,
                    reason="Initial test assignment",
                ),
                UserSite(user_id=scoped_user.id, site_id=source["id"]),
            )
        )
        await session.commit()
        device_id = device.id
        account_id = account.id
        user_id = scoped_user.id

    dependencies = (await client.get(f"/api/v1/admin/sites/{source['id']}/dependencies")).json()
    assert {item["resource"] for item in dependencies["required_actions"]} == {
        "sensors",
        "utility_accounts",
        "user_access",
    }

    blocked = await client.post(
        f"/api/v1/admin/sites/{source['id']}/remove",
        headers=headers,
        json={
            "revision": dependencies["revision"],
            "reason": "Dependency protection test",
            "confirmation": source["code"],
            "dependency_reviewed": True,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "site_dependencies_unresolved"

    resolved = await client.post(
        f"/api/v1/admin/sites/{source['id']}/transfer-resources",
        headers=headers,
        json={
            "revision": dependencies["revision"],
            "reason": "Move sensor and preserve account evidence",
            "sensors": [
                {
                    "device_id": device_id,
                    "action": "transfer",
                    "target_site_id": destination["id"],
                }
            ],
            "utility_accounts": [{"utility_account_id": account_id, "action": "archive"}],
            "end_user_access_ids": [user_id],
        },
    )
    assert resolved.status_code == 200, resolved.text
    source = resolved.json()["site"]
    assert source["dependencies"]["required_actions"] == []

    removed = await client.post(
        f"/api/v1/admin/sites/{source['id']}/remove",
        headers=headers,
        json={
            "revision": source["revision"],
            "reason": "Dependencies explicitly resolved",
            "confirmation": source["code"],
            "dependency_reviewed": True,
        },
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["lifecycle_state"] == "removed"

    async with session_factory_fixture() as session:
        preserved_site = await session.get(Site, source["id"])
        preserved_device = await session.get(Device, device_id)
        preserved_account = await session.get(UtilityAccount, account_id)
        preserved_user = await session.get(User, user_id)
        device_assignments = list(
            await session.scalars(
                select(DeviceSiteAssignment)
                .where(DeviceSiteAssignment.device_id == device_id)
                .order_by(DeviceSiteAssignment.effective_from)
            )
        )
        assert preserved_site is not None
        assert preserved_device is not None and preserved_device.site_id == destination["id"]
        assert preserved_account is not None and preserved_account.status == "archived"
        assert preserved_user is not None and preserved_user.lifecycle_state == "active"
        assert await session.get(UserSite, (user_id, source["id"])) is None
        assert len(device_assignments) == 2
        assert device_assignments[0].effective_to is not None
        assert device_assignments[1].site_id == destination["id"]
        assert device_assignments[1].effective_to is None

    audit = await client.get(f"/api/v1/admin/sites/{source['id']}/audit")
    actions = {event["action"] for event in audit.json()}
    assert {
        "site.removal_blocked",
        "site.sensor_transferred",
        "site.utility_account_archived",
        "site.access_assignments_ended",
        "site.dependencies_resolved",
        "site.removal_initiated",
        "site.removed",
    } <= actions
