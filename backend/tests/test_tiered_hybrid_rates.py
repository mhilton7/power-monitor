from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.rates.documents import RatePlanDocument, engine_plan
from app.rates.engine import RateEngine, project_billing_cycle
from app.rates.tiered import import_quality, normalized_import_rows

CYCLE_START = datetime(2026, 7, 22, 7, tzinfo=UTC)
CYCLE_END = datetime(2026, 8, 20, 7, tzinfo=UTC)


def test_shared_tiered_fixture_matches_schema_and_reference_allocation() -> None:
    path = Path(__file__).resolve().parents[2] / "shared" / "examples" / "tiered-rate-plan.json"
    document = RatePlanDocument.model_validate_json(path.read_text(encoding="utf-8"))
    result = RateEngine(engine_plan(document)).calculate(
        start=CYCLE_START,
        end=CYCLE_END,
        energy_kwh=Decimal("951"),
        cycle_start=CYCLE_START,
        cycle_end=CYCLE_END,
    )
    assert json.loads(path.read_text(encoding="utf-8"))["source_note"] == (
        "Not a production utility tariff."
    )
    assert result.energy_by_tier == {
        "Tier 1": Decimal("579"),
        "Tier 2": Decimal("372"),
    }
    assert result.energy_charge == Decimal("322.50")


def tiered_plan(
    *,
    upper: str = "579",
    threshold: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "code": "DOMESTIC",
        "timezone": "America/Los_Angeles",
        "pricing_model": "tiered",
        "billing_cycle": {
            "expected_start_day": 22,
            "threshold": threshold
            or {
                "basis": "fixed_cycle_kwh",
                "rounding_policy": "none",
                "seasonal_baselines": [],
            },
        },
        "tiers": [
            {
                "tier_id": "tier-1",
                "name": "Tier 1",
                "order": 0,
                "lower_bound_inclusive_kwh": "0",
                "upper_bound_exclusive_kwh": upper,
                "lower_bound_multiplier": None,
                "upper_bound_multiplier": None,
                "price_per_kwh": "0.30",
                "tou_prices": {},
            },
            {
                "tier_id": "tier-2",
                "name": "Tier 2",
                "order": 1,
                "lower_bound_inclusive_kwh": upper,
                "upper_bound_exclusive_kwh": None,
                "lower_bound_multiplier": None,
                "upper_bound_multiplier": None,
                "price_per_kwh": "0.40",
                "tou_prices": {},
            },
        ],
        "adjustments": [],
    }


def test_reference_usage_is_allocated_chronologically_without_hard_coding_engine() -> None:
    result = RateEngine(tiered_plan()).calculate(
        start=CYCLE_START,
        end=CYCLE_END,
        energy_kwh=Decimal("951"),
        cycle_start=CYCLE_START,
        cycle_end=CYCLE_END,
    )

    assert result.energy_by_tier == {
        "Tier 1": Decimal("579"),
        "Tier 2": Decimal("372"),
    }
    assert result.charge_by_tier == {
        "Tier 1": Decimal("173.70"),
        "Tier 2": Decimal("148.80"),
    }
    assert result.energy_charge == Decimal("322.50")


def test_bill_reported_usage_is_reference_only_when_pricing_sensor_usage() -> None:
    plan = tiered_plan()
    plan["tiers"] = [
        {**plan["tiers"][0], "price_per_kwh": "0.32"},  # type: ignore[index]
        {**plan["tiers"][1], "price_per_kwh": "0.42"},  # type: ignore[index]
    ]
    bill_reported_usage = Decimal("850")
    sensor_measured_usage = Decimal("100")

    result = RateEngine(plan).calculate(
        start=CYCLE_START,
        end=CYCLE_END,
        energy_kwh=sensor_measured_usage,
        cycle_start=CYCLE_START,
        cycle_end=CYCLE_END,
    )

    assert bill_reported_usage == Decimal("850")  # retained reference fixture
    assert result.energy_by_tier == {"Tier 1": Decimal("100")}
    assert result.energy_charge == Decimal("32.00")


