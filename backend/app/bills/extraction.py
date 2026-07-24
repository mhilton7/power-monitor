from __future__ import annotations

import hashlib
import importlib
import io
import re
import subprocess
import tempfile
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.bills.sce import (
    ADAPTER_ID as SCE_PARSER_ID,
)
from app.bills.sce import (
    ADAPTER_VERSION as SCE_PARSER_VERSION,
)
from app.bills.sce import (
    parse_sce_residential,
    recognizes_sce_residential,
)
from app.config import Settings

PARSER_ID = "utility_bill_pdf_v1"
PARSER_VERSION = "1.0.0"
SUSPICIOUS_MOJIBAKE = ("\u00c3", "\u00c2", "\u00e2\u20ac", "\ufffd", "\u00ef\u00bf\u00bd")
BLOCKED_PDF_TOKENS = (
    b"/JavaScript",
    b"/OpenAction",
    b"/AA",
    b"/Launch",
    b"/EmbeddedFile",
    b"/RichMedia",
)
DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%m/%d/%y")


class BillPdfError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TextRegion:
    page_number: int
    text: str
    method: str
    confidence: float
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None


@dataclass(frozen=True)
class ExtractedField:
    output_kind: str
    field_key: str
    raw_value: Any | None
    normalized_value: Any | None
    page_number: int | None
    source_excerpt: str | None
    text_region: dict[str, Any] | None
    extraction_method: str
    confidence: str
    warnings: list[dict[str, Any]] = field(default_factory=list)
    normalization_history: list[dict[str, Any]] = field(default_factory=list)
    parser_rule: str | None = None
    validation_result: dict[str, Any] | None = None


@dataclass(frozen=True)
class PdfInspection:
    reader: PdfReader
    page_count: int
    sha256: str


