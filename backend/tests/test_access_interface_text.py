from __future__ import annotations

import httpx
import pytest

from app.main import app


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    token = client.cookies.get("pm_csrf")
    assert token
    return {"X-CSRF-Token": token}


async def bootstrap_admin(client: httpx.AsyncClient) -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": "admin@example.com",
            "display_name": "Access Admin",
            "password": "Production-Admin-Password-42!",
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_custom_role_site_scope_session_revocation_and_last_admin(
    api_client: httpx.AsyncClient,
) -> None:
    admin_session = await bootstrap_admin(api_client)
    assert "users.manage" in admin_session["user"]["permissions"]

    permission_response = await api_client.get("/api/v1/admin/permissions")
    assert permission_response.status_code == 200
    permission_codes = {item["code"] for item in permission_response.json()["permissions"]}
    assert {"users.manage", "roles.manage", "interface_text.manage"} <= permission_codes

    sites = await api_client.get("/api/v1/sites")
    primary_site = sites.json()[0]
    second_site = await api_client.post(
        "/api/v1/sites",
        headers=csrf(api_client),
        json={
            "name": "Restricted Site",
            "timezone": "America/Los_Angeles",
            "allowed_cidrs": [],
            "allowed_domains": [],
            "allow_public_polling": False,
        },
    )
    assert second_site.status_code == 201, second_site.text

    role = await api_client.post(
        "/api/v1/admin/roles",
        headers=csrf(api_client),
        json={
            "display_name": "Site observer",
            "description": "Read one assigned site",
            "permissions": ["overview.view", "sites.view", "devices.view"],
        },
    )
    assert role.status_code == 201, role.text
    role_id = role.json()["id"]
    assert role.json()["built_in"] is False

    dependency_failure = await api_client.post(
        "/api/v1/admin/roles",
        headers=csrf(api_client),
        json={
            "display_name": "Unsafe role",
            "description": "Missing its parent permission",
            "permissions": ["roles.manage"],
        },
    )
    assert dependency_failure.status_code == 422
    assert dependency_failure.json()["code"] == "permission_dependencies_missing"

    created = await api_client.post(
        "/api/v1/users",
        headers=csrf(api_client),
        json={
            "email": "viewer@example.com",
            "display_name": "Scoped Viewer",
            "password": "Production-Viewer-Password-42!",
            "roles": ["viewer"],
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as user_client:
        login = await user_client.post(
            "/api/v1/auth/login",
            json={
                "email": "viewer@example.com",
                "password": "Production-Viewer-Password-42!",
            },
        )
        assert login.status_code == 200
        before = await api_client.get(f"/api/v1/admin/users/{user_id}")
        assert before.status_code == 200
        assert before.json()["active_session_count"] == 1

        updated = await api_client.put(
            f"/api/v1/admin/users/{user_id}/access",
            headers=csrf(api_client),
            json={
                "role_ids": [role_id],
                "all_sites": False,
                "site_ids": [primary_site["id"]],
                "expected_revision": before.json()["access_revision"],
                "reason": "Limit contractor to the primary site",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["sessions_revoked"] == 1
        assert updated.json()["site_ids"] == [primary_site["id"]]
        assert updated.json()["permissions"] == [
            "devices.view",
            "overview.view",
            "sites.view",
        ]
        assert (await user_client.get("/api/v1/auth/session")).json()["authenticated"] is False

        relogin = await user_client.post(
            "/api/v1/auth/login",
            json={
                "email": "viewer@example.com",
                "password": "Production-Viewer-Password-42!",
            },
        )
        assert relogin.status_code == 200
        visible_sites = await user_client.get("/api/v1/sites")
        assert [site["id"] for site in visible_sites.json()] == [primary_site["id"]]
        denied_users = await user_client.get("/api/v1/admin/users")
        assert denied_users.status_code == 403

    admin_detail = await api_client.get(f"/api/v1/admin/users/{admin_session['user']['id']}")
    last_admin = await api_client.put(
        f"/api/v1/admin/users/{admin_session['user']['id']}/access",
        headers=csrf(api_client),
        json={
            "role_ids": [role_id],
            "all_sites": True,
            "site_ids": [],
            "expected_revision": admin_detail.json()["access_revision"],
            "confirm_high_risk": True,
        },
    )
    assert last_admin.status_code == 409
    assert last_admin.json()["code"] == "last_admin_required"

    history = await api_client.get(f"/api/v1/admin/users/{user_id}/access-history")
    assert history.status_code == 200
    assert any(item["action"] == "user.access_updated" for item in history.json()["events"])


@pytest.mark.asyncio
async def test_high_risk_role_requires_reauthentication(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap_admin(api_client)
    payload = {
        "display_name": "User manager",
        "description": "Delegated local user management",
        "permissions": ["users.view", "users.manage"],
        "confirm_high_risk": True,
    }
    missing_reauth = await api_client.post(
        "/api/v1/admin/roles", headers=csrf(api_client), json=payload
    )
    assert missing_reauth.status_code == 428
    confirmed = await api_client.post(
        "/api/v1/auth/reauthenticate",
        headers=csrf(api_client),
        json={"password": "Production-Admin-Password-42!"},
    )
    assert confirmed.status_code == 200
    created = await api_client.post("/api/v1/admin/roles", headers=csrf(api_client), json=payload)
    assert created.status_code == 201, created.text


@pytest.mark.asyncio
async def test_legacy_admin_creation_requires_confirmation_and_reauthentication(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap_admin(api_client)
    payload = {
        "email": "second-admin@example.com",
        "display_name": "Second Administrator",
        "password": "Production-Second-Admin-Password-42!",
        "roles": ["admin"],
    }
    missing_confirmation = await api_client.post(
        "/api/v1/users", headers=csrf(api_client), json=payload
    )
    assert missing_confirmation.status_code == 409
    assert missing_confirmation.json()["code"] == "protected_confirmation_required"

    payload["confirm_high_risk"] = True
    missing_reauthentication = await api_client.post(
        "/api/v1/users", headers=csrf(api_client), json=payload
    )
    assert missing_reauthentication.status_code == 428
    assert (
        await api_client.post(
            "/api/v1/auth/reauthenticate",
            headers=csrf(api_client),
            json={"password": "Production-Admin-Password-42!"},
        )
    ).status_code == 200
    created = await api_client.post("/api/v1/users", headers=csrf(api_client), json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["roles"] == ["admin"]


@pytest.mark.asyncio
async def test_interface_text_draft_publish_reset_restore_and_public_safety(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap_admin(api_client)
    original = await api_client.get("/api/v1/public/interface-text")
    assert original.status_code == 200
    assert original.json()["revision"] == 0
    assert original.json()["values"]["login.heading"] == "Sign in to your dashboard"
    assert "navigation.overview" not in original.json()["values"]
    assert "general.application_short_name" not in original.json()["values"]
    assert "general.organization_tagline" not in original.json()["values"]

    catalog = await api_client.get("/api/v1/admin/interface-text/catalog")
    assert catalog.status_code == 200
    assert any(item["key"] == "login.heading" for item in catalog.json()["definitions"])

    script = await api_client.put(
        "/api/v1/admin/interface-text/draft",
        headers=csrf(api_client),
        json={
            "base_revision": 0,
            "values": {"login.heading": "<script>alert(1)</script>"},
        },
    )
    assert script.status_code == 422
    unsafe_url = await api_client.put(
        "/api/v1/admin/interface-text/draft",
        headers=csrf(api_client),
        json={
            "base_revision": 0,
            "values": {"login.support_url": "javascript:alert(1)"},
        },
    )
    assert unsafe_url.status_code == 422

    saved = await api_client.put(
        "/api/v1/admin/interface-text/draft",
        headers=csrf(api_client),
        json={
            "base_revision": 0,
            "values": {
                "login.heading": "Welcome to Upland Energy",
                "navigation.devices": "Sensors",
            },
            "reason": "Local terminology",
        },
    )
    assert saved.status_code == 200, saved.text
    unpreviewed = await api_client.post(
        "/api/v1/admin/interface-text/publish",
        headers=csrf(api_client),
        json={
            "base_revision": 0,
            "draft_revision": saved.json()["draft_revision"],
            "confirm": True,
        },
    )
    assert unpreviewed.status_code == 409
    assert unpreviewed.json()["code"] == "interface_text_preview_required"
    preview = await api_client.post(
        "/api/v1/admin/interface-text/preview", headers=csrf(api_client)
    )
    assert preview.json()["values"]["login.heading"] == "Welcome to Upland Energy"
    assert (await api_client.get("/api/v1/public/interface-text")).json()["values"][
        "login.heading"
    ] == "Sign in to your dashboard"

    published = await api_client.post(
        "/api/v1/admin/interface-text/publish",
        headers=csrf(api_client),
        json={
            "base_revision": 0,
            "draft_revision": saved.json()["draft_revision"],
            "reason": "Approved local terminology",
            "confirm": True,
        },
    )
    assert published.status_code == 201, published.text
    first_revision_id = published.json()["id"]
    public = await api_client.get("/api/v1/public/interface-text")
    assert public.json()["revision"] == 1
    assert public.json()["values"]["login.heading"] == "Welcome to Upland Energy"
    etag = public.headers["etag"]
    cached = await api_client.get("/api/v1/public/interface-text", headers={"If-None-Match": etag})
    assert cached.status_code == 304

    reset = await api_client.post(
        "/api/v1/admin/interface-text/reset",
        headers=csrf(api_client),
        json={"base_revision": 1, "key": "login.heading", "reason": "Reset heading"},
    )
    assert reset.status_code == 201
    assert reset.json()["revision"] == 2
    assert reset.json()["values"]["login.heading"] == "Sign in to your dashboard"

    restored = await api_client.post(
        f"/api/v1/admin/interface-text/revisions/{first_revision_id}/restore",
        headers=csrf(api_client),
        json={
            "base_revision": 2,
            "reason": "Restore approved wording",
            "confirm": True,
        },
    )
    assert restored.status_code == 201, restored.text
    assert restored.json()["revision"] == 3
    assert restored.json()["restored_from_id"] == first_revision_id

    stale = await api_client.put(
        "/api/v1/admin/interface-text/draft",
        headers=csrf(api_client),
        json={"base_revision": 0, "values": {"login.heading": "Stale update"}},
    )
    assert stale.status_code == 409

    revisions = await api_client.get("/api/v1/admin/interface-text/revisions")
    assert [item["revision"] for item in revisions.json()["revisions"]] == [3, 2, 1]


@pytest.mark.asyncio
async def test_custom_role_clone_revision_assignment_and_archive(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap_admin(api_client)
    clone = await api_client.post(
        "/api/v1/admin/roles/viewer/clone",
        headers=csrf(api_client),
        json={
            "display_name": "Contractor viewer",
            "description": "Cloned read-only role for temporary contractors",
            "permissions": [
                "overview.view",
                "usage.view",
                "history.view",
                "history.export",
                "costs.view",
                "costs.export",
                "sites.view",
                "topology.view",
                "devices.view",
                "rates.view",
                "alerts.view",
                "status_indicators.view",
            ],
        },
    )
    assert clone.status_code == 201, clone.text
    role = clone.json()
    assert role["built_in"] is False

    immutable = await api_client.put(
        "/api/v1/admin/roles/viewer",
        headers=csrf(api_client),
        json={
            "display_name": "Changed viewer",
            "description": "Built in roles must remain immutable",
            "permissions": role["permissions"],
            "expected_revision": 1,
        },
    )
    assert immutable.status_code == 409
    assert immutable.json()["code"] == "builtin_role_immutable"

    stale = await api_client.put(
        f"/api/v1/admin/roles/{role['id']}",
        headers=csrf(api_client),
        json={
            "display_name": role["display_name"],
            "description": "A stale role edit",
            "permissions": role["permissions"],
            "expected_revision": role["revision"] + 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "role_revision_conflict"

    created = await api_client.post(
        "/api/v1/users",
        headers=csrf(api_client),
        json={
            "email": "contractor@example.com",
            "display_name": "Contractor",
            "password": "Production-Contractor-Password-42!",
            "roles": ["viewer"],
        },
    )
    user_id = created.json()["id"]
    detail = (await api_client.get(f"/api/v1/admin/users/{user_id}")).json()
    assigned = await api_client.put(
        f"/api/v1/admin/users/{user_id}/access",
        headers=csrf(api_client),
        json={
            "role_ids": [role["id"]],
            "all_sites": True,
            "site_ids": [],
            "expected_revision": detail["access_revision"],
        },
    )
    assert assigned.status_code == 200, assigned.text

    in_use = await api_client.post(
        f"/api/v1/admin/roles/{role['id']}/archive",
        headers=csrf(api_client),
        json={"reason": "Should fail while assigned"},
    )
    assert in_use.status_code == 409
    assert in_use.json()["code"] == "role_in_use"

    reassigned = await api_client.put(
        f"/api/v1/admin/users/{user_id}/access",
        headers=csrf(api_client),
        json={
            "role_ids": ["viewer"],
            "all_sites": True,
            "site_ids": [],
            "expected_revision": assigned.json()["access_revision"],
        },
    )
    assert reassigned.status_code == 200
    archived = await api_client.post(
        f"/api/v1/admin/roles/{role['id']}/archive",
        headers=csrf(api_client),
        json={"reason": "Contract completed"},
    )
    assert archived.status_code == 200
    rejected_assignment = await api_client.put(
        f"/api/v1/admin/users/{user_id}/access",
        headers=csrf(api_client),
        json={
            "role_ids": [role["id"]],
            "all_sites": True,
            "site_ids": [],
            "expected_revision": reassigned.json()["access_revision"],
        },
    )
    assert rejected_assignment.status_code == 422
    revisions = await api_client.get(f"/api/v1/admin/roles/{role['id']}/revisions")
    assert revisions.status_code == 200
    assert revisions.json()["revisions"][0]["revision"] == 1


@pytest.mark.asyncio
async def test_disable_revokes_sessions_retains_user_and_protects_last_admin(
    api_client: httpx.AsyncClient,
) -> None:
    admin = await bootstrap_admin(api_client)
    last_admin = await api_client.post(
        f"/api/v1/admin/users/{admin['user']['id']}/disable",
        headers=csrf(api_client),
        json={"reason": "Unsafe", "confirm_high_risk": True},
    )
    assert last_admin.status_code == 409
    assert last_admin.json()["code"] == "last_admin_required"

    created = await api_client.post(
        "/api/v1/users",
        headers=csrf(api_client),
        json={
            "email": "disabled@example.com",
            "display_name": "Disabled User",
            "password": "Production-Disabled-Password-42!",
            "roles": ["viewer"],
        },
    )
    user_id = created.json()["id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as user_client:
        assert (
            await user_client.post(
                "/api/v1/auth/login",
                json={
                    "email": "disabled@example.com",
                    "password": "Production-Disabled-Password-42!",
                },
            )
        ).status_code == 200
        disabled = await api_client.post(
            f"/api/v1/admin/users/{user_id}/disable",
            headers=csrf(api_client),
            json={"reason": "Contract ended"},
        )
        assert disabled.status_code == 200
        assert disabled.json()["sessions_revoked"] == 1
        assert (await user_client.get("/api/v1/auth/session")).json()["authenticated"] is False
        denied_login = await user_client.post(
            "/api/v1/auth/login",
            json={
                "email": "disabled@example.com",
                "password": "Production-Disabled-Password-42!",
            },
        )
        assert denied_login.status_code == 401

    retained = await api_client.get(f"/api/v1/admin/users/{user_id}")
    assert retained.status_code == 200
    assert retained.json()["status"] == "disabled"
    history = await api_client.get(f"/api/v1/admin/users/{user_id}/access-history")
    assert any(event["action"] == "user.disabled" for event in history.json()["events"])


@pytest.mark.asyncio
async def test_delegated_manager_cannot_escalate_permissions_or_site_scope(
    api_client: httpx.AsyncClient,
) -> None:
    admin = await bootstrap_admin(api_client)
    primary_site = (await api_client.get("/api/v1/sites")).json()[0]
    second_site = await api_client.post(
        "/api/v1/sites",
        headers=csrf(api_client),
        json={
            "name": "Second Site",
            "timezone": "America/Los_Angeles",
            "allowed_cidrs": [],
            "allowed_domains": [],
            "allow_public_polling": False,
        },
    )
    assert second_site.status_code == 201
    assert (
        await api_client.post(
            "/api/v1/auth/reauthenticate",
            headers=csrf(api_client),
            json={"password": "Production-Admin-Password-42!"},
        )
    ).status_code == 200
    delegated = await api_client.post(
        "/api/v1/admin/roles",
        headers=csrf(api_client),
        json={
            "display_name": "Scoped user manager",
            "description": "Manage users only within the assigned site",
            "permissions": ["sites.view", "users.view", "users.manage"],
            "confirm_high_risk": True,
        },
    )
    assert delegated.status_code == 201, delegated.text
    actor = await api_client.post(
        "/api/v1/users",
        headers=csrf(api_client),
        json={
            "email": "manager@example.com",
            "display_name": "Scoped Manager",
            "password": "Production-Manager-Password-42!",
            "roles": ["viewer"],
        },
    )
    target = await api_client.post(
        "/api/v1/users",
        headers=csrf(api_client),
        json={
            "email": "target@example.com",
            "display_name": "Scoped Target",
            "password": "Production-Target-Password-42!",
            "roles": ["viewer"],
        },
    )
    for user, role_ids in ((actor.json(), [delegated.json()["id"]]), (target.json(), ["viewer"])):
        detail = (await api_client.get(f"/api/v1/admin/users/{user['id']}")).json()
        update = await api_client.put(
            f"/api/v1/admin/users/{user['id']}/access",
            headers=csrf(api_client),
            json={
                "role_ids": role_ids,
                "all_sites": False,
                "site_ids": [primary_site["id"]],
                "expected_revision": detail["access_revision"],
                "confirm_high_risk": True,
            },
        )
        assert update.status_code == 200, update.text

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as manager_client:
        login = await manager_client.post(
            "/api/v1/auth/login",
            json={
                "email": "manager@example.com",
                "password": "Production-Manager-Password-42!",
            },
        )
        assert login.status_code == 200
        target_detail = await manager_client.get(f"/api/v1/admin/users/{target.json()['id']}")
        assert target_detail.status_code == 200
        role_escalation = await manager_client.put(
            f"/api/v1/admin/users/{target.json()['id']}/access",
            headers=csrf(manager_client),
            json={
                "role_ids": ["admin"],
                "all_sites": False,
                "site_ids": [primary_site["id"]],
                "expected_revision": target_detail.json()["access_revision"],
                "confirm_high_risk": True,
            },
        )
        assert role_escalation.status_code == 403
        assert role_escalation.json()["code"] == "permission_delegation_forbidden"
        site_escalation = await manager_client.put(
            f"/api/v1/admin/users/{target.json()['id']}/access",
            headers=csrf(manager_client),
            json={
                "role_ids": [delegated.json()["id"]],
                "all_sites": False,
                "site_ids": [second_site.json()["id"]],
                "expected_revision": target_detail.json()["access_revision"],
            },
        )
        assert site_escalation.status_code == 403
        assert site_escalation.json()["code"] == "site_delegation_forbidden"
        hidden_admin = await manager_client.get(f"/api/v1/admin/users/{admin['user']['id']}")
        assert hidden_admin.status_code == 404


@pytest.mark.asyncio
async def test_interface_text_rejects_blank_oversized_controls_and_unknown_keys(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap_admin(api_client)
    invalid_values = [
        ({"login.heading": ""}, "interface_text_required"),
        ({"login.heading": "x" * 161}, "interface_text_length"),
        ({"login.heading": "hello\u0007world"}, "interface_text_control_characters"),
        ({"login.heading.unknown": "value"}, "interface_text_key_unknown"),
    ]
    for values, expected_code in invalid_values:
        response = await api_client.put(
            "/api/v1/admin/interface-text/draft",
            headers=csrf(api_client),
            json={"base_revision": 0, "values": values},
        )
        assert response.status_code == 422
        assert response.json()["code"] == expected_code

    public = (await api_client.get("/api/v1/public/interface-text")).json()
    assert set(public) == {"revision", "values"}
    assert "footer.dashboard" not in public["values"]
    assert "login.heading" in public["values"]
    assert set(public["values"]) == {
        "general.application_name",
        "login.email_label",
        "login.footer",
        "login.heading",
        "login.help_text",
        "login.password_label",
        "login.sign_in_button",
        "login.subtitle",
        "login.support_label",
        "login.support_url",
    }
    audit = await api_client.get("/api/v1/audit-events")
    actions = {event["action"] for event in audit.json()}
    assert "interface_text.validation_failed" in actions
