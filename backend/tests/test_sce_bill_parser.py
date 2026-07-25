from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import jsonschema

from app.bills.extraction import TextRegion, extract_bill
from app.bills.sce import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    ALLOWED_FIELD_KEYS,
    parse_sce_residential,
    recognizes_sce_residential,
)

FIXTURES = Path(__file__).parent / "fixtures" / "bills"
SCE_PDF = FIXTURES / "sanitized-sce-domestic-bill.pdf"
SCE_SINGLE_DETAIL_PDF = FIXTURES / "sanitized-sce-single-detail-page.pdf"
EXPECTED = FIXTURES / "sanitized-sce-expected-extraction.json"


def _extract(test_settings: Any) -> Any:
    return extract_bill(
        SCE_PDF.read_bytes(),
        test_settings,
        pdf_path=SCE_PDF,
    )


def _extract_single_detail(test_settings: Any) -> Any:
    return extract_bill(
        SCE_SINGLE_DETAIL_PDF.read_bytes(),
        test_settings,
        pdf_path=SCE_SINGLE_DETAIL_PDF,
    )


def _subset(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    return {key: actual.get(key) for key in expected}


def test_sanitized_sce_fixture_matches_authoritative_expected_extraction(
    test_settings: Any,
) -> None:
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))["expected_extraction"]
    result = _extract(test_settings)
    parsed = result.adapter_result
    assert parsed is not None
    plan = parsed["plan_draft"]
    cycle = parsed["billing_cycle_draft"]

    assert result.parser_id == ADAPTER_ID
    assert result.parser_version == ADAPTER_VERSION
    schema = json.loads(
        (
            Path(__file__).parents[2] / "shared" / "schemas" / "sce-bill-extraction-1.0.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(parsed)
    assert parsed["utility"] == expected["utility"]
    assert plan["plan_code"] == expected["rate_plan_code"]
    assert cycle["starts_at"] == expected["billing_period"]["start"]
    assert cycle["ends_at"] == expected["billing_period"]["end"]
    assert cycle["cycle_days"] == expected["billing_period"]["days"]
    assert cycle["bill_prepared_date"] == expected["bill_prepared_date"]
    assert cycle["next_cycle_end_estimate"] == expected["next_cycle_end_estimate"]
    assert cycle["total_usage_kwh"] == expected["total_usage_kwh"]
    assert cycle["baseline_allowance_kwh"] == expected["baseline_allowance_kwh"]
    assert len(cycle["line_items"]) == len(expected["line_items"])
    assert [
        _subset(actual, expected_item)
        for actual, expected_item in zip(
            cycle["line_items"],
            expected["line_items"],
            strict=True,
        )
    ] == expected["line_items"]
    assert cycle["energy_subtotal"] == expected["subtotal_new_charges"]
    assert cycle["full_bill_total"] == expected["total_new_charges"]
    assert _subset(plan["summary_chart"], expected["summary_chart"]) == expected["summary_chart"]
    tiers = {item["name"]: item for item in plan["tiers"]}
    assert (
        tiers["Tier 1"]["price_per_kwh"]
        == expected["derived_validation_only"]["tier_1_variable_rate_sum"]
    )
    assert (
        tiers["Tier 2"]["price_per_kwh"]
        == expected["derived_validation_only"]["tier_2_variable_rate_sum"]
    )


def test_sce_single_detail_page_with_image_only_logo_is_strictly_supported(
    test_settings: Any,
) -> None:
    result = _extract_single_detail(test_settings)
    parsed = result.adapter_result
    assert parsed is not None
    assert result.parser_id == ADAPTER_ID
    assert result.parser_version == ADAPTER_VERSION == "1.1.0"
    assert parsed["supported_layout"] == "sce_residential_single_charge_detail_page"
    assert result.page_classifications == [
        {
            "page_number": 1,
            "page_class": "new_charge_details",
            "anchor_score": 11,
            "matched_anchors": [
                "details of your new charges",
                "your rate",
                "billing period",
                "delivery charges",
                "generation charges",
                "other charges or credits",
                "subtotal of your new charges",
                "state tax",
                "your new charges",
                "additional information",
                "baseline allowance",
            ],
            "authoritative_for_rate_plan": True,
        }
    ]
    assert result.rate_data["plan_code"] == "DOMESTIC"
    assert result.cycle_data["starts_at"] == "2026-06-22"
    assert result.cycle_data["ends_at"] == "2026-07-21"
    assert result.cycle_data["cycle_days"] == 30
    assert result.cycle_data["total_usage_kwh"] == "951"
    assert result.cycle_data["baseline_allowance_kwh"] == "579.0"
    assert len(result.cycle_data["line_items"]) == 8
    assert result.cycle_data["energy_subtotal"] == "353.86"
    assert result.cycle_data["full_bill_total"] == "354.15"
    assert result.validation["valid"] is True
    assert result.rate_data["summary_chart"] == {
        "tier_1_usage_kwh": "579",
        "tier_2_usage_kwh": "372",
        "tier_1_display_average_rate": "0.30",
        "tier_2_display_average_rate": "0.40",
        "display_only": True,
        "authoritative_for_rate_plan": False,
        "reason": "Rounded explanatory chart; actual prices may vary.",
    }
    fields = {field.field_key: field for field in result.fields}
    assert fields["service_voltage"].normalized_value == "240"
    assert fields["bill_prepared_date"].normalized_value is None
    assert fields["account_suffix"].normalized_value is None
    assert fields["account_suffix"].warnings[0]["code"] == "field_not_found"
    assert [item["code"] for item in result.blocking_warnings] == ["single_bill_incomplete_tariff"]


def test_sce_single_detail_page_requires_domain_provider_and_strong_anchors(
    test_settings: Any,
) -> None:
    result = _extract_single_detail(test_settings)
    assert recognizes_sce_residential(result.regions)
    without_provider = [region for region in result.regions if region.text.upper() != "SCE"]
    without_domain = [region for region in result.regions if "sce.com" not in region.text.lower()]
    without_heading = [
        region for region in result.regions if region.text.lower() != "details of your new charges"
    ]
    assert not recognizes_sce_residential(without_provider)
    assert not recognizes_sce_residential(without_domain)
    assert not recognizes_sce_residential(without_heading)


def test_sce_page_and_section_classification_excludes_irrelevant_numbers(
    test_settings: Any,
) -> None:
    result = _extract(test_settings)
    classes = {item["page_number"]: item["page_class"] for item in result.page_classifications}
    assert classes == {
        1: "account_and_usage_summary",
        2: "generic_information",
        3: "new_charge_details",
        4: "blank_or_separator",
        5: "regulatory_notice",
        6: "other",
    }
    assert [
        item["page_number"]
        for item in result.page_classifications
        if item["authoritative_for_rate_plan"]
    ] == [3]
    ignored_reasons = {reason for item in result.ignored_sections for reason in item["reasons"]}
    assert "payment_or_balance" in ignored_reasons
    assert "generic_definition" in ignored_reasons
    assert "regulatory_notice" in ignored_reasons
    assert "informational_breakdown" in ignored_reasons
    assert "rounded_explanatory_tier_chart" in ignored_reasons

    mutated = [
        *result.regions,
        TextRegion(
            page_number=5,
            text=(
                "Customer service 1-999-567-8910 proposed 88.75% on Dec 31, 2099 "
                "example charge $98765.43"
            ),
            method="text",
            confidence=1.0,
        ),
    ]
    mutation_result = parse_sce_residential(mutated)
    assert mutation_result.rate_data == result.rate_data
    assert mutation_result.cycle_data == result.cycle_data
    normalized = json.dumps(mutation_result.normalized_result)
    assert "98765.43" not in normalized
    assert "2099-12-31" not in normalized
    assert "88.75" not in normalized


def test_sce_arithmetic_and_usage_reconciliation_pass_exactly(test_settings: Any) -> None:
    validation = _extract(test_settings).validation
    assert validation["valid"] is True
    assert validation["automatic_publication_eligible"] is False
    assert all(item["status"] == "pass" for item in validation["row_arithmetic"])
    assert all(item["status"] == "pass" for item in validation["usage"])
    assert validation["subtotal"] == {
        "calculated": "353.86",
        "printed": "353.86",
        "status": "pass",
    }
    assert validation["total"] == {
        "calculated": "354.15",
        "printed": "354.15",
        "state_tax": "0.29",
        "status": "pass",
    }


def test_sce_missing_rules_are_null_with_reasons_and_output_is_allowlisted(
    test_settings: Any,
) -> None:
    result = _extract(test_settings)
    plan = result.rate_data
    assert plan["missing_rules"] == [
        {
            "field": "daily_baseline_formula",
            "value": None,
            "state": "not_found_on_bill",
            "reason": (
                "This bill proves a 579.0 kWh cycle allowance, not the reusable daily formula."
            ),
        },
        {
            "field": "winter_rates",
            "value": None,
            "state": "not_found_on_bill",
            "reason": "Only summer charge rows are present.",
        },
        {
            "field": "future_rates",
            "value": None,
            "state": "not_applicable",
            "reason": "Regulatory notices are not authoritative tariff evidence.",
        },
    ]
    assert all(
        item.field_key in ALLOWED_FIELD_KEYS or item.field_key.startswith("line_items.")
        for item in result.fields
    )
    assert all(item.parser_rule for item in result.fields)
    assert all(
        item.page_number is not None and item.text_region is not None
        for item in result.fields
        if item.normalized_value is not None
    )


def test_sce_unsupported_layout_returns_typed_null_result(test_settings: Any) -> None:
    extracted = _extract(test_settings)
    summary_only = [region for region in extracted.regions if region.page_number in {1, 2, 5}]
    result = parse_sce_residential(summary_only)
    assert result.rate_data["plan_code"] is None
    assert result.rate_data["pricing_model"] is None
    assert result.normalized_result["plan_draft"] is None
    assert result.normalized_result["billing_cycle_draft"] is None
    assert result.blocking_warnings[0]["code"] == "required_section_missing"
    assert all(field.normalized_value is None for field in result.fields)


def test_sce_masks_pii_and_flags_low_confidence_ocr_digits(test_settings: Any) -> None:
    extracted = _extract(test_settings)
    ocr_regions = [replace(region, method="ocr", confidence=0.35) for region in extracted.regions]
    result = parse_sce_residential(ocr_regions)
    assert result.account_data["account_suffix"] == "1234"
    assert result.account_data["service_account_suffix"] == "5678"
    assert result.account_data["meter_identifiers"] == ["9012"]
    assert any(item["code"] == "low_ocr_confidence" for item in result.blocking_warnings)
    normalized = json.dumps(result.normalized_result)
    assert "Example Customer" not in normalized
    assert "100 Example Street" not in normalized
    assert "0000111122221234" not in normalized
    assert "$0.30/kWh" not in json.dumps(result.rate_data["tiers"])
