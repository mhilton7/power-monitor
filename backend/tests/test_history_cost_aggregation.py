from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AggregateMember,
    AggregateSet,
    BillingCycle,
    Circuit,
    Device,
    NormalizedInterval,
    RateAssignment,
    RatePlan,
    RateTierDefinition,
    RateVersion,
    RawReading,
    Site,
    SiteDataState,
    TierAllocationSegment,
    User,
    UserSite,
    Utility,
    UtilityAccount,
)
from app.history import TierSegmentIndex
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


def canonical_numeric_values(value: Any) -> Any:
    """Compare JSON numerics by exact value, independent of Decimal exponent."""
    if isinstance(value, dict):
        return {key: canonical_numeric_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [canonical_numeric_values(item) for item in value]
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return value
    return value


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


def tier_rate_document() -> dict[str, Any]:
    document = flat_rate_document()
    document.update(
        {
            "pricing_model": "tiered",
            "flat_rate_per_kwh": None,
            "seasons": [],
            "billing_cycle": {
                "expected_start_day": 1,
                "threshold": {
                    "basis": "fixed_cycle_kwh",
                    "daily_baseline_kwh": None,
                    "baseline_region": None,
                    "baseline_category": None,
                    "rounding_policy": "none",
                    "seasonal_baselines": [],
                    "source_citation": "History equivalence fixture",
                },
            },
            "tiers": [
                {
                    "tier_id": "tier-1",
                    "name": "Tier 1",
                    "order": 0,
                    "lower_bound_inclusive_kwh": "0",
                    "upper_bound_exclusive_kwh": "1",
                    "lower_bound_multiplier": None,
                    "upper_bound_multiplier": None,
                    "price_per_kwh": "0.30",
                    "tou_prices": {},
                    "season": None,
                    "source_citation": "History equivalence fixture",
                },
                {
                    "tier_id": "tier-2",
                    "name": "Tier 2",
                    "order": 1,
                    "lower_bound_inclusive_kwh": "1",
                    "upper_bound_exclusive_kwh": None,
                    "lower_bound_multiplier": None,
                    "upper_bound_multiplier": None,
                    "price_per_kwh": "0.40",
                    "tou_prices": {},
                    "season": None,
                    "source_citation": "History equivalence fixture",
                },
            ],
            "hybrid_pricing": None,
        }
    )
    return document


async def convert_seed_to_tiered_history(
    factory: async_sessionmaker[AsyncSession], version_id: str
) -> None:
    async with factory() as session:
        version = await session.get(RateVersion, version_id)
        assert version
        version.pricing_model = "tiered"
        version.normalized_payload = tier_rate_document()
        account = await session.scalar(
            select(UtilityAccount).where(UtilityAccount.active_rate_version_id == version_id)
        )
        assert account
        definitions = [
            RateTierDefinition(
                rate_version_id=version_id,
                stable_tier_id="tier-1",
                name="Tier 1",
                display_order=0,
                lower_bound_kwh=Decimal("0"),
                upper_bound_kwh=Decimal("1"),
                price_per_kwh=Decimal("0.30"),
            ),
            RateTierDefinition(
                rate_version_id=version_id,
                stable_tier_id="tier-2",
                name="Tier 2",
                display_order=1,
                lower_bound_kwh=Decimal("1"),
                upper_bound_kwh=None,
                price_per_kwh=Decimal("0.40"),
            ),
        ]
        cycle = BillingCycle(
            utility_account_id=account.id,
            starts_at=datetime(2026, 7, 1, tzinfo=UTC),
            ends_at=datetime(2026, 8, 1, tzinfo=UTC),
            status="confirmed",
            recalculation_version=1,
        )
        session.add_all([*definitions, cycle])
        await session.flush()
        intervals = list(
            await session.scalars(
                select(NormalizedInterval)
                .where(
                    NormalizedInterval.device_id.in_(
                        select(Device.id).where(Device.utility_account_id == account.id)
                    )
                )
                .order_by(NormalizedInterval.interval_start, NormalizedInterval.device_id)
            )
        )
        assert len(intervals) == 4
        for index, interval in enumerate(intervals):
            tier_index = 0 if index == 0 else 1
            tier = definitions[tier_index]
            cumulative = Decimal(index)
            for recalculation_version, cost_multiplier in (
                (0, Decimal("100")),
                (1, Decimal("1")),
            ):
                session.add(
                    TierAllocationSegment(
                        billing_cycle_id=cycle.id,
                        utility_account_id=account.id,
                        normalized_interval_id=interval.id,
                        segment_order=0,
                        interval_start=interval.interval_start,
                        interval_end=interval.interval_end,
                        rate_version_id=version_id,
                        tier_definition_id=tier.id,
                        tier_stable_id=tier.stable_tier_id,
                        tier_name=tier.name,
                        tou_period=None,
                        cumulative_start_kwh=cumulative,
                        cumulative_end_kwh=cumulative + Decimal("1"),
                        segment_energy_kwh=Decimal("1"),
                        price_per_kwh=tier.price_per_kwh,
                        unrounded_energy_charge=(tier.price_per_kwh * cost_multiplier),
                        usage_authority_type="sensor_measurements",
                        recalculation_version=recalculation_version,
                        created_at=datetime(2026, 7, 21, tzinfo=UTC),
                    )
                )
        await session.commit()


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
        "metrics": [
            "power_w",
            "energy_kwh",
            "energy_cost",
            "usage_cost",
            "voltage_v",
            "current_a",
        ],
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
async def test_site_history_includes_only_active_sensor_without_explicit_default(
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
        assert first is not None and second is not None
        first.include_in_default_site_total = False
        second.include_in_default_site_total = False
        second.lifecycle_status = "removed"
        await session.commit()

    response = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={
            "scope": {"type": "site", "site_id": site_id},
            "display_mode": "combined_plus_individual",
            "metrics": ["power_w", "energy_kwh"],
            "start_utc": "2026-07-21T03:00:00Z",
            "end_utc": "2026-07-21T05:00:00Z",
            "bucket": "1h",
            "timezone": "America/Los_Angeles",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["combined"]) == 2
    assert {series["device_id"] for series in data["individual"]} == {first_id}
    assert Decimal(data["summary"]["energy_kwh"]) == Decimal("2")
    assert any(warning["code"] == "single_sensor_site_fallback" for warning in data["warnings"])


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


@pytest.mark.asyncio
async def test_history_execution_plan_skips_unrequested_cost_work_and_series(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    _, first_id, _, _ = await seed_two_sensor_history(session_factory_fixture)

    def unexpected_individual_bucket(**_kwargs: object) -> None:
        raise AssertionError("combined-only history constructed an individual series")

    monkeypatch.setattr("app.history._individual_bucket", unexpected_individual_bucket)
    engine = session_factory_fixture.kw["bind"]
    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement.lower())

    event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
    try:
        base = {
            "scope": {"type": "device", "device_id": first_id},
            "display_mode": "combined",
            "start_utc": "2026-07-21T03:00:00Z",
            "end_utc": "2026-07-21T05:00:00Z",
            "bucket": "1h",
            "timezone": "America/Los_Angeles",
        }
        power_response = await client.post(
            "/api/v1/history/query",
            headers=csrf(client),
            json={**base, "metrics": ["power_w"]},
        )
        assert power_response.status_code == 200, power_response.text
        assert power_response.json()["individual"] == []
        assert any("raw_readings" in statement for statement in statements)
        assert not any("normalized_intervals" in statement for statement in statements)
        assert not any("rate_assignments" in statement for statement in statements)
        assert not any("rate_versions" in statement for statement in statements)
        assert not any("tier_allocation_segments" in statement for statement in statements)

        statements.clear()
        energy_response = await client.post(
            "/api/v1/history/query",
            headers=csrf(client),
            json={**base, "metrics": ["energy_kwh"]},
        )
        assert energy_response.status_code == 200, energy_response.text
        assert any("normalized_intervals" in statement for statement in statements)
        assert not any("rate_assignments" in statement for statement in statements)
        assert not any("rate_versions" in statement for statement in statements)
        assert not any("tier_allocation_segments" in statement for statement in statements)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metrics", "display_mode"),
    [
        (["power_w"], "combined"),
        (["energy_kwh"], "individual"),
        (
            ["power_w", "energy_kwh", "energy_cost", "usage_cost"],
            "combined_plus_individual",
        ),
    ],
)
async def test_coarse_history_is_exactly_equivalent_to_raw_history(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    metrics: list[str],
    display_mode: str,
) -> None:
    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    _, first_id, second_id, _ = await seed_two_sensor_history(session_factory_fixture)
    payload = {
        "scope": {"type": "devices", "device_ids": [first_id, second_id]},
        "display_mode": display_mode,
        "metrics": metrics,
        "start_utc": "2026-07-21T03:00:00Z",
        "end_utc": "2026-07-21T05:00:00Z",
        "bucket": "1h",
        "timezone": "America/Los_Angeles",
        "selection_start_utc": "2026-07-21T03:30:00Z",
        "selection_end_utc": "2026-07-21T04:30:00Z",
        "page_size": 1,
    }
    monkeypatch.setattr("app.history.COARSE_HISTORY_BUCKETS", set())
    raw_response = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert raw_response.status_code == 200, raw_response.text

    monkeypatch.setattr("app.history.COARSE_HISTORY_BUCKETS", {"1h", "1d"})
    coarse_response = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert coarse_response.status_code == 200, coarse_response.text
    coarse_payload = coarse_response.json()
    raw_payload = raw_response.json()
    assert coarse_payload.pop("next_continuation_token")
    assert raw_payload.pop("next_continuation_token")
    assert canonical_numeric_values(coarse_payload) == canonical_numeric_values(raw_payload)


@pytest.mark.asyncio
async def test_history_continuation_reuses_exact_summary_and_bounds_detail_work(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.history as history_module

    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    _, first_id, second_id, _ = await seed_two_sensor_history(session_factory_fixture)
    payload = {
        "scope": {"type": "devices", "device_ids": [first_id, second_id]},
        "display_mode": "combined_plus_individual",
        "metrics": ["power_w", "energy_kwh", "energy_cost"],
        "start_utc": "2026-07-21T03:00:00Z",
        "end_utc": "2026-07-21T05:00:00Z",
        "bucket": "1h",
        "timezone": "America/Los_Angeles",
        "page_size": 1,
    }
    observed_window_counts: list[int] = []
    original_loader = history_module._load_coarse_measurements

    async def observed_loader(*args: Any, **kwargs: Any) -> Any:
        observed_window_counts.append(len(kwargs["boundaries"]) - 1)
        return await original_loader(*args, **kwargs)

    monkeypatch.setattr(history_module, "_load_coarse_measurements", observed_loader)
    first_response = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    assert observed_window_counts == [2]
    assert first["next_page"] == 2
    token = first["next_continuation_token"]
    assert token

    missing_token = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={**payload, "page": 2},
    )
    assert missing_token.status_code == 409
    assert missing_token.json()["code"] == "history_continuation_required"

    observed_window_counts.clear()
    second_response = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={**payload, "page": 2, "continuation_token": token},
    )
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()
    assert observed_window_counts == [1]
    assert second["summary"] == first["summary"]
    assert second["selected_summary"] == first["selected_summary"]
    assert second["rate_versions_used"] == first["rate_versions_used"]
    assert second["warnings"] == first["warnings"]
    assert second["combined"][0]["interval_start_utc"] == "2026-07-21T04:00:00Z"
    assert second["next_page"] is None
    assert second["next_continuation_token"] is None

    tampered = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={**payload, "page": 2, "continuation_token": f"{token}x"},
    )
    assert tampered.status_code == 409
    assert tampered.json()["code"] == "history_continuation_invalid"


