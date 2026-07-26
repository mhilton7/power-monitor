from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


async def bootstrap(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "configuration-status@example.com",
            "display_name": "Configuration status owner",
            "password": "Long-Production-Password-42!",
        },
    )
    assert response.status_code == 201, response.text


async def published_versions(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    plans = (await client.get("/api/v1/rates/plans")).json()
    return [
        version
        for plan in plans
        for version in plan["versions"]
        if version["publication_status"] == "published"
    ]


@pytest.mark.asyncio
async def test_set_current_updates_canonical_service_and_configuration_status(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap(api_client)
    site = (await api_client.get("/api/v1/sites")).json()[0]
    before = await api_client.get(f"/api/v1/configuration-status?site_id={site['id']}")
    assert before.status_code == 200, before.text
    assert before.json()["state"] == "setup_needed"
    assert "electric-service.missing" in {issue["id"] for issue in before.json()["issues"]}

    created = await api_client.post(
        "/api/v1/utility-accounts",
        headers=csrf(api_client),
        json={
            "site_id": site["id"],
            "name": "Single Home Electric Service",
            "timezone": site["timezone"],
            "currency": site["currency"],
            "billing_cycle_start_day": 1,
            "generation_provider": "sce",
        },
    )
    assert created.status_code == 201, created.text
    service_id = created.json()["id"]
    unconfigured = await api_client.get("/api/v1/utility-accounts")
    assert unconfigured.status_code == 200, unconfigured.text
    service = next(item for item in unconfigured.json() if item["id"] == service_id)
    assert service["rate_context"]["state"] == "no_rate_assignment"
    assert service["rate_context"]["current_plan"] is None

    versions = await published_versions(api_client)
    assert len(versions) >= 2
    assigned = await api_client.post(
        "/api/v1/rates/assignments/replace",
        headers=csrf(api_client),
        json={
            "utility_account_id": service_id,
            "rate_version_id": versions[0]["id"],
            "effective_from": datetime.now(UTC).isoformat(),
            "effective_to": None,
            "assignment_reason": "Owner selected the current Single Home rate",
            "replace_current": True,
            "confirmation": "REPLACE CURRENT",
            "idempotency_key": "configuration-current-plan-0001",
            "expected_account_revision": service["revision"],
        },
    )
    assert assigned.status_code == 200, assigned.text
    result = assigned.json()
    assert result["schema_version"] == "rate-assignment-result/1.0"
    assert result["electric_service_id"] == service_id
    assert result["version_id"] == versions[0]["id"]
    assert result["state"] == "current"
    assert result["effective_now"] is True
    assert result["service_revision"] == service["revision"] + 1

    canonical = await api_client.get("/api/v1/utility-accounts")
    assert canonical.status_code == 200, canonical.text
    service_after = next(item for item in canonical.json() if item["id"] == service_id)
    assert service_after["rate_context"]["state"] == "rate_configured_effective"
    assert service_after["rate_context"]["rate_version_id"] == versions[0]["id"]
    assert service_after["rate_context"]["current_assignment_id"] == result["assignment_id"]
    assert service_after["rate_context"]["current_assignment_revision"] == 1

    current = await api_client.get(
        f"/api/v1/electric-services/default/current-rate-assignment?site_id={site['id']}"
    )
    assert current.status_code == 200, current.text
    assert current.json()["assignment"]["assignment_id"] == result["assignment_id"]
    assert current.json()["assignment"]["version_id"] == versions[0]["id"]
    assert current.json()["assignment"]["state"] == "current"

    status = await api_client.get(f"/api/v1/configuration-status?site_id={site['id']}")
    assert status.status_code == 200, status.text
    issue_ids = {issue["id"] for issue in status.json()["issues"]}
    assert "rate-assignment.missing" not in issue_ids
    assert "rate-assignment.invalid" not in issue_ids
    assert "sensor.missing" in issue_ids

    stale = await api_client.post(
        "/api/v1/rates/assignments/replace",
        headers=csrf(api_client),
        json={
            "utility_account_id": service_id,
            "rate_version_id": versions[1]["id"],
            "effective_from": datetime.now(UTC).isoformat(),
            "effective_to": None,
            "assignment_reason": "Stale browser must not replace the current plan",
            "replace_current": True,
            "confirmation": "REPLACE CURRENT",
            "idempotency_key": "configuration-current-plan-stale-0001",
            "expected_account_revision": service["revision"],
            "expected_current_assignment_revision": 1,
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "stale_electric_service"
    assert stale.json()["blockers"][0]["action"] == "reload"
