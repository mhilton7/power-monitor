from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

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
)
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
    without_query = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    if without_query in APPROVED_SOURCE_URLS:
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
class ParseResult:
    status: str
    documents: list[RatePlanDocument] = field(default_factory=list)
    discovered_links: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)


class RateSourceAdapter(Protocol):
    parser_id: str

    def parse(self, content: bytes, source_url: str, content_type: str) -> ParseResult: ...


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

    def parse(self, content: bytes, source_url: str, content_type: str) -> ParseResult:
        if content_type not in {"text/html", "application/xhtml+xml"}:
            return ParseResult(
                status="failed",
                errors=[{"code": "content_type", "message": "Expected HTML source"}],
            )
        parser = _EvidenceHTMLParser()
        try:
            parser.feed(content.decode("utf-8"))
            if not parser.script_parts:
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
            documents = _documents_from_payload(json.loads("".join(parser.script_parts)))
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

    def parse(self, content: bytes, source_url: str, content_type: str) -> ParseResult:
        result = super().parse(content, source_url, content_type)
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

    def parse(self, content: bytes, source_url: str, content_type: str) -> ParseResult:
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

    def parse(self, content: bytes, source_url: str, content_type: str) -> ParseResult:
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
