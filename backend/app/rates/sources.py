from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any, ClassVar, Protocol
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx

from app.rates.documents import (
    DayScheduleDocument,
    RateAdjustmentDocument,
    RatePeriodDocument,
    RatePlanDocument,
    RateSeasonDocument,
)

APPROVED_SOURCE_URLS = {
    "https://www.sce.com/save-money/rates-financing/residential-rate-plans/time-of-use-plans",
    "https://www.sce.com/save-money/rates-financing/sce-rate-advisory",
    "https://www.sce.com/regulatory/regulatory-information/tariff-books/rates-pricing-choices",
    "https://www.sce.com/regulatory/tariff-books/historical-rates",
}
APPROVED_DOCUMENT_PREFIXES = (
    "/regulatory/regulatory-information/tariff-books/",
    "/regulatory/tariff-books/",
    "/sites/default/files/inline-files/",
    "/sites/default/files/custom-files/",
)
APPROVED_MANAGED_SOURCE_PREFIXES = (
    "/save-money/rates-financing/",
    "/regulatory/regulatory-information/tariff-books/",
    "/regulatory/tariff-books/",
)
REMOTE_SOURCE_PARSER_IDS = {
    "sce_public_tou_html_v1",
    "sce_rate_advisory_html_v1",
    "sce_tariff_index_html_v1",
    "sce_tariff_pdf_v1",
}
PARSER_VERSION = "1.0.0"
PERMITTED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "application/pdf",
    "application/json",
    "text/json",
    "text/csv",
}


class SourceSecurityError(ValueError):
    pass


class SourceFetchError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def normalize_url(url: str) -> str:
    parsed = urlsplit(url)
    path = str(PurePosixPath(parsed.path))
    if parsed.path.endswith("/") and not path.endswith("/"):
        path += "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def validate_source_url(url: str, *, document_link: bool = False) -> str:
    normalized = normalize_url(url)
    parsed = urlsplit(normalized)
    if parsed.scheme != "https":
        raise SourceSecurityError("Rate sources must use HTTPS")
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise SourceSecurityError("Credentials and non-standard ports are not allowed")
    if parsed.hostname not in {"sce.com", "www.sce.com"}:
        raise SourceSecurityError("Source host is not approved")
    decoded_path = unquote(parsed.path)
    if any(part in {".", ".."} for part in decoded_path.split("/")):
        raise SourceSecurityError("Source path contains traversal segments")
    without_query = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    if without_query in APPROVED_SOURCE_URLS:
        return normalized
    if any(parsed.path.startswith(prefix) for prefix in APPROVED_MANAGED_SOURCE_PREFIXES):
        return normalized
    if (
        document_link
        and parsed.path.lower().endswith(".pdf")
        and any(parsed.path.startswith(prefix) for prefix in APPROVED_DOCUMENT_PREFIXES)
    ):
        return normalized
    raise SourceSecurityError("Source path is not on the server-side allowlist")


async def assert_public_resolution(hostname: str) -> None:
    records = await asyncio.to_thread(socket.getaddrinfo, hostname, 443, type=socket.SOCK_STREAM)
    if not records:
        raise SourceSecurityError("Approved source did not resolve")
    for record in records:
        address = ipaddress.ip_address(record[4][0])
        if not address.is_global:
            raise SourceSecurityError("Approved source resolved to a non-public address")


@dataclass(frozen=True)
class FetchResult:
    status_code: int
    final_url: str
    content: bytes
    content_type: str
    etag: str | None
    last_modified: str | None
    duration_ms: int


