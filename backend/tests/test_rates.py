from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.rates.engine import RateEngine, load_seed_plans, project_billing_cycle


@pytest.mark.parametrize("code", ["TOU-D-4-9PM", "TOU-D-5-8PM", "TOU-D-PRIME"])
def test_seed_plans_cover_every_day(code: str) -> None:
    RateEngine(load_seed_plans()[code]).validate_plan()


def test_sce_summer_weekday_boundary() -> None:
    engine = RateEngine(load_seed_plans()["TOU-D-4-9PM"])
    result = engine.calculate(
        start=datetime(2026, 7, 20, 22, 30, tzinfo=UTC),  # 15:30 PDT
        end=datetime(2026, 7, 21, 0, 30, tzinfo=UTC),  # 17:30 PDT
        energy_kwh=Decimal("2"),
    )
    assert result.energy_by_bucket == {"off-peak": Decimal("0.5"), "on-peak": Decimal("1.5")}
    assert result.energy_charge == Decimal("1.04")


def test_dst_spring_and_fall_use_real_elapsed_seconds() -> None:
    engine = RateEngine(load_seed_plans()["TOU-D-4-9PM"])
    spring = engine.calculate(
        start=datetime(2026, 3, 8, 9, 30, tzinfo=UTC),
        end=datetime(2026, 3, 8, 10, 30, tzinfo=UTC),
        energy_kwh=Decimal("1"),
    )
    assert sum(spring.energy_by_bucket.values()) == Decimal("1")
    fall = engine.calculate(
        start=datetime(2026, 11, 1, 8, 30, tzinfo=UTC),
        end=datetime(2026, 11, 1, 10, 30, tzinfo=UTC),
        energy_kwh=Decimal("2"),
    )
    assert sum(fall.energy_by_bucket.values()) == Decimal("2")
    assert len(fall.slices) >= 2


def test_full_account_components_once_and_baseline_cap() -> None:
    engine = RateEngine(load_seed_plans()["TOU-D-4-9PM"])
    common = dict(
        start=datetime(2026, 7, 20, 7, tzinfo=UTC),
        end=datetime(2026, 7, 21, 7, tzinfo=UTC),
        energy_kwh=Decimal("20"),
        baseline_allocation_kwh=Decimal("10"),
        billing_days=1,
        cca_adjustment_per_kwh=Decimal("0.02"),
        other_adjustment=Decimal("-1.25"),
    )
    full = engine.calculate(cost_scope="full_account", **common)
    assert full.fixed_charge == Decimal("0.79")
    assert full.baseline_credit == Decimal("1.00")
    assert full.cca_adjustment == Decimal("0.40")
    assert full.other_adjustment == Decimal("-1.25")
    energy_only = engine.calculate(cost_scope="energy_only", **common)
    assert energy_only.fixed_charge == 0
    assert energy_only.baseline_credit == 0
    assert energy_only.other_adjustment == 0
    assert engine.display_currency(Decimal("1.005")) == Decimal("1.01")


def test_projection_methods_and_coverage_confidence() -> None:
    straight = project_billing_cycle(
        actual_energy_kwh=Decimal("100"),
        elapsed_seconds=10 * 86400,
        total_seconds=30 * 86400,
        method="straight_line",
    )
    assert straight.projected_energy_kwh == Decimal("300")
    assert straight.confidence == "medium"
    recent = project_billing_cycle(
        actual_energy_kwh=Decimal("100"),
        elapsed_seconds=10 * 86400,
        total_seconds=30 * 86400,
        method="recent_7_day",
        recent_daily_average_kwh=Decimal("12"),
    )
    assert recent.projected_energy_kwh == Decimal("340")
    profile = project_billing_cycle(
        actual_energy_kwh=Decimal("100"),
        elapsed_seconds=20 * 86400,
        total_seconds=30 * 86400,
        method="same_weekday_profile",
        profile_remaining_kwh=Decimal("115"),
    )
    assert profile.projected_energy_kwh == Decimal("215")
    assert profile.confidence == "high"


def test_summer_to_winter_and_local_midnight_boundaries_preserve_energy() -> None:
    engine = RateEngine(load_seed_plans()["TOU-D-5-8PM"])
    result = engine.calculate(
        start=datetime(2026, 10, 1, 6, 30, tzinfo=UTC),
        end=datetime(2026, 10, 1, 8, 30, tzinfo=UTC),
        energy_kwh=Decimal("2"),
    )
    assert sum(result.energy_by_bucket.values()) == Decimal("2")
    assert len(result.slices) >= 2