def test_bill_usage_is_not_a_projection_fallback_for_sensor_trend() -> None:
    bill_reported_usage = Decimal("1200")
    projection = project_billing_cycle(
        actual_energy_kwh=Decimal("100"),
        elapsed_seconds=7 * 86400,
        total_seconds=28 * 86400,
        method="straight_line",
    )

    assert bill_reported_usage == Decimal("1200")  # retained reference fixture
    assert projection.projected_energy_kwh == Decimal("400")
    assert projection.method == "straight_line"


def test_six_hundred_sensor_kwh_is_split_chronologically_across_tiers() -> None:
    plan = tiered_plan()
    plan["tiers"] = [
        {**plan["tiers"][0], "price_per_kwh": "0.32"},  # type: ignore[index]
        {**plan["tiers"][1], "price_per_kwh": "0.42"},  # type: ignore[index]
    ]

    result = RateEngine(plan).calculate(
        start=CYCLE_START,
        end=CYCLE_END,
        energy_kwh=Decimal("600"),
        cycle_start=CYCLE_START,
        cycle_end=CYCLE_END,
    )

    assert result.energy_by_tier == {
        "Tier 1": Decimal("579"),
        "Tier 2": Decimal("21"),
    }
    assert result.charge_by_tier == {
        "Tier 1": Decimal("185.28"),
        "Tier 2": Decimal("8.82"),
    }
    assert result.energy_charge == Decimal("194.10")


def test_interval_crossing_threshold_splits_at_exact_cumulative_boundary() -> None:
    result = RateEngine(tiered_plan()).calculate(
        start=datetime(2026, 7, 30, 7, tzinfo=UTC),
        end=datetime(2026, 7, 30, 8, tzinfo=UTC),
        energy_kwh=Decimal("10"),
        cumulative_usage_before_kwh=Decimal("575"),
        cycle_start=CYCLE_START,
        cycle_end=CYCLE_END,
    )

    assert [value.energy_kwh for value in result.slices] == [
        Decimal("4"),
        Decimal("6"),
    ]
    assert [value.cumulative_end_kwh for value in result.slices] == [
        Decimal("579"),
        Decimal("585"),
    ]
    assert result.energy_charge == Decimal("3.60")


def test_three_tiers_allocate_sequentially_and_support_multiple_crossings() -> None:
    plan = tiered_plan(upper="100")
    plan["tiers"] = [
        {
            **plan["tiers"][0],  # type: ignore[index]
            "upper_bound_exclusive_kwh": "100",
            "price_per_kwh": "0.10",
        },
        {
            **plan["tiers"][1],  # type: ignore[index]
            "tier_id": "tier-2",
            "name": "Tier 2",
            "order": 1,
            "lower_bound_inclusive_kwh": "100",
            "upper_bound_exclusive_kwh": "250",
            "price_per_kwh": "0.20",
        },
        {
            **plan["tiers"][1],  # type: ignore[index]
            "tier_id": "tier-3",
            "name": "Tier 3",
            "order": 2,
            "lower_bound_inclusive_kwh": "250",
            "upper_bound_exclusive_kwh": None,
            "price_per_kwh": "0.50",
        },
    ]
    result = RateEngine(plan).calculate(
        start=CYCLE_START,
        end=CYCLE_END,
        energy_kwh=Decimal("300"),
        cycle_start=CYCLE_START,
        cycle_end=CYCLE_END,
    )

    assert result.energy_by_tier == {
        "Tier 1": Decimal("100"),
        "Tier 2": Decimal("150"),
        "Tier 3": Decimal("50"),
    }
    assert result.energy_charge == Decimal("65.00")


def test_usage_starting_at_exact_boundary_enters_next_tier_without_zero_slice() -> None:
    result = RateEngine(tiered_plan(upper="100")).calculate(
        start=CYCLE_START,
        end=CYCLE_START.replace(hour=8),
        energy_kwh=Decimal("1"),
        cumulative_usage_before_kwh=Decimal("100"),
        cycle_start=CYCLE_START,
        cycle_end=CYCLE_END,
    )

    assert [(item.tier_name, item.energy_kwh) for item in result.slices] == [
        ("Tier 2", Decimal("1")),
    ]
    assert result.energy_charge == Decimal("0.40")


