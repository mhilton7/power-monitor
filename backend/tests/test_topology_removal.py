from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.db.models import AggregateMember, AggregateSet, Circuit, Device


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    token = client.cookies.get("pm_csrf")
    assert token
    return {"X-CSRF-Token": token}


@pytest.mark.asyncio
async def test_circuit_removal_preserves_devices_and_reports_dependencies(
    api_client: Any,
    session: Any,
) -> None:
    client: httpx.AsyncClient = api_client
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "topology-owner@example.com",
            "display_name": "Topology Owner",
            "password": "Long-Production-Topology-Password-42!",
        },
    )
    assert bootstrap.status_code == 201, bootstrap.text
    site_id = (await client.get("/api/v1/sites")).json()[0]["id"]

    unused = Circuit(site_id=site_id, name="Unused branch", measurement_role="branch")
    assigned = Circuit(site_id=site_id, name="Assigned branch", measurement_role="branch")
    parent = Circuit(site_id=site_id, name="Parent branch", measurement_role="branch")
    aggregate_circuit = Circuit(
        site_id=site_id,
        name="Aggregate branch",
        measurement_role="branch",
    )
    session.add_all([unused, assigned, parent, aggregate_circuit])
    await session.flush()
    child = Circuit(
        site_id=site_id,
        parent_id=parent.id,
        name="Child branch",
        measurement_role="branch",
    )
    sensor = Device(
        site_id=site_id,
        circuit_id=assigned.id,
        hardware_id="topology-removal-sensor",
        name="Assigned sensor",
    )
    aggregate = AggregateSet(
        site_id=site_id,
        name="Protected aggregate",
        cost_scope="energy_only",
    )
    session.add_all([child, sensor, aggregate])
    await session.flush()
    session.add(
        AggregateMember(
            aggregate_set_id=aggregate.id,
            circuit_id=aggregate_circuit.id,
            allocation_percent=100,
        )
    )
    await session.commit()
    ids = {
        "unused": unused.id,
        "assigned": assigned.id,
        "parent": parent.id,
        "aggregate": aggregate_circuit.id,
        "sensor": sensor.id,
    }

    assigned_response = await client.delete(
        f"/api/v1/circuits/{ids['assigned']}", headers=csrf(client)
    )
    assert assigned_response.status_code == 409
    assert assigned_response.json()["code"] == "circuit_dependency_conflict"
    assert assigned_response.json()["dependencies"]["assigned_sensors"][0]["name"] == (
        "Assigned sensor"
    )

    child_response = await client.delete(f"/api/v1/circuits/{ids['parent']}", headers=csrf(client))
    assert child_response.status_code == 409
    assert child_response.json()["dependencies"]["child_circuits"][0]["name"] == ("Child branch")

    aggregate_response = await client.delete(
        f"/api/v1/circuits/{ids['aggregate']}", headers=csrf(client)
    )
    assert aggregate_response.status_code == 409
    assert aggregate_response.json()["dependencies"]["aggregate_sets"][0]["name"] == (
        "Protected aggregate"
    )

    removed = await client.delete(f"/api/v1/circuits/{ids['unused']}", headers=csrf(client))
    assert removed.status_code == 204, removed.text
    await session.rollback()
    session.expire_all()
    assert await session.get(Device, ids["sensor"]) is not None
    assert await session.get(Circuit, ids["unused"]) is None
    remaining_device_ids = set(await session.scalars(select(Device.id)))
    assert ids["sensor"] in remaining_device_ids