async def fetch_source(
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    max_bytes: int = 10_485_760,
    connect_timeout: float = 10,
    read_timeout: float = 30,
    total_timeout: float = 45,
    max_redirects: int = 3,
    max_retries: int = 3,
    client: httpx.AsyncClient | None = None,
    verify_dns: bool = True,
) -> FetchResult:
    current = validate_source_url(url)
    headers = {
        "User-Agent": "PowerMonitorRateEvidence/1.0",
        "Accept": "text/html,application/pdf,application/json",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    owned_client = client is None
    if client is None:
        timeout = httpx.Timeout(total_timeout, connect=connect_timeout, read=read_timeout)
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    started = time.monotonic()
    try:
        for attempt in range(max_retries):
            try:
                redirects = 0
                while True:
                    parsed = urlsplit(current)
                    if verify_dns:
                        await assert_public_resolution(parsed.hostname or "")
                    async with client.stream("GET", current, headers=headers) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location or redirects >= max_redirects:
                                raise SourceFetchError(
                                    "redirect_limit", "Source exceeded redirect limit"
                                )
                            candidate = urljoin(current, location)
                            current = validate_source_url(
                                candidate,
                                document_link=urlsplit(candidate).path.lower().endswith(".pdf"),
                            )
                            redirects += 1
                            continue
                        if response.status_code == 304:
                            return FetchResult(
                                304,
                                current,
                                b"",
                                response.headers.get("content-type", ""),
                                response.headers.get("etag", etag),
                                response.headers.get("last-modified", last_modified),
                                round((time.monotonic() - started) * 1000),
                            )
                        if response.status_code >= 500 or response.status_code in {408, 429}:
                            raise SourceFetchError(
                                "upstream_error",
                                f"Approved source returned HTTP {response.status_code}",
                            )
                        if response.status_code != 200:
                            raise SourceFetchError(
                                "http_error",
                                f"Approved source returned HTTP {response.status_code}",
                            )
                        declared = response.headers.get("content-length")
                        if declared and int(declared) > max_bytes:
                            raise SourceFetchError(
                                "source_too_large", "Source exceeds maximum size"
                            )
                        response_type = (
                            response.headers.get("content-type", "application/octet-stream")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        if response_type not in PERMITTED_CONTENT_TYPES:
                            raise SourceFetchError(
                                "unsupported_content_type",
                                "Approved source returned an unsupported content type",
                            )
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                raise SourceFetchError(
                                    "source_too_large", "Source exceeds maximum size"
                                )
                            chunks.append(chunk)
                        return FetchResult(
                            200,
                            current,
                            b"".join(chunks),
                            response_type,
                            response.headers.get("etag"),
                            response.headers.get("last-modified"),
                            round((time.monotonic() - started) * 1000),
                        )
            except SourceFetchError as exc:
                if exc.code != "upstream_error" or attempt + 1 == max_retries:
                    raise
                await asyncio.sleep(0.1 * (2**attempt))
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt + 1 == max_retries:
                    raise SourceFetchError(
                        "network_error", "Approved source was unavailable"
                    ) from exc
                await asyncio.sleep(0.1 * (2**attempt))
        raise SourceFetchError("network_error", "Approved source was unavailable")
    finally:
        if owned_client:
            await client.aclose()


class _EvidenceHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_rate_script = False
        self.script_parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script" and "data-power-monitor-rates" in attributes:
            self.in_rate_script = True
        if tag == "a" and attributes.get("href"):
            self.links.append(str(attributes["href"]))

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.in_rate_script = False

    def handle_data(self, data: str) -> None:
        if self.in_rate_script:
            self.script_parts.append(data)


@dataclass
class _HTMLNode:
    tag: str
    attributes: dict[str, str]
    children: list[_HTMLNode] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attributes.get("class", "").split())

    @property
    def text(self) -> str:
        return " ".join(html.unescape(" ".join(self.text_parts)).split())


class _HTMLTreeParser(HTMLParser):
    void_elements: ClassVar[set[str]] = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__()
        self.roots: list[_HTMLNode] = []
        self.stack: list[_HTMLNode] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _HTMLNode(tag, {key: value or "" for key, value in attrs})
        if self.stack:
            self.stack[-1].children.append(node)
        else:
            self.roots.append(node)
        if tag not in self.void_elements:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        for node in self.stack:
            node.text_parts.append(data)


def _nodes(
    roots: list[_HTMLNode] | _HTMLNode,
    *,
    class_name: str | None = None,
    tag: str | None = None,
) -> list[_HTMLNode]:
    pending = list(roots if isinstance(roots, list) else roots.children)
    found: list[_HTMLNode] = []
    while pending:
        node = pending.pop(0)
        if (class_name is None or class_name in node.classes) and (tag is None or node.tag == tag):
            found.append(node)
        pending[0:0] = node.children
    return found


def _first_text(root: _HTMLNode, class_name: str) -> str:
    matches = _nodes(root, class_name=class_name)
    return matches[0].text if matches else ""


def _parse_dollars(value: str) -> str | None:
    match = re.search(r"\$\s*(\d+(?:\.\d+)?)", value)
    return format(Decimal(match.group(1)), "f") if match else None


def _parse_cents(value: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:¢|cents?)", value, re.IGNORECASE)
    if not match:
        raise ValueError(f"Published price is not a cent value: {value}")
    return format(Decimal(match.group(1)) / Decimal("100"), "f")


def _minute_of_day(value: str) -> int:
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\b", value, re.IGNORECASE)
    if not match:
        raise ValueError(f"Published period boundary is not recognized: {value}")
    hour = int(match.group(1)) % 12
    minute = int(match.group(2) or 0)
    if match.group(3).lower() == "p":
        hour += 12
    return hour * 60 + minute


def _plan_code(heading: str) -> str | None:
    normalized = " ".join(heading.upper().replace(chr(0x2013), "-").split())
    known = {
        "TOU-D 4 PM TO 9 PM": "TOU-D-4-9PM",
        "TOU-D 5 PM TO 8 PM": "TOU-D-5-8PM",
        "TOU-D-PRIME": "TOU-D-PRIME",
    }
    return known.get(normalized)


def _schedule_from_section(section: _HTMLNode) -> DayScheduleDocument | None:
    header_nodes = _nodes(section, class_name="header-block")
    header = _first_text(header_nodes[0], "rate-header-text") if header_nodes else ""
    if header == "After Baseline Credit":
        return None
    day_type = {
        "Weekdays": "weekday",
        "Weekend": "weekend",
        "Weekdays & Weekend": "all-days",
    }.get(header)
    if day_type is None:
        return None
    blocks = _nodes(section, class_name="rate-block-wrapper")
    parsed: list[tuple[str, str, int]] = []
    for block in blocks:
        label = _first_text(block, "rate-block-sub-heading")
        price = _parse_cents(_first_text(block, "rate-block-text-1"))
        boundaries = _nodes(block, class_name="rate-block-text-2")
        if not label or not boundaries:
            raise ValueError(f"Published {header} rate block is incomplete")
        parsed.append((label.lower().replace(" ", "-"), price, _minute_of_day(boundaries[0].text)))
    periods: list[RatePeriodDocument] = []
    for index, (label, price, start_minute) in enumerate(parsed):
        end_minute = parsed[index + 1][2] if index + 1 < len(parsed) else 1440
        periods.append(
            RatePeriodDocument(
                label=label,
                start_minute=start_minute,
                end_minute=end_minute,
                price_per_kwh=price,
                delivery_per_kwh=price,
                display_order=index,
            )
        )
    return DayScheduleDocument(day_type=day_type, periods=periods)


def _documents_from_sce_tou_page(
    content: str, source_url: str, effective_from: date
) -> list[RatePlanDocument]:
    documents: list[RatePlanDocument] = []
    heading_pattern = re.compile(
        r'<h2[^>]*class=["\'][^"\']*accordion-container-header-button-headline'
        r'[^"\']*["\'][^>]*>(.*?)</h2>',
        re.IGNORECASE | re.DOTALL,
    )
    headings = list(heading_pattern.finditer(content))
    for index, match in enumerate(headings):
        heading = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).split())
        plan_code = _plan_code(heading)
        if plan_code is None:
            continue
        fragment_end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        tree = _HTMLTreeParser()
        tree.feed(content[match.end() : fragment_end])
        container = _HTMLNode("section", {}, children=tree.roots)
        header_values: dict[str, str] = {}
        for row in _nodes(container, tag="tr"):
            key = _first_text(row, "rate-header-text").rstrip(":")
            value = _first_text(row, "data-text")
            if key and value:
                header_values[key] = value
        seasons: list[RateSeasonDocument] = []
        for tab in _nodes(container, class_name="tab-panel"):
            tab_text = tab.text
            if "June - September" in tab_text:
                season_name, start, end = "summer", "06-01", "09-30"
            elif "October - May" in tab_text:
                season_name, start, end = "winter", "10-01", "05-31"
            else:
                continue
            schedules = [
                schedule
                for section in _nodes(tab, class_name="rcb-variation-1-content")
                if (schedule := _schedule_from_section(section)) is not None
            ]
            if schedules:
                seasons.append(
                    RateSeasonDocument(
                        name=season_name,
                        start=start,
                        end=end,
                        schedules=schedules,
                    )
                )
        if not seasons:
            raise ValueError(f"No published seasonal schedule was found for {plan_code}")
        adjustments: list[RateAdjustmentDocument] = []
        service_charge = _parse_dollars(header_values.get("Base Services Charge", ""))
        if service_charge is not None:
            adjustments.append(
                RateAdjustmentDocument(
                    name="Base service charge",
                    component="daily_fixed_charge",
                    value=service_charge,
                    unit="per_day",
                    scope="full_account_estimate",
                )
            )
        baseline_credit = _parse_dollars(header_values.get("Baseline Credit", ""))
        if baseline_credit is not None:
            adjustments.append(
                RateAdjustmentDocument(
                    name="Baseline credit",
                    component="baseline_credit",
                    operation="subtract",
                    value=baseline_credit,
                    unit="per_kwh",
                    scope="full_account_estimate",
                )
            )
        documents.append(
            RatePlanDocument(
                plan_name=heading,
                plan_code=plan_code,
                utility="Southern California Edison",
                description="Official SCE residential time-of-use rate page",
                currency="USD",
                timezone="America/Los_Angeles",
                effective_from=effective_from,
                cost_scope_default="energy_only",
                source_label="SCE public residential TOU page",
                source_note=(
                    "Published summary rates extracted from the official SCE page; "
                    "the administrator supplied the effective date and must verify it "
                    f"against filed tariff evidence before approval. Source: {source_url}"
                ),
                provider_mode="sce_delivery_generation",
                seasons=seasons,
                adjustments=adjustments,
            )
        )
    return documents


