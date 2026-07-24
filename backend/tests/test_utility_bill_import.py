from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.bills.extraction import (
    BillPdfError,
    extract_bill,
    inspect_pdf,
    normalize_extracted_text,
    redact_sensitive_text,
)
from app.db.models import (
    AuditEvent,
    RateVersion,
    UtilityBillCycleDraft,
    UtilityBillExtractionRevision,
    UtilityBillImport,
)
from app.formatting import (
    format_billing_period,
    format_currency,
    format_decimal_detail,
    format_energy,
    format_energy_rate,
    format_tier_range,
)

FIXTURES = Path(__file__).parent / "fixtures" / "bills"
REQUIRED_FIELDS = {
    "utility",
    "plan_code",
    "pricing_model",
    "starts_at",
    "ends_at",
    "total_usage_kwh",
    "threshold_interpretation",
}


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


async def bootstrap(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "bill-admin@example.com",
            "display_name": "Bill Administrator",
            "password": "Long-Production-Password-42!",
        },
    )
    assert response.status_code == 201, response.text


def account_payload(version_id: str) -> dict[str, Any]:
    return {
        "name": "Imported bill account",
        "nickname": "Whole home",
        "account_number_suffix": "1234",
        "utility_provider": "sce",
        "generation_provider": "sce",
        "provider_mode": "sce_bundled",
        "billing_cycle_start_day": 22,
        "currency": "USD",
        "service_class": "Residential",
        "rate_assignment": {
            "rate_version_id": version_id,
            "effective_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "assignment_reason": "Bill import fixture setup",
        },
        "cost_scope": "energy_only",
        "adjustments": [],
        "confirmation": True,
    }


def tesseract_tsv(lines: list[str], confidence: int = 94) -> str:
    output = [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    ]
    top = 20
    for line_number, line in enumerate(lines, start=1):
        left = 20
        for word_number, word in enumerate(line.split(), start=1):
            width = max(12, len(word) * 8)
            output.append(
                f"5\t1\t1\t1\t{line_number}\t{word_number}\t{left}\t{top}\t"
                f"{width}\t18\t{confidence}\t{word}"
            )
            left += width + 8
        top += 24
    return "\n".join(output)


