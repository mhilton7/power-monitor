from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from worker.app.rate_sync import document_differences, latest_scheduled_time

from app.db.models import (
    AuditEvent,
    BackgroundJob,
    RateChangeCandidate,
    RateExtractionResult,
    RatePlan,
    RateSource,
    RateSourceArtifact,
    RateSourceCheckRun,
    RateVersion,
)
from app.problem import ProblemError
from app.rates.candidates import create_candidate_from_document
from app.rates.documents import RatePlanDocument, document_hash, engine_plan, validate_document
from app.rates.engine import RateEngine
from app.rates.service import (
    activate_version,
    clone_plan_version,
    create_custom_plan,
    update_draft_version,
    version_document,
)
from app.rates.sources import (
    ADAPTERS,
    APPROVED_SOURCE_URLS,
    SourceFetchError,
    SourceSecurityError,
    fetch_source,
    validate_source_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sce"


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


def example_document() -> RatePlanDocument:
    path = Path(__file__).resolve().parents[2] / "shared" / "examples" / "custom-rate-plan.json"
    return RatePlanDocument.model_validate_json(path.read_bytes())


def test_normalized_document_exact_decimals_coverage_and_hash() -> None:
    document = example_document()
    report = validate_document(document)
    assert report.valid
    assert report.coverage == {"all-year/all-days": True}
    assert report.integrity_sha256 == document_hash(document)
    assert document.seasons[0].schedules[0].periods[0].price_per_kwh == "0.25000000"
    assert RateEngine(engine_plan(document)).period_at(
        datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
    ) == ("flat", Decimal("0.25000000"))


def test_custom_adjustments_respect_scope_and_calculation_order() -> None:
    document = example_document()
    payload = document.model_dump(mode="json")
    payload["adjustments"] = [
        {
            "name": "Daily account charge",
            "component": "daily_fixed_charge",
            "operation": "add",
            "value": "0.80",
            "unit": "per_day",
            "scope": "full_account_estimate",
            "eligibility": {},
            "effective_from": None,
            "effective_to": None,
            "calculation_order": 1,
            "description": "",
        },
        {
            "name": "Local tax",
            "component": "percentage_tax",
            "operation": "add",
            "value": "10",
            "unit": "percent",
            "scope": "full_account_estimate",
            "eligibility": {},
            "effective_from": None,
            "effective_to": None,
            "calculation_order": 2,
            "description": "",
        },
    ]
    engine = RateEngine(engine_plan(RatePlanDocument.model_validate(payload)))
    common = {
        "start": datetime(2026, 8, 1, 7, 0, tzinfo=UTC),
        "end": datetime(2026, 8, 2, 7, 0, tzinfo=UTC),
        "energy_kwh": Decimal("4"),
    }
    energy_only = engine.calculate(**common, cost_scope="energy_only")
    full = engine.calculate(**common, cost_scope="full_account_estimate", billing_days=1)
    assert energy_only.total == Decimal("1.00000000")
    assert full.total == Decimal("1.980000000")
    assert full.adjustment_breakdown == {
        "Daily account charge": Decimal("0.80"),
        "Local tax": Decimal("0.180000000"),
    }


def test_missing_period_and_overlap_are_blocking() -> None:
    parsed = ADAPTERS["sce_public_tou_html_v1"].parse(
        (FIXTURES / "missing-period.html").read_bytes(),
        next(iter(APPROVED_SOURCE_URLS)),
        "text/html",
    )
    assert parsed.status == "succeeded"
    report = validate_document(parsed.documents[0])
    assert not report.valid
    assert {item.code for item in report.errors} == {"period_gap"}

    payload = example_document().model_dump(mode="json")
    payload["seasons"][0]["schedules"][0]["periods"] = [
        {**payload["seasons"][0]["schedules"][0]["periods"][0], "end_minute": 800},
        {**payload["seasons"][0]["schedules"][0]["periods"][0], "start_minute": 700},
    ]
    report = validate_document(RatePlanDocument.model_validate(payload))
    assert not report.valid
    assert "period_overlap" in {item.code for item in report.errors}


def test_sce_source_allowlist_is_exact_and_ssrf_safe() -> None:
    for url in APPROVED_SOURCE_URLS:
        assert validate_source_url(url) == url
    tariff_pdf = "https://www.sce.com/regulatory/tariff-books/current-residential.pdf"
    assert validate_source_url(tariff_pdf, document_link=True) == tariff_pdf
    for unsafe in (
        "http://www.sce.com/regulatory/tariff-books/current-residential.pdf",
        "https://example.com/regulatory/tariff-books/current-residential.pdf",
        "https://127.0.0.1/regulatory/tariff-books/current-residential.pdf",
        "https://user:password@www.sce.com/save-money/rates-financing/sce-rate-advisory",
        "https://www.sce.com:8443/save-money/rates-financing/sce-rate-advisory",
        "https://www.sce.com/unapproved",
    ):
        with pytest.raises(SourceSecurityError):
            validate_source_url(unsafe, document_link=True)


@pytest.mark.asyncio
async def test_conditional_fetch_redirect_and_size_controls() -> None:
    start = "https://www.sce.com/save-money/rates-financing/sce-rate-advisory"
    final = (
        "https://www.sce.com/save-money/rates-financing/residential-rate-plans/time-of-use-plans"
    )
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        if str(request.url) == start:
            return httpx.Response(302, headers={"Location": final})
        return httpx.Response(
            304,
            headers={"ETag": '"fixture-v1"', "Last-Modified": "Sun, 19 Jul 2026 00:00:00 GMT"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_source(
            start,
            etag='"old"',
            last_modified="Sat, 18 Jul 2026 00:00:00 GMT",
            client=client,
            verify_dns=False,
        )
    assert result.status_code == 304
    assert result.final_url == final
    assert seen_headers[0]["if-none-match"] == '"old"'
    assert "if-modified-since" in seen_headers[0]

    def oversized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 65, headers={"Content-Length": "65"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(oversized)) as client:
        with pytest.raises(SourceFetchError, match="maximum size"):
            await fetch_source(start, max_bytes=64, client=client, verify_dns=False)


@pytest.mark.asyncio
async def test_fetch_classifies_permanent_transient_timeout_and_content_errors() -> None:
    source = "https://www.sce.com/save-money/rates-financing/sce-rate-advisory"

    for status, expected in ((404, "http_error"), (500, "upstream_error")):
        calls = 0

        def status_handler(request: httpx.Request, _status: int = status) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(_status, headers={"Content-Type": "text/html"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(status_handler)) as client:
            with pytest.raises(SourceFetchError) as failure:
                await fetch_source(source, client=client, verify_dns=False, max_retries=2)
        assert failure.value.code == expected
        assert calls == (2 if status == 500 else 1)

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("fixture timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
        with pytest.raises(SourceFetchError) as timeout:
            await fetch_source(source, client=client, verify_dns=False, max_retries=2)
    assert timeout.value.code == "network_error"

    def unsupported(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"unsupported",
            headers={"Content-Type": "application/octet-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(unsupported)) as client:
        with pytest.raises(SourceFetchError) as content_error:
            await fetch_source(source, client=client, verify_dns=False)
    assert content_error.value.code == "unsupported_content_type"

    def redirect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://example.com/rates"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        with pytest.raises(SourceSecurityError):
            await fetch_source(source, client=client, verify_dns=False)


def test_html_pdf_and_tariff_index_adapters_are_deterministic() -> None:
    current = ADAPTERS["sce_public_tou_html_v1"].parse(
        (FIXTURES / "current-tou.html").read_bytes(),
        next(iter(APPROVED_SOURCE_URLS)),
        "text/html",
    )
    assert current.status == "succeeded"
    assert current.documents[0].plan_code == "TOU-D-4-9PM"
    assert validate_document(current.documents[0]).valid

    malformed = ADAPTERS["sce_public_tou_html_v1"].parse(
        (FIXTURES / "malformed.html").read_bytes(),
        next(iter(APPROVED_SOURCE_URLS)),
        "text/html",
    )
    assert malformed.status == "failed"
    assert malformed.errors[0]["code"] == "parse_error"

    index_url = (
        "https://www.sce.com/regulatory/regulatory-information/tariff-books/rates-pricing-choices"
    )
    index = ADAPTERS["sce_tariff_index_html_v1"].parse(
        (FIXTURES / "tariff-index.html").read_bytes(), index_url, "text/html"
    )
    assert index.discovered_links == [
        "https://www.sce.com/regulatory/tariff-books/current-residential.pdf"
    ]
    pdf = ADAPTERS["sce_tariff_pdf_v1"].parse(
        (FIXTURES / "current-tariff.pdf").read_bytes(),
        index.discovered_links[0],
        "application/pdf",
    )
    assert pdf.status == "manual_review"
    assert pdf.warnings[0]["code"] == "pdf_requires_review"

    unstructured = ADAPTERS["sce_rate_advisory_html_v1"].parse(
        (FIXTURES / "advisory-without-effective-date.html").read_bytes(),
        "https://www.sce.com/save-money/rates-financing/sce-rate-advisory",
        "text/html",
    )
    assert unstructured.status == "manual_review"
    assert unstructured.warnings[0]["code"] == "unstructured_html"

    missing_date = ADAPTERS["admin_uploaded_structured_v1"].parse(
        (FIXTURES / "no-effective-date.json").read_bytes(),
        "https://www.sce.com/save-money/rates-financing/sce-rate-advisory",
        "application/json",
    )
    assert missing_date.status == "failed"


def test_candidate_diff_marks_rate_and_effective_changes_material() -> None:
    before = example_document().model_dump(mode="json")
    after = json.loads(json.dumps(before))
    after["effective_from"] = "2026-09-01"
    after["seasons"][0]["schedules"][0]["periods"][0]["price_per_kwh"] = "0.31"
    differences = document_differences(before, after)
    assert len(differences) == 2
    assert all(item["material"] for item in differences)


def test_weekly_schedule_is_timezone_and_dst_aware() -> None:
    before = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    scheduled = latest_scheduled_time(before, "15 3 * * 0", "America/Los_Angeles")
    assert scheduled == datetime(2026, 7, 19, 10, 15, tzinfo=UTC)
    winter = latest_scheduled_time(
        datetime(2026, 12, 7, 12, 0, tzinfo=UTC),
        "15 3 * * 0",
        "America/Los_Angeles",
    )
    assert winter.hour == 11


@pytest.mark.asyncio
async def test_custom_plan_draft_activation_immutability_and_clone(session) -> None:
    document = example_document().model_copy(update={"effective_from": date.today()})
    plan, version = await create_custom_plan(session, document, "admin-user")
    await session.flush()
    stored = await version_document(session, version)
    assert stored == document
    report = await update_draft_version(session, version, document)
    assert report.valid

    status, _ = await activate_version(session, version, "admin-user")
    assert status == "active"
    assert version.immutable_after_use
    with pytest.raises(ProblemError) as immutable:
        await update_draft_version(session, version, document)
    assert immutable.value.code == "rate_version_immutable"

    cloned_plan, cloned_version = await clone_plan_version(session, version, "admin-user")
    await session.flush()
    assert cloned_plan.id != plan.id
    assert cloned_plan.plan_kind == "custom"
    assert cloned_plan.cloned_from_rate_version_id == version.id
    assert cloned_version.status == "draft"
    assert (await version_document(session, cloned_version)).plan_name.endswith(" Copy")
    assert len(list(await session.scalars(select(RatePlan)))) == 2
    assert len(list(await session.scalars(select(RateVersion)))) == 2


async def _official_candidate_context(
    session, tmp_path: Path, *, code: str
) -> tuple[RatePlanDocument, RateExtractionResult, RateSourceArtifact, RateVersion]:
    document = example_document().model_copy(
        update={
            "plan_code": code,
            "utility": "Southern California Edison",
            "provider_mode": "sce_delivery_generation",
            "effective_from": date.today(),
        }
    )
    plan, active = await create_custom_plan(session, document, "admin-user")
    plan.plan_kind = "official_sce"
    await activate_version(session, active, "admin-user")
    source = RateSource(
        name="SCE TOU fixture",
        url="https://www.sce.com/save-money/rates-financing/residential-rate-plans/time-of-use-plans",
        parser_id="sce_public_tou_html_v1",
        enabled=True,
        consecutive_failures=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    job = BackgroundJob(
        job_type="rate_source_sync",
        status="running",
        requested_at=datetime.now(UTC),
        correlation_id=f"fixture-{code}",
        progress={},
        result={},
    )
    session.add_all([source, job])
    await session.flush()
    check = RateSourceCheckRun(
        job_id=job.id,
        rate_source_id=source.id,
        checked_at=datetime.now(UTC),
        outcome="succeeded",
    )
    session.add(check)
    await session.flush()
    artifact_path = tmp_path / f"{code}.html"
    artifact_path.write_bytes(b"fixture evidence")
    artifact = RateSourceArtifact(
        source_check_id=check.id,
        sha256="a" * 64,
        content_type="text/html",
        byte_size=16,
        storage_path=str(artifact_path),
        captured_at=datetime.now(UTC),
    )
    session.add(artifact)
    await session.flush()
    extraction = RateExtractionResult(
        artifact_id=artifact.id,
        parser_id="sce_public_tou_html_v1",
        parser_version="1.0.0",
        status="succeeded",
        normalized_payload={},
        warnings=[],
        errors=[],
        extracted_at=datetime.now(UTC),
    )
    session.add(extraction)
    await session.flush()
    return document, extraction, artifact, active


@pytest.mark.asyncio
async def test_strict_auto_activation_allows_only_verified_changes(session, tmp_path: Path) -> None:
    document, extraction, artifact, active = await _official_candidate_context(
        session, tmp_path, code="SCE-AUTO-ALLOW"
    )
    changed = document.model_copy(deep=True)
    changed.seasons[0].schedules[0].periods[0].price_per_kwh = "0.27"
    candidate = await create_candidate_from_document(
        session,
        changed,
        extraction,
        artifact,
        approval_mode="auto_activate_verified",
        auto_activate_verified=True,
        maximum_percent_change=Decimal("25"),
        retroactive_days=0,
    )
    assert candidate is not None
    assert candidate.status == "automatically_activated"
    assert candidate.risk_level == "verified"
    assert not active.is_active
    new_version = await session.get(RateVersion, candidate.candidate_rate_version_id)
    assert new_version is not None and new_version.is_active
    assert new_version.automatically_activated
    audit = await session.scalar(
        select(AuditEvent).where(AuditEvent.action == "rate_candidate.automatically_activated")
    )
    assert audit is not None and audit.actor_type == "system"


@pytest.mark.asyncio
async def test_strict_auto_activation_blocks_warning_and_large_change(
    session, tmp_path: Path
) -> None:
    document, extraction, artifact, active = await _official_candidate_context(
        session, tmp_path, code="SCE-AUTO-BLOCK"
    )
    extraction.warnings = [{"code": "fixture_warning", "message": "review"}]
    changed = document.model_copy(deep=True)
    changed.seasons[0].schedules[0].periods[0].price_per_kwh = "0.50"
    candidate = await create_candidate_from_document(
        session,
        changed,
        extraction,
        artifact,
        approval_mode="auto_activate_verified",
        auto_activate_verified=True,
        maximum_percent_change=Decimal("25"),
        retroactive_days=0,
    )
    assert candidate is not None and candidate.status == "pending_review"
    assert active.is_active
    reasons = candidate.summary["automatic_activation_blocked"]
    assert "parser_warning_present" in reasons
    assert "change_threshold_exceeded" in reasons


@pytest.mark.asyncio
async def test_conflicting_official_candidates_are_blocking(session, tmp_path: Path) -> None:
    document, extraction, artifact, _active = await _official_candidate_context(
        session, tmp_path, code="SCE-CONFLICT"
    )
    first_document = document.model_copy(deep=True)
    first_document.seasons[0].schedules[0].periods[0].price_per_kwh = "0.26"
    first = await create_candidate_from_document(session, first_document, extraction, artifact)
    assert first is not None

    second_path = tmp_path / "conflict-second.html"
    second_path.write_bytes(b"conflicting official evidence")
    second_artifact = RateSourceArtifact(
        source_check_id=artifact.source_check_id,
        sha256="b" * 64,
        content_type="text/html",
        byte_size=29,
        storage_path=str(second_path),
        captured_at=datetime.now(UTC),
    )
    session.add(second_artifact)
    await session.flush()
    second_extraction = RateExtractionResult(
        artifact_id=second_artifact.id,
        parser_id=extraction.parser_id,
        parser_version=extraction.parser_version,
        status="succeeded",
        normalized_payload={},
        warnings=[],
        errors=[],
        extracted_at=datetime.now(UTC),
    )
    session.add(second_extraction)
    await session.flush()
    second_document = document.model_copy(deep=True)
    second_document.seasons[0].schedules[0].periods[0].price_per_kwh = "0.27"
    second = await create_candidate_from_document(
        session, second_document, second_extraction, second_artifact
    )
    assert second is not None
    assert first.risk_level == second.risk_level == "blocking"
    assert first.summary["source_conflict"] is True
    assert second.summary["conflicting_candidate_ids"] == [first.id]
    assert len(list(await session.scalars(select(RateChangeCandidate)))) == 2


@pytest.mark.asyncio
async def test_rate_management_api_lifecycle_and_async_check(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "rates@example.com",
            "display_name": "Rate administrator",
            "password": "Long-Production-Password-42!",
        },
    )
    assert bootstrap.status_code == 201, bootstrap.text
    document = example_document().model_copy(update={"effective_from": date.today()})
    created = await client.post(
        "/api/v1/rates/plans",
        headers=csrf(client),
        json=document.model_dump(mode="json"),
    )
    assert created.status_code == 201, created.text
    plan = created.json()["plan"]
    version_id = plan["versions"][0]["id"]

    validation = await client.post(
        f"/api/v1/rates/versions/{version_id}/validate", headers=csrf(client)
    )
    assert validation.status_code == 200
    assert validation.json()["valid"] is True
    activated = await client.post(
        f"/api/v1/rates/versions/{version_id}/activate", headers=csrf(client)
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"

    immutable = await client.patch(
        f"/api/v1/rates/versions/{version_id}",
        headers=csrf(client),
        json=document.model_dump(mode="json"),
    )
    assert immutable.status_code == 409
    exported = await client.get(f"/api/v1/rates/versions/{version_id}/export")
    assert exported.status_code == 200
    assert exported.json()["document"]["schema_version"] == "power-monitor-rate-plan/1.0"
    assert exported.json()["integrity_sha256"]

    cloned = await client.post(f"/api/v1/rates/plans/{plan['id']}/clone", headers=csrf(client))
    assert cloned.status_code == 201, cloned.text
    assert cloned.json()["editor_url"].startswith("/rates/")

    sources = await client.get("/api/v1/admin/rate-sources")
    assert sources.status_code == 200
    assert len(sources.json()["sources"]) == 4
    source_id = sources.json()["sources"][0]["id"]
    unrestricted = await client.patch(
        f"/api/v1/admin/rate-sources/{source_id}",
        headers=csrf(client),
        json={"url": "https://example.com/rates"},
    )
    assert unrestricted.status_code == 422
    queued = await client.post("/api/v1/admin/rate-sources/check-now", headers=csrf(client))
    assert queued.status_code == 202
    job = await client.get(f"/api/v1/jobs/{queued.json()['job_id']}")
    assert job.status_code == 200
    assert job.json()["status"] == "queued"