@dataclass
class ParseResult:
    status: str
    documents: list[RatePlanDocument] = field(default_factory=list)
    discovered_links: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)


class RateSourceAdapter(Protocol):
    parser_id: str

    def parse(
        self,
        content: bytes,
        source_url: str,
        content_type: str,
        *,
        effective_from: date | None = None,
    ) -> ParseResult: ...


def _seed_plan_to_document(plan: dict[str, Any], metadata: dict[str, Any]) -> RatePlanDocument:
    seasons: list[RateSeasonDocument] = []
    for season_name, season_range in plan["seasons"].items():
        schedules = []
        for day_type, periods in plan["periods"][season_name].items():
            schedules.append(
                DayScheduleDocument(
                    day_type=day_type,
                    periods=[
                        RatePeriodDocument(
                            label=str(period[2]),
                            start_minute=int(period[0]),
                            end_minute=int(period[1]),
                            price_per_kwh=str(period[3]),
                            delivery_per_kwh=str(period[3]),
                        )
                        for period in periods
                    ],
                )
            )
        seasons.append(
            RateSeasonDocument(
                name=season_name,
                start=season_range["start"],
                end=season_range["end"],
                schedules=schedules,
            )
        )
    adjustments = [
        RateAdjustmentDocument(
            name="Base service charge",
            component="daily_fixed_charge",
            value=str(plan.get("base_service_charge_per_day", "0")),
            unit="per_day",
            scope="full_account_estimate",
        )
    ]
    if plan.get("baseline_credit_per_kwh") is not None:
        adjustments.append(
            RateAdjustmentDocument(
                name="Baseline credit",
                component="baseline_credit",
                operation="subtract",
                value=str(plan["baseline_credit_per_kwh"]),
                unit="per_kwh",
                scope="full_account_estimate",
            )
        )
    return RatePlanDocument(
        plan_name=plan["name"],
        plan_code=plan["code"],
        utility="Southern California Edison",
        description=plan.get("eligibility") or "Official SCE residential rate candidate",
        currency=plan.get("currency", metadata.get("currency", "USD")),
        timezone=plan.get("timezone", metadata.get("timezone", "America/Los_Angeles")),
        effective_from=plan.get("effective_from", metadata["effective_from"]),
        effective_through=plan.get("effective_to"),
        cost_scope_default="energy_only",
        source_label=metadata.get("source_type", "SCE public source"),
        source_note=metadata.get("notes", ""),
        provider_mode="sce_delivery_generation",
        seasons=seasons,
        adjustments=adjustments,
    )