@pytest.mark.asyncio
async def test_history_continuation_is_invalidated_by_data_reset_revision(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    site_id, first_id, second_id, _ = await seed_two_sensor_history(session_factory_fixture)
    payload = {
        "scope": {"type": "devices", "device_ids": [first_id, second_id]},
        "display_mode": "combined_plus_individual",
        "metrics": ["power_w", "energy_kwh", "energy_cost"],
        "start_utc": "2026-07-21T03:00:00Z",
        "end_utc": "2026-07-21T05:00:00Z",
        "bucket": "1h",
        "timezone": "America/Los_Angeles",
        "page_size": 1,
    }
    first_response = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert first_response.status_code == 200, first_response.text
    token = first_response.json()["next_continuation_token"]
    assert token

    async with session_factory_fixture() as session:
        session.add(
            SiteDataState(
                site_id=site_id,
                data_generation=1,
                history_revision=1,
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()

    continuation = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={**payload, "page": 2, "continuation_token": token},
    )
    assert continuation.status_code == 409, continuation.text
    assert continuation.json()["code"] == "history_continuation_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed_input",
    ["rate_assignment", "rate_version", "billing_cycle_recalculation"],
)
async def test_history_continuation_rejects_changed_pricing_snapshot(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    changed_input: str,
) -> None:
    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    _, first_id, second_id, version_id = await seed_two_sensor_history(session_factory_fixture)
    if changed_input == "billing_cycle_recalculation":
        await convert_seed_to_tiered_history(session_factory_fixture, version_id)

    payload = {
        "scope": {"type": "devices", "device_ids": [first_id, second_id]},
        "display_mode": "combined_plus_individual",
        "metrics": ["energy_kwh", "energy_cost"],
        "start_utc": "2026-07-21T03:00:00Z",
        "end_utc": "2026-07-21T05:00:00Z",
        "bucket": "1h",
        "timezone": "America/Los_Angeles",
        "page_size": 1,
    }
    first_response = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert first_response.status_code == 200, first_response.text
    token = first_response.json()["next_continuation_token"]
    assert token

    async with session_factory_fixture() as session:
        if changed_input == "rate_assignment":
            assignment = await session.scalar(
                select(RateAssignment).where(RateAssignment.rate_version_id == version_id)
            )
            assert assignment
            assignment.effective_to = datetime(2026, 7, 21, 4, tzinfo=UTC)
        elif changed_input == "rate_version":
            version = await session.get(RateVersion, version_id)
            assert version
            changed_document = flat_rate_document()
            changed_document["seasons"][0]["schedules"][0]["periods"][0]["price_per_kwh"] = (
                "2.00000000"
            )
            version.normalized_payload = changed_document
            version.content_hash = "b" * 64
        else:
            cycle = await session.scalar(
                select(BillingCycle).where(BillingCycle.recalculation_version == 1)
            )
            assert cycle
            cycle.recalculation_version = 0
        await session.commit()

    continuation = await client.post(
        "/api/v1/history/query",
        headers=csrf(client),
        json={**payload, "page": 2, "continuation_token": token},
    )
    assert continuation.status_code == 409, continuation.text
    problem = continuation.json()
    assert problem["code"] == "history_continuation_pricing_changed"
    assert problem["detail"] == "Restart the History query from page 1"


@pytest.mark.asyncio
async def test_coarse_history_uses_only_current_exact_tier_allocations(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    _, first_id, second_id, version_id = await seed_two_sensor_history(session_factory_fixture)
    await convert_seed_to_tiered_history(session_factory_fixture, version_id)
    payload = {
        "scope": {"type": "devices", "device_ids": [first_id, second_id]},
        "display_mode": "combined_plus_individual",
        "metrics": ["energy_kwh", "energy_cost"],
        "start_utc": "2026-07-21T03:00:00Z",
        "end_utc": "2026-07-21T05:00:00Z",
        "bucket": "1h",
        "timezone": "America/Los_Angeles",
    }

    monkeypatch.setattr("app.history.COARSE_HISTORY_BUCKETS", set())
    raw_response = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert raw_response.status_code == 200, raw_response.text
    raw = raw_response.json()
    assert Decimal(raw["summary"]["energy_cost"]) == Decimal("1.50")
    assert {
        item["recalculation_version"]
        for point in raw["combined"]
        for item in point["rate_contributions"]
    } == {1}

    monkeypatch.setattr("app.history.COARSE_HISTORY_BUCKETS", {"1h", "1d"})
    coarse_response = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert coarse_response.status_code == 200, coarse_response.text
    assert canonical_numeric_values(coarse_response.json()) == canonical_numeric_values(raw)


@pytest.mark.asyncio
async def test_coarse_history_falls_back_for_account_level_tier_segments(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    _, first_id, second_id, version_id = await seed_two_sensor_history(session_factory_fixture)
    await convert_seed_to_tiered_history(session_factory_fixture, version_id)
    async with session_factory_fixture() as session:
        segments = list(await session.scalars(select(TierAllocationSegment)))
        assert segments
        for segment in segments:
            segment.normalized_interval_id = None
        await session.commit()
    payload = {
        "scope": {"type": "devices", "device_ids": [first_id, second_id]},
        "display_mode": "combined_plus_individual",
        "metrics": ["energy_kwh", "energy_cost"],
        "start_utc": "2026-07-21T03:00:00Z",
        "end_utc": "2026-07-21T05:00:00Z",
        "bucket": "1h",
        "timezone": "America/Los_Angeles",
    }
    monkeypatch.setattr("app.history.COARSE_HISTORY_BUCKETS", set())
    raw_response = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert raw_response.status_code == 200, raw_response.text
    monkeypatch.setattr("app.history.COARSE_HISTORY_BUCKETS", {"1h", "1d"})
    fallback_response = await client.post(
        "/api/v1/history/query", headers=csrf(client), json=payload
    )
    assert fallback_response.status_code == 200, fallback_response.text
    assert canonical_numeric_values(fallback_response.json()) == canonical_numeric_values(
        raw_response.json()
    )


@pytest.mark.asyncio
async def test_coarse_history_matches_raw_across_dst_fold_and_partial_buckets(
    api_client: object,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = api_client
    assert isinstance(client, httpx.AsyncClient)
    await bootstrap(client)
    _, first_id, second_id, _ = await seed_two_sensor_history(session_factory_fixture)
    async with session_factory_fixture() as session:
        readings = list(
            await session.scalars(
                select(RawReading)
                .where(RawReading.device_id.in_([first_id, second_id]))
                .order_by(RawReading.device_id, RawReading.sequence)
            )
        )
        for reading in readings:
            offset = reading.sequence - 1
            reading.interval_start = datetime(2026, 11, 1, 7, 30, tzinfo=UTC) + offset * timedelta(
                hours=1
            )
            reading.interval_end = reading.interval_start + timedelta(hours=1)
            normalized = await session.scalar(
                select(NormalizedInterval).where(NormalizedInterval.raw_reading_id == reading.id)
            )
            assert normalized
            normalized.interval_start = reading.interval_start
            normalized.interval_end = reading.interval_end
        await session.commit()

    payload = {
        "scope": {"type": "devices", "device_ids": [first_id, second_id]},
        "display_mode": "combined_plus_individual",
        "metrics": ["power_w", "energy_kwh", "energy_cost"],
        "start_utc": "2026-11-01T07:30:00Z",
        "end_utc": "2026-11-01T10:30:00Z",
        "bucket": "1h",
        "timezone": "America/Los_Angeles",
    }
    monkeypatch.setattr("app.history.COARSE_HISTORY_BUCKETS", set())
    raw_response = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert raw_response.status_code == 200, raw_response.text
    monkeypatch.setattr("app.history.COARSE_HISTORY_BUCKETS", {"1h", "1d"})
    coarse_response = await client.post("/api/v1/history/query", headers=csrf(client), json=payload)
    assert coarse_response.status_code == 200, coarse_response.text
    assert canonical_numeric_values(coarse_response.json()) == canonical_numeric_values(
        raw_response.json()
    )
    local_starts = [item["local_start"] for item in coarse_response.json()["combined"]]
    assert any("-07:00" in value for value in local_starts)
    assert any("-08:00" in value for value in local_starts)


def test_tier_segment_index_prefers_exact_interval_and_bounds_fallback_search() -> None:
    start = datetime(2026, 7, 21, tzinfo=UTC)
    segments = [
        TierAllocationSegment(
            rate_version_id="version-1",
            normalized_interval_id=f"interval-{index}",
            interval_start=start.replace(minute=index),
            interval_end=start.replace(minute=index + 1),
            segment_order=index,
        )
        for index in range(30)
    ]
    index = TierSegmentIndex.build(segments)
    exact, used_fallback = index.overlapping(
        version_id="version-1",
        normalized_interval_id="interval-17",
        start=start.replace(minute=17),
        end=start.replace(minute=18),
    )
    assert not used_fallback
    assert [item.segment.normalized_interval_id for item in exact] == ["interval-17"]

    fallback, used_fallback = index.overlapping(
        version_id="version-1",
        normalized_interval_id="missing",
        start=start.replace(minute=17, second=30),
        end=start.replace(minute=18, second=30),
    )
    assert used_fallback
    assert [item.segment.normalized_interval_id for item in fallback] == [
        "interval-17",
        "interval-18",
    ]
