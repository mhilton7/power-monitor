from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

ADAPTER_ID = "sce_residential_bill_v1"
ADAPTER_VERSION = "1.1.0"
SCHEMA_VERSION = "sce_bill_v1"
FIXTURE_VERSION = "sanitized_sce_domestic_tiered_bill_2026_07"
UTILITY = "Southern California Edison"
DOCUMENT_CLASS = "residential_electric_bill"
AUTOMATIC_PUBLICATION_ELIGIBLE = False
CURRENCY_QUANTUM = Decimal("0.01")

PageClass = Literal[
    "account_and_usage_summary",
    "generic_information",
    "new_charge_details",
    "blank_or_separator",
    "regulatory_notice",
    "other",
]

ALLOWED_FIELD_KEYS = frozenset(
    {
        "utility",
        "document_class",
        "bill_prepared_date",
        "billing_period_start",
        "billing_period_end",
        "billing_cycle_days",
        "next_cycle_end_estimate",
        "account_suffix",
        "service_account_suffix",
        "meter_suffix",
        "rate_plan_code",
        "generation_provider",
        "service_voltage",
        "total_usage_kwh",
        "daily_average_usage_kwh",
        "baseline_allowance_kwh",
        "pricing_model",
        "season",
        "subtotal_new_charges",
        "state_tax",
        "total_new_charges",
        "daily_baseline_formula",
        "winter_rates",
        "future_rates",
    }
)

IGNORED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "payment_or_balance",
        re.compile(
            r"(?i)\b(payment received|previous balance|amount due|credit balance|"
            r"payment method|pay your bill)\b"
        ),
    ),
    (
        "contact_information",
        re.compile(r"(?i)\b(customer service|contact us|phone|mailing form|direct payment)\b"),
    ),
    (
        "generic_definition",
        re.compile(r"(?i)\b(what is|definition|means on your bill|understanding your bill)\b"),
    ),
    (
        "regulatory_notice",
        re.compile(
            r"(?i)\b(public hearing|proposed rate|CPUC|application number|public comment|"
            r"regulatory notice)\b"
        ),
    ),
    (
        "informational_breakdown",
        re.compile(r"(?i)\byour (?:delivery|generation|overall energy) charges include\b"),
    ),
)

DETAIL_ANCHORS = (
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
)
REQUIRED_DETAIL_ANCHOR_SCORE = 7


@dataclass(frozen=True)
class RegionLine:
    page_number: int
    text: str
    method: str
    confidence: float
    x: float | None
    y: float | None
    width: float | None
    height: float | None

    @property
    def region(self) -> dict[str, float | None]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class SceField:
    output_kind: Literal["account", "rate_plan", "billing_cycle"]
    field_key: str
    raw_value: Any | None
    normalized_value: Any | None
    line: RegionLine | None
    confidence: str
    parser_rule: str
    validation_result: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SceParseResult:
    account_data: dict[str, Any]
    rate_data: dict[str, Any]
    cycle_data: dict[str, Any]
    fields: list[SceField]
    warnings: list[dict[str, Any]]
    blocking_warnings: list[dict[str, Any]]
    page_classifications: list[dict[str, Any]]
    ignored_sections: list[dict[str, Any]]
    validation: dict[str, Any]
    normalized_result: dict[str, Any]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00d7", "x").strip())


def _lines(regions: Sequence[Any]) -> list[RegionLine]:
    result: list[RegionLine] = []
    for region in regions:
        for value in str(region.text).splitlines():
            cleaned = _clean(value)
            if cleaned:
                result.append(
                    RegionLine(
                        page_number=int(region.page_number),
                        text=cleaned,
                        method=str(region.method),
                        confidence=float(region.confidence),
                        x=region.x,
                        y=region.y,
                        width=region.width,
                        height=region.height,
                    )
                )
    return result


def _by_page(lines: Sequence[RegionLine]) -> dict[int, list[RegionLine]]:
    pages: dict[int, list[RegionLine]] = {}
    for line in lines:
        pages.setdefault(line.page_number, []).append(line)
    return pages


def _page_text(lines: Sequence[RegionLine]) -> str:
    return "\n".join(line.text for line in lines)


def _signal_summary(pages: dict[int, list[RegionLine]]) -> dict[str, bool]:
    text = "\n".join(_page_text(lines) for lines in pages.values()).lower()
    return {
        "full_utility_name": "southern california edison" in text,
        "official_website": "sce.com" in text,
        "bill_heading": "details of your new charges" in text,
        "rate_heading": "your rate:" in text or "your rate :" in text,
        "new_charges_heading": "your new charges" in text,
    }


