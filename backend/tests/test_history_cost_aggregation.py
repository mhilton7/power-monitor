from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AggregateMember,
    AggregateSet,
    Circuit,
    Device,
    NormalizedInterval,
    RateAssignment,
    RatePlan,
    RateVersion,
    RawReading,
    Site,
    User,
    UserSite,
    Utility,
    UtilityAccount,
)
from app.ingestion.service import ingest_readings
from app.main import app
from app.schemas import Reading


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


def flat_rate_document() -> dict[str, Any]:
    return {
        "schema_version": "power-monitor-rate-plan/1.0",
        "plan_name": "Deterministic one-dollar history plan",
        "plan_code": "HISTORY-ONE-DOLLAR",
        "utility": "custom",
        "description": "Deterministic history fixture",
        "currency": "USD",
        "timezone": "America/Los_Angeles",
        "ownership_scope": "global",
        "owner_id": None,
        "effective_from": "2026-01-01",
        "effective_through": None,
        "cost_scope_default": "energy_only",
        "source_label": "Deterministic test fixture",
        "source_note": "Not a live utility source",
        "provider_mode": "custom_combined",
        "seasons": [
            {
                "name": "all-year",
                "start": "01-01",
                "end": "12-31",
                "priority": 0,
                "leap_day_behavior": "include",
                "schedules": [
                    {
                        "day_type": "all-days",
                        "dates": [],
                        "periods": [
                            {
                                "label": "test-peak",
                                "start_minute": 0,
                                "end_minute": 1440,
                                "price_per_kwh": "1.00000000",
                                "delivery_per_kwh": "0",
                                "generation_per_kwh": "0",
                                "adjustment_per_kwh": "0",
                                "display_order": 0,
                            }
                        ],
                    }
                ],
            }
        ],
        "adjustments": [],
        "custom_notes": "",
        "cloned_from_rate_version_id": None,
    }


