from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AggregateSet,
    AuditEvent,
    BackgroundJob,
    CostCalculationRun,
    RateAssignment,
    RateExtractionResult,
    RatePlan,
    RateSource,
    RateSourceArtifact,
    RateSourceCheckRun,
    RateVersion,
    RateVersionSource,
    Site,
    UtilityAccount,
)
from app.main import app
from app.rates.documents import RatePlanDocument


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    token = client.cookies.get("pm_csrf")
    assert token
    return {"X-CSRF-Token": token}


async def bootstrap(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "rate-lifecycle@example.com",
            "display_name": "Rate lifecycle administrator",
            "password": "Lifecycle-Production-Password-42!",
        },
    )
    assert response.status_code == 201, response.text


def custom_document(code: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "shared" / "examples" / "custom-rate-plan.json"
    document = RatePlanDocument.model_validate_json(path.read_bytes())
    return document.model_copy(
        update={
            "plan_code": code,
            "plan_name": f"{code} lifecycle fixture",
            "effective_from": date.today(),
        }
    ).model_dump(mode="json")


async def create_plan(
    client: httpx.AsyncClient,
    code: str,
    *,
    activate: bool = False,
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/rates/plans",
        headers=csrf(client),
        json=custom_document(code),
    )
    assert response.status_code == 201, response.text
    plan = response.json()["plan"]
    if activate:
        activated = await client.post(
            f"/api/v1/rates/versions/{plan['versions'][0]['id']}/activate",
            headers=csrf(client),
        )
        assert activated.status_code == 200, activated.text
        refreshed = await client.get(f"/api/v1/rates/plans/{plan['id']}")
        assert refreshed.status_code == 200
        plan = refreshed.json()
    return plan


def remove_payload(plan: dict[str, Any], reason: str = "Lifecycle test removal") -> dict[str, Any]:
    return {
        "expected_revision": plan["lifecycle_revision"],
        "confirmation": plan["code"],
        "reason": reason,
        "idempotency_key": f"remove-{plan['id']}",
    }


@pytest.mark.asyncio
async def test_unused_draft_delete_and_server_authorization(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap(api_client)
    plan = await create_plan(api_client, "REMOVE-UNUSED-DRAFT")
    dependencies = await api_client.get(f"/api/v1/admin/rate-plans/{plan['id']}/dependencies")
    assert dependencies.status_code == 200
    assert dependencies.json()["permanent_draft_deletion_eligible"] is True

    created_user = await api_client.post(
        "/api/v1/users",
        headers=csrf(api_client),
        json={
            "email": "rate-viewer@example.com",
            "display_name": "Rate viewer",
            "password": "Viewer-Production-Password-42!",
            "roles": ["viewer"],
        },
    )
    assert created_user.status_code == 201, created_user.text
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as viewer:
        login = await viewer.post(
            "/api/v1/auth/login",
            json={
                "email": "rate-viewer@example.com",
                "password": "Viewer-Production-Password-42!",
            },
        )
        assert login.status_code == 200
        forbidden = await viewer.request(
            "DELETE",
            f"/api/v1/admin/rate-plan-drafts/{plan['id']}",
            headers=csrf(viewer),
            json={
                "expected_revision": plan["lifecycle_revision"],
                "confirmation": plan["code"],
                "reason": "Viewer must not delete this draft",
            },
        )
        assert forbidden.status_code == 403

    deleted = await api_client.request(
        "DELETE",
        f"/api/v1/admin/rate-plan-drafts/{plan['id']}",
        headers=csrf(api_client),
        json={
            "expected_revision": plan["lifecycle_revision"],
            "confirmation": plan["code"],
            "reason": "Discard unused test draft",
        },
    )
    assert deleted.status_code == 204, deleted.text
    assert (await api_client.get(f"/api/v1/rates/plans/{plan['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_published_custom_plan_remove_filter_restore_stale_and_idempotent(
    api_client: httpx.AsyncClient,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    await bootstrap(api_client)
    plan = await create_plan(api_client, "REMOVE-PUBLISHED-CUSTOM", activate=True)
    version_id = plan["versions"][0]["id"]

    removed = await api_client.post(
        f"/api/v1/admin/rate-plans/{plan['id']}/remove",
        headers=csrf(api_client),
        json=remove_payload(plan),
    )
    assert removed.status_code == 200, removed.text
    removed_plan = removed.json()["plan"]
    assert removed_plan["status"] == "removed"
    assert removed_plan["removal_reason"] == "Lifecycle test removal"

    duplicate = await api_client.post(
        f"/api/v1/admin/rate-plans/{plan['id']}/remove",
        headers=csrf(api_client),
        json=remove_payload(plan),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["idempotent"] is True

    active = await api_client.get("/api/v1/rates/plans?status=active")
    removed_list = await api_client.get("/api/v1/admin/rate-plans?status=removed")
    assert plan["id"] not in {item["id"] for item in active.json()}
    assert plan["id"] in {item["id"] for item in removed_list.json()}

    blocked_activation = await api_client.post(
        f"/api/v1/rates/versions/{version_id}/activate",
        headers=csrf(api_client),
    )
    assert blocked_activation.status_code == 409
    assert blocked_activation.json()["code"] == "rate_plan_removed"

    restored = await api_client.post(
        f"/api/v1/admin/rate-plans/{plan['id']}/restore",
        headers=csrf(api_client),
        json={
            "expected_revision": removed_plan["lifecycle_revision"],
            "reason": "Restore lifecycle test plan",
            "idempotency_key": f"restore-{plan['id']}",
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["plan"]["status"] == "active"
    assert restored.json()["assignments_restored"] is False

    stale = await api_client.post(
        f"/api/v1/admin/rate-plans/{plan['id']}/remove",
        headers=csrf(api_client),
        json=remove_payload(plan, "Stale removal attempt"),
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_revision"

    async with session_factory_fixture() as session:
        assert await session.get(RateVersion, version_id) is not None
        actions = set(
            await session.scalars(
                select(AuditEvent.action).where(AuditEvent.object_id == plan["id"])
            )
        )
        assert {"rate_plan.removed", "rate_plan.restored"} <= actions


@pytest.mark.asyncio
async def test_assignments_block_removal_then_history_and_costs_are_preserved(
    api_client: httpx.AsyncClient,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    await bootstrap(api_client)
    plan = await create_plan(api_client, "REMOVE-DEPENDENCY-CUSTOM", activate=True)
    version_id = plan["versions"][0]["id"]
    now = datetime.now(UTC)

    async with session_factory_fixture() as session:
        rate_plan = await session.get(RatePlan, plan["id"])
        version = await session.get(RateVersion, version_id)
        site = await session.scalar(select(Site).order_by(Site.created_at))
        assert rate_plan and version and site
        account = UtilityAccount(
            site_id=site.id,
            utility_id=rate_plan.utility_id,
            name="Rate dependency account",
            timezone="America/Los_Angeles",
            active_rate_version_id=version.id,
        )
        session.add(account)
        await session.flush()
        active_assignment = RateAssignment(
            utility_account_id=account.id,
            rate_version_id=version.id,
            effective_from=now - timedelta(days=30),
            created_at=now,
        )
        future_assignment = RateAssignment(
            utility_account_id=account.id,
            rate_version_id=version.id,
            effective_from=now + timedelta(days=30),
            effective_to=now + timedelta(days=60),
            created_at=now,
        )
        session.add_all([active_assignment, future_assignment])
        await session.commit()
        account_id = account.id
        active_assignment_id = active_assignment.id
        future_assignment_id = future_assignment.id

    blocked = await api_client.post(
        f"/api/v1/admin/rate-plans/{plan['id']}/remove",
        headers=csrf(api_client),
        json=remove_payload(plan),
    )
    assert blocked.status_code == 409
    dependencies = blocked.json()["dependencies"]
    assert len(dependencies["active_assignments"]) == 1
    assert len(dependencies["future_assignments"]) == 1
    assert len(dependencies["active_account_pointers"]) == 1
    assert dependencies["dependency_actions"] == [
        "replace_assignment",
        "schedule_replacement",
        "end_future_assignment",
        "cancel_removal",
    ]

    async with session_factory_fixture() as session:
        account = await session.get(UtilityAccount, account_id)
        active_assignment = await session.get(RateAssignment, active_assignment_id)
        future_assignment = await session.get(RateAssignment, future_assignment_id)
        site = await session.scalar(select(Site).order_by(Site.created_at))
        assert account and active_assignment and future_assignment and site
        account.active_rate_version_id = None
        active_assignment.effective_to = now - timedelta(days=1)
        await session.delete(future_assignment)
        aggregate = AggregateSet(
            site_id=site.id,
            utility_account_id=account.id,
            name="Preserved lifecycle aggregate",
            cost_scope="energy_only",
        )
        session.add(aggregate)
        await session.flush()
        run = CostCalculationRun(
            utility_account_id=account.id,
            aggregate_set_id=aggregate.id,
            rate_version_id=version_id,
            input_start=now - timedelta(days=2),
            input_end=now - timedelta(days=1),
            algorithm_version="lifecycle-test/1",
            status="completed",
            coverage_percent=Decimal("100"),
            created_at=now,
            completed_at=now,
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    refreshed = await api_client.get(f"/api/v1/rates/plans/{plan['id']}")
    removed = await api_client.post(
        f"/api/v1/admin/rate-plans/{plan['id']}/remove",
        headers=csrf(api_client),
        json=remove_payload(refreshed.json(), "Retire after assignment ended"),
    )
    assert removed.status_code == 200, removed.text
    after = removed.json()["dependencies"]
    assert after["historical_assignment_count"] == 1
    assert after["historical_calculation_count"] == 1
    assert after["preservation"]["costs"] is True

    async with session_factory_fixture() as session:
        assert await session.get(RateVersion, version_id) is not None
        assert await session.get(CostCalculationRun, run_id) is not None
        assert (
            await session.scalar(
                select(func.count(RateAssignment.id)).where(
                    RateAssignment.id == active_assignment_id
                )
            )
            == 1
        )


@pytest.mark.asyncio
async def test_managed_plan_retires_and_restores_with_source_evidence(
    api_client: httpx.AsyncClient,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    await bootstrap(api_client)
    plans_response = await api_client.get("/api/v1/rates/plans?status=active")
    assert plans_response.status_code == 200
    managed = next(item for item in plans_response.json() if item["plan_kind"] == "official_sce")
    version_id = managed["versions"][0]["id"]
    now = datetime.now(UTC)

    async with session_factory_fixture() as session:
        source = RateSource(
            name="SCE lifecycle evidence",
            url="https://www.sce.com/lifecycle-evidence",
            parser_id="sce-public-rate-page",
            enabled=True,
            consecutive_failures=0,
            created_at=now,
            updated_at=now,
        )
        session.add(source)
        await session.flush()
        job = BackgroundJob(
            job_type="rate_source_check",
            status="completed",
            requested_at=now,
            started_at=now,
            completed_at=now,
            correlation_id="rate-lifecycle-evidence",
            progress={},
            result={},
        )
        session.add(job)
        await session.flush()
        check = RateSourceCheckRun(
            job_id=job.id,
            rate_source_id=source.id,
            checked_at=now,
            http_status=200,
            outcome="unchanged",
        )
        session.add(check)
        await session.flush()
        artifact = RateSourceArtifact(
            source_check_id=check.id,
            sha256="e" * 64,
            content_type="text/html",
            byte_size=128,
            storage_path="rate-evidence/lifecycle.html",
            original_filename="lifecycle.html",
            captured_at=now,
        )
        session.add(artifact)
        await session.flush()
        extraction = RateExtractionResult(
            artifact_id=artifact.id,
            parser_id="sce-public-rate-page",
            parser_version="1.0.0",
            status="succeeded",
            normalized_payload={"fixture": "lifecycle"},
            warnings=[],
            errors=[],
            extracted_at=now,
        )
        session.add(extraction)
        await session.flush()
        session.add(
            RateVersionSource(
                rate_version_id=version_id,
                artifact_id=artifact.id,
                extraction_result_id=extraction.id,
                relationship="primary",
            )
        )
        await session.commit()
        artifact_id = artifact.id
        extraction_id = extraction.id

    dependencies = await api_client.get(f"/api/v1/admin/rate-plans/{managed['id']}/dependencies")
    assert dependencies.status_code == 200
    assert dependencies.json()["source_evidence_count"] >= 1

    retired = await api_client.post(
        f"/api/v1/admin/rate-plans/{managed['id']}/remove",
        headers=csrf(api_client),
        json=remove_payload(managed, "Managed plan lifecycle test"),
    )
    assert retired.status_code == 200, retired.text
    retired_plan = retired.json()["plan"]
    assert retired_plan["status"] == "retired"
    assert retired.json()["dependencies"]["preservation"]["source_evidence"] is True

    restored = await api_client.post(
        f"/api/v1/admin/rate-plans/{managed['id']}/restore",
        headers=csrf(api_client),
        json={
            "expected_revision": retired_plan["lifecycle_revision"],
            "reason": "Restore managed lifecycle fixture",
            "idempotency_key": f"restore-managed-{managed['id']}",
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["plan"]["status"] == "active"
    assert restored.json()["assignments_restored"] is False

    async with session_factory_fixture() as session:
        assert await session.get(RateSourceArtifact, artifact_id) is not None
        assert await session.get(RateExtractionResult, extraction_id) is not None
        assert await session.get(RateVersion, version_id) is not None
