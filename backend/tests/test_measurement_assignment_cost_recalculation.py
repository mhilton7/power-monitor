from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.security.protocol import PROTOCOL


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


async def bootstrap(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "topology-cost-owner@example.com",
            "display_name": "Topology and cost owner",
            "password": "Long-Production-Password-42!",
        },
    )
    assert response.status_code == 201, response.text
    return (await client.get("/api/v1/sites")).json()[0]


async def create_account(client: httpx.AsyncClient, site: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/utility-accounts",
        headers=csrf(client),
        json={
            "site_id": site["id"],
            "name": "Single Home Electric Service",
            "timezone": site["timezone"],
            "currency": site["currency"],
            "billing_cycle_start_day": 1,
            "generation_provider": "sce",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def enroll_sensor(
    client: httpx.AsyncClient,
    site_id: str,
    *,
    hardware_suffix: str = "0001",
) -> str:
    token = await client.post(
        "/api/v1/enrollment-tokens",
        headers=csrf(client),
        json={"site_id": site_id, "name": "Indoor AC"},
    )
    assert token.status_code == 201, token.text
    claim = await client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token.json()["token"],
            "protocol_version": PROTOCOL,
            "hardware_id": f"esp32s3-assignment-test-{hardware_suffix}",
            "capabilities": {
                "hardware_target": "esp32-s3-pzem004t-v4",
                "pzem_model": "PZEM-004T V4.0",
                "sd_present": True,
                "sd_required": True,
                "supported_endpoints": ["health", "readings"],
            },
        },
    )
    assert claim.status_code == 201, claim.text
    return str(claim.json()["device_id"])


@pytest.mark.asyncio
async def test_existing_sensor_can_be_assigned_to_circuit_and_account(
    api_client: httpx.AsyncClient,
) -> None:
    site = await bootstrap(api_client)
    account = await create_account(api_client, site)
    device_id = await enroll_sensor(api_client, site["id"])

    before = await api_client.get(f"/api/v1/configuration-status?site_id={site['id']}")
    assert before.status_code == 200, before.text
    assert "sensor.measurement-assignment-incomplete" in {
        issue["id"] for issue in before.json()["issues"]
    }

    circuit = await api_client.post(
        "/api/v1/circuits",
        headers=csrf(api_client),
        json={
            "site_id": site["id"],
            "parent_id": None,
            "name": "Indoor AC",
            "measurement_role": "branch",
            "split_phase_group": None,
        },
    )
    assert circuit.status_code == 201, circuit.text

    assigned = await api_client.put(
        f"/api/v1/admin/devices/{device_id}/measurement-assignment",
        headers=csrf(api_client),
        json={
            "circuit_id": circuit.json()["id"],
            "utility_account_id": account["id"],
            "include_in_default_site_total": True,
            "reason": "Owner verified this CT is installed on the Indoor AC branch",
        },
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json() == {
        "device_id": device_id,
        "site_id": site["id"],
        "circuit_id": circuit.json()["id"],
        "circuit_name": "Indoor AC",
        "utility_account_id": account["id"],
        "utility_account_name": "Single Home Electric Service",
        "measurement_role": "branch",
        "cost_scope": "energy_only",
        "included_in_default_site_total": True,
    }

    devices = await api_client.get(f"/api/v1/devices?site_id={site['id']}")
    assert devices.status_code == 200, devices.text
    sensor = next(item for item in devices.json() if item["id"] == device_id)
    assert sensor["circuit_id"] == circuit.json()["id"]
    assert sensor["utility_account_id"] == account["id"]
    assert sensor["measurement_role"] == "branch"
    assert sensor["included_in_default"] is True
    configured = await api_client.get(f"/api/v1/configuration-status?site_id={site['id']}")
    assert configured.status_code == 200, configured.text
    assert "sensor.measurement-assignment-incomplete" not in {
        issue["id"] for issue in configured.json()["issues"]
    }

    duplicate_device_id = await enroll_sensor(
        api_client,
        site["id"],
        hardware_suffix="0002",
    )
    duplicate = await api_client.put(
        f"/api/v1/admin/devices/{duplicate_device_id}/measurement-assignment",
        headers=csrf(api_client),
        json={
            "circuit_id": circuit.json()["id"],
            "utility_account_id": account["id"],
            "include_in_default_site_total": True,
            "reason": "This duplicate Home-total selection must be rejected",
        },
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "device_default_total_overlap"

    after = await api_client.get(f"/api/v1/configuration-status?site_id={site['id']}")
    assert after.status_code == 200, after.text
    assert "sensor.measurement-assignment-incomplete" in {
        issue["id"] for issue in after.json()["issues"]
    }

    audit = await api_client.get("/api/v1/audit-events?limit=100")
    assert audit.status_code == 200, audit.text
    assert any(
        event["action"] == "device.measurement_assignment_changed"
        and event["object_id"] == device_id
        for event in audit.json()
    )


@pytest.mark.asyncio
async def test_current_cycle_recalculation_creates_audited_cycle_without_guessing(
    api_client: httpx.AsyncClient,
) -> None:
    site = await bootstrap(api_client)
    account = await create_account(api_client, site)

    response = await api_client.post(
        f"/api/v1/admin/utility-accounts/{account['id']}/billing-cycles/current/recalculate",
        headers=csrf(api_client),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["available"] is False
    assert result["cycle"]["id"]
    assert result["recalculation_version"] == 0
    assert result["warnings"] == [
        "Configure an account-usage authority before calculating billing-cycle tiers."
    ]

    audit = await api_client.get("/api/v1/audit-events?limit=100")
    assert audit.status_code == 200, audit.text
    assert any(
        event["action"] == "billing_cycle.recalculated"
        and event["object_id"] == result["cycle"]["id"]
        for event in audit.json()
    )