def recognizes_sce_residential(regions: Sequence[Any]) -> bool:
    lines = _lines(regions)
    pages = _by_page(lines)
    signals = _signal_summary(pages)
    if signals["full_utility_name"] and sum(signals.values()) >= 2:
        return True
    # Some exported SCE bills contain only the detail page and render the
    # utility logo as an image. Accept that layout only when the text layer
    # still proves SCE's official domain, exact generation-provider label,
    # and the fully anchored authoritative charge section.
    exact_sce_provider = any(line.text.upper() == "SCE" for line in lines)
    strongly_anchored_detail = any(
        _classify_page(page_lines)[0] == "new_charge_details" for page_lines in pages.values()
    )
    return (
        signals["official_website"]
        and signals["bill_heading"]
        and signals["rate_heading"]
        and signals["new_charges_heading"]
        and exact_sce_provider
        and strongly_anchored_detail
    )


def _classify_page(lines: Sequence[RegionLine]) -> tuple[PageClass, list[str], int]:
    text = _page_text(lines).lower()
    if len(text.strip()) < 12 or "intentionally left blank" in text:
        return "blank_or_separator", [], 0
    matched = [anchor for anchor in DETAIL_ANCHORS if anchor in text]
    if "details of your new charges" in matched and len(matched) >= REQUIRED_DETAIL_ANCHOR_SCORE:
        return "new_charge_details", matched, len(matched)
    if re.search(r"\b(public hearing|proposed rate|cpuc|regulatory notice)\b", text):
        return "regulatory_notice", [], 0
    if re.search(
        r"\b(payment methods?|contact us|understanding your bill|generic definitions?)\b",
        text,
    ):
        return "generic_information", [], 0
    if "southern california edison" in text and re.search(
        r"\b(total (?:electricity )?usage|bill prepared)\b", text
    ):
        return "account_and_usage_summary", [], 0
    return "other", [], 0


def classify_pages(regions: Sequence[Any]) -> list[dict[str, Any]]:
    pages = _by_page(_lines(regions))
    result: list[dict[str, Any]] = []
    for page_number in sorted(pages):
        page_class, anchors, score = _classify_page(pages[page_number])
        result.append(
            {
                "page_number": page_number,
                "page_class": page_class,
                "anchor_score": score,
                "matched_anchors": anchors,
                "authoritative_for_rate_plan": page_class == "new_charge_details",
            }
        )
    return result


