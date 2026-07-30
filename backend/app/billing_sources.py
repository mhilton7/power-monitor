from __future__ import annotations

from typing import Literal

CalculationRole = Literal[
    "tariff_rule",
    "reference_only",
    "sensor_measurements",
    "advanced_external_correction",
]
UsageSourceType = Literal[
    "sensor_measurements",
    "advanced_external_correction",
    "unavailable",
]

TARIFF_RULE: Literal["tariff_rule"] = "tariff_rule"
REFERENCE_ONLY: Literal["reference_only"] = "reference_only"
SENSOR_MEASUREMENTS: Literal["sensor_measurements"] = "sensor_measurements"
ADVANCED_EXTERNAL_CORRECTION: Literal["advanced_external_correction"] = (
    "advanced_external_correction"
)
UNAVAILABLE: Literal["unavailable"] = "unavailable"

SENSOR_AUTHORITY_TYPES = frozenset(
    {
        "complete_site_aggregate",
        "service_leg_pair",
        "whole_account_meter",
    }
)
ADVANCED_AUTHORITY_TYPES = frozenset(
    {
        "utility_interval_import",
        "manual_cycle_usage",
        "external_feed",
        "partial_monitored_circuits",
    }
)


_TARIFF_THRESHOLD_FIELDS = frozenset(
    {
        "baseline_allowance_kwh",
        "daily_baseline_formula",
        "threshold_interpretation",
    }
)


def bill_field_calculation_role(
    output_kind: str,
    field_key: str,
) -> CalculationRole:
    """Classify normalized bill fields at the server-side evidence boundary."""

    if field_key in _TARIFF_THRESHOLD_FIELDS:
        return TARIFF_RULE
    if output_kind != "rate_plan":
        return REFERENCE_ONLY
    reference_fragments = (
        "usage",
        "energy_charge",
        "subtotal",
        "total",
        "payment",
        "credit",
        "tax",
        "adjustment",
        "balance",
        "service_voltage",
    )
    return (
        REFERENCE_ONLY
        if any(fragment in field_key for fragment in reference_fragments)
        else TARIFF_RULE
    )


def is_bill_reference(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    return normalized.startswith(("utility-bill:", "urn:power-monitor:utility-bill:"))


def authority_calculation_role(authority_type: str) -> CalculationRole:
    if authority_type in SENSOR_AUTHORITY_TYPES:
        return SENSOR_MEASUREMENTS
    if authority_type in ADVANCED_AUTHORITY_TYPES:
        return ADVANCED_EXTERNAL_CORRECTION
    return REFERENCE_ONLY


def rate_source_type(source_kind: str | None) -> str:
    if source_kind == "utility_bill_candidate":
        return "reviewed_bill"
    if source_kind == "official_sce_candidate":
        return "managed_source"
    return "custom_rate_plan"
