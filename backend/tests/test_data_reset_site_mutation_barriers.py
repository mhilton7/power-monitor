from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from worker.app.rate_sync import activate_due_versions, process_rate_sync_jobs
from worker.app.tasks import process_export_jobs

from app.api.routes import logs as log_routes
from app.api.routes.rates import _rate_plan_site_ids
from app.config import Settings
from app.data_reset import service as data_reset_service
from app.db.models import (
    AggregateSet,
    BackgroundJob,
    CostCalculationRun,
    DataResetOperation,
    DataResetPlan,
    ExportJob,
    LogExportJob,
    RateAssignment,
    RatePlan,
    RateSource,
    RateSourceArtifact,
    RateSourceCheckRun,
    RateVersion,
    SensorNetworkPolicy,
    Site,
    User,
    Utility,
    UtilityAccount,
    new_uuid,
)
from app.problem import ProblemError
from app.rates import reset_barrier as rate_reset_barrier


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


async def bootstrap(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "reset-barrier-admin@example.com",
            "display_name": "Reset Barrier Admin",
            "password": "Long-Production-Password-42!",
        },
    )
    assert response.status_code == 201, response.text
    sites = await client.get("/api/v1/sites")
    assert sites.status_code == 200, sites.text
    return str(sites.json()[0]["id"])


def account_payload(version_id: str) -> dict[str, Any]:
    return {
        "name": "Reset barrier account",
        "utility_provider": "sce",
        "generation_provider": "sce",
        "provider_mode": "sce_bundled",
        "billing_cycle_start_day": 1,
        "currency": "USD",
        "rate_assignment": {
            "rate_version_id": version_id,
            "effective_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "assignment_reason": "Reset barrier fixture",
        },
        "cost_scope": "energy_only",
        "adjustments": [],
        "confirmation": True,
    }