async def seed_two_sensor_history(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[str, str, str, str]:
    async with factory() as session:
        site = await session.scalar(select(Site))
        utility = await session.scalar(select(Utility))
        assert site and utility
        account = UtilityAccount(
            site_id=site.id,
            utility_id=utility.id,
            name="History test account",
            timezone="America/Los_Angeles",
        )
        plan = RatePlan(
            utility_id=utility.id,
            code="HISTORY-ONE-DOLLAR",
            name="Deterministic one-dollar history plan",
            description="History fixture",
            plan_kind="custom",
            ownership_scope="global",
            currency="USD",
            timezone="America/Los_Angeles",
            status="active",
        )
        session.add_all([account, plan])
        await session.flush()
        version = RateVersion(
            rate_plan_id=plan.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            timezone="America/Los_Angeles",
            currency="USD",
            source_url="https://example.test/history-fixture",
            source_checked_on=date(2026, 7, 20),
            source_notes="Deterministic fixture",
            content_hash="a" * 64,
            immutable_after_use=True,
            is_active=True,
            status="active",
            source_kind="custom",
            normalized_payload=flat_rate_document(),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.add(version)
        await session.flush()
        account.active_rate_version_id = version.id
        session.add(
            RateAssignment(
                utility_account_id=account.id,
                rate_version_id=version.id,
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                effective_to=None,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        leg_one = Circuit(
            site_id=site.id,
            name="Main Panel L1",
            measurement_role="service-leg",
            split_phase_group="main-panel",
        )
        leg_two = Circuit(
            site_id=site.id,
            name="Main Panel L2",
            measurement_role="service-leg",
            split_phase_group="main-panel",
        )
        session.add_all([leg_one, leg_two])
        await session.flush()
        devices = [
            Device(
                site_id=site.id,
                utility_account_id=account.id,
                circuit_id=leg_one.id,
                hardware_id="history-leg-one",
                name="Main Panel L1",
                measurement_role="service-leg",
                include_in_default_site_total=True,
            ),
            Device(
                site_id=site.id,
                utility_account_id=account.id,
                circuit_id=leg_two.id,
                hardware_id="history-leg-two",
                name="Main Panel L2",
                measurement_role="service-leg",
                include_in_default_site_total=True,
            ),
        ]
        session.add_all(devices)
        await session.flush()
        for device_index, device in enumerate(devices):
            readings = [
                Reading(
                    sequence=hour + 1,
                    boot_id=f"00000000-0000-0000-0000-00000000000{device_index}",
                    interval_start=datetime(2026, 7, 21, 3 + hour, tzinfo=UTC),
                    interval_end=datetime(2026, 7, 21, 4 + hour, tzinfo=UTC),
                    time_trusted=True,
                    voltage_avg=Decimal("120"),
                    voltage_min=Decimal("119"),
                    voltage_max=Decimal("121"),
                    current_avg=Decimal("8.333333"),
                    power_avg=Decimal("1000"),
                    power_max=Decimal("1200"),
                    power_factor=Decimal("1"),
                    frequency_hz=Decimal("60"),
                    interval_energy_wh=Decimal("1000"),
                    energy_method="interval",
                    ct_rating_amps=Decimal("100"),
                    firmware_version="1.0.0",
                )
                for hour in range(2)
            ]
            result = await ingest_readings(
                session, device_id=device.id, readings=readings, source="push"
            )
            assert result.accepted == [1, 2]
        await session.commit()
        return site.id, devices[0].id, devices[1].id, version.id


@pytest.mark.asyncio
async def test_history_combines_two_sensors_and_calculates_exact_range_cost(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    site_id, first_id, second_id, version_id = await seed_two_sensor_history(
        session_factory_fixture
    )
    payload = {
        "scope": {"type": "devices", "device_ids": [first_id, second_id]},
        "display_mode": "combined_plus_individual",
        "metrics": ["power_w", "energy_kwh", "energy_cost", "usage_cost"],
        "start_utc": "2026-07-21T03:00:00Z",
        "end_utc": "2026-07-21T05:00:00Z",
        "bucket": "1h",
        "timezone": "America/Los_Angeles",
        "selection_start_utc": "2026-07-21T03:00:00Z",
        "selection_end_utc": "2026-07-21T05:00:00Z",
    }
    response = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["scope"]["site_id"] == site_id
    assert Decimal(data["summary"]["energy_kwh"]) == Decimal("4")
    assert Decimal(data["summary"]["energy_cost"]) == Decimal("4")
    assert data["selected_summary"]["energy_cost"] == data["summary"]["energy_cost"]
    assert len(data["combined"]) == 2
    assert len(data["individual"]) == 2
    assert all(Decimal(point["average_power_w"]) == Decimal("2000") for point in data["combined"])
    assert all(Decimal(point["energy_kwh"]) == Decimal("2") for point in data["combined"])
    assert all(Decimal(point["energy_cost"]) == Decimal("2") for point in data["combined"])
    assert all(Decimal(point["voltage_avg_v"]) == Decimal("120") for point in data["combined"])
    assert all(point["current_a"] is None for point in data["combined"])
    assert all(
        "aggregate_current_unavailable" in point["quality_flags"] for point in data["combined"]
    )
    assert {item["rate_version_id"] for item in data["rate_versions_used"]} == {version_id}

    paged = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={**payload, "page_size": 1},
    )
    assert paged.status_code == 200
    assert len(paged.json()["combined"]) == 1
    assert paged.json()["next_page"] == 2

    individual = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={**payload, "display_mode": "individual"},
    )
    assert individual.status_code == 200
    assert individual.json()["combined"] == []
    assert {series["device_id"] for series in individual.json()["individual"]} == {
        first_id,
        second_id,
    }

    legacy = await client.get(
        "/api/v1/readings/history",
        params={
            "device_id": first_id,
            "start": "2026-07-21T03:00:00Z",
            "end": "2026-07-21T05:00:00Z",
            "resolution": "raw",
        },
    )
    assert legacy.status_code == 200
    assert len(legacy.json()["points"]) == 2

    exported = await client.post("/api/v1/history/export", headers=csrf(client), json=payload)
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("text/csv")
    assert "power-monitor-history-export/1.0" in exported.text
    assert "interval_energy_cost" in exported.text
    assert version_id in exported.text


@pytest.mark.asyncio
async def test_history_rejects_parent_child_combined_selection(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    _, first_id, _, _ = await seed_two_sensor_history(session_factory_fixture)
    async with session_factory_fixture() as session:
        first = await session.get(Device, first_id)
        assert first and first.circuit_id
        child = Circuit(
            site_id=first.site_id,
            parent_id=first.circuit_id,
            name="Child branch",
            measurement_role="branch",
        )
        session.add(child)
        await session.flush()
        child_device = Device(
            site_id=first.site_id,
            utility_account_id=first.utility_account_id,
            circuit_id=child.id,
            hardware_id="history-child",
            name="Child branch sensor",
            measurement_role="branch",
        )
        session.add(child_device)
        await session.commit()
        child_id = child_device.id
    response = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={
            "scope": {"type": "devices", "device_ids": [first_id, child_id]},
            "display_mode": "combined",
            "metrics": ["power_w"],
            "start_utc": "2026-07-21T03:00:00Z",
            "end_utc": "2026-07-21T05:00:00Z",
            "bucket": "1h",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "history_topology_overlap"

    individual = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={
            "scope": {"type": "devices", "device_ids": [first_id, child_id]},
            "display_mode": "individual",
            "metrics": ["power_w", "energy_kwh", "energy_cost"],
            "start_utc": "2026-07-21T03:00:00Z",
            "end_utc": "2026-07-21T05:00:00Z",
            "selection_start_utc": "2026-07-21T03:00:00Z",
            "selection_end_utc": "2026-07-21T05:00:00Z",
            "bucket": "1h",
        },
    )
    assert individual.status_code == 200, individual.text
    result = individual.json()
    assert result["combined"] == []
    assert len(result["individual"]) == 2
    assert result["summary"]["energy_kwh"] is None
    assert result["summary"]["average_power_w"] is None
    assert result["selected_summary"]["energy_cost"] is None
    assert result["selected_summary"]["tou_breakdown"] == {}
    assert any(warning["code"] == "topology_overlap" for warning in result["warnings"])


@pytest.mark.asyncio
async def test_history_supports_circuit_site_aggregate_and_partial_strict_coverage(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    site_id, first_id, second_id, _ = await seed_two_sensor_history(session_factory_fixture)
    async with session_factory_fixture() as session:
        first = await session.get(Device, first_id)
        second = await session.get(Device, second_id)
        assert first and second and first.circuit_id and first.utility_account_id
        aggregate = AggregateSet(
            site_id=site_id,
            utility_account_id=first.utility_account_id,
            name="Two-leg saved aggregate",
            cost_scope="energy_only",
        )
        session.add(aggregate)
        await session.flush()
        session.add_all(
            [
                AggregateMember(aggregate_set_id=aggregate.id, device_id=first_id),
                AggregateMember(aggregate_set_id=aggregate.id, device_id=second_id),
            ]
        )
        missing_raw = await session.scalar(
            select(RawReading).where(RawReading.device_id == second_id, RawReading.sequence == 2)
        )
        assert missing_raw
        await session.execute(
            delete(NormalizedInterval).where(NormalizedInterval.raw_reading_id == missing_raw.id)
        )
        await session.delete(missing_raw)
        await session.commit()
        aggregate_id = aggregate.id
        circuit_id = first.circuit_id

    base = {
        "display_mode": "combined",
        "metrics": ["energy_kwh", "energy_cost"],
        "start_utc": "2026-07-21T03:00:00Z",
        "end_utc": "2026-07-21T05:00:00Z",
        "bucket": "1h",
    }
    for scope in (
        {"type": "circuit", "circuit_id": circuit_id},
        {"type": "site", "site_id": site_id},
        {"type": "aggregate_set", "aggregate_set_id": aggregate_id},
    ):
        response = await client.post(
            "/api/v1/history/query", headers=csrf(client), json={**base, "scope": scope}
        )
        assert response.status_code == 200, response.text

    partial = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={
            **base,
            "scope": {"type": "devices", "device_ids": [first_id, second_id]},
        },
    )
    second_bucket = partial.json()["combined"][1]
    assert Decimal(second_bucket["coverage_percent"]) == Decimal("50")
    assert Decimal(second_bucket["energy_kwh"]) == Decimal("1")
    assert second_id in second_bucket["missing_sensor_ids"]
    assert "partial_coverage" in second_bucket["quality_flags"]

    strict = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={
            **base,
            "scope": {"type": "devices", "device_ids": [first_id, second_id]},
            "strict_coverage": True,
        },
    )
    strict_bucket = strict.json()["combined"][1]
    assert strict_bucket["energy_kwh"] is None
    assert strict_bucket["energy_cost"] is None
    assert "strict_coverage_withheld" in strict_bucket["quality_flags"]


@pytest.mark.asyncio
async def test_history_uses_assignment_version_boundaries_and_never_guesses_missing_rate(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    _, first_id, second_id, first_version_id = await seed_two_sensor_history(
        session_factory_fixture
    )
    async with session_factory_fixture() as session:
        first_version = await session.get(RateVersion, first_version_id)
        assert first_version
        plan = await session.get(RatePlan, first_version.rate_plan_id)
        account = await session.scalar(
            select(UtilityAccount).where(UtilityAccount.name == "History test account")
        )
        assert plan and account
        two_dollar = flat_rate_document()
        two_dollar["seasons"][0]["schedules"][0]["periods"][0]["price_per_kwh"] = "2.00000000"
        second_version = RateVersion(
            rate_plan_id=plan.id,
            version=2,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            timezone="America/Los_Angeles",
            currency="USD",
            source_url="https://example.test/history-fixture-v2",
            source_checked_on=date(2026, 7, 20),
            source_notes="Deterministic second fixture",
            content_hash="b" * 64,
            immutable_after_use=True,
            is_active=True,
            status="active",
            source_kind="custom",
            normalized_payload=two_dollar,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.add(second_version)
        await session.flush()
        assignment = await session.scalar(
            select(RateAssignment).where(RateAssignment.utility_account_id == account.id)
        )
        assert assignment
        boundary = datetime(2026, 7, 21, 4, tzinfo=UTC)
        assignment.effective_to = boundary
        session.add(
            RateAssignment(
                utility_account_id=account.id,
                rate_version_id=second_version.id,
                effective_from=boundary,
                effective_to=None,
                created_at=boundary,
            )
        )
        account.active_rate_version_id = second_version.id
        await session.commit()
        second_version_id = second_version.id

    payload = {
        "scope": {"type": "devices", "device_ids": [first_id, second_id]},
        "display_mode": "combined",
        "metrics": ["energy_kwh", "energy_cost"],
        "start_utc": "2026-07-21T03:00:00Z",
        "end_utc": "2026-07-21T05:00:00Z",
        "bucket": "1h",
    }
    response = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert Decimal(result["summary"]["energy_cost"]) == Decimal("6")
    assert Decimal(result["combined"][0]["energy_cost"]) == Decimal("2")
    assert Decimal(result["combined"][1]["energy_cost"]) == Decimal("4")
    assert result["scope"]["mixed_rates"] is True
    assert {item["rate_version_id"] for item in result["rate_versions_used"]} == {
        first_version_id,
        second_version_id,
    }

    async with session_factory_fixture() as session:
        account = await session.scalar(
            select(UtilityAccount).where(UtilityAccount.name == "History test account")
        )
        assert account
        await session.execute(
            delete(RateAssignment).where(RateAssignment.utility_account_id == account.id)
        )
        account.active_rate_version_id = None
        await session.commit()
    unavailable = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert unavailable.status_code == 200
    unavailable_result = unavailable.json()
    assert unavailable_result["summary"]["energy_cost"] is None
    assert all(point["energy_cost"] is None for point in unavailable_result["combined"])
    assert any(warning["code"] == "rate_unavailable" for warning in unavailable_result["warnings"])


@pytest.mark.asyncio
async def test_history_enforces_site_scope_cross_site_and_query_limits(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    primary_site_id, first_id, second_id, _ = await seed_two_sensor_history(session_factory_fixture)
    created = await client.post(
        "/api/v1/users",
        headers=csrf(client),
        json={
            "email": "history-viewer@example.com",
            "display_name": "Scoped history viewer",
            "password": "Production-History-Viewer-42!",
            "roles": ["viewer"],
        },
    )
    assert created.status_code == 201, created.text
    async with session_factory_fixture() as session:
        viewer = await session.get(User, created.json()["id"])
        assert viewer
        viewer.all_sites = False
        other_site = Site(
            name="Unauthorized history site",
            timezone="America/Los_Angeles",
            allowed_cidrs=[],
            allowed_domains=[],
            allow_public_polling=False,
        )
        session.add(other_site)
        await session.flush()
        other_device = Device(
            site_id=other_site.id,
            hardware_id="history-other-site",
            name="Hidden site sensor",
        )
        session.add_all(
            [
                UserSite(user_id=viewer.id, site_id=primary_site_id),
                other_device,
            ]
        )
        await session.commit()
        other_site_id = other_site.id
        other_device_id = other_device.id

    base = {
        "display_mode": "combined",
        "metrics": ["energy_kwh"],
        "start_utc": "2026-07-21T03:00:00Z",
        "end_utc": "2026-07-21T05:00:00Z",
        "bucket": "1h",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as scoped_client:
        login = await scoped_client.post(
            "/api/v1/auth/login",
            json={
                "email": "history-viewer@example.com",
                "password": "Production-History-Viewer-42!",
            },
        )
        assert login.status_code == 200
        allowed = await scoped_client.post(
            "/api/v1/history/query",
            headers=csrf(scoped_client),
            json={**base, "scope": {"type": "device", "device_id": first_id}},
        )
        assert allowed.status_code == 200
        denied = await scoped_client.post(
            "/api/v1/history/query",
            headers=csrf(scoped_client),
            json={**base, "scope": {"type": "site", "site_id": other_site_id}},
        )
        assert denied.status_code == 404
        assert denied.json()["code"] == "resource_missing"
        assert "Unauthorized history site" not in denied.text

        duplicate = await scoped_client.post(
            "/api/v1/history/query",
            headers=csrf(scoped_client),
            json={
                **base,
                "scope": {"type": "devices", "device_ids": [first_id, first_id]},
            },
        )
        assert duplicate.status_code == 422

        too_many = await scoped_client.post(
            "/api/v1/history/query",
            headers=csrf(scoped_client),
            json={
                **base,
                "scope": {
                    "type": "devices",
                    "device_ids": [f"device-{index}" for index in range(33)],
                },
            },
        )
        assert too_many.status_code == 422

        too_long = await scoped_client.post(
            "/api/v1/history/query",
            headers=csrf(scoped_client),
            json={
                **base,
                "scope": {"type": "device", "device_id": first_id},
                "start_utc": "2025-01-01T00:00:00Z",
            },
        )
        assert too_long.status_code == 422
        assert too_long.json()["code"] == "history_range_limit"

    cross_site = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={
            **base,
            "scope": {
                "type": "devices",
                "device_ids": [second_id, other_device_id],
            },
        },
    )
    assert cross_site.status_code == 422
    assert cross_site.json()["code"] == "history_cross_site"