def _decimal(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        result = Decimal(value.replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None
    return format(result, "f")


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    cleaned = _clean(value)
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _confidence(line: RegionLine | None) -> str:
    if line is None:
        return "missing"
    if line.method == "text":
        return "high"
    if line.confidence >= 0.92:
        return "high"
    if line.confidence >= 0.80:
        return "medium"
    return "low"


def _masked_suffix(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\W", "", value)
    return cleaned[-4:] if len(cleaned) >= 4 else None


def _first(
    lines: Sequence[RegionLine], pattern: str, group: int = 1
) -> tuple[str | None, RegionLine | None]:
    compiled = re.compile(pattern, re.IGNORECASE)
    for line in lines:
        match = compiled.search(line.text)
        if match:
            return match.group(group).strip(), line
    return None, None


def _following_value(
    lines: Sequence[RegionLine],
    label_pattern: str,
    value_pattern: str,
    *,
    max_following_lines: int = 5,
    max_vertical_gap: float = 24.0,
) -> tuple[str | None, RegionLine | None]:
    label = re.compile(label_pattern, re.IGNORECASE)
    value = re.compile(value_pattern, re.IGNORECASE)
    for index, line in enumerate(lines):
        if not label.fullmatch(line.text):
            continue
        for candidate in lines[index + 1 : index + 1 + max_following_lines]:
            if candidate.page_number != line.page_number:
                break
            if (
                line.y is not None
                and candidate.y is not None
                and abs(line.y - candidate.y) > max_vertical_gap
            ):
                continue
            match = value.fullmatch(candidate.text)
            if match:
                return match.group(1).strip(), candidate
    return None, None


def _field(
    output_kind: Literal["account", "rate_plan", "billing_cycle"],
    field_key: str,
    raw: Any | None,
    normalized: Any | None,
    line: RegionLine | None,
    rule: str,
    *,
    validation: dict[str, Any] | None = None,
    missing_reason: str | None = None,
    missing_confidence: Literal["missing", "not_applicable"] = "missing",
) -> SceField:
    warnings: list[dict[str, Any]] = []
    if normalized is None and missing_reason:
        warnings.append(
            {
                "code": "field_not_found",
                "message": missing_reason,
                "searched_area": "recognized SCE summary and authoritative charge-detail sections",
                "administrator_action": (
                    "Review the source evidence and enter the value manually if needed."
                ),
            }
        )
    return SceField(
        output_kind=output_kind,
        field_key=field_key,
        raw_value=raw,
        normalized_value=normalized,
        line=line,
        confidence=_confidence(line) if normalized is not None else missing_confidence,
        parser_rule=rule,
        validation_result=validation,
        warnings=warnings,
    )


ROW_PATTERN = re.compile(
    r"^(?P<label>[A-Za-z0-9 ()/&.-]+?)\s+"
    r"(?P<quantity>[\d,]+(?:\.\d+)?)\s*(?P<unit>days?|kWh)\s+"
    r"[xX]\s*\$(?P<rate>\d+(?:\.\d+)?)\s+\$(?P<amount>\d+(?:\.\d{2}))$",
    re.IGNORECASE,
)

COMPONENTS = {
    "base services charge": "base_services_charge",
    "tier 1 (within baseline)": "energy",
    "tier 2 (over baseline)": "energy",
    "wildfire fund charge": "wildfire_fund_charge",
    "fixed recovery charge": "fixed_recovery_charge",
    "state tax": "state_tax",
}


def _row_validation(quantity: str, rate: str, amount: str) -> dict[str, Any]:
    product = Decimal(quantity) * Decimal(rate)
    rounded = product.quantize(CURRENCY_QUANTUM, rounding=ROUND_HALF_UP)
    printed = Decimal(amount)
    difference = printed - rounded
    return {
        "exact_product": format(product, "f"),
        "rounded_product": format(rounded, ".2f"),
        "printed_amount": format(printed, ".2f"),
        "difference": format(difference, ".2f"),
        "tolerance": "0.01",
        "status": "pass" if abs(difference) <= CURRENCY_QUANTUM else "fail",
    }


def _parse_rows(
    detail_lines: Sequence[RegionLine],
) -> tuple[list[dict[str, Any]], list[SceField], list[dict[str, Any]]]:
    section = "delivery"
    season: str | None = None
    provider: str | None = None
    rows: list[dict[str, Any]] = []
    fields: list[SceField] = []
    warnings: list[dict[str, Any]] = []
    for line in detail_lines:
        lowered = line.text.lower().rstrip(":")
        if lowered == "delivery charges":
            section, provider, season = "delivery", None, None
            continue
        if lowered == "generation charges":
            section, provider, season = "generation", None, None
            continue
        if lowered == "other charges or credits":
            section, provider, season = "other", None, None
            continue
        if lowered.startswith("energy-"):
            season = lowered.split("-", 1)[1].strip()
            continue
        if section == "generation" and lowered == "sce":
            provider = "SCE"
            continue
        match = ROW_PATTERN.fullmatch(line.text)
        if not match:
            continue
        label = _clean(match.group("label"))
        normalized_label = label.lower()
        component = COMPONENTS.get(normalized_label)
        if component is None:
            warnings.append(
                {
                    "code": "unrecognized_charge_row",
                    "page": line.page_number,
                    "message": f"Charge row '{label}' was retained as evidence but not normalized.",
                }
            )
            continue
        quantity = _decimal(match.group("quantity"))
        rate = _decimal(match.group("rate"))
        amount = _decimal(match.group("amount"))
        assert quantity is not None and rate is not None and amount is not None
        row_section = "tax" if component == "state_tax" else section
        tier = (
            "tier_1"
            if normalized_label.startswith("tier 1")
            else "tier_2"
            if normalized_label.startswith("tier 2")
            else None
        )
        validation = _row_validation(quantity, rate, amount)
        row: dict[str, Any] = {
            "component": component,
            "section": row_section,
            "quantity": quantity if match.group("unit").lower().startswith("day") else None,
            "quantity_unit": "day" if match.group("unit").lower().startswith("day") else None,
            "usage_kwh": quantity if match.group("unit").lower() == "kwh" else None,
            "unit_rate": rate,
            "amount": amount,
            "recurrence": "daily" if component == "base_services_charge" else "per_kwh",
            "provider": provider,
            "season": season if component == "energy" else None,
            "tier": tier,
            "tier_label": (
                "within baseline"
                if tier == "tier_1"
                else "over baseline"
                if tier == "tier_2"
                else None
            ),
            "source_page": line.page_number,
            "source_region": line.region,
            "source_text": line.text,
            "confidence": _confidence(line),
            "parser_rule": "sce.authoritative_charge_row.v1",
            "validation": validation,
        }
        row = {key: value for key, value in row.items() if value is not None}
        index = len(rows)
        rows.append(row)
        fields.append(
            _field(
                "billing_cycle",
                f"line_items.{index}",
                line.text,
                row,
                line,
                "sce.authoritative_charge_row.v1",
                validation=validation,
            )
        )
    return rows, fields, warnings


def _ignored_sections(
    pages: dict[int, list[RegionLine]], classifications: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    classes = {item["page_number"]: item["page_class"] for item in classifications}
    for page_number, lines in pages.items():
        text = _page_text(lines)
        matched = [name for name, pattern in IGNORED_PATTERNS if pattern.search(text)]
        page_class = classes.get(page_number, "other")
        if matched or page_class in {"generic_information", "regulatory_notice"}:
            result.append(
                {
                    "page_number": page_number,
                    "page_class": page_class,
                    "reasons": sorted(set(matched or [page_class])),
                    "authoritative_for_rate_plan": False,
                }
            )
    chart_pages = [
        number
        for number, lines in pages.items()
        if "usage by tier" in _page_text(lines).lower()
        and re.search(r"\$0\.30\s*/?\s*kwh", _page_text(lines), re.IGNORECASE)
    ]
    for page_number in chart_pages:
        result.append(
            {
                "page_number": page_number,
                "page_class": classes.get(page_number, "other"),
                "reasons": ["rounded_explanatory_tier_chart"],
                "display_only": True,
                "authoritative_for_rate_plan": False,
            }
        )
    return sorted(result, key=lambda item: (item["page_number"], str(item["reasons"])))


def _validation(
    rows: Sequence[dict[str, Any]],
    total_usage: str | None,
    subtotal: str | None,
    total: str | None,
) -> dict[str, Any]:
    row_results = [row["validation"] | {"component": row["component"]} for row in rows]
    usage_groups: list[dict[str, Any]] = []
    for section in ("delivery", "generation"):
        tier_rows = [
            row for row in rows if row["section"] == section and row["component"] == "energy"
        ]
        if not tier_rows:
            continue
        tier_sum = sum(Decimal(row["usage_kwh"]) for row in tier_rows)
        expected = Decimal(total_usage) if total_usage is not None else None
        usage_groups.append(
            {
                "section": section,
                "tier_count": len(tier_rows),
                "tier_usage_sum_kwh": format(tier_sum, "f"),
                "total_usage_kwh": format(expected, "f") if expected is not None else None,
                "status": "pass" if expected is not None and tier_sum == expected else "fail",
            }
        )
    calculated_subtotal = sum(Decimal(row["amount"]) for row in rows if row["section"] != "tax")
    printed_subtotal = Decimal(subtotal) if subtotal is not None else None
    tax_total = sum(Decimal(row["amount"]) for row in rows if row["section"] == "tax")
    calculated_total = calculated_subtotal + tax_total
    printed_total = Decimal(total) if total is not None else None
    subtotal_status = (
        "pass"
        if printed_subtotal is not None and calculated_subtotal == printed_subtotal
        else "fail"
    )
    total_status = (
        "pass" if printed_total is not None and calculated_total == printed_total else "fail"
    )
    valid = (
        all(item["status"] == "pass" for item in row_results)
        and all(item["status"] == "pass" for item in usage_groups)
        and subtotal_status == "pass"
        and total_status == "pass"
    )
    return {
        "valid": valid,
        "automatic_publication_eligible": False,
        "row_arithmetic": row_results,
        "usage": usage_groups,
        "subtotal": {
            "calculated": format(calculated_subtotal, ".2f"),
            "printed": format(printed_subtotal, ".2f") if printed_subtotal is not None else None,
            "status": subtotal_status,
        },
        "total": {
            "calculated": format(calculated_total, ".2f"),
            "printed": format(printed_total, ".2f") if printed_total is not None else None,
            "state_tax": format(tax_total, ".2f"),
            "status": total_status,
        },
    }


def _combined_tier_rates(rows: Sequence[dict[str, Any]]) -> dict[str, str]:
    common = sum(
        Decimal(row["unit_rate"])
        for row in rows
        if row["component"] in {"wildfire_fund_charge", "fixed_recovery_charge", "state_tax"}
    )
    tier_names = sorted(
        {str(row["tier"]) for row in rows if row.get("component") == "energy" and row.get("tier")}
    )
    result: dict[str, str] = {}
    for tier in tier_names:
        energy = sum(
            Decimal(row["unit_rate"])
            for row in rows
            if row.get("component") == "energy" and row.get("tier") == tier
        )
        result[tier] = format(energy + common, "f")
    return result


def _component_rate(
    rows: Sequence[dict[str, Any]],
    *,
    section: str,
    tier: str,
) -> str | None:
    return next(
        (
            str(row["unit_rate"])
            for row in rows
            if row.get("component") == "energy"
            and row.get("section") == section
            and row.get("tier") == tier
        ),
        None,
    )


def _tier_number(value: str) -> int:
    match = re.search(r"(\d+)$", value)
    return int(match.group(1)) if match else 999


def parse_sce_residential(regions: Sequence[Any]) -> SceParseResult:
    lines = _lines(regions)
    pages = _by_page(lines)
    classifications = classify_pages(regions)
    detail_pages = [
        item["page_number"]
        for item in classifications
        if item["page_class"] == "new_charge_details"
    ]
    ignored = _ignored_sections(pages, classifications)
    if not detail_pages:
        warning = {
            "code": "required_section_missing",
            "message": "The authoritative SCE 'Details of your new charges' section was not found.",
            "searched_area": "All classified PDF pages",
            "administrator_action": "Review the bill layout and enter tariff rules manually.",
        }
        unsupported_validation: dict[str, Any] = {
            "valid": False,
            "automatic_publication_eligible": False,
        }
        missing_fields = [
            _field(
                "rate_plan",
                key,
                None,
                None,
                None,
                "sce.required_section.v1",
                missing_reason=warning["message"],
            )
            for key in ("rate_plan_code", "pricing_model", "baseline_allowance_kwh")
        ]
        result = {
            "schema_version": SCHEMA_VERSION,
            "parser_id": ADAPTER_ID,
            "parser_version": ADAPTER_VERSION,
            "fixture_version": FIXTURE_VERSION,
            "utility": UTILITY,
            "document_class": DOCUMENT_CLASS,
            "plan_draft": None,
            "billing_cycle_draft": None,
            "evidence": [],
            "ignored_sections": ignored,
            "validation": unsupported_validation,
            "warnings": [warning],
            "page_classifications": classifications,
        }
        return SceParseResult(
            account_data={"utility": UTILITY},
            rate_data={
                "utility": UTILITY,
                "plan_name": None,
                "plan_code": None,
                "pricing_model": None,
                "tiers": [],
                "tou_periods": [],
                "threshold_interpretation": "unknown",
            },
            cycle_data={},
            fields=missing_fields,
            warnings=[warning],
            blocking_warnings=[warning],
            page_classifications=classifications,
            ignored_sections=ignored,
            validation=unsupported_validation,
            normalized_result=result,
        )

    detail_lines = pages[detail_pages[0]]
    summary_lines = [
        line
        for item in classifications
        if item["page_class"] == "account_and_usage_summary"
        for line in pages[item["page_number"]]
    ]
    authoritative = [*summary_lines, *detail_lines]
    fields: list[SceField] = []

    plan_raw, plan_line = _first(detail_lines, r"\byour rate\s*:\s*([A-Z0-9._-]+)")
    plan_code = plan_raw.upper() if plan_raw else None
    period_date = r"(?:[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})"
    period_pattern = (
        rf"\bbilling period\s*:\s*({period_date})"
        rf"\s+(?:through|to|-)\s+({period_date})"
    )
    period_match: tuple[str | None, RegionLine | None] = _first(
        detail_lines,
        period_pattern,
    )
    period_line = period_match[1]
    period_regex = (
        re.search(
            period_pattern,
            period_line.text,
            re.IGNORECASE,
        )
        if period_line
        else None
    )
    starts = _date(period_regex.group(1)) if period_regex else None
    ends = _date(period_regex.group(2)) if period_regex else None
    cycle_days = (ends - starts).days + 1 if starts and ends else None
    usage_raw, usage_line = _first(
        authoritative, r"\btotal (?:electricity )?usage\s*:?\s*([\d,]+(?:\.\d+)?)\s*kWh"
    )
    if usage_raw is None:
        usage_raw, usage_line = _following_value(
            authoritative,
            r"(?:your )?total (?:electricity )?usage\s*:?",
            r"([\d,]+(?:\.\d+)?)\s*kWh",
        )
    total_usage = _decimal(usage_raw)
    baseline_raw, baseline_line = _first(
        detail_lines,
        r"\b(?:summer )?baseline allowance(?: is|:)?\s*([\d,]+(?:\.\d+)?)\s*kWh",
    )
    if baseline_raw is None:
        baseline_raw, baseline_line = _following_value(
            detail_lines,
            r"(?:your )?(?:summer )?baseline allowance\s*:?",
            r"([\d,]+(?:\.\d+)?)\s*kWh",
        )
    baseline = _decimal(baseline_raw)
    prepared_raw, prepared_line = _first(
        summary_lines, r"\bbill prepared(?: on|:)?\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})"
    )
    prepared = _date(prepared_raw)
    next_raw, next_line = _first(
        summary_lines,
        r"\bnext (?:billing )?cycle (?:estimated )?(?:ends|end)\s*"
        r"(?:on|:)?\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})",
    )
    next_end = _date(next_raw)
    account_raw, account_line = _first(
        summary_lines, r"\baccount(?: number)?\s+(?:ending|ends in)\s+([A-Z0-9 -]{4,})"
    )
    service_raw, service_line = _first(
        summary_lines, r"\bservice account\s+(?:ending|ends in)\s+([A-Z0-9 -]{4,})"
    )
    meter_raw, meter_line = _first(
        summary_lines, r"\bmeter(?: number)?\s+(?:ending|ends in)\s+([A-Z0-9 -]{4,})"
    )
    daily_raw, daily_line = _first(
        summary_lines, r"\bdaily average usage\s*:?\s*([\d,]+(?:\.\d+)?)\s*kWh"
    )
    daily_average = _decimal(daily_raw)
    provider_raw, provider_line = _first(detail_lines, r"^(SCE)$")
    provider = provider_raw.upper() if provider_raw else None
    voltage_raw, voltage_line = _first(
        detail_lines, r"\bservice voltage\s*:\s*([\d,]+(?:\.\d+)?)\s*volts?\b"
    )
    if voltage_raw is None:
        voltage_raw, voltage_line = _following_value(
            detail_lines,
            r"service voltage\s*:?",
            r"([\d,]+(?:\.\d+)?)\s*volts?",
        )
    service_voltage = _decimal(voltage_raw)

    scalar_fields = [
        _field(
            "account",
            "utility",
            UTILITY,
            UTILITY,
            summary_lines[0] if summary_lines else None,
            "sce.utility_signals.v1",
        ),
        _field(
            "account",
            "document_class",
            DOCUMENT_CLASS,
            DOCUMENT_CLASS,
            summary_lines[0] if summary_lines else None,
            "sce.document_class.v1",
        ),
        _field(
            "rate_plan",
            "rate_plan_code",
            plan_raw,
            plan_code,
            plan_line,
            "sce.rate_heading.v1",
            missing_reason="Rate plan code was not found in the authoritative detail section.",
        ),
        _field(
            "rate_plan", "pricing_model", "tiered", "tiered", plan_line, "sce.charge_table_model.v1"
        ),
        _field(
            "billing_cycle",
            "billing_period_start",
            period_regex.group(1) if period_regex else None,
            starts.isoformat() if starts else None,
            period_line,
            "sce.billing_period.v1",
            missing_reason="Billing-period start was not found.",
        ),
        _field(
            "billing_cycle",
            "billing_period_end",
            period_regex.group(2) if period_regex else None,
            ends.isoformat() if ends else None,
            period_line,
            "sce.billing_period.v1",
            missing_reason="Billing-period end was not found.",
        ),
        _field(
            "billing_cycle",
            "billing_cycle_days",
            cycle_days,
            cycle_days,
            period_line,
            "sce.inclusive_cycle_days.v1",
            missing_reason="Cycle length could not be derived.",
        ),
        _field(
            "billing_cycle",
            "bill_prepared_date",
            prepared_raw,
            prepared.isoformat() if prepared else None,
            prepared_line,
            "sce.summary_prepared_date.v1",
            missing_reason="Bill prepared date was not found on the summary page.",
        ),
        _field(
            "billing_cycle",
            "next_cycle_end_estimate",
            next_raw,
            next_end.isoformat() if next_end else None,
            next_line,
            "sce.summary_next_cycle.v1",
            missing_reason="Next-cycle estimate was not found on the summary page.",
        ),
        _field(
            "billing_cycle",
            "total_usage_kwh",
            usage_raw,
            total_usage,
            usage_line,
            "sce.summary_total_usage.v1",
            missing_reason="Total usage was not found in a recognized summary/detail section.",
        ),
        _field(
            "billing_cycle",
            "baseline_allowance_kwh",
            baseline_raw,
            baseline,
            baseline_line,
            "sce.detail_baseline_allowance.v1",
            missing_reason="Baseline allowance was not found in Additional information.",
        ),
        _field(
            "billing_cycle",
            "daily_average_usage_kwh",
            daily_raw,
            daily_average,
            daily_line,
            "sce.summary_daily_average.v1",
            missing_reason="Daily average usage was not found on this bill.",
        ),
        _field(
            "account",
            "account_suffix",
            account_raw,
            _masked_suffix(account_raw),
            account_line,
            "sce.masked_identifier.v1",
            missing_reason="Masked account suffix was not found.",
        ),
        _field(
            "account",
            "service_account_suffix",
            service_raw,
            _masked_suffix(service_raw),
            service_line,
            "sce.masked_identifier.v1",
            missing_reason="Masked service-account suffix was not found.",
        ),
        _field(
            "account",
            "meter_suffix",
            meter_raw,
            _masked_suffix(meter_raw),
            meter_line,
            "sce.masked_identifier.v1",
            missing_reason="Masked meter suffix was not found.",
        ),
        _field(
            "rate_plan",
            "generation_provider",
            provider_raw,
            provider,
            provider_line,
            "sce.generation_provider_header.v1",
            missing_reason="Generation provider was not shown in the authoritative section.",
        ),
        _field(
            "rate_plan",
            "service_voltage",
            voltage_raw,
            service_voltage,
            voltage_line,
            "sce.service_voltage.v1",
            missing_reason="Service voltage was not found on this bill.",
        ),
    ]
    fields.extend(scalar_fields)

    rows, row_fields, row_warnings = _parse_rows(detail_lines)
    fields.extend(row_fields)

    subtotal_raw, subtotal_line = _first(
        detail_lines, r"^subtotal of your new charges\s+\$([\d,]+(?:\.\d{2}))$"
    )
    subtotal = _decimal(subtotal_raw)
    total_raw, total_line = _first(detail_lines, r"^your new charges\s+\$([\d,]+(?:\.\d{2}))$")
    total = _decimal(total_raw)
    tax_row = next((row for row in rows if row["component"] == "state_tax"), None)
    fields.extend(
        [
            _field(
                "billing_cycle",
                "subtotal_new_charges",
                subtotal_raw,
                subtotal,
                subtotal_line,
                "sce.detail_subtotal.v1",
                missing_reason="Subtotal of new charges was not found.",
            ),
            _field(
                "billing_cycle",
                "state_tax",
                tax_row["amount"] if tax_row else None,
                tax_row["amount"] if tax_row else None,
                next((item.line for item in row_fields if item.normalized_value == tax_row), None),
                "sce.authoritative_charge_row.v1",
                validation=tax_row["validation"] if tax_row else None,
                missing_reason="State tax was not found as an authoritative charge row.",
            ),
            _field(
                "billing_cycle",
                "total_new_charges",
                total_raw,
                total,
                total_line,
                "sce.detail_total.v1",
                missing_reason="Your new charges total was not found.",
            ),
        ]
    )

    validation = _validation(rows, total_usage, subtotal, total)
    rates = _combined_tier_rates(rows)
    delivery_tiers = [
        row for row in rows if row["section"] == "delivery" and row["component"] == "energy"
    ]
    usage_by_tier = [
        {
            "name": f"Tier {index + 1}",
            "tier": row["tier"],
            "usage_kwh": row["usage_kwh"],
            "energy_charge": row["amount"],
        }
        for index, row in enumerate(delivery_tiers)
    ]
    tiers: list[dict[str, Any]] = []
    tier_names = sorted(rates, key=_tier_number)
    for index, tier_name in enumerate(tier_names):
        delivery_rate = _component_rate(rows, section="delivery", tier=tier_name)
        generation_rate = _component_rate(rows, section="generation", tier=tier_name)
        energy_rate_sum = sum(
            Decimal(row["unit_rate"])
            for row in rows
            if row.get("component") == "energy" and row.get("tier") == tier_name
        )
        combined_rate = rates[tier_name]
        tiers.append(
            {
                "name": f"Tier {_tier_number(tier_name)}",
                "lower_bound_kwh": "0" if index == 0 else baseline,
                "upper_bound_kwh": baseline if index == 0 else None,
                "usage_kwh": next(
                    (row["usage_kwh"] for row in delivery_tiers if row.get("tier") == tier_name),
                    None,
                ),
                "price_per_kwh": combined_rate,
                "energy_charge": None,
                "season": "summer",
                "component_rates": {
                    "delivery": delivery_rate,
                    "generation": generation_rate,
                    "recurring_common": format(
                        Decimal(combined_rate) - energy_rate_sum,
                        "f",
                    ),
                },
            }
        )
    base_charge = next((row for row in rows if row["component"] == "base_services_charge"), None)
    missing_rules: list[dict[str, Any]] = [
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
    for missing in missing_rules:
        fields.append(
            _field(
                "rate_plan",
                missing["field"],
                None,
                None,
                None,
                f"sce.missing_rule.{missing['field']}.v1",
                missing_reason=str(missing["reason"]),
                missing_confidence=(
                    "not_applicable" if missing["state"] == "not_applicable" else "missing"
                ),
            )
        )

    display_average_rates = [
        rate
        for line in lines
        for rate in re.findall(r"\$(\d+(?:\.\d+)?)/kWh\b", line.text, re.IGNORECASE)
    ]
    chart = {
        "tier_1_usage_kwh": (delivery_tiers[0]["usage_kwh"] if len(delivery_tiers) >= 1 else None),
        "tier_2_usage_kwh": (delivery_tiers[1]["usage_kwh"] if len(delivery_tiers) >= 2 else None),
        "tier_1_display_average_rate": (
            _decimal(display_average_rates[0]) if len(display_average_rates) >= 1 else None
        ),
        "tier_2_display_average_rate": (
            _decimal(display_average_rates[1]) if len(display_average_rates) >= 2 else None
        ),
        "display_only": True,
        "authoritative_for_rate_plan": False,
        "reason": "Rounded explanatory chart; actual prices may vary.",
    }
    account_data = {
        "utility": UTILITY,
        "plan_code": plan_code,
        "account_suffix": _masked_suffix(account_raw),
        "service_account_suffix": _masked_suffix(service_raw),
        "meter_identifiers": [_masked_suffix(meter_raw)] if meter_raw else [],
        "provider_mode": "bundled",
        "document_class": DOCUMENT_CLASS,
    }
    rate_data = {
        "utility": UTILITY,
        "plan_name": plan_code,
        "plan_code": plan_code,
        "pricing_model": "tiered",
        "currency": "USD",
        "effective_from": starts.isoformat() if starts else None,
        "tiers": tiers,
        "tou_periods": [],
        "threshold_interpretation": "fixed_cycle_threshold",
        "baseline_allowance_kwh": baseline,
        "season": "summer",
        "generation_provider": provider,
        "recurring_daily_charge": base_charge,
        "recurring_components": rows,
        "missing_rules": missing_rules,
        "summary_chart": chart,
        "automatic_publication_eligible": False,
    }
    cycle_data = {
        "starts_at": starts.isoformat() if starts else None,
        "ends_at": ends.isoformat() if ends else None,
        "cycle_days": cycle_days,
        "meter_read_date": ends.isoformat() if ends else None,
        "bill_prepared_date": prepared.isoformat() if prepared else None,
        "next_cycle_end_estimate": next_end.isoformat() if next_end else None,
        "days_remaining": None,
        "days_elapsed": None,
        "total_usage_kwh": total_usage,
        "baseline_allowance_kwh": baseline,
        "usage_by_tier": usage_by_tier,
        "usage_by_tou": [],
        "meter_records": [{"meter_suffix": _masked_suffix(meter_raw)}] if meter_raw else [],
        "current_tier": "Tier 2",
        "projected_tier": None,
        "energy_subtotal": subtotal,
        "full_bill_total": total,
        "fixed_charges": base_charge["amount"] if base_charge else None,
        "taxes_fees": tax_row["amount"] if tax_row else None,
        "credits": None,
        "adjustments": None,
        "line_items": rows,
        "validation": validation,
    }
    warnings = list(row_warnings)
    if not validation["valid"]:
        warnings.append(
            {
                "code": "bill_arithmetic_conflict",
                "message": (
                    "One or more printed charge relationships failed exact Decimal validation."
                ),
            }
        )
    warnings.extend(
        {
            "code": "missing_reusable_rule",
            "field": item["field"],
            "message": item["reason"],
        }
        for item in missing_rules
    )
    blocking = [
        {
            "code": "single_bill_incomplete_tariff",
            "fields": [item["field"] for item in missing_rules],
            "message": (
                "A single bill does not prove all reusable tariff rules; "
                "administrator review is required."
            ),
        }
    ]
    if not validation["valid"]:
        blocking.append(
            {
                "code": "bill_arithmetic_conflict",
                "message": "Arithmetic validation must pass or be explicitly corrected before use.",
            }
        )
    if any(
        field.confidence == "low" and field.line and field.line.method == "ocr" for field in fields
    ):
        blocking.append(
            {
                "code": "low_ocr_confidence",
                "message": "Low-confidence OCR digits require administrator correction.",
            }
        )
    evidence = [
        {
            "field": item.field_key,
            "value": item.normalized_value,
            "confidence": item.confidence,
            "source_page": item.line.page_number if item.line else None,
            "source_region": item.line.region if item.line else None,
            "source_text": item.line.text if item.line else None,
            "extraction_method": item.line.method if item.line else "text",
            "parser_rule": item.parser_rule,
            "validation_result": item.validation_result,
            "review_status": "needs_review",
            "reason": item.warnings[0]["message"] if item.warnings else None,
        }
        for item in fields
    ]
    unexpected_fields = [
        item.field_key
        for item in fields
        if item.field_key not in ALLOWED_FIELD_KEYS and not item.field_key.startswith("line_items.")
    ]
    if unexpected_fields:
        raise ValueError(
            "SCE adapter emitted fields outside its allowlist: "
            + ", ".join(sorted(unexpected_fields))
        )
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "parser_id": ADAPTER_ID,
        "parser_version": ADAPTER_VERSION,
        "fixture_version": FIXTURE_VERSION,
        "utility": UTILITY,
        "document_class": DOCUMENT_CLASS,
        "supported_layout": (
            "sce_residential_multi_page_charge_details"
            if summary_lines
            else "sce_residential_single_charge_detail_page"
        ),
        "automatic_publication_eligible": False,
        "plan_draft": rate_data,
        "billing_cycle_draft": cycle_data,
        "evidence": evidence,
        "ignored_sections": ignored,
        "validation": validation,
        "warnings": warnings,
        "page_classifications": classifications,
    }
    return SceParseResult(
        account_data=account_data,
        rate_data=rate_data,
        cycle_data=cycle_data,
        fields=fields,
        warnings=warnings,
        blocking_warnings=blocking,
        page_classifications=classifications,
        ignored_sections=ignored,
        validation=validation,
        normalized_result=normalized,
    )