@pytest.mark.parametrize(
    ("start", "end", "expected_baseline"),
    [
        (
            datetime(2026, 2, 1, 8, tzinfo=UTC),
            datetime(2026, 3, 1, 8, tzinfo=UTC),
            Decimal("280"),
        ),
        (
            datetime(2028, 2, 1, 8, tzinfo=UTC),
            datetime(2028, 3, 1, 8, tzinfo=UTC),
            Decimal("290"),
        ),
        (
            datetime(2026, 4, 1, 7, tzinfo=UTC),
            datetime(2026, 5, 1, 7, tzinfo=UTC),
            Decimal("300"),
        ),
        (
            datetime(2026, 7, 1, 7, tzinfo=UTC),
            datetime(2026, 8, 1, 7, tzinfo=UTC),
            Decimal("310"),
        ),
    ],
)
def test_daily_baseline_threshold_uses_exact_local_cycle_days(
    start: datetime, end: datetime, expected_baseline: Decimal
) -> None:
    plan = tiered_plan(
        threshold={
            "basis": "daily_baseline_kwh",
            "daily_baseline_kwh": "10",
            "rounding_policy": "none",
            "seasonal_baselines": [],
        }
    )
    plan["tiers"] = [
        {
            **plan["tiers"][0],  # type: ignore[index]
            "upper_bound_exclusive_kwh": None,
            "lower_bound_multiplier": "0",
            "upper_bound_multiplier": "1",
        },
        {
            **plan["tiers"][1],  # type: ignore[index]
            "lower_bound_inclusive_kwh": "0",
            "lower_bound_multiplier": "1",
            "upper_bound_multiplier": None,
        },
    ]
    tiers = RateEngine(plan).resolved_tiers(cycle_start=start, cycle_end=end)

    assert tiers[0]["derived_baseline_kwh"] == expected_baseline
    assert tiers[0]["upper_bound_kwh"] == expected_baseline
    assert tiers[1]["lower_bound_kwh"] == expected_baseline


def test_seasonal_baseline_and_rounding_are_evidence_driven() -> None:
    plan = tiered_plan(
        threshold={
            "basis": "daily_baseline_kwh",
            "daily_baseline_kwh": None,
            "rounding_policy": "ceil_kwh",
            "seasonal_baselines": [
                {
                    "name": "summer",
                    "start": "06-01",
                    "end": "09-30",
                    "daily_kwh": "9.25",
                    "source_citation": "fixture",
                }
            ],
        }
    )
    plan["tiers"] = [
        {
            **plan["tiers"][0],  # type: ignore[index]
            "upper_bound_exclusive_kwh": None,
            "lower_bound_multiplier": "0",
            "upper_bound_multiplier": "1",
        },
        {
            **plan["tiers"][1],  # type: ignore[index]
            "lower_bound_inclusive_kwh": "0",
            "lower_bound_multiplier": "1",
            "upper_bound_multiplier": None,
        },
    ]

    tiers = RateEngine(plan).resolved_tiers(cycle_start=CYCLE_START, cycle_end=CYCLE_END)
    assert tiers[0]["derived_baseline_kwh"] == Decimal("268.25")
    assert tiers[0]["upper_bound_kwh"] == Decimal("269")


def test_flat_plan_uses_one_exact_energy_price() -> None:
    plan = {
        "code": "FLAT",
        "timezone": "America/Los_Angeles",
        "pricing_model": "flat",
        "flat_rate_per_kwh": "0.275",
        "adjustments": [],
    }
    result = RateEngine(plan).calculate(
        start=CYCLE_START,
        end=CYCLE_START.replace(hour=8),
        energy_kwh=Decimal("12.5"),
    )
    assert result.energy_charge == Decimal("3.4375")
    assert result.energy_by_bucket == {"Flat": Decimal("12.5")}