def fake_ocr_runner(lines: list[str], confidence: int = 94) -> Any:
    def runner(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        stdout = tesseract_tsv(lines, confidence) if "tesseract" in command[0].lower() else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    return runner


def test_text_pdf_extracts_separate_exact_outputs_with_evidence(test_settings: Any) -> None:
    path = FIXTURES / "text-tiered-bill.pdf"
    result = extract_bill(path.read_bytes(), test_settings, pdf_path=path)
    assert result.extraction_method == "text"
    assert result.page_count == 2
    assert result.account_data == {
        "utility": "Southern California Edison",
        "plan_code": "DOMESTIC",
        "account_suffix": "1234",
        "meter_identifiers": ["MTR-001", "MTR-002"],
        "provider_mode": "bundled",
    }
    assert result.rate_data["pricing_model"] == "tiered"
    assert result.rate_data["tiers"] == [
        {
            "name": "Tier 1",
            "lower_bound_kwh": "0",
            "upper_bound_kwh": "579",
            "usage_kwh": "579",
            "price_per_kwh": "0.30",
            "energy_charge": "173.7000000",
        },
        {
            "name": "Tier 2",
            "lower_bound_kwh": "580",
            "upper_bound_kwh": None,
            "usage_kwh": "372",
            "price_per_kwh": "0.40",
            "energy_charge": "148.8000000",
        },
    ]
    assert result.cycle_data["total_usage_kwh"] == "951"
    assert result.cycle_data["energy_subtotal"] == "322.500000000"
    assert result.cycle_data["full_bill_total"] == "355.00"
    assert result.cycle_data["delivery_charges"] == "170.00"
    assert result.cycle_data["generation_charges"] == "152.500000000"
    tier_field = next(item for item in result.fields if item.field_key == "tiers.0.price_per_kwh")
    assert tier_field.page_number == 2
    assert tier_field.source_excerpt
    assert tier_field.text_region
    assert tier_field.extraction_method == "text"
    assert tier_field.confidence == "high"


def test_rotated_and_scanned_pdf_paths_use_text_then_bounded_ocr(test_settings: Any) -> None:
    rotated = FIXTURES / "rotated-tiered-bill.pdf"
    rotated_result = extract_bill(rotated.read_bytes(), test_settings, pdf_path=rotated)
    assert rotated_result.account_data["utility"] == "Southern California Edison"
    scanned = FIXTURES / "scanned-tiered-bill.pdf"
    runner = fake_ocr_runner(
        [
            "Southern California Edison",
            "Rate Plan: DOMESTIC",
            "Billing Period: Jul 22, 2026 - Aug 20, 2026",
            "Account number: 0000111122221234",
            "Meter ID: MTR-001",
            "Total Usage: 951 kWh",
            "Energy Charges: $322.500000000",
            "Bill Total: $355.00",
            "Tier 1 | 0-579 kWh | 579 kWh | $0.30/kWh | $173.7000000",
            "Tier 2 | 580+ kWh | 372 kWh | $0.40/kWh | $148.8000000",
        ]
    )
    scanned_result = extract_bill(
        scanned.read_bytes(),
        test_settings,
        pdf_path=scanned,
        runner=runner,
    )
    assert scanned_result.extraction_method == "ocr"
    assert scanned_result.ocr_version == "tesseract-local"
    assert scanned_result.rate_data["pricing_model"] == "tiered"
    assert scanned_result.cycle_data["total_usage_kwh"] == "951"
    assert all(
        item.extraction_method == "ocr" for item in scanned_result.fields if item.page_number
    )


def test_low_confidence_ocr_is_never_confirmed(test_settings: Any) -> None:
    scanned = FIXTURES / "scanned-tiered-bill.pdf"
    result = extract_bill(
        scanned.read_bytes(),
        test_settings,
        pdf_path=scanned,
        runner=fake_ocr_runner(
            [
                "Southern California Edison",
                "Rate Plan: DOMESTIC",
                "Billing Period: Jul 22, 2026 - Aug 20, 2026",
                "Total Usage: 951 kWh",
            ],
            confidence=35,
        ),
    )
    assert any(item.confidence == "low" for item in result.fields)
    assert any(item["code"] == "low_ocr_confidence" for item in result.blocking_warnings)


def test_pdf_security_limits_and_malformed_inputs(test_settings: Any) -> None:
    encrypted = (FIXTURES / "encrypted-tiered-bill.pdf").read_bytes()
    with pytest.raises(BillPdfError, match="encrypted"):
        inspect_pdf(encrypted, test_settings)
    with pytest.raises(BillPdfError) as malformed:
        inspect_pdf(b"%PDF-not-a-valid-document", test_settings)
    assert malformed.value.code == "bill_pdf_malformed"
    with pytest.raises(BillPdfError) as active:
        inspect_pdf(b"%PDF-1.7\n/OpenAction /JavaScript", test_settings)
    assert active.value.code == "bill_pdf_active_content"
    limited = test_settings.model_copy(update={"utility_bill_max_bytes": 10})
    with pytest.raises(BillPdfError) as oversized:
        inspect_pdf((FIXTURES / "text-tiered-bill.pdf").read_bytes(), limited)
    assert oversized.value.code == "bill_pdf_too_large"


def test_redaction_utf8_repair_and_shared_exact_formatting() -> None:
    repaired, history = normalize_extracted_text("Tier 1 0\u00e2\u20ac\u201c579 kWh")
    assert repaired == "Tier 1 0\u2013579 kWh"
    assert history[0]["operation"] == "targeted_utf8_repair"
    redacted = redact_sensitive_text(
        "Customer Name: Example Customer\n"
        "Service Address: 100 Example Street\n"
        "Payment method: 4444333322221111\n"
        "Account number: 0000111122221234"
    )
    assert "Example Customer" not in redacted
    assert "100 Example Street" not in redacted
    assert "4444333322221111" not in redacted
    assert "ending 1234" in redacted
    assert format_currency("322.500000000") == "$322.50"
    assert format_currency("173.7000000") == "$173.70"
    assert format_energy_rate("0.3391167192429022082018927445", derived=True) == "$0.3391/kWh"
    assert format_energy_rate("0.34000") == "$0.34/kWh"
    assert format_energy("12.3456") == "12.346 kWh"
    assert format_tier_range("0", "579") == "0\u2013579 kWh"
    assert format_tier_range("579", None) == "580 kWh and above"
    assert format_tier_range("579.5", None) == "579.5 kWh and above"
    assert format_decimal_detail("322.500000000") == "322.500000000"
    assert (
        format_billing_period(
            datetime(2026, 7, 22, tzinfo=UTC),
            datetime(2026, 8, 20, tzinfo=UTC),
        )
        == "Jul 22, 2026 \u2013 Aug 20, 2026"
    )


@pytest.mark.asyncio
async def test_complete_bill_api_workflow_is_reviewed_separate_and_private(
    api_client: httpx.AsyncClient,
    session: Any,
) -> None:
    await bootstrap(api_client)
    site = (await api_client.get("/api/v1/sites")).json()[0]
    plans = (await api_client.get("/api/v1/rates/plans")).json()
    active_version = next(
        version
        for plan in plans
        for version in plan["versions"]
        if version["status"] in {"active", "approved"}
    )
    account_response = await api_client.post(
        f"/api/v1/admin/sites/{site['id']}/utility-accounts",
        headers=csrf(api_client),
        json=account_payload(active_version["id"]),
    )
    assert account_response.status_code == 201, account_response.text
    account = account_response.json()
    context = await api_client.get(
        "/api/v1/admin/utility-bill-import-context",
        params={"account_id": account["id"]},
    )
    assert context.status_code == 200, context.text
    context_payload = context.json()
    assert context_payload["schema_version"] == "utility-account-rate-context/1.0"
    assert context_payload["account"]["id"] == account["id"]
    assert context_payload["current_plan"] is not None
    assert context_payload["current_assignment"] is not None
    assert context_payload["current_rate_version"] is not None
    assert "current_period" in context_payload
    content = (FIXTURES / "text-tiered-bill.pdf").read_bytes()
    upload = await api_client.post(
        f"/api/v1/admin/utility-accounts/{account['id']}/bill-imports",
        params={"retention_mode": "retain", "source_role": "supporting"},
        headers=csrf(api_client),
        files={"upload": ("ignored-client-name.pdf", content, "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    imported = upload.json()
    assert imported["status"] == "review_required"
    assert imported["original_available"] is True
    assert imported["content_sha256"]
    assert imported["rate_plan_id"] and imported["rate_version_id"]
    assert imported["cycle_draft"]["id"]
    assert imported["cycle_draft"]["status"] == "draft"
    assert imported["normalized"]["rate_plan"]["tiers"][0]["energy_charge"] == "173.7000000"

    bypass = await api_client.post(
        f"/api/v1/rates/versions/{imported['rate_version_id']}/activate",
        headers=csrf(api_client),
    )
    assert bypass.status_code == 409
    assert bypass.json()["code"] == "utility_bill_review_required"

    duplicate = await api_client.post(
        f"/api/v1/admin/utility-accounts/{account['id']}/bill-imports",
        headers=csrf(api_client),
        files={"upload": ("different-name.pdf", content, "application/pdf")},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == imported["id"]
    assert duplicate.json()["duplicate"] is True
    revision_count = await session.scalar(select(func.count(UtilityBillExtractionRevision.id)))
    assert revision_count == 1

    reviews = [
        {"field_id": field["id"], "action": "confirm"}
        for field in imported["fields"]
        if field["field_key"] in REQUIRED_FIELDS
        and field["field_key"] != "threshold_interpretation"
    ]
    threshold_field = next(
        field for field in imported["fields"] if field["field_key"] == "threshold_interpretation"
    )
    reviews.append(
        {
            "field_id": threshold_field["id"],
            "action": "correct",
            "value": "fixed_cycle_threshold",
        }
    )
    conflicts = [
        {
            "conflict_id": conflict["id"],
            "decision": "accepted_bill",
            "note": "Test administrator reviewed both sources",
        }
        for conflict in imported["conflicts"]
    ]
    reviewed = await api_client.put(
        f"/api/v1/admin/utility-bill-imports/{imported['id']}/review",
        headers=csrf(api_client),
        json={
            "revision": imported["revision"],
            "field_reviews": reviews,
            "conflict_resolutions": conflicts,
            "threshold_interpretation": "fixed_cycle_threshold",
            "source_role": "authoritative_account_specific",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "ready_to_publish"

    validation = await api_client.post(
        f"/api/v1/admin/utility-bill-imports/{imported['id']}/validate",
        headers=csrf(api_client),
    )
    assert validation.status_code == 200
    assert validation.json()["validation"]["valid"] is True
    comparison = await api_client.get(
        f"/api/v1/admin/utility-bill-imports/{imported['id']}/comparison"
    )
    assert comparison.status_code == 200
    result = comparison.json()
    assert result["available"] is True
    assert Decimal(result["exact"]["calculated_energy_subtotal"]) == Decimal("322.50")
    assert Decimal(result["exact"]["utility_energy_subtotal"]) == Decimal("322.50")
    assert Decimal(result["exact"]["utility_full_bill_total"]) == Decimal("355.00")
    assert result["display"]["calculated_energy_subtotal"] == "$322.50"
    assert result["display"]["blended_energy_rate"] == "$0.3391/kWh"
    assert result["display"]["utility_full_bill_total"] == "$355.00"
    assert result["tiers"][0]["display_range"] == "0\u2013579 kWh"
    assert result["tiers"][1]["display_range"] == "580 kWh and above"

    published = await api_client.post(
        f"/api/v1/admin/utility-bill-imports/{imported['id']}/publish-and-assign",
        headers=csrf(api_client),
        json={},
    )
    assert published.status_code == 200, published.text
    version = await session.get(RateVersion, imported["rate_version_id"])
    assert version is not None and version.status in {"active", "approved"}
    cycle = await api_client.post(
        f"/api/v1/admin/utility-bill-imports/{imported['id']}/import-billing-cycle",
        headers=csrf(api_client),
    )
    assert cycle.status_code == 200, cycle.text
    cycle_draft = await session.scalar(
        select(UtilityBillCycleDraft).where(UtilityBillCycleDraft.bill_import_id == imported["id"])
    )
    assert cycle_draft is not None and cycle_draft.status == "imported"

    removed = await api_client.delete(
        f"/api/v1/admin/utility-bill-imports/{imported['id']}/original",
        headers=csrf(api_client),
    )
    assert removed.status_code == 200
    original = await api_client.get(f"/api/v1/admin/utility-bill-imports/{imported['id']}/original")
    assert original.status_code == 410
    evidence = await api_client.get(
        f"/api/v1/admin/utility-bill-imports/{imported['id']}/sanitized-evidence"
    )
    assert evidence.status_code == 200
    payload = json.loads(evidence.content)
    assert payload["normalized"]["billing_cycle"]["energy_subtotal"] == "322.500000000"
    assert (
        await session.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.action.like("utility_bill.%"))
        )
        >= 4
    )
    bill_row = await session.get(UtilityBillImport, imported["id"])
    assert bill_row is not None and bill_row.original_deleted_at is not None


@pytest.mark.asyncio
async def test_unassigned_bill_import_extracts_without_plan_or_account(
    api_client: httpx.AsyncClient,
    session: Any,
) -> None:
    await bootstrap(api_client)
    context = await api_client.get("/api/v1/admin/utility-bill-import-context")
    assert context.status_code == 200, context.text
    assert context.json()["schema_version"] == "utility-account-rate-context/1.0"
    assert context.json()["generated_client_schema_version"] == context.json()["schema_version"]
    assert context.json()["account_id"] is None
    assert context.json()["site_id"] is None
    assert context.json()["account"] is None
    assert context.json()["current_plan"] is None
    assert context.json()["current_assignment"] is None
    assert context.json()["current_rate_version"] is None
    assert context.json()["current_period"] is None
    assert context.json()["readiness"] == {
        "account_configured": False,
        "rate_assigned": False,
        "rate_effective": False,
    }

    content = (FIXTURES / "text-tiered-bill.pdf").read_bytes()
    upload = await api_client.post(
        "/api/v1/admin/utility-bill-imports",
        params={
            "timezone": "America/Los_Angeles",
            "currency": "USD",
            "retention_mode": "retain",
            "source_role": "supporting",
        },
        headers={
            **csrf(api_client),
            "X-Idempotency-Key": "unassigned-bill-import-test",
        },
        files={"upload": ("ignored-client-name.pdf", content, "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    imported = upload.json()
    assert imported["utility_account_id"] is None
    assert imported["utility_account_name"] == "Not assigned yet"
    assert imported["rate_plan_id"]
    assert imported["rate_version_id"]
    assert imported["cycle_draft"]["utility_account_id"] is None

    duplicate = await api_client.post(
        "/api/v1/admin/utility-bill-imports",
        headers=csrf(api_client),
        files={"upload": ("renamed.pdf", content, "application/pdf")},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == imported["id"]
    assert duplicate.json()["duplicate"] is True

    publish = await api_client.post(
        f"/api/v1/admin/utility-bill-imports/{imported['id']}/publish-and-assign",
        headers=csrf(api_client),
        json={},
    )
    assert publish.status_code == 409
    assert publish.json()["code"] == "bill_account_context_required"
    cycle = await api_client.post(
        f"/api/v1/admin/utility-bill-imports/{imported['id']}/import-billing-cycle",
        headers=csrf(api_client),
    )
    assert cycle.status_code == 409
    assert cycle.json()["code"] == "bill_account_context_required"
    row = await session.get(UtilityBillImport, imported["id"])
    assert row is not None and row.utility_account_id is None


@pytest.mark.asyncio
async def test_non_admin_cannot_access_private_bill_artifacts(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap(api_client)
    create_user = await api_client.post(
        "/api/v1/users",
        headers=csrf(api_client),
        json={
            "email": "viewer-bill@example.com",
            "display_name": "Bill Viewer",
            "password": "Long-Production-Password-43!",
            "roles": ["viewer"],
        },
    )
    assert create_user.status_code == 201
    await api_client.post("/api/v1/auth/logout", headers=csrf(api_client))
    login = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "viewer-bill@example.com", "password": "Long-Production-Password-43!"},
    )
    assert login.status_code == 200
    private_list = await api_client.get("/api/v1/admin/utility-bill-imports")
    assert private_list.status_code == 403
