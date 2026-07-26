from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from worker.app import rate_sync as rate_sync_worker
from worker.app.rate_sync import process_rate_sync_jobs

from app.db.models import (
    AuditEvent,
    RateAssignment,
    RateSyncConfiguration,
    UtilityAccountAdjustment,
)
from app.rates.documents import RatePlanDocument
from app.rates.sources import FetchResult


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


async def bootstrap(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "rate-workflow@example.com",
            "display_name": "Rate workflow administrator",
            "password": "Long-Production-Password-42!",
        },
    )
    assert response.status_code == 201, response.text


async def published_versions(
    client: httpx.AsyncClient,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    plans = (await client.get("/api/v1/rates/plans")).json()
    return [
        (plan, version)
        for plan in plans
        for version in plan["versions"]
        if version["publication_status"] == "published"
    ]


def account_payload(version_id: str, *, name: str) -> dict[str, Any]:
    return {
        "name": name,
        "nickname": name,
        "account_number_suffix": "4321",
        "utility_provider": "sce",
        "generation_provider": "sce",
        "provider_mode": "sce_bundled",
        "billing_cycle_start_day": 1,
        "currency": "USD",
        "service_class": "Residential",
        "rate_assignment": {
            "rate_version_id": version_id,
            "effective_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "assignment_reason": "Initial workflow fixture assignment",
        },
        "cost_scope": "energy_only",
        "adjustments": [],
        "confirmation": True,
    }


async def create_account(
    client: httpx.AsyncClient,
    *,
    version_id: str,
    name: str,
) -> dict[str, Any]:
    site = (await client.get("/api/v1/sites")).json()[0]
    response = await client.post(
        f"/api/v1/admin/sites/{site['id']}/utility-accounts",
        headers=csrf(client),
        json=account_payload(version_id, name=name),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_atomic_current_replacement_scheduling_overlap_and_idempotency(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap(api_client)
    versions = await published_versions(api_client)
    assert len(versions) >= 3
    account = await create_account(
        api_client,
        version_id=versions[0][1]["id"],
        name="Atomic assignment fixture",
    )

    replaced_at = datetime.now(UTC)
    replacement_payload = {
        "utility_account_id": account["id"],
        "rate_version_id": versions[1][1]["id"],
        "effective_from": replaced_at.isoformat(),
        "effective_to": None,
        "assignment_reason": "Owner selected a different current plan",
        "replace_current": True,
        "idempotency_key": "atomic-replace-current-0001",
        "confirmation": "REPLACE CURRENT",
    }
    replaced = await api_client.post(
        "/api/v1/rates/assignments/replace",
        headers=csrf(api_client),
        json=replacement_payload,
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["effective_now"] is True
    assert len(replaced.json()["replaced_assignment_ids"]) == 1
    assert replaced.json()["idempotent"] is False

    repeated = await api_client.post(
        "/api/v1/rates/assignments/replace",
        headers=csrf(api_client),
        json=replacement_payload,
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["assignment_id"] == replaced.json()["assignment_id"]
    assert repeated.json()["idempotent"] is True

    future_boundary = replaced_at + timedelta(days=30)
    scheduled = await api_client.post(
        "/api/v1/rates/assignments/replace",
        headers=csrf(api_client),
        json={
            **replacement_payload,
            "rate_version_id": versions[2][1]["id"],
            "effective_from": future_boundary.isoformat(),
            "assignment_reason": "Schedule the next published plan at a clear boundary",
            "idempotency_key": "atomic-future-replace-0001",
        },
    )
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["effective_now"] is False

    rows = [
        row
        for row in (await api_client.get("/api/v1/rates/assignments")).json()
        if row["utility_account_id"] == account["id"]
    ]
    current = [row for row in rows if row["state"] == "current"]
    future = [row for row in rows if row["state"] == "scheduled"]
    assert len(current) == 1
    assert len(future) == 1
    assert datetime.fromisoformat(current[0]["effective_to"]) == datetime.fromisoformat(
        future[0]["effective_from"]
    )

    overlap = await api_client.post(
        f"/api/v1/admin/utility-accounts/{account['id']}/rate-assignments",
        headers=csrf(api_client),
        json={
            "rate_version_id": versions[0][1]["id"],
            "effective_from": (future_boundary + timedelta(hours=1)).isoformat(),
            "assignment_reason": "This intentionally overlaps the scheduled plan",
            "replace_current": False,
        },
    )
    assert overlap.status_code == 409
    assert overlap.json()["code"] == "rate_assignment_overlap"

    cancelled = await api_client.delete(
        f"/api/v1/rates/assignments/{future[0]['id']}",
        headers=csrf(api_client),
    )
    assert cancelled.status_code == 204, cancelled.text
    after_cancel = [
        row
        for row in (await api_client.get("/api/v1/rates/assignments")).json()
        if row["utility_account_id"] == account["id"]
    ]
    assert [row for row in after_cancel if row["state"] == "scheduled"] == []
    assert len([row for row in after_cancel if row["state"] == "current"]) == 1

    end_payload = {
        "utility_account_id": account["id"],
        "effective_at": datetime.now(UTC).isoformat(),
        "reason": "Owner explicitly ended the current rate assignment",
        "confirmation": "END CURRENT",
        "idempotency_key": "end-current-assignment-0001",
    }
    ended = await api_client.post(
        "/api/v1/rates/assignments/end",
        headers=csrf(api_client),
        json=end_payload,
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["cost_estimates_available_after"] is False
    assert ended.json()["idempotent"] is False
    repeated_end = await api_client.post(
        "/api/v1/rates/assignments/end",
        headers=csrf(api_client),
        json=end_payload,
    )
    assert repeated_end.status_code == 200, repeated_end.text
    assert repeated_end.json()["assignment_id"] == ended.json()["assignment_id"]
    assert repeated_end.json()["idempotent"] is True
    final_rows = [
        row
        for row in (await api_client.get("/api/v1/rates/assignments")).json()
        if row["utility_account_id"] == account["id"]
    ]
    assert [row for row in final_rows if row["state"] == "current"] == []
    assert any(row["state"] == "historical" for row in final_rows)


@pytest.mark.asyncio
async def test_existing_assignment_conflict_requires_explicit_repair(
    api_client: httpx.AsyncClient,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    await bootstrap(api_client)
    versions = await published_versions(api_client)
    account = await create_account(
        api_client,
        version_id=versions[0][1]["id"],
        name="Conflict repair fixture",
    )
    async with session_factory_fixture() as session:
        keeper = await session.scalar(
            select(RateAssignment).where(RateAssignment.utility_account_id == account["id"])
        )
        assert keeper is not None
        conflict = RateAssignment(
            utility_account_id=account["id"],
            rate_version_id=versions[1][1]["id"],
            effective_from=keeper.effective_from + timedelta(hours=1),
            assignment_reason="Legacy overlapping assignment fixture",
            created_at=datetime.now(UTC),
        )
        session.add(conflict)
        await session.commit()
        keeper_id = keeper.id
        conflict_id = conflict.id

    report = await api_client.get("/api/v1/rates/assignments/conflicts")
    assert report.status_code == 200, report.text
    conflict_report = next(
        item for item in report.json()["conflicts"] if item["utility_account_id"] == account["id"]
    )
    expected_ids = sorted(item["assignment_id"] for item in conflict_report["assignments"])
    assert expected_ids == sorted([keeper_id, conflict_id])

    blocked = await api_client.post(
        "/api/v1/rates/assignments/replace",
        headers=csrf(api_client),
        json={
            "utility_account_id": account["id"],
            "rate_version_id": versions[2][1]["id"],
            "effective_from": datetime.now(UTC).isoformat(),
            "assignment_reason": "Conflict must be repaired before replacement",
            "replace_current": True,
            "idempotency_key": "blocked-conflict-replace-0001",
            "confirmation": "REPLACE CURRENT",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "rate_assignment_repair_required"

    repair_payload = {
        "utility_account_id": account["id"],
        "keep_assignment_id": keeper_id,
        "expected_assignment_ids": expected_ids,
        "reason": "Owner selected the authoritative current assignment",
        "confirmation": "REPAIR ASSIGNMENTS",
        "idempotency_key": "repair-assignment-conflict-0001",
    }
    repaired = await api_client.post(
        "/api/v1/rates/assignments/conflicts/resolve",
        headers=csrf(api_client),
        json=repair_payload,
    )
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["kept_assignment_id"] == keeper_id
    assert repaired.json()["ended_assignment_ids"] == [conflict_id]
    assert repaired.json()["history_preserved"] is True

    repeated = await api_client.post(
        "/api/v1/rates/assignments/conflicts/resolve",
        headers=csrf(api_client),
        json=repair_payload,
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["idempotent"] is True
    assert (await api_client.get("/api/v1/rates/assignments/conflicts")).json()[
        "requires_explicit_resolution"
    ] is False


def custom_document(code: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "shared" / "examples" / "custom-rate-plan.json"
    document = RatePlanDocument.model_validate_json(path.read_bytes())
    return document.model_copy(
        update={
            "plan_code": code,
            "plan_name": f"{code} revision fixture",
            "effective_from": date.today(),
        }
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_same_plan_adjustment_version_publish_remove_restore_and_draft_delete(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap(api_client)
    document = custom_document("SAME-PLAN-REVISION")
    created = await api_client.post(
        "/api/v1/rates/plans",
        headers=csrf(api_client),
        json=document,
    )
    assert created.status_code == 201, created.text
    plan = created.json()["plan"]
    first = plan["versions"][0]
    published = await api_client.post(
        f"/api/v1/rates/versions/{first['id']}/activate",
        headers=csrf(api_client),
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"

    drafted = await api_client.post(
        f"/api/v1/rates/plans/{plan['id']}/versions",
        headers=csrf(api_client),
    )
    assert drafted.status_code == 201, drafted.text
    second = drafted.json()
    assert second["parent_version_id"] == first["id"]
    assert second["version"] == 2
    assert second["reused"] is False
    reopened = await api_client.post(
        f"/api/v1/rates/plans/{plan['id']}/versions",
        headers=csrf(api_client),
    )
    assert reopened.status_code == 201
    assert reopened.json()["id"] == second["id"]
    assert reopened.json()["reused"] is True

    edited_document = {
        **document,
        "description": "Administrator-adjusted revision under the same plan identity",
    }
    edited = await api_client.patch(
        f"/api/v1/rates/versions/{second['id']}",
        headers=csrf(api_client),
        json=edited_document,
    )
    assert edited.status_code == 200, edited.text
    second_published = await api_client.post(
        f"/api/v1/rates/versions/{second['id']}/activate",
        headers=csrf(api_client),
    )
    assert second_published.status_code == 200, second_published.text

    versions = (await api_client.get(f"/api/v1/rates/plans/{plan['id']}/versions")).json()
    first_after = next(item for item in versions if item["id"] == first["id"])
    second_after = next(item for item in versions if item["id"] == second["id"])
    assert first_after["publication_status"] == "superseded"
    assert second_after["publication_status"] == "published"
    assert all(item["assignment_status"] == "unassigned" for item in versions)

    account = await create_account(
        api_client,
        version_id=second["id"],
        name="Version removal dependency fixture",
    )
    blocked_current = await api_client.post(
        f"/api/v1/rates/versions/{second['id']}/remove",
        headers=csrf(api_client),
        json={
            "expected_revision": second_after["lifecycle_revision"],
            "reason": "This current version must remain protected",
            "confirmation": str(second_after["version"]),
            "idempotency_key": "blocked-current-version-remove-0001",
        },
    )
    assert blocked_current.status_code == 409
    assert blocked_current.json()["code"] == "rate_version_assignment_blocked"
    ended = await api_client.post(
        "/api/v1/rates/assignments/end",
        headers=csrf(api_client),
        json={
            "utility_account_id": account["id"],
            "effective_at": datetime.now(UTC).isoformat(),
            "reason": "End the dependency fixture before later lifecycle work",
            "confirmation": "END CURRENT",
            "idempotency_key": "end-version-dependency-fixture-0001",
        },
    )
    assert ended.status_code == 200, ended.text

    removed = await api_client.post(
        f"/api/v1/rates/versions/{first['id']}/remove",
        headers=csrf(api_client),
        json={
            "expected_revision": first_after["lifecycle_revision"],
            "reason": "Preserve superseded history while removing it from the library",
            "confirmation": "1",
            "idempotency_key": "remove-version-history-0001",
        },
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["version"]["publication_status"] == "removed"
    restored = await api_client.post(
        f"/api/v1/rates/versions/{first['id']}/restore",
        headers=csrf(api_client),
        json={
            "expected_revision": removed.json()["version"]["lifecycle_revision"],
            "reason": "Restore the superseded version to the library",
            "idempotency_key": "restore-version-history-0001",
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"]["publication_status"] == "superseded"
    assert restored.json()["assignments_restored"] is False

    third = await api_client.post(
        f"/api/v1/rates/plans/{plan['id']}/versions",
        headers=csrf(api_client),
    )
    assert third.status_code == 201, third.text
    deleted = await api_client.request(
        "DELETE",
        f"/api/v1/rates/versions/{third.json()['id']}/draft",
        headers=csrf(api_client),
        json={
            "expected_revision": third.json()["lifecycle_revision"],
            "reason": "Discard the unused adjustment draft",
            "confirmation": str(third.json()["version"]),
            "idempotency_key": "delete-unused-version-0001",
        },
    )
    assert deleted.status_code == 204, deleted.text


@pytest.mark.asyncio
async def test_source_check_is_deduplicated_observable_retryable_and_audited(
    api_client: httpx.AsyncClient,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    test_settings: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await bootstrap(api_client)
    sources = (await api_client.get("/api/v1/admin/rate-sources")).json()["sources"]
    source = next(item for item in sources if item["enabled"])
    requested = await api_client.post(
        f"/api/v1/admin/rate-sources/{source['id']}/check",
        headers={**csrf(api_client), "Idempotency-Key": "source-run-request-0001"},
    )
    assert requested.status_code == 202, requested.text
    duplicate = await api_client.post(
        f"/api/v1/admin/rate-sources/{source['id']}/check",
        headers={**csrf(api_client), "Idempotency-Key": "source-run-request-0002"},
    )
    assert duplicate.status_code == 202, duplicate.text
    assert duplicate.json()["job_id"] == requested.json()["job_id"]
    assert duplicate.json()["deduplicated"] is True

    async def not_modified(*_args: Any, **_kwargs: Any) -> FetchResult:
        return FetchResult(
            status_code=304,
            final_url=source["url"],
            content=b"",
            content_type="text/html",
            etag='"unchanged"',
            last_modified="Sat, 25 Jul 2026 12:00:00 GMT",
            duration_ms=7,
        )

    monkeypatch.setattr(rate_sync_worker, "fetch_source", not_modified)
    async with session_factory_fixture() as session:
        configuration = await session.get(RateSyncConfiguration, "default")
        assert configuration is not None
        configuration.enabled = False
        await session.commit()
        result = await process_rate_sync_jobs(
            session,
            test_settings.model_copy(
                update={"rate_sync_artifact_path": tmp_path / "observable-source-artifacts"}
            ),
        )
        assert result["jobs_completed"] == 1

    detail = await api_client.get(
        f"/api/v1/admin/rate-sources/check-runs/{requested.json()['job_id']}"
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "succeeded"
    assert body["progress"]["completed"] == body["progress"]["total"] == 1
    assert body["successes"] == 1
    assert body["failures"] == 0
    assert body["items"][0]["outcome"] == "not_modified"
    assert body["items"][0]["finished_at"]

    same_key = await api_client.post(
        f"/api/v1/admin/rate-sources/{source['id']}/check",
        headers={**csrf(api_client), "Idempotency-Key": "source-run-request-0001"},
    )
    assert same_key.status_code == 202
    assert same_key.json()["job_id"] == requested.json()["job_id"]
    assert same_key.json()["deduplicated"] is True

    retry = await api_client.post(
        f"/api/v1/admin/rate-sources/{source['id']}/check",
        headers={**csrf(api_client), "Idempotency-Key": "source-run-retry-0001"},
    )
    assert retry.status_code == 202, retry.text
    assert retry.json()["job_id"] != requested.json()["job_id"]
    history = await api_client.get("/api/v1/admin/rate-sources/check-runs")
    assert history.status_code == 200
    assert requested.json()["job_id"] in {item["id"] for item in history.json()}

    async with session_factory_fixture() as session:
        actions = set(
            await session.scalars(
                select(AuditEvent.action).where(AuditEvent.object_id == requested.json()["job_id"])
            )
        )
        assert {
            "rate_source.check_requested",
            "rate_source.check_started",
            "rate_source.check_completed",
        } <= actions


@pytest.mark.asyncio
async def test_effective_dated_adjustment_edit_revision_remove_and_audit(
    api_client: httpx.AsyncClient,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    await bootstrap(api_client)
    version = (await published_versions(api_client))[0][1]
    account = await create_account(
        api_client,
        version_id=version["id"],
        name="Adjustment lifecycle fixture",
    )
    payload = {
        "component": "custom_per_kwh",
        "value": "0.01250000",
        "unit": "per_kwh",
        "provenance": "Administrator-entered tariff evidence",
        "reason": "Apply the reviewed generation adjustment",
        "evidence_reference": "Local tariff worksheet page 2",
        "effective_from": datetime.now(UTC).isoformat(),
        "effective_to": None,
        "enabled": True,
    }
    created = await api_client.post(
        f"/api/v1/admin/utility-accounts/{account['id']}/adjustments",
        headers=csrf(api_client),
        json=payload,
    )
    assert created.status_code == 201, created.text
    adjustment = created.json()
    assert adjustment["revision"] == 1

    updated = await api_client.patch(
        f"/api/v1/admin/utility-accounts/{account['id']}/adjustments/{adjustment['id']}",
        headers=csrf(api_client),
        json={**payload, "value": "0.01500000", "revision": 1},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    stale = await api_client.patch(
        f"/api/v1/admin/utility-accounts/{account['id']}/adjustments/{adjustment['id']}",
        headers=csrf(api_client),
        json={**payload, "revision": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "adjustment_revision_conflict"

    removed = await api_client.delete(
        f"/api/v1/admin/utility-accounts/{account['id']}/adjustments/{adjustment['id']}?revision=2",
        headers=csrf(api_client),
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["history_preserved"] is True
    assert (
        await api_client.get(f"/api/v1/admin/utility-accounts/{account['id']}/adjustments")
    ).json() == []

    async with session_factory_fixture() as session:
        stored = await session.get(UtilityAccountAdjustment, adjustment["id"])
        assert stored is not None
        assert stored.status == "removed"
        assert stored.value == Decimal("0.01500000")
        assert (
            await session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_id == adjustment["id"])
            )
            == 3
        )


@pytest.mark.asyncio
async def test_viewer_cannot_mutate_assignments_versions_sources_or_adjustments(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap(api_client)
    plan, version = (await published_versions(api_client))[0]
    account = await create_account(
        api_client,
        version_id=version["id"],
        name="Permission enforcement fixture",
    )
    created = await api_client.post(
        "/api/v1/users",
        headers=csrf(api_client),
        json={
            "email": "rate-viewer@example.com",
            "display_name": "Rate workflow viewer",
            "password": "Long-Production-Password-43!",
            "roles": ["viewer"],
        },
    )
    assert created.status_code == 201, created.text
    logged_out = await api_client.post("/api/v1/auth/logout", headers=csrf(api_client))
    assert logged_out.status_code == 204
    logged_in = await api_client.post(
        "/api/v1/auth/login",
        json={
            "email": "rate-viewer@example.com",
            "password": "Long-Production-Password-43!",
        },
    )
    assert logged_in.status_code == 200, logged_in.text

    denied_requests = [
        await api_client.post(
            "/api/v1/rates/assignments/end",
            headers=csrf(api_client),
            json={
                "utility_account_id": account["id"],
                "effective_at": datetime.now(UTC).isoformat(),
                "reason": "Viewer must not end a current assignment",
                "confirmation": "END CURRENT",
                "idempotency_key": "viewer-end-denied-0001",
            },
        ),
        await api_client.post(
            f"/api/v1/rates/versions/{version['id']}/remove",
            headers=csrf(api_client),
            json={
                "expected_revision": version["lifecycle_revision"],
                "reason": "Viewer must not remove a rate version",
                "confirmation": str(version["version"]),
                "idempotency_key": "viewer-version-remove-denied-0001",
            },
        ),
        await api_client.post(
            "/api/v1/admin/rate-sources/check-now",
            headers=csrf(api_client),
        ),
        await api_client.post(
            f"/api/v1/admin/utility-accounts/{account['id']}/adjustments",
            headers=csrf(api_client),
            json={
                "component": "custom_per_kwh",
                "value": "0.01000000",
                "unit": "per_kwh",
                "provenance": "Unauthorized viewer attempt",
                "reason": "Viewer must not create an adjustment",
                "effective_from": datetime.now(UTC).isoformat(),
                "effective_to": None,
                "enabled": True,
            },
        ),
    ]
    assert plan["id"]
    assert [response.status_code for response in denied_requests] == [403, 403, 403, 403]
