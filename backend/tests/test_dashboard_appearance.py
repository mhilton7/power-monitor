from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditEvent, DashboardAppearance


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


@pytest.mark.asyncio
async def test_admin_publishes_one_revision_checked_palette_for_every_user(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client: httpx.AsyncClient = api_client
    await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "appearance@example.com",
            "display_name": "Appearance Admin",
            "password": "Long-Production-Password-42!",
        },
    )
    initial = await client.get("/api/v1/appearance")
    assert initial.status_code == 200
    assert initial.json()["chart_power_color"] == "#78DFBF"
    revision = initial.json()["revision"]

    updated = await client.put(
        "/api/v1/appearance",
        headers=csrf(client),
        json={
            "chart_power_color": "#123456",
            "chart_energy_color": "#345678",
            "chart_cost_color": "#abcdef",
            "expected_revision": revision,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["chart_cost_color"] == "#ABCDEF"
    assert updated.json()["revision"] == revision + 1
    assert (await client.get("/api/v1/appearance")).json() == updated.json()

    stale = await client.put(
        "/api/v1/appearance",
        headers=csrf(client),
        json={
            "chart_power_color": "#111111",
            "chart_energy_color": "#222222",
            "chart_cost_color": "#333333",
            "expected_revision": revision,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "appearance_revision_conflict"

    async with session_factory_fixture() as session:
        stored = await session.get(DashboardAppearance, "current")
        assert stored and stored.chart_power_color == "#123456"
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "appearance.chart_colors_published")
        )
        assert audit and audit.details["revision"] == revision + 1