@dataclass(frozen=True)
class BillExtraction:
    page_count: int
    extraction_method: str
    ocr_version: str | None
    regions: list[TextRegion]
    raw_text: str
    normalized_text: str
    normalization_history: list[dict[str, Any]]
    account_data: dict[str, Any]
    rate_data: dict[str, Any]
    cycle_data: dict[str, Any]
    fields: list[ExtractedField]
    warnings: list[dict[str, Any]]
    blocking_warnings: list[dict[str, Any]]
    parser_id: str = PARSER_ID
    parser_version: str = PARSER_VERSION
    page_classifications: list[dict[str, Any]] = field(default_factory=list)
    ignored_sections: list[dict[str, Any]] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    adapter_result: dict[str, Any] | None = None


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def inspect_pdf(content: bytes, settings: Settings) -> PdfInspection:
    if len(content) > settings.utility_bill_max_bytes:
        raise BillPdfError("bill_pdf_too_large", "Utility-bill PDFs exceed the configured limit")
    if not content.startswith(b"%PDF-"):
        raise BillPdfError("bill_pdf_type", "The uploaded file is not a PDF")
    if any(token in content for token in BLOCKED_PDF_TOKENS):
        raise BillPdfError(
            "bill_pdf_active_content",
            "PDFs with active, embedded, or executable content are not supported",
        )
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise BillPdfError(
                "bill_pdf_encrypted",
                "Password-protected or encrypted PDFs are not supported",
            )
        page_count = len(reader.pages)
    except BillPdfError:
        raise
    except (PdfReadError, OSError, ValueError, TypeError) as exc:
        raise BillPdfError("bill_pdf_malformed", "The PDF could not be parsed safely") from exc
    if page_count < 1:
        raise BillPdfError("bill_pdf_empty", "The PDF contains no pages")
    if page_count > settings.utility_bill_max_pages:
        raise BillPdfError(
            "bill_pdf_page_limit",
            "The PDF exceeds the configured utility-bill page limit",
        )
    return PdfInspection(
        reader=reader,
        page_count=page_count,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _suspicious_score(value: str) -> int:
    return sum(value.count(marker) for marker in SUSPICIOUS_MOJIBAKE)


def normalize_extracted_text(value: str) -> tuple[str, list[dict[str, Any]]]:
    history: list[dict[str, Any]] = []
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    normalized = "".join(
        character
        for character in normalized
        if character in {"\n", "\t"} or unicodedata.category(character) != "Cc"
    )
    repaired_lines: list[str] = []
    for line_number, line in enumerate(normalized.splitlines(), start=1):
        repaired = line
        if _suspicious_score(line):
            candidates = [line]
            for source_encoding in ("cp1252", "latin-1"):
                try:
                    candidates.append(line.encode(source_encoding).decode("utf-8"))
                except (UnicodeEncodeError, UnicodeDecodeError):
                    continue
            candidate = min(candidates, key=_suspicious_score)
            if _suspicious_score(candidate) < _suspicious_score(line):
                repaired = candidate
                history.append(
                    {
                        "operation": "targeted_utf8_repair",
                        "line": line_number,
                        "before_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                        "after_sha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
                    }
                )
        repaired_lines.append(repaired)
    return "\n".join(repaired_lines).strip(), history


def suspicious_mojibake(value: str) -> bool:
    return _suspicious_score(value) > 0


def redact_sensitive_text(value: str) -> str:
    result = unicodedata.normalize("NFC", value)

    def mask_account(match: re.Match[str]) -> str:
        suffix = re.sub(r"\W", "", match.group(2))[-4:]
        return f"{match.group(1)}ending {suffix}"

    result = re.sub(
        r"(?i)\b(account(?:\s+(?:number|no\.?|#))?\s*[:#-]?\s*)"
        r"([A-Z0-9 -]{5,})(?=\s|$)",
        mask_account,
        result,
    )
    result = re.sub(
        r"(?im)^(?:customer\s+name|service\s+address|mailing\s+address|payment\s+method)"
        r"\s*:.*$",
        lambda match: f"{match.group(0).split(':', 1)[0]}: [redacted]",
        result,
    )
    result = re.sub(r"\b\d{12,19}\b", "[redacted-number]", result)
    return result


def _extract_text_regions(inspection: PdfInspection) -> tuple[list[TextRegion], list[int]]:
    regions: list[TextRegion] = []
    empty_pages: list[int] = []
    for page_index, page in enumerate(inspection.reader.pages, start=1):
        page_regions: list[TextRegion] = []

        def visitor(
            text: str,
            _current_matrix: Sequence[float],
            text_matrix: Sequence[float],
            _font_dictionary: dict[str, Any] | None,
            font_size: float,
            target: list[TextRegion] = page_regions,
            page_number: int = page_index,
        ) -> None:
            cleaned = text.strip()
            if not cleaned:
                return
            target.append(
                TextRegion(
                    page_number=page_number,
                    text=cleaned,
                    method="text",
                    confidence=0.99,
                    x=float(text_matrix[4]) if len(text_matrix) > 4 else None,
                    y=float(text_matrix[5]) if len(text_matrix) > 5 else None,
                    height=float(font_size) if font_size else None,
                )
            )

        try:
            fallback_text = page.extract_text(visitor_text=visitor) or ""
        except (KeyError, TypeError, ValueError):
            fallback_text = page.extract_text() or ""
        if not page_regions and fallback_text.strip():
            page_regions.append(
                TextRegion(
                    page_number=page_index,
                    text=fallback_text.strip(),
                    method="text",
                    confidence=0.92,
                )
            )
        usable = sum(len(region.text) for region in page_regions)
        if usable < 24:
            empty_pages.append(page_index)
        regions.extend(page_regions)
    return regions, empty_pages


def _memory_limit(settings: Settings) -> Callable[[], None] | None:
    try:
        resource_module = importlib.import_module("resource")
        set_limit = resource_module.setrlimit
        address_space = resource_module.RLIMIT_AS
    except (ImportError, AttributeError):
        return None

    def limit() -> None:
        maximum = settings.utility_bill_ocr_max_memory_mb * 1024 * 1024
        set_limit(address_space, (maximum, maximum))

    return limit


def _run_ocr(
    pdf_path: Path,
    pages: list[int],
    settings: Settings,
    *,
    runner: CommandRunner = subprocess.run,
) -> tuple[list[TextRegion], list[dict[str, Any]]]:
    regions: list[TextRegion] = []
    warnings: list[dict[str, Any]] = []
    timeout = settings.utility_bill_ocr_timeout_seconds
    with tempfile.TemporaryDirectory(prefix="pm-bill-ocr-") as temporary:
        root = Path(temporary)
        for page_number in pages:
            prefix = root / f"page-{page_number}"
            render_command = [
                settings.utility_bill_pdf_render_command,
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-r",
                str(settings.utility_bill_ocr_dpi),
                "-png",
                "-singlefile",
                str(pdf_path),
                str(prefix),
            ]
            try:
                runner(
                    render_command,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    preexec_fn=_memory_limit(settings),
                )
                image_path = prefix.with_suffix(".png")
                result = runner(
                    [
                        settings.utility_bill_ocr_command,
                        str(image_path),
                        "stdout",
                        "-l",
                        "eng",
                        "--psm",
                        "6",
                        "tsv",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    preexec_fn=_memory_limit(settings),
                )
            except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
                warnings.append(
                    {
                        "code": "ocr_page_failed",
                        "page": page_number,
                        "message": type(exc).__name__,
                    }
                )
                continue
            page_lines: dict[
                tuple[str, str, str],
                list[tuple[str, float, float, float, float, float]],
            ] = {}
            for line in result.stdout.splitlines()[1:]:
                columns = line.split("\t")
                if len(columns) < 12 or not columns[11].strip():
                    continue
                try:
                    confidence = max(0.0, min(100.0, float(columns[10]))) / 100
                    x, y, width, height = (float(columns[index]) for index in range(6, 10))
                except ValueError:
                    continue
                line_key = (columns[2], columns[3], columns[4])
                page_lines.setdefault(line_key, []).append(
                    (columns[11].strip(), confidence, x, y, width, height)
                )
            for words in page_lines.values():
                line_text = " ".join(word[0] for word in words)
                line_confidence = sum(word[1] for word in words) / len(words)
                left = min(word[2] for word in words)
                top = min(word[3] for word in words)
                right = max(word[2] + word[4] for word in words)
                bottom = max(word[3] + word[5] for word in words)
                regions.append(
                    TextRegion(
                        page_number=page_number,
                        text=line_text,
                        method="ocr",
                        confidence=line_confidence,
                        x=left,
                        y=top,
                        width=right - left,
                        height=bottom - top,
                    )
                )
    return regions, warnings


def _page_text(regions: list[TextRegion]) -> dict[int, str]:
    pages: dict[int, list[str]] = {}
    for region in regions:
        pages.setdefault(region.page_number, []).append(region.text)
    return {page: "\n".join(values) for page, values in pages.items()}


def _parse_date(value: str) -> date | None:
    cleaned = re.sub(r"\s+", " ", value.strip())
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
    return None


def _decimal_string(value: str) -> str | None:
    cleaned = value.strip().replace(",", "").replace("$", "").replace(" ", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    if cleaned.endswith("\u00a2"):
        cleaned = cleaned[:-1]
        divisor = Decimal("100")
    else:
        divisor = Decimal("1")
    try:
        number = Decimal(cleaned) / divisor
    except InvalidOperation:
        return None
    if negative:
        number = -number
    return format(number, "f")


def _field_region(
    regions: list[TextRegion], *needles: str, fallback_value: str | None = None
) -> TextRegion | None:
    lowered = [needle.lower() for needle in needles if needle]
    for region in regions:
        text = region.text.lower()
        if any(needle in text for needle in lowered):
            return region
    if fallback_value:
        for region in regions:
            if fallback_value.lower() in region.text.lower():
                return region
    return None


def _confidence(region: TextRegion | None, *, required: bool = False) -> str:
    if region is None:
        return "missing" if required else "not_applicable"
    if region.method == "text":
        return "high"
    if region.confidence >= 0.90:
        return "high"
    if region.confidence >= 0.70:
        return "medium"
    return "low"


def _evidence(
    regions: list[TextRegion],
    output_kind: str,
    field_key: str,
    raw_value: Any | None,
    normalized_value: Any | None,
    *needles: str,
    required: bool = False,
    force_confidence: str | None = None,
    warning: dict[str, Any] | None = None,
) -> ExtractedField:
    fallback = str(raw_value) if raw_value is not None else None
    region = _field_region(regions, *needles, fallback_value=fallback)
    excerpt = redact_sensitive_text(region.text)[:500] if region else None
    text_region = (
        {
            "x": region.x,
            "y": region.y,
            "width": region.width,
            "height": region.height,
        }
        if region
        else None
    )
    return ExtractedField(
        output_kind=output_kind,
        field_key=field_key,
        raw_value=raw_value,
        normalized_value=normalized_value,
        page_number=region.page_number if region else None,
        source_excerpt=excerpt,
        text_region=text_region,
        extraction_method=region.method if region else "text",
        confidence=force_confidence or _confidence(region, required=required),
        warnings=[warning] if warning else [],
    )


def _search(pattern: str, text: str, flags: int = re.IGNORECASE) -> re.Match[str] | None:
    return re.search(pattern, text, flags)


def _parse_regions(
    regions: list[TextRegion],
    normalization_history: list[dict[str, Any]],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    list[ExtractedField],
    list[dict[str, Any]],
]:
    text = "\n".join(region.text for region in regions)
    text, additional_history = normalize_extracted_text(text)
    normalization_history.extend(additional_history)
    fields: list[ExtractedField] = []
    warnings: list[dict[str, Any]] = []

    utility_match = _search(r"\b(Southern California Edison|SCE)\b", text)
    utility = "Southern California Edison" if utility_match else None
    fields.append(
        _evidence(
            regions,
            "account",
            "utility",
            utility_match.group(1) if utility_match else None,
            utility,
            "Southern California Edison",
            "SCE",
            required=True,
        )
    )

    plan_match = _search(
        r"(?:rate\s+plan|tariff|schedule)\s*[:#-]?\s*([A-Z][A-Z0-9._ -]{1,40})",
        text,
    )
    plan_code = (
        re.sub(r"[^A-Z0-9._-]+", "-", plan_match.group(1).strip().upper()) if plan_match else None
    )
    fields.append(
        _evidence(
            regions,
            "rate_plan",
            "plan_code",
            plan_match.group(1).strip() if plan_match else None,
            plan_code,
            "Rate Plan",
            "Tariff",
            required=True,
        )
    )

    period_match = _search(
        r"(?:billing\s+(?:period|cycle)|service\s+period)\s*[:#-]?\s*"
        r"([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})"
        r"\s*(?:-|to|\u2013|\u2014)\s*"
        r"([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})",
        text,
    )
    cycle_start = _parse_date(period_match.group(1)) if period_match else None
    cycle_end = _parse_date(period_match.group(2)) if period_match else None
    cycle_days = (cycle_end - cycle_start).days if cycle_start and cycle_end else None
    fields.extend(
        (
            _evidence(
                regions,
                "billing_cycle",
                "starts_at",
                period_match.group(1) if period_match else None,
                cycle_start.isoformat() if cycle_start else None,
                "Billing Period",
                "Billing Cycle",
                required=True,
            ),
            _evidence(
                regions,
                "billing_cycle",
                "ends_at",
                period_match.group(2) if period_match else None,
                cycle_end.isoformat() if cycle_end else None,
                "Billing Period",
                "Billing Cycle",
                required=True,
            ),
        )
    )

    account_match = _search(
        r"account(?:\s+(?:number|ending|no\.?|#))?\s*[:#-]?\s*(?:ending\s*)?([A-Z0-9 -]{4,})",
        text,
    )
    suffix = re.sub(r"\W", "", account_match.group(1))[-4:] if account_match else None
    fields.append(
        _evidence(
            regions,
            "account",
            "account_suffix",
            f"ending {suffix}" if suffix else None,
            suffix,
            "Account",
        )
    )

    meters = sorted(
        {
            match.group(1).strip()
            for match in re.finditer(
                r"\bmeter(?:\s+(?:number|id|no\.?|#))?\s*[:#-]?\s*([A-Z0-9-]{4,})",
                text,
                re.IGNORECASE,
            )
        }
    )
    for index, meter in enumerate(meters):
        fields.append(
            _evidence(
                regions,
                "billing_cycle",
                f"meters.{index}.identifier",
                meter,
                meter,
                "Meter",
                meter,
            )
        )

    usage_match = _search(
        r"(?:total\s+(?:electricity\s+)?usage|cycle\s+usage|usage\s+total)"
        r"\s*[:#-]?\s*([\d,]+(?:\.\d+)?)\s*kWh",
        text,
    )
    total_usage = _decimal_string(usage_match.group(1)) if usage_match else None
    fields.append(
        _evidence(
            regions,
            "billing_cycle",
            "total_usage_kwh",
            usage_match.group(1) if usage_match else None,
            total_usage,
            "Total Usage",
            "Cycle Usage",
            required=True,
        )
    )

    days_remaining_match = _search(r"(\d+)\s+days?\s+(?:remaining|left)", text)
    days_elapsed_match = _search(r"(\d+)\s+days?\s+elapsed", text)
    read_date_match = _search(
        r"(?:meter\s+)?read\s+date\s*[:#-]?\s*"
        r"([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})",
        text,
    )
    meter_read_date = _parse_date(read_date_match.group(1)) if read_date_match else cycle_end

    tier_pattern = re.compile(
        r"(Tier\s+\d+)\s*(?:\||:)?\s*"
        r"(\d+(?:\.\d+)?)\s*(?:-|to|\u2013|\u2014)\s*(\d+(?:\.\d+)?)\s*kWh"
        r"\s*(?:\||,)?\s*([\d,]+(?:\.\d+)?)\s*kWh"
        r"\s*(?:\||,)?\s*\$?(\d+(?:\.\d+)?)\s*(?:/kWh|\u00a2/kWh)?"
        r"(?:\s*(?:\||,)?\s*\$?(\d+(?:\.\d+)?))?",
        re.IGNORECASE,
    )
    open_tier_pattern = re.compile(
        r"(Tier\s+\d+)\s*(?:\||:)?\s*"
        r"(\d+(?:\.\d+)?)\s*(?:\+|kWh\s+and\s+above|and\s+above)"
        r"(?:\s*kWh)?\s*(?:\||,)?\s*([\d,]+(?:\.\d+)?)\s*kWh"
        r"\s*(?:\||,)?\s*\$?(\d+(?:\.\d+)?)\s*(?:/kWh|\u00a2/kWh)?"
        r"(?:\s*(?:\||,)?\s*\$?(\d+(?:\.\d+)?))?",
        re.IGNORECASE,
    )
    tiers: list[dict[str, Any]] = []
    for match in tier_pattern.finditer(text):
        lower = _decimal_string(match.group(2))
        upper = _decimal_string(match.group(3))
        usage = _decimal_string(match.group(4))
        rate = _decimal_string(match.group(5))
        charge = _decimal_string(match.group(6)) if match.group(6) else None
        tiers.append(
            {
                "name": match.group(1).title(),
                "lower_bound_kwh": lower,
                "upper_bound_kwh": upper,
                "usage_kwh": usage,
                "price_per_kwh": rate,
                "energy_charge": charge,
            }
        )
    for match in open_tier_pattern.finditer(text):
        lower = _decimal_string(match.group(2))
        usage = _decimal_string(match.group(3))
        rate = _decimal_string(match.group(4))
        charge = _decimal_string(match.group(5)) if match.group(5) else None
        tiers.append(
            {
                "name": match.group(1).title(),
                "lower_bound_kwh": lower,
                "upper_bound_kwh": None,
                "usage_kwh": usage,
                "price_per_kwh": rate,
                "energy_charge": charge,
            }
        )
    tiers.sort(key=lambda item: int(re.search(r"\d+", str(item["name"])).group(0)))  # type: ignore[union-attr]
    for index, tier in enumerate(tiers):
        for key in ("lower_bound_kwh", "upper_bound_kwh", "usage_kwh", "price_per_kwh"):
            fields.append(
                _evidence(
                    regions,
                    "rate_plan" if key != "usage_kwh" else "billing_cycle",
                    f"tiers.{index}.{key}",
                    tier[key],
                    tier[key],
                    tier["name"],
                    str(tier[key] or ""),
                    required=key in {"lower_bound_kwh", "usage_kwh", "price_per_kwh"},
                )
            )

    tou_rows: list[dict[str, Any]] = []
    for match in re.finditer(
        r"(On[- ]?Peak|Mid[- ]?Peak|Off[- ]?Peak|Super Off[- ]?Peak)"
        r"\s*(?:\||:)?\s*([\d,]+(?:\.\d+)?)\s*kWh"
        r"\s*(?:\||,)?\s*\$?(\d+(?:\.\d+)?)\s*/kWh",
        text,
        re.IGNORECASE,
    ):
        tou_rows.append(
            {
                "name": match.group(1).strip().lower().replace(" ", "-"),
                "usage_kwh": _decimal_string(match.group(2)),
                "price_per_kwh": _decimal_string(match.group(3)),
            }
        )

    pricing_model = (
        "time_of_use_tiered"
        if tiers and tou_rows
        else "tiered"
        if tiers
        else "time_of_use"
        if tou_rows
        else "flat"
    )
    fields.append(
        _evidence(
            regions,
            "rate_plan",
            "pricing_model",
            pricing_model,
            pricing_model,
            "Usage by Tier" if tiers else "On-Peak",
            force_confidence="high" if tiers or tou_rows else "low",
            warning=(
                None
                if tiers or tou_rows
                else {
                    "code": "pricing_model_ambiguous",
                    "message": "No complete tier or time-of-use table was detected",
                }
            ),
        )
    )

    amount_patterns = {
        "energy_subtotal": r"(?:energy\s+(?:charges|subtotal)|electricity\s+charges)"
        r"\s*[:#-]?\s*\$?\(?(-?[\d,]+(?:\.\d+)?)\)?",
        "full_bill_total": r"(?:total\s+(?:amount\s+due|current\s+charges|bill)|bill\s+total)"
        r"\s*[:#-]?\s*\$?\(?(-?[\d,]+(?:\.\d+)?)\)?",
        "fixed_charges": r"(?:fixed|service|basic)\s+charges?\s*[:#-]?\s*\$?"
        r"\(?(-?[\d,]+(?:\.\d+)?)\)?",
        "taxes_fees": r"(?:taxes?(?:\s+and)?\s+fees?|fees?)\s*[:#-]?\s*\$?"
        r"\(?(-?[\d,]+(?:\.\d+)?)\)?",
        "credits": r"(?:climate|baseline|care|fera)?\s*credits?\s*[:#-]?\s*\$?"
        r"\(?(-?[\d,]+(?:\.\d+)?)\)?",
        "adjustments": r"(?:one[- ]time|manual|other)\s+adjustments?\s*[:#-]?\s*\$?"
        r"\(?(-?[\d,]+(?:\.\d+)?)\)?",
        "delivery_charges": r"delivery\s+charges?\s*[:#-]?\s*\$?"
        r"\(?(-?[\d,]+(?:\.\d+)?)\)?",
        "generation_charges": r"generation\s+charges?\s*[:#-]?\s*\$?"
        r"\(?(-?[\d,]+(?:\.\d+)?)\)?",
        "daily_service_charge": r"daily\s+(?:service|basic)\s+charge\s*[:#-]?\s*\$?"
        r"\(?(-?[\d,]+(?:\.\d+)?)\)?",
        "minimum_bill": r"minimum\s+(?:bill|charge)\s*[:#-]?\s*\$?"
        r"\(?(-?[\d,]+(?:\.\d+)?)\)?",
        "projected_energy_subtotal": r"projected\s+energy\s+(?:charge|subtotal)"
        r"\s*[:#-]?\s*\$?\(?(-?[\d,]+(?:\.\d+)?)\)?",
        "projected_bill_total": r"projected\s+(?:bill|total)"
        r"\s*[:#-]?\s*\$?\(?(-?[\d,]+(?:\.\d+)?)\)?",
    }
    amounts: dict[str, str | None] = {}
    for key, pattern in amount_patterns.items():
        amount_match = _search(pattern, text)
        amount = _decimal_string(amount_match.group(1)) if amount_match else None
        amounts[key] = amount
        fields.append(
            _evidence(
                regions,
                "billing_cycle",
                key,
                amount_match.group(1) if amount_match else None,
                amount,
                key.replace("_", " "),
                required=key in {"energy_subtotal", "full_bill_total"},
            )
        )

    current_tier_match = _search(
        r"current(?:ly)?\s*(?:tier)?\s*[:#-]?\s*(?:in\s+)?(Tier\s+\d+)",
        text,
    )
    projected_tier_match = _search(
        r"projected\s+(?:to\s+(?:remain|stay)\s+in\s+)?(Tier\s+\d+)",
        text,
    )
    current_tier = current_tier_match.group(1).title() if current_tier_match else None
    projected_tier = projected_tier_match.group(1).title() if projected_tier_match else None
    provider_mode = (
        "direct_access"
        if _search(r"\bdirect\s+access\b", text)
        else "cca"
        if _search(r"\b(?:community\s+choice|cca)\b", text)
        else "bundled"
    )

    threshold_warning = {
        "code": "threshold_interpretation_required",
        "message": (
            "A displayed tier threshold does not prove whether the rule is fixed, "
            "daily-baseline-derived, or multiplier-derived"
        ),
    }
    fields.append(
        _evidence(
            regions,
            "rate_plan",
            "threshold_interpretation",
            None,
            "unknown",
            "Usage by Tier",
            force_confidence="low",
            warning=threshold_warning,
        )
    )

    if suspicious_mojibake(text):
        warnings.append(
            {
                "code": "suspicious_mojibake",
                "message": "Extracted text contains suspicious encoding sequences",
            }
        )

    account_data = {
        "utility": utility,
        "plan_code": plan_code,
        "account_suffix": suffix,
        "meter_identifiers": meters,
        "provider_mode": provider_mode,
    }
    rate_data = {
        "utility": utility,
        "plan_name": plan_code or "Imported utility bill draft",
        "plan_code": plan_code,
        "pricing_model": pricing_model,
        "currency": "USD",
        "effective_from": cycle_start.isoformat() if cycle_start else None,
        "tiers": tiers,
        "tou_periods": tou_rows,
        "threshold_interpretation": "unknown",
    }
    cycle_data = {
        "starts_at": cycle_start.isoformat() if cycle_start else None,
        "ends_at": cycle_end.isoformat() if cycle_end else None,
        "cycle_days": cycle_days,
        "meter_read_date": meter_read_date.isoformat() if meter_read_date else None,
        "days_remaining": int(days_remaining_match.group(1)) if days_remaining_match else None,
        "days_elapsed": int(days_elapsed_match.group(1)) if days_elapsed_match else None,
        "total_usage_kwh": total_usage,
        "usage_by_tier": [
            {
                "name": tier["name"],
                "usage_kwh": tier["usage_kwh"],
                "energy_charge": tier["energy_charge"],
            }
            for tier in tiers
        ],
        "usage_by_tou": tou_rows,
        "meter_records": [{"meter_identifier": meter} for meter in meters],
        "current_tier": current_tier,
        "projected_tier": projected_tier,
        **amounts,
    }
    return account_data, rate_data, cycle_data, fields, warnings


def extract_bill(
    content: bytes,
    settings: Settings,
    *,
    pdf_path: Path,
    runner: CommandRunner = subprocess.run,
) -> BillExtraction:
    inspection = inspect_pdf(content, settings)
    text_regions, ocr_pages = _extract_text_regions(inspection)
    warnings: list[dict[str, Any]] = []
    ocr_regions: list[TextRegion] = []
    if ocr_pages:
        bounded_ocr_pages = ocr_pages[:3]
        if len(ocr_pages) > len(bounded_ocr_pages):
            warnings.append(
                {
                    "code": "ocr_page_scope_limited",
                    "pages": ocr_pages[len(bounded_ocr_pages) :],
                    "message": (
                        "OCR was limited to the first three candidate pages; "
                        "remaining image-only pages require manual review."
                    ),
                }
            )
        ocr_regions, ocr_warnings = _run_ocr(
            pdf_path,
            bounded_ocr_pages,
            settings,
            runner=runner,
        )
        warnings.extend(ocr_warnings)
    regions = [
        region for region in text_regions if region.page_number not in set(ocr_pages)
    ] + ocr_regions
    regions.sort(key=lambda item: (item.page_number, -(item.y or 0), item.x or 0))
    if not regions:
        raise BillPdfError(
            "bill_pdf_no_text",
            "No usable text could be extracted from the PDF",
        )
    raw_text = "\n".join(region.text for region in regions)
    normalized_text, normalization_history = normalize_extracted_text(raw_text)
    parser_id = PARSER_ID
    parser_version = PARSER_VERSION
    page_classifications: list[dict[str, Any]] = []
    ignored_sections: list[dict[str, Any]] = []
    validation: dict[str, Any] = {}
    adapter_result: dict[str, Any] | None = None
    adapter_blocking: list[dict[str, Any]] | None = None
    if recognizes_sce_residential(regions):
        sce = parse_sce_residential(regions)
        parser_id = SCE_PARSER_ID
        parser_version = SCE_PARSER_VERSION
        account, rate, cycle = sce.account_data, sce.rate_data, sce.cycle_data
        fields = [
            ExtractedField(
                output_kind=item.output_kind,
                field_key=item.field_key,
                raw_value=item.raw_value,
                normalized_value=item.normalized_value,
                page_number=item.line.page_number if item.line else None,
                source_excerpt=(redact_sensitive_text(item.line.text)[:500] if item.line else None),
                text_region=item.line.region if item.line else None,
                extraction_method=item.line.method if item.line else "text",
                confidence=item.confidence,
                warnings=item.warnings,
                parser_rule=item.parser_rule,
                validation_result=item.validation_result,
            )
            for item in sce.fields
        ]
        warnings.extend(sce.warnings)
        page_classifications = sce.page_classifications
        ignored_sections = sce.ignored_sections
        validation = sce.validation
        adapter_result = sce.normalized_result
        adapter_blocking = sce.blocking_warnings
    else:
        account, rate, cycle, fields, parse_warnings = _parse_regions(
            regions,
            normalization_history,
        )
        warnings.extend(parse_warnings)
    blocking = adapter_blocking or [
        {
            "code": "required_field_review",
            "field": item.field_key,
            "message": "Required field must be confirmed or corrected",
        }
        for item in fields
        if item.confidence in {"missing", "low"}
        and item.field_key
        in {
            "utility",
            "plan_code",
            "pricing_model",
            "starts_at",
            "ends_at",
            "total_usage_kwh",
            "threshold_interpretation",
        }
    ]
    if rate.get("pricing_model") in {"time_of_use", "time_of_use_tiered"}:
        blocking.append(
            {
                "code": "incomplete_tou_rules",
                "message": (
                    "Bill TOU rows do not prove complete clock, day-type, season, "
                    "or holiday coverage; complete the linked draft in the rate editor"
                ),
            }
        )
    if any(item.confidence == "low" and item.extraction_method == "ocr" for item in fields):
        blocking.append(
            {
                "code": "low_ocr_confidence",
                "message": "Low-confidence OCR tariff fields require administrator correction",
            }
        )
    method = "mixed" if text_regions and ocr_regions else "ocr" if ocr_regions else "text"
    return BillExtraction(
        page_count=inspection.page_count,
        extraction_method=method,
        ocr_version="tesseract-local" if ocr_regions else None,
        regions=regions,
        raw_text=raw_text,
        normalized_text=normalized_text,
        normalization_history=normalization_history,
        account_data=account,
        rate_data=rate,
        cycle_data=cycle,
        fields=fields,
        warnings=warnings,
        blocking_warnings=blocking,
        parser_id=parser_id,
        parser_version=parser_version,
        page_classifications=page_classifications,
        ignored_sections=ignored_sections,
        validation=validation,
        adapter_result=adapter_result,
    )