def active_operation(site_id: str) -> tuple[DataResetPlan, DataResetOperation]:
    now = datetime.now(UTC)
    plan_id = new_uuid()
    plan = DataResetPlan(
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
    )
    operation = DataResetOperation(
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
        reason="Exercise reset mutation barriers",
        idempotency_key=f"barrier-{new_uuid()}",
        request_fingerprint="b" * 64,
        plan_revision=1,
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    return plan, operation


@pytest.mark.asyncio
async def test_rate_plan_scope_includes_owners_active_accounts_and_future_assignments(
    session: AsyncSession,
) -> None:
    sites = [
        Site(id=new_uuid(), name=f"Rate scope {index}", code=f"rate-scope-{index}")
        for index in range(1, 5)
    ]
    utility = Utility(id=new_uuid(), name="Rate scope utility")
    accounts = [
        UtilityAccount(
            id=new_uuid(),
            site_id=site.id,
            utility_id=utility.id,
            name=f"Account {index}",
        )
        for index, site in enumerate(sites, start=1)
    ]
    session.add_all([*sites, utility, *accounts])
    await session.flush()

    plan = RatePlan(
        id=new_uuid(),
        utility_id=utility.id,
        code="RESET-SCOPE",
        name="Reset scope plan",
        owner_site_id=sites[0].id,
        owner_utility_account_id=accounts[1].id,
    )
    version = RateVersion(
        id=new_uuid(),
        rate_plan_id=plan.id,
        version=1,
        effective_from=date.today(),
        timezone="America/Los_Angeles",
        currency="USD",
        source_url="https://example.invalid/rate",
        source_checked_on=date.today(),
        source_notes="Test fixture",
        content_hash="c" * 64,
        status="published",
        created_at=datetime.now(UTC),
    )
    session.add_all([plan, version])
    await session.flush()
    accounts[3].active_rate_version_id = version.id
    session.add(
        RateAssignment(
            utility_account_id=accounts[2].id,
            rate_version_id=version.id,
            effective_from=datetime.now(UTC) + timedelta(days=30),
            assignment_reason="Future cross-site assignment",
            revision=1,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()

    assert await _rate_plan_site_ids(session, plan) == sorted(site.id for site in sites)


@pytest.mark.asyncio
async def test_rate_plan_barrier_locks_sites_before_plan_and_retries_on_scope_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = RatePlan(id="plan-1", utility_id="utility-1", code="PLAN", name="Plan")
    events: list[tuple[str, tuple[str, ...]]] = []
    scope_reads = 0

    async def expanding_scope(_session: Any, _plan: RatePlan) -> list[str]:
        nonlocal scope_reads
        scope_reads += 1
        scope = ["site-1"] if scope_reads == 1 else ["site-1", "site-2"]
        events.append(("scope", tuple(scope)))
        return scope

    async def site_barrier(_session: Any, site_ids: list[str]) -> None:
        events.append(("sites", tuple(site_ids)))

    async def plan_lock(_session: Any, plan_ids: Any) -> dict[str, RatePlan]:
        events.append(("plans", tuple(sorted(plan_ids))))
        return {plan.id: plan}

    monkeypatch.setattr(rate_reset_barrier, "rate_plan_site_ids", expanding_scope)
    monkeypatch.setattr(data_reset_service, "ensure_site_reset_mutations_allowed", site_barrier)
    monkeypatch.setattr(rate_reset_barrier, "lock_rate_plans", plan_lock)

    with pytest.raises(ProblemError) as captured:
        await rate_reset_barrier.ensure_rate_plans_reset_mutations_allowed(object(), [plan])  # type: ignore[arg-type]

    assert captured.value.code == "rate_plan_dependency_scope_changed"
    assert captured.value.extra["retryable"] is True
    assert events == [
        ("scope", ("site-1",)),
        ("sites", ("site-1",)),
        ("plans", ("plan-1",)),
        ("scope", ("site-1", "site-2")),
    ]


@pytest.mark.asyncio
async def test_active_reset_blocks_account_rate_network_and_output_mutations(
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
    account = account_response.json()
    policies_response = await client.get("/api/v1/admin/network/policies")
    assert policies_response.status_code == 200, policies_response.text
    policy = policies_response.json()[0]

    aggregate_id = new_uuid()
    async with session_factory_fixture() as session:
        session.add(
            AggregateSet(
                id=aggregate_id,
                site_id=site_id,
                utility_account_id=account["id"],
                name="Reset barrier aggregate",
            )
        )
        session.add_all(active_operation(site_id))
        await session.commit()

    now = datetime.now(UTC)
    requests = [
        await client.put(
            f"/api/v1/admin/utility-accounts/{account['id']}",
            headers=csrf(client),
            json={"revision": account["revision"], "nickname": "Must not persist"},
        ),
        await client.post(
            f"/api/v1/admin/utility-accounts/{account['id']}/recalculate",
            headers=csrf(client),
        ),
        await client.post(
            "/api/v1/billing/recalculations",
            headers=csrf(client),
            json={
                "utility_account_id": account["id"],
                "aggregate_set_id": aggregate_id,
                "rate_version_id": version["id"],
                "input_start": (now - timedelta(hours=1)).isoformat(),
                "input_end": now.isoformat(),
            },
        ),
        await client.post(
            "/api/v1/rates/assignments",
            headers=csrf(client),
            json={
                "utility_account_id": account["id"],
                "rate_version_id": version["id"],
                "effective_from": (now + timedelta(days=30)).isoformat(),
            },
        ),
        await client.post(
            f"/api/v1/rates/versions/{version['id']}/activate",
            headers=csrf(client),
        ),
        await client.patch(
            "/api/v1/admin/rate-source-settings",
            headers=csrf(client),
            json={"enabled": False},
        ),
        await client.post(
            f"/api/v1/rate-versions/{version['id']}/activate",
            headers=csrf(client),
        ),
        await client.put(
            f"/api/v1/admin/network/policies/{policy['id']}",
            headers=csrf(client),
            json={
                "revision": policy["revision"],
                "mode": "allow_all_private",
                "reason": "Must be reset-gated",
            },
        ),
        await client.post(
            "/api/v1/exports",
            headers=csrf(client),
            json={"format": "csv", "site_id": site_id},
        ),
        await client.post(
            "/api/v1/exports",
            headers=csrf(client),
            json={"format": "json"},
        ),
        await client.post(
            "/api/v1/admin/logs/exports",
            headers=csrf(client),
            json={},
        ),
    ]
    for response in requests:
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "data_reset_site_mutation_blocked"

    async with session_factory_fixture() as session:
        stored_account = await session.get(UtilityAccount, account["id"])
        stored_policy = await session.get(SensorNetworkPolicy, policy["id"])
        assert stored_account is not None and stored_account.nickname is None
        assert stored_policy is not None and stored_policy.revision == policy["revision"]
        cost_run_count = await session.scalar(select(func.count()).select_from(CostCalculationRun))
        assert int(cost_run_count or 0) == 0
        assert int(await session.scalar(select(func.count()).select_from(ExportJob)) or 0) == 0
        assert int(await session.scalar(select(func.count()).select_from(LogExportJob)) or 0) == 0


@pytest.mark.asyncio
async def test_log_export_rechecks_reset_barrier_before_building_archive(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: httpx.AsyncClient = api_client
    site_id = await bootstrap(client)
    test_settings.log_path = tmp_path / "logs"
    test_settings.log_path.mkdir(parents=True)
    now = datetime.now(UTC)
    (test_settings.log_path / f"api-{now.date().isoformat()}.jsonl").write_text(
        json.dumps({"event": "reset_barrier_test"}) + "\n",
        encoding="utf-8",
    )

    actual_barrier = log_routes._ensure_fleet_site_reset_mutations_allowed
    calls = 0

    async def inject_reset_before_build(session: AsyncSession) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            async with session_factory_fixture() as other_session:
                other_session.add_all(active_operation(site_id))
                await other_session.commit()
        await actual_barrier(session)

    monkeypatch.setattr(
        log_routes,
        "_ensure_fleet_site_reset_mutations_allowed",
        inject_reset_before_build,
    )
    response = await client.post(
        "/api/v1/admin/logs/exports",
        headers=csrf(client),
        json={},
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "data_reset_site_mutation_blocked"
    assert calls == 2

    async with session_factory_fixture() as session:
        jobs = list(await session.scalars(select(LogExportJob)))
        assert len(jobs) == 1
        assert jobs[0].status == "preparing"
        assert jobs[0].file_path is None
    assert not (test_settings.log_path / ".exports").exists()


@pytest.mark.asyncio
async def test_export_worker_does_not_publish_during_active_reset(
    session: AsyncSession,
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    site = Site(id=new_uuid(), name="Worker reset scope", code="worker-reset-scope")
    user = User(
        id=new_uuid(),
        email="worker-reset-scope@example.com",
        display_name="Worker Reset Scope",
        password_hash="not-used",
    )
    job = ExportJob(
        id=new_uuid(),
        requested_by=user.id,
        format="json",
        query={"site_id": site.id},
        status="queued",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add_all([site, user, job])
    session.add_all(active_operation(site.id))
    await session.commit()
    test_settings.report_path = tmp_path / "exports"

    assert await process_export_jobs(session, test_settings) == 0
    await session.refresh(job)
    assert job.status == "queued"
    assert job.file_path is None
    assert not test_settings.report_path.exists()


@pytest.mark.asyncio
async def test_rate_sync_and_due_activation_defer_during_active_reset(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    site = Site(id=new_uuid(), name="Rate worker reset scope", code="rate-worker-reset-scope")
    utility = Utility(id=new_uuid(), name="Rate worker reset utility")
    plan = RatePlan(
        id=new_uuid(),
        utility_id=utility.id,
        code="RATE-WORKER-RESET",
        name="Rate worker reset plan",
        owner_site_id=site.id,
    )
    version = RateVersion(
        id=new_uuid(),
        rate_plan_id=plan.id,
        version=1,
        effective_from=date.today(),
        timezone="America/Los_Angeles",
        currency="USD",
        source_url="https://example.invalid/rate-worker-reset",
        source_checked_on=date.today(),
        source_notes="Reset barrier fixture",
        content_hash="d" * 64,
        status="approved",
        created_at=datetime.now(UTC),
    )
    source = RateSource(
        id=new_uuid(),
        name="Rate worker reset source",
        url="https://www.sce.com/rate-worker-reset",
        parser_id="sce_public_tou_html_v1",
        enabled=True,
        consecutive_failures=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    job = BackgroundJob(
        id=new_uuid(),
        job_type="rate_source_sync",
        status="queued",
        requested_at=datetime.now(UTC),
        correlation_id=f"rate-reset-barrier-{new_uuid()}",
        dedupe_key=f"source:{source.id}",
        trigger_type="manual",
        progress={"source_ids": [source.id], "completed": 0, "total": 1},
        result={},
    )
    session.add_all([site, utility, plan, version, source, job])
    session.add_all(active_operation(site.id))
    await session.commit()
    test_settings.rate_sync_artifact_path = (
        Path(__file__).resolve().parents[2] / ".test-runtime" / f"rate-sync-{new_uuid()}"
    )

    assert await process_rate_sync_jobs(session, test_settings) == {
        "jobs_completed": 0,
        "source_failures": 0,
        "candidates": 0,
    }
    assert await activate_due_versions(session) == 0

    await session.refresh(job)
    await session.refresh(version)
    assert job.status == "queued"
    assert version.status == "approved"
    assert int(await session.scalar(select(func.count()).select_from(RateSourceCheckRun)) or 0) == 0
    assert int(await session.scalar(select(func.count()).select_from(RateSourceArtifact)) or 0) == 0
    assert not test_settings.rate_sync_artifact_path.exists()
