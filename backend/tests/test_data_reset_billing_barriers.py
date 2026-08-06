from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bills import service as bill_service
from app.db.models import (
    BillingCycle,
    DataResetOperation,
    DataResetPlan,
    ManualAccountUsage,
    UtilityBillImport,
    UtilityUsageImport,
    new_uuid,
)
from app.problem import ProblemError


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


async def bootstrap(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "reset-billing-barrier@example.com",
            "display_name": "Reset Billing Barrier",
            "password": "Long-Production-Password-42!",
        },
    )
    assert response.status_code == 201, response.text
    sites = await client.get("/api/v1/sites")
    assert sites.status_code == 200, sites.text
    return str(sites.json()[0]["id"])


def account_payload(version_id: str) -> dict[str, Any]:
    return {
        "name": "Reset billing barrier account",
        "utility_provider": "sce",
        "generation_provider": "sce",
        "provider_mode": "sce_bundled",
        "billing_cycle_start_day": 1,
        "currency": "USD",
        "rate_assignment": {
            "rate_version_id": version_id,
            "effective_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "assignment_reason": "Reset billing barrier fixture",
        },
        "cost_scope": "energy_only",
        "adjustments": [],
        "confirmation": True,
    }


def active_operation(site_id: str) -> tuple[DataResetPlan, DataResetOperation]:
    now = datetime.now(UTC)
    plan_id = new_uuid()
    return (
        DataResetPlan(
            id=plan_id,
            site_id=site_id,
            requested_categories=["generated_outputs"],
            delete_imported_bill_documents=False,
            disconnected_sensor_policy="defer_until_reconnect",
            plan_snapshot={},
            plan_fingerprint="a" * 64,
            revision=1,
            created_at=now,
            expires_at=now + timedelta(minutes=15),
        ),
        DataResetOperation(
            id=new_uuid(),
            plan_id=plan_id,
            site_id=site_id,
            state="preparing_sensors",
            revision=1,
            reset_generation=2,
            reset_timestamp=now,
            requested_categories=["generated_outputs"],
            delete_imported_bill_documents=False,
            disconnected_sensor_policy="defer_until_reconnect",
            backup_mode="permanent_without_backup",
            reason="Exercise billing mutation barriers",
            idempotency_key=f"billing-barrier-{new_uuid()}",
            request_fingerprint="b" * 64,
            plan_revision=1,
            started_at=now,
            created_at=now,
            updated_at=now,
        ),
    )


@pytest.mark.asyncio
async def test_active_reset_blocks_tier_and_assigned_bill_mutations_but_allows_preview(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client: httpx.AsyncClient = api_client
    site_id = await bootstrap(client)
    plans = (await client.get("/api/v1/rates/plans")).json()
    version = next(
        version
        for plan in plans
        for version in plan["versions"]
        if version["status"] == "published"
    )
    account_response = await client.post(
        f"/api/v1/admin/sites/{site_id}/utility-accounts",
        headers=csrf(client),
        json=account_payload(str(version["id"])),
    )
    assert account_response.status_code == 201, account_response.text
    account_id = str(account_response.json()["id"])

    async with session_factory_fixture() as session:
        session.add_all(active_operation(site_id))
        await session.commit()

    now = datetime.now(UTC)
    preview_payload = {
        "import_kind": "cycle_dates",
        "timezone": "America/Los_Angeles",
        "source_name": "Reset-safe preview",
        "field_mapping": {},
        "rows": [
            {
                "starts_at": (now - timedelta(days=30)).isoformat(),
                "ends_at": now.isoformat(),
            }
        ],
        "conflict_policy": "reject",
        "commit": False,
        "calculation_role": "reference_only",
    }
    preview = await client.post(
        f"/api/v1/admin/utility-accounts/{account_id}/usage-imports",
        headers=csrf(client),
        json=preview_payload,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["will_commit"] is False

    fixture_pdf = (
        Path(__file__).parent / "fixtures" / "bills" / "text-tiered-bill.pdf"
    ).read_bytes()
    blocked = [
        await client.put(
            f"/api/v1/admin/utility-accounts/{account_id}/usage-authority",
            headers=csrf(client),
            json={
                "revision": None,
                "authority_type": "whole_account_meter",
                "device_ids": [new_uuid()],
                "confidence": "high",
                "complete_account": True,
                "calculation_role": "sensor_measurements",
            },
        ),
        await client.post(
            f"/api/v1/admin/utility-accounts/{account_id}/manual-usage",
            headers=csrf(client),
            json={
                "effective_at": now.isoformat(),
                "cumulative_kwh": "10.25",
                "source_note": "Must not persist during reset",
                "verification_status": "verified",
                "idempotency_key": "reset-barrier-manual-usage",
                "calculation_role": "advanced_external_correction",
                "confirmation": "ALTER TIER PROGRESSION",
            },
        ),
        await client.post(
            f"/api/v1/admin/utility-accounts/{account_id}/usage-imports",
            headers=csrf(client),
            json={**preview_payload, "commit": True},
        ),
        await client.post(
            f"/api/v1/admin/utility-accounts/{account_id}/billing-cycles",
            headers=csrf(client),
            json={
                "starts_at": (now - timedelta(days=30)).isoformat(),
                "ends_at": now.isoformat(),
                "source": "manual_override",
                "reason": "Must not persist during reset",
            },
        ),
        await client.post(
            f"/api/v1/admin/utility-accounts/{account_id}/billing-cycles/current/recalculate",
            headers=csrf(client),
        ),
        await client.post(
            f"/api/v1/admin/utility-accounts/{account_id}/bill-imports",
            headers=csrf(client),
            params={"retention_mode": "retain", "source_role": "supporting"},
            files={"upload": ("bill.pdf", fixture_pdf, "application/pdf")},
        ),
    ]
    for response in blocked:
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "data_reset_site_mutation_blocked"

    async with session_factory_fixture() as session:
        assert (
            int(await session.scalar(select(func.count()).select_from(ManualAccountUsage)) or 0)
            == 0
        )
        assert (
            int(await session.scalar(select(func.count()).select_from(UtilityUsageImport)) or 0)
            == 0
        )
        assert int(await session.scalar(select(func.count()).select_from(BillingCycle)) or 0) == 0
        assert (
            int(await session.scalar(select(func.count()).select_from(UtilityBillImport)) or 0) == 0
        )


@pytest.mark.asyncio
async def test_due_bill_retention_defers_assigned_site_during_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assigned = SimpleNamespace(utility_account_id="account-1")
    unassigned = SimpleNamespace(utility_account_id=None)
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=[assigned, unassigned]),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [("account-1", "site-1")])),
    )
    barrier = AsyncMock(
        side_effect=ProblemError(
            409,
            "Site reset in progress",
            "Mutation blocked",
            "data_reset_site_mutation_blocked",
        )
    )
    delete_original = AsyncMock(return_value=True)
    monkeypatch.setattr(bill_service, "ensure_site_reset_mutations_allowed", barrier)
    monkeypatch.setattr(bill_service, "delete_original_artifact", delete_original)

    assert await bill_service.due_retention_deletions(session) == 1
    barrier.assert_awaited_once_with(session, ["site-1"])
    delete_original.assert_awaited_once_with(session, bill=unassigned)