def _documents_from_payload(payload: Any) -> list[RatePlanDocument]:
    if isinstance(payload, dict) and payload.get("schema_version") == "power-monitor-rate-plan/1.0":
        return [RatePlanDocument.model_validate(payload)]
    if isinstance(payload, dict) and isinstance(payload.get("plans"), list):
        metadata = payload.get("metadata", {})
        return [_seed_plan_to_document(plan, metadata) for plan in payload["plans"]]
    if isinstance(payload, list):
        return [RatePlanDocument.model_validate(item) for item in payload]
    raise ValueError("No supported normalized rate document was found")


class EmbeddedRateHTMLAdapter:
    parser_id = "sce_public_tou_html_v1"

    def parse(
        self,
        content: bytes,
        source_url: str,
        content_type: str,
        *,
        effective_from: date | None = None,
    ) -> ParseResult:
        if content_type not in {"text/html", "application/xhtml+xml"}:
            return ParseResult(
                status="failed",
                errors=[{"code": "content_type", "message": "Expected HTML source"}],
            )
        parser = _EvidenceHTMLParser()
        try:
            decoded = content.decode("utf-8")
            parser.feed(decoded)
            if parser.script_parts:
                documents = _documents_from_payload(json.loads("".join(parser.script_parts)))
            else:
                if effective_from is None:
                    probe = _HTMLTreeParser()
                    probe.feed(decoded)
                    if _nodes(probe.roots, class_name="accordion-container-bg-layout"):
                        return ParseResult(
                            status="manual_review",
                            warnings=[
                                {
                                    "code": "effective_date_required",
                                    "message": (
                                        "The SCE page contains rate blocks, but the source "
                                        "needs a verified effective date before candidates "
                                        "can be created"
                                    ),
                                }
                            ],
                            citations=[
                                {"url": source_url, "sha256": hashlib.sha256(content).hexdigest()}
                            ],
                        )
                else:
                    documents = _documents_from_sce_tou_page(decoded, source_url, effective_from)
                    if documents:
                        return ParseResult(
                            status="succeeded",
                            documents=documents,
                            citations=[
                                {"url": source_url, "sha256": hashlib.sha256(content).hexdigest()}
                            ],
                        )
                return ParseResult(
                    status="manual_review",
                    warnings=[
                        {
                            "code": "unstructured_html",
                            "message": (
                                "Source was archived but did not expose deterministic "
                                "structured rate data"
                            ),
                        }
                    ],
                    citations=[{"url": source_url, "sha256": hashlib.sha256(content).hexdigest()}],
                )
            return ParseResult(
                status="succeeded",
                documents=documents,
                citations=[{"url": source_url, "sha256": hashlib.sha256(content).hexdigest()}],
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return ParseResult(
                status="failed", errors=[{"code": "parse_error", "message": str(exc)}]
            )


class AdvisoryHTMLAdapter(EmbeddedRateHTMLAdapter):
    parser_id = "sce_rate_advisory_html_v1"


class TariffIndexHTMLAdapter(EmbeddedRateHTMLAdapter):
    parser_id = "sce_tariff_index_html_v1"

    def parse(
        self,
        content: bytes,
        source_url: str,
        content_type: str,
        *,
        effective_from: date | None = None,
    ) -> ParseResult:
        result = super().parse(content, source_url, content_type, effective_from=effective_from)
        parser = _EvidenceHTMLParser()
        try:
            parser.feed(content.decode("utf-8"))
        except UnicodeDecodeError:
            return result
        for link in parser.links:
            candidate = urljoin(source_url, link)
            try:
                result.discovered_links.append(validate_source_url(candidate, document_link=True))
            except SourceSecurityError:
                continue
        result.discovered_links = sorted(set(result.discovered_links))[:50]
        return result


class TariffPDFAdapter:
    parser_id = "sce_tariff_pdf_v1"
    begin = b"POWER_MONITOR_RATE_JSON_BEGIN\n"
    end = b"\nPOWER_MONITOR_RATE_JSON_END"

    def parse(
        self,
        content: bytes,
        source_url: str,
        content_type: str,
        *,
        effective_from: date | None = None,
    ) -> ParseResult:
        if not (content.startswith(b"%PDF") or content_type == "application/pdf"):
            return ParseResult(
                status="failed", errors=[{"code": "content_type", "message": "Expected PDF source"}]
            )
        start = content.find(self.begin)
        finish = content.find(self.end, start + len(self.begin))
        if start < 0 or finish < 0:
            return ParseResult(
                status="manual_review",
                warnings=[
                    {
                        "code": "pdf_requires_review",
                        "message": (
                            "PDF was archived; no deterministic structured rate attachment "
                            "was present"
                        ),
                    }
                ],
                citations=[{"url": source_url, "sha256": hashlib.sha256(content).hexdigest()}],
            )
        try:
            payload = json.loads(content[start + len(self.begin) : finish].decode("utf-8"))
            return ParseResult(
                status="succeeded",
                documents=_documents_from_payload(payload),
                citations=[{"url": source_url, "sha256": hashlib.sha256(content).hexdigest()}],
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return ParseResult(
                status="failed", errors=[{"code": "parse_error", "message": str(exc)}]
            )


class AdminStructuredAdapter:
    parser_id = "admin_uploaded_structured_v1"

    def parse(
        self,
        content: bytes,
        source_url: str,
        content_type: str,
        *,
        effective_from: date | None = None,
    ) -> ParseResult:
        if content_type not in {"application/json", "text/json"}:
            return ParseResult(
                status="failed",
                errors=[{"code": "content_type", "message": "Uploads must be JSON"}],
            )
        try:
            return ParseResult(
                status="succeeded", documents=_documents_from_payload(json.loads(content))
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return ParseResult(
                status="failed", errors=[{"code": "parse_error", "message": str(exc)}]
            )


ADAPTERS: dict[str, RateSourceAdapter] = {
    adapter.parser_id: adapter
    for adapter in (
        EmbeddedRateHTMLAdapter(),
        AdvisoryHTMLAdapter(),
        TariffIndexHTMLAdapter(),
        TariffPDFAdapter(),
        AdminStructuredAdapter(),
    )
}