def test_hybrid_matrix_applies_tou_price_inside_each_tier() -> None:
    plan = tiered_plan(upper="5")
    plan.update(
        {
            "pricing_model": "time_of_use_tiered",
            "hybrid_pricing": {"method": "tier_period_matrix"},
            "seasons": {
                "all-year": {
                    "start": "01-01",
                    "end": "12-31",
                    "priority": 0,
                }
            },
            "periods": {
                "all-year": {
                    "weekday": [
                        [0, 960, "off-peak", "0"],
                        [960, 1260, "on-peak", "0"],
                        [1260, 1440, "off-peak", "0"],
                    ],
                    "weekend": [[0, 1440, "off-peak", "0"]],
                }
            },
        }
    )
    plan["tiers"] = [
        {
            **plan["tiers"][0],  # type: ignore[index]
            "tou_prices": {"off-peak": "0.20", "on-peak": "0.30"},
        },
        {
            **plan["tiers"][1],  # type: ignore[index]
            "tou_prices": {"off-peak": "0.40", "on-peak": "0.50"},
        },
    ]
    result = RateEngine(plan).calculate(
        start=datetime(2026, 7, 22, 22, tzinfo=UTC),  # 15:00 PDT
        end=datetime(2026, 7, 23, 0, tzinfo=UTC),  # 17:00 PDT
        energy_kwh=Decimal("10"),
        cycle_start=CYCLE_START,
        cycle_end=CYCLE_END,
    )
    assert result.energy_by_tier == {
        "Tier 1": Decimal("5"),
        "Tier 2": Decimal("5"),
    }
    assert [(item.tier_name, item.tou_period, item.energy_kwh) for item in result.slices] == [
        ("Tier 1", "off-peak", Decimal("5")),
        ("Tier 2", "on-peak", Decimal("5")),
    ]
    assert result.energy_charge == Decimal("3.50")


def test_invalid_tier_order_and_closed_final_tier_are_rejected() -> None:
    plan = tiered_plan()
    plan["tiers"][1]["order"] = 3  # type: ignore[index]
    with pytest.raises(ValueError, match="ordered contiguous tiers"):
        RateEngine(plan)

    plan = tiered_plan()
    plan["tiers"][-1]["upper_bound_exclusive_kwh"] = "1000"  # type: ignore[index]
    with pytest.raises(ValueError, match="final tier must be open-ended"):
        RateEngine(plan)


def test_gapped_and_duplicate_tier_boundaries_are_rejected() -> None:
    plan = tiered_plan()
    plan["tiers"][1]["lower_bound_inclusive_kwh"] = "580"  # type: ignore[index]
    with pytest.raises(ValueError, match="gap-free"):
        RateEngine(plan)

    plan = tiered_plan()
    duplicate = {**plan["tiers"][1], "tier_id": "tier-1"}  # type: ignore[index]
    plan["tiers"][1] = duplicate  # type: ignore[index]
    with pytest.raises(ValueError, match="unique"):
        RateEngine(plan)


def test_usage_import_normalization_is_timezone_aware_ordered_and_quality_checked() -> None:
    rows = normalized_import_rows(
        [
            {
                "start": "2026-07-22T02:00:00",
                "end": "2026-07-22T03:00:00",
                "energy_kwh": "2.00",
            },
            {
                "start": "2026-07-22T00:00:00",
                "end": "2026-07-22T01:00:00",
                "energy_kwh": "1.00",
            },
            {
                "start": "2026-07-22T00:30:00",
                "end": "2026-07-22T00:45:00",
                "energy_kwh": "0.25",
            },
        ],
        "interval",
        "America/Los_Angeles",
    )

    assert rows[0]["start"] == "2026-07-22T07:00:00+00:00"
    assert rows[-1]["start"] == "2026-07-22T09:00:00+00:00"
    assert import_quality(rows, "interval") == {
        "duplicate_row_count": 0,
        "overlap_count": 1,
        "gap_count": 1,
    }
