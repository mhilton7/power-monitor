from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bills.extraction import (
    PARSER_ID,
    PARSER_VERSION,
    BillExtraction,
    ExtractedField,
    extract_bill,
    redact_sensitive_text,
)
from app.config import Settings
from app.db.models import (
    AccountUsageAuthority,
    BackgroundJob,
    BillingCycle,
    ManualAccountUsage,
    RateAssignment,
    RateExtractionResult,
    RatePlan,
    RateSource,
    RateSourceArtifact,
    RateSourceCheckRun,
    RateVersion,
    RateVersionSource,
    UtilityAccount,
    UtilityBillCycleDraft,
    UtilityBillExtractedField,
    UtilityBillExtractionRevision,
    UtilityBillFieldConflict,
    UtilityBillImport,
    UtilityUsageImport,
)
from app.formatting import (
    format_currency,
    format_energy,
    format_energy_rate,
    format_tier_range,
)
from app.problem import ProblemError
from app.rates.documents import RatePlanDocument, engine_plan, validate_document
from app.rates.engine import RateEngine
from app.rates.service import activate_version, create_custom_plan, update_draft_version

REQUIRED_REVIEW_FIELDS = {
    "utility",
    "plan_code",
    "pricing_model",
    "starts_at",
    "ends_at",
    "total_usage_kwh",
    "threshold_interpretation",
}


def _safe_path(root: Path, digest: str, filename: str) -> Path:
    resolved_root = root.resolve()
    target = (resolved_root / digest[:2] / digest / filename).resolve()
    if resolved_root not in target.parents:
        raise ValueError("utility-bill artifact path escaped configured root")
    return target


def _write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


async def _bill_source(
    session: AsyncSession,
    account: UtilityAccount,
    user_id: str,
    now: datetime,
) -> RateSource:
    url = f"urn:power-monitor:utility-bill:{account.id}"
    source = await session.scalar(select(RateSource).where(RateSource.url == url))
    if source is None:
        source = RateSource(
            name=f"Private utility bills for account {account.id[:8]}",
            url=url,
            parser_id=PARSER_ID,
            enabled=False,
            consecutive_failures=0,
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(source)
        await session.flush()
    return source


def _field_payload(field: ExtractedField) -> dict[str, Any]:
    return {
        "output_kind": field.output_kind,
        "field_key": field.field_key,
        "raw_value": field.raw_value,
        "normalized_value": field.normalized_value,
        "page_number": field.page_number,
        "source_excerpt": field.source_excerpt,
        "text_region": field.text_region,
        "extraction_method": field.extraction_method,
        "parser_version": PARSER_VERSION,
        "confidence": field.confidence,
        "warnings": field.warnings,
        "normalization_history": field.normalization_history,
    }


def _provider_mode(account: UtilityAccount) -> str:
    return {
        "sce_bundled": "sce_delivery_generation",
        "sce_delivery_generation": "sce_delivery_generation",
        "sce_delivery_cca": "sce_delivery_cca",
        "sce_delivery_direct_access": "sce_delivery_direct_access",
        "custom_combined": "custom_combined",
    }.get(account.provider_mode, "custom_combined")


def _plan_document(
    extraction: BillExtraction,
    account: UtilityAccount,
    content_sha256: str,
) -> RatePlanDocument:
    rate = extraction.rate_data
    extracted_tiers = list(rate.get("tiers") or [])
    pricing_model = str(rate.get("pricing_model") or "flat")
    can_build_tiers = len(extracted_tiers) >= 2
    document_model = "tiered" if can_build_tiers else "flat"
    tiers: list[dict[str, Any]] = []
    previous_upper = "0"
    if can_build_tiers:
        for index, item in enumerate(extracted_tiers):
            final = index == len(extracted_tiers) - 1
            lower = "0" if index == 0 else previous_upper
            upper = None if final else str(item.get("upper_bound_kwh") or "")
            if not final and not upper:
                upper = str(item.get("lower_bound_kwh") or "0")
            previous_upper = upper or previous_upper
            tiers.append(
                {
                    "tier_id": f"bill-tier-{index + 1}",
                    "name": str(item.get("name") or f"Tier {index + 1}"),
                    "order": index,
                    "lower_bound_inclusive_kwh": lower,
                    "upper_bound_exclusive_kwh": upper,
                    "lower_bound_multiplier": None,
                    "upper_bound_multiplier": None,
                    "price_per_kwh": str(item.get("price_per_kwh") or "0"),
                    "tou_prices": {},
                    "season": None,
                    "source_citation": f"Private utility bill SHA-256 {content_sha256}",
                }
            )
    flat_rate = None
    if document_model == "flat":
        candidate_rates = [
            str(item.get("price_per_kwh"))
            for item in extracted_tiers
            if item.get("price_per_kwh") is not None
        ]
        flat_rate = candidate_rates[0] if candidate_rates else "0"
    raw_code = str(rate.get("plan_code") or f"BILL-{content_sha256[:8]}").upper()
    code = re.sub(r"[^A-Z0-9._-]", "-", raw_code).strip("-")[:64]
    if len(code) < 2:
        code = f"BILL-{content_sha256[:8].upper()}"
    effective_from = date.fromisoformat(str(rate.get("effective_from") or date.today().isoformat()))
    notes = (
        "Imported from one private utility bill. The extracted model was "
        f"{pricing_model}; unsupported or incomplete tariff rules remain review blockers. "
        "Bill-specific adjustments are not recurring rate rules."
    )
    return RatePlanDocument.model_validate(
        {
            "schema_version": "power-monitor-rate-plan/1.0",
            "plan_name": str(rate.get("plan_name") or "Imported utility bill draft"),
            "plan_code": code,
            "utility": str(rate.get("utility") or "custom"),
            "description": "Administrator-reviewed custom plan drafted from a utility bill.",
            "currency": str(rate.get("currency") or account.currency),
            "timezone": account.timezone,
            "pricing_model": document_model,
            "flat_rate_per_kwh": flat_rate,
            "billing_cycle": {
                "expected_start_day": effective_from.day,
                "threshold": {
                    "basis": "fixed_cycle_kwh",
                    "daily_baseline_kwh": None,
                    "baseline_region": None,
                    "baseline_category": None,
                    "rounding_policy": "none",
                    "seasonal_baselines": [],
                    "source_citation": f"Private utility bill SHA-256 {content_sha256}",
                },
            },
            "tiers": tiers,
            "hybrid_pricing": None,
            "ownership_scope": "utility_account",
            "owner_id": account.id,
            "effective_from": effective_from,
            "effective_through": None,
            "cost_scope_default": account.cost_scope_default,
            "source_label": "Uploaded utility bill (supporting source)",
            "source_note": notes,
            "provider_mode": _provider_mode(account),
            "seasons": [],
            "adjustments": [],
            "custom_notes": notes,
            "cloned_from_rate_version_id": None,
        }
    )


def _as_decimal(value: Any | None) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _as_utc_date(value: Any | None, timezone: str) -> datetime | None:
    if value is None:
        return None
    local_date = date.fromisoformat(str(value))
    return datetime.combine(local_date, time.min, tzinfo=ZoneInfo(timezone)).astimezone(UTC)


def _aware_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _configured_conflicts(
    session: AsyncSession,
    bill: UtilityBillImport,
    account: UtilityAccount,
    extraction: BillExtraction,
) -> list[UtilityBillFieldConflict]:
    conflicts: list[UtilityBillFieldConflict] = []
    version = (
        await session.get(RateVersion, account.active_rate_version_id)
        if account.active_rate_version_id
        else None
    )
    plan = await session.get(RatePlan, version.rate_plan_id) if version else None
    comparisons = (
        (
            "plan_code",
            extraction.rate_data.get("plan_code"),
            plan.code if plan else None,
            "current_rate_version",
        ),
        (
            "pricing_model",
            extraction.rate_data.get("pricing_model"),
            version.pricing_model if version else None,
            "current_rate_version",
        ),
        (
            "account_suffix",
            extraction.account_data.get("account_suffix"),
            account.account_number_suffix,
            "utility_account",
        ),
    )
    for field_key, extracted, configured, source in comparisons:
        if extracted is None or configured is None or str(extracted) == str(configured):
            continue
        conflict = UtilityBillFieldConflict(
            bill_import_id=bill.id,
            field_key=field_key,
            extracted_value=extracted,
            configured_value=configured,
            comparison_source=source,
            status="unresolved",
            blocking=True,
        )
        session.add(conflict)
        conflicts.append(conflict)
    return conflicts


async def create_bill_import(
    session: AsyncSession,
    *,
    account: UtilityAccount,
    content: bytes,
    user_id: str,
    settings: Settings,
    correlation_id: str,
    retention_mode: str,
    retain_until: datetime | None,
    source_role: str,
    runner: Any | None = None,
) -> tuple[UtilityBillImport, bool]:
    digest = hashlib.sha256(content).hexdigest()
    existing = await session.scalar(
        select(UtilityBillImport).where(
            UtilityBillImport.utility_account_id == account.id,
            UtilityBillImport.content_sha256 == digest,
        )
    )
    if existing is not None:
        return existing, True
    now = datetime.now(UTC)
    source = await _bill_source(session, account, user_id, now)
    job = BackgroundJob(
        job_type="utility_bill_import",
        status="running",
        requested_by=user_id,
        requested_at=now,
        started_at=now,
        correlation_id=correlation_id,
        progress={"phase": "document_inspection", "percent": 5},
        result={},
    )
    session.add(job)
    await session.flush()
    check = RateSourceCheckRun(
        job_id=job.id,
        rate_source_id=source.id,
        checked_at=now,
        outcome="processing",
        final_url=source.url,
        response_bytes=len(content),
    )
    session.add(check)
    await session.flush()
    original_path = _safe_path(
        settings.utility_bill_artifact_path,
        digest,
        "original.pdf",
    )
    _write_private(original_path, content)
    artifact = RateSourceArtifact(
        source_check_id=check.id,
        sha256=digest,
        content_type="application/pdf",
        byte_size=len(content),
        storage_path=str(original_path),
        original_filename=f"utility-bill-{digest[:12]}.pdf",
        captured_at=now,
    )
    session.add(artifact)
    await session.flush()
    try:
        extraction = extract_bill(
            content,
            settings,
            pdf_path=original_path,
            **({"runner": runner} if runner is not None else {}),
        )
    except Exception:
        if original_path.is_file():
            original_path.unlink()
        raise
    sanitized_text = redact_sensitive_text(extraction.normalized_text)
    sanitized_path = _safe_path(
        settings.utility_bill_artifact_path,
        digest,
        "sanitized-evidence.txt",
    )
    _write_private(sanitized_path, sanitized_text.encode("utf-8"))
    bill = UtilityBillImport(
        job_id=job.id,
        utility_account_id=account.id,
        artifact_id=artifact.id,
        content_sha256=digest,
        status="review_required",
        source_role=source_role,
        extraction_method=extraction.extraction_method,
        parser_version=PARSER_VERSION,
        page_count=extraction.page_count,
        retention_mode=retention_mode,
        retain_until=retain_until,
        sanitized_evidence_path=str(sanitized_path),
        revision=1,
        blocking_warnings=extraction.blocking_warnings,
        extraction_warnings=extraction.warnings,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(bill)
    await session.flush()
    normalized_payload = {
        "account": extraction.account_data,
        "rate_plan": extraction.rate_data,
        "billing_cycle": extraction.cycle_data,
    }
    extraction_record = RateExtractionResult(
        artifact_id=artifact.id,
        parser_id=PARSER_ID,
        parser_version=PARSER_VERSION,
        status="succeeded",
        normalized_payload=normalized_payload,
        warnings=extraction.warnings,
        errors=[],
        extracted_at=now,
    )
    session.add(extraction_record)
    revision = UtilityBillExtractionRevision(
        bill_import_id=bill.id,
        revision=1,
        status="review_required",
        parser_version=PARSER_VERSION,
        ocr_version=extraction.ocr_version,
        normalized_account_data=extraction.account_data,
        normalized_rate_data=extraction.rate_data,
        normalized_cycle_data=extraction.cycle_data,
        raw_text_sha256=hashlib.sha256(extraction.raw_text.encode("utf-8")).hexdigest(),
        normalized_text_sha256=hashlib.sha256(
            extraction.normalized_text.encode("utf-8")
        ).hexdigest(),
        sanitized_text_path=str(sanitized_path),
        extraction_metadata={
            "page_count": extraction.page_count,
            "method": extraction.extraction_method,
            "normalization_history": extraction.normalization_history,
            "parser_id": PARSER_ID,
        },
        created_by=user_id,
        created_at=now,
    )
    session.add(revision)
    await session.flush()
    for item in extraction.fields:
        session.add(
            UtilityBillExtractedField(
                extraction_revision_id=revision.id,
                **_field_payload(item),
            )
        )
    document = _plan_document(extraction, account, digest)
    plan, version = await create_custom_plan(
        session,
        document,
        user_id,
        duplicate_suffix=True,
    )
    version.source_kind = "utility_bill_candidate"
    version.source_url = f"urn:power-monitor:utility-bill:{bill.id}"
    plan.name = f"{plan.name} (bill draft)"
    bill.rate_plan_id = plan.id
    bill.rate_version_id = version.id
    session.add(
        RateVersionSource(
            rate_version_id=version.id,
            artifact_id=artifact.id,
            extraction_result_id=extraction_record.id,
            relationship="supporting",
        )
    )
    cycle_data = extraction.cycle_data
    cycle_draft = UtilityBillCycleDraft(
        bill_import_id=bill.id,
        extraction_revision_id=revision.id,
        utility_account_id=account.id,
        status="draft",
        starts_at=_as_utc_date(cycle_data.get("starts_at"), account.timezone),
        ends_at=_as_utc_date(cycle_data.get("ends_at"), account.timezone),
        cycle_days=cycle_data.get("cycle_days"),
        meter_read_date=(
            date.fromisoformat(str(cycle_data["meter_read_date"]))
            if cycle_data.get("meter_read_date")
            else None
        ),
        total_usage_kwh=_as_decimal(cycle_data.get("total_usage_kwh")),
        usage_by_tier=list(cycle_data.get("usage_by_tier") or []),
        usage_by_tou=list(cycle_data.get("usage_by_tou") or []),
        meter_records=list(cycle_data.get("meter_records") or []),
        current_tier=cycle_data.get("current_tier"),
        projected_tier=cycle_data.get("projected_tier"),
        energy_subtotal=_as_decimal(cycle_data.get("energy_subtotal")),
        full_bill_total=_as_decimal(cycle_data.get("full_bill_total")),
        fixed_charges=_as_decimal(cycle_data.get("fixed_charges")),
        taxes_fees=_as_decimal(cycle_data.get("taxes_fees")),
        credits=_as_decimal(cycle_data.get("credits")),
        adjustments=_as_decimal(cycle_data.get("adjustments")),
        threshold_interpretation="unknown",
        reconciliation_status="not_compared",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(cycle_draft)
    conflicts = await _configured_conflicts(session, bill, account, extraction)
    if conflicts:
        bill.blocking_warnings = [
            *bill.blocking_warnings,
            {
                "code": "configured_source_conflict",
                "count": len(conflicts),
                "message": "Extracted values conflict with current account configuration",
            },
        ]
    evidence_payload = {
        "schema_version": "utility-bill-evidence/1.0",
        "artifact_sha256": digest,
        "parser_id": PARSER_ID,
        "parser_version": PARSER_VERSION,
        "extraction_method": extraction.extraction_method,
        "normalized": normalized_payload,
        "fields": [_field_payload(item) for item in extraction.fields],
        "warnings": extraction.warnings,
    }
    evidence_path = _safe_path(
        settings.utility_bill_artifact_path,
        digest,
        "sanitized-evidence.json",
    )
    _write_private(evidence_path, _json_bytes(evidence_payload))
    bill.sanitized_evidence_path = str(evidence_path)
    check.outcome = "succeeded"
    job.status = "completed"
    job.completed_at = datetime.now(UTC)
    job.progress = {"phase": "administrator_review", "percent": 100}
    job.result = {
        "bill_import_id": bill.id,
        "rate_plan_id": plan.id,
        "rate_version_id": version.id,
        "cycle_draft_id": cycle_draft.id,
        "automatic_activation": False,
    }
    await session.flush()
    return bill, False


async def latest_extraction_revision(
    session: AsyncSession, bill_id: str
) -> UtilityBillExtractionRevision:
    revision = await session.scalar(
        select(UtilityBillExtractionRevision)
        .where(UtilityBillExtractionRevision.bill_import_id == bill_id)
        .order_by(UtilityBillExtractionRevision.revision.desc())
        .limit(1)
    )
    if revision is None:
        raise ProblemError(
            404,
            "Bill extraction missing",
            "No extraction revision exists for this import",
            "bill_extraction_missing",
        )
    return revision


async def import_payload(session: AsyncSession, bill: UtilityBillImport) -> dict[str, Any]:
    revision = await latest_extraction_revision(session, bill.id)
    fields = list(
        await session.scalars(
            select(UtilityBillExtractedField)
            .where(UtilityBillExtractedField.extraction_revision_id == revision.id)
            .order_by(
                UtilityBillExtractedField.output_kind,
                UtilityBillExtractedField.field_key,
            )
        )
    )
    conflicts = list(
        await session.scalars(
            select(UtilityBillFieldConflict)
            .where(UtilityBillFieldConflict.bill_import_id == bill.id)
            .order_by(UtilityBillFieldConflict.field_key)
        )
    )
    cycle = await session.scalar(
        select(UtilityBillCycleDraft).where(UtilityBillCycleDraft.bill_import_id == bill.id)
    )
    account = await session.get(UtilityAccount, bill.utility_account_id)
    return {
        "id": bill.id,
        "job_id": bill.job_id,
        "utility_account_id": bill.utility_account_id,
        "utility_account_name": account.name if account else "Unavailable",
        "artifact_id": bill.artifact_id,
        "content_sha256": bill.content_sha256,
        "status": bill.status,
        "source_role": bill.source_role,
        "extraction_method": bill.extraction_method,
        "parser_version": bill.parser_version,
        "page_count": bill.page_count,
        "retention_mode": bill.retention_mode,
        "retain_until": bill.retain_until,
        "original_available": bill.original_deleted_at is None,
        "original_deleted_at": bill.original_deleted_at,
        "rate_plan_id": bill.rate_plan_id,
        "rate_version_id": bill.rate_version_id,
        "revision": bill.revision,
        "blocking_warnings": bill.blocking_warnings,
        "extraction_warnings": bill.extraction_warnings,
        "created_at": bill.created_at,
        "updated_at": bill.updated_at,
        "normalized": {
            "account": revision.normalized_account_data,
            "rate_plan": revision.normalized_rate_data,
            "billing_cycle": revision.normalized_cycle_data,
        },
        "fields": [
            {
                "id": item.id,
                "output_kind": item.output_kind,
                "field_key": item.field_key,
                "raw_value": item.raw_value,
                "normalized_value": item.normalized_value,
                "corrected_value": item.corrected_value,
                "effective_value": (
                    item.corrected_value
                    if item.corrected_value is not None
                    else item.normalized_value
                ),
                "page_number": item.page_number,
                "text_region": item.text_region,
                "source_excerpt": item.source_excerpt,
                "extraction_method": item.extraction_method,
                "parser_version": item.parser_version,
                "confidence": item.confidence,
                "review_state": item.review_state,
                "warnings": item.warnings,
                "normalization_history": item.normalization_history,
            }
            for item in fields
        ],
        "conflicts": [
            {
                "id": item.id,
                "field_key": item.field_key,
                "extracted_value": item.extracted_value,
                "configured_value": item.configured_value,
                "comparison_source": item.comparison_source,
                "status": item.status,
                "blocking": item.blocking,
                "resolution_note": item.resolution_note,
            }
            for item in conflicts
        ],
        "cycle_draft": (
            {
                "id": cycle.id,
                "status": cycle.status,
                "starts_at": cycle.starts_at,
                "ends_at": cycle.ends_at,
                "cycle_days": cycle.cycle_days,
                "meter_read_date": cycle.meter_read_date,
                "total_usage_kwh": (
                    str(cycle.total_usage_kwh) if cycle.total_usage_kwh is not None else None
                ),
                "usage_by_tier": cycle.usage_by_tier,
                "usage_by_tou": cycle.usage_by_tou,
                "meter_records": cycle.meter_records,
                "current_tier": cycle.current_tier,
                "projected_tier": cycle.projected_tier,
                "energy_subtotal": (
                    str(cycle.energy_subtotal) if cycle.energy_subtotal is not None else None
                ),
                "full_bill_total": (
                    str(cycle.full_bill_total) if cycle.full_bill_total is not None else None
                ),
                "fixed_charges": (
                    str(cycle.fixed_charges) if cycle.fixed_charges is not None else None
                ),
                "taxes_fees": (str(cycle.taxes_fees) if cycle.taxes_fees is not None else None),
                "credits": str(cycle.credits) if cycle.credits is not None else None,
                "adjustments": (str(cycle.adjustments) if cycle.adjustments is not None else None),
                "threshold_interpretation": cycle.threshold_interpretation,
                "reconciliation_status": cycle.reconciliation_status,
                "billing_cycle_id": cycle.billing_cycle_id,
                "utility_usage_import_id": cycle.utility_usage_import_id,
                "revision": cycle.revision,
            }
            if cycle
            else None
        ),
    }


def _set_path(document: dict[str, Any], path: str, value: Any) -> None:
    current: Any = document
    parts = path.split(".")
    for index, part in enumerate(parts[:-1]):
        next_part = parts[index + 1]
        if isinstance(current, list):
            current = current[int(part)]
        else:
            if part not in current:
                current[part] = [] if next_part.isdigit() else {}
            current = current[part]
    final = parts[-1]
    if isinstance(current, list):
        current[int(final)] = value
    else:
        current[final] = value


async def review_import(
    session: AsyncSession,
    *,
    bill: UtilityBillImport,
    user_id: str,
    expected_revision: int,
    field_reviews: list[dict[str, Any]],
    conflict_resolutions: list[dict[str, Any]],
    threshold_interpretation: str,
    source_role: str,
) -> dict[str, Any]:
    if bill.revision != expected_revision:
        raise ProblemError(
            409,
            "Bill import changed",
            "Reload the import before applying review decisions",
            "stale_revision",
        )
    revision = await latest_extraction_revision(session, bill.id)
    fields_by_id = {
        item.id: item
        for item in await session.scalars(
            select(UtilityBillExtractedField).where(
                UtilityBillExtractedField.extraction_revision_id == revision.id
            )
        )
    }
    now = datetime.now(UTC)
    for review in field_reviews:
        item = fields_by_id.get(str(review.get("field_id")))
        if item is None:
            raise ProblemError(
                422,
                "Invalid field review",
                "One reviewed field does not belong to this extraction",
                "bill_field_invalid",
            )
        action = str(review.get("action"))
        if action not in {"confirm", "correct", "reject"}:
            raise ProblemError(
                422,
                "Invalid field review",
                "Field review action is not supported",
                "bill_field_action_invalid",
            )
        if action == "correct":
            if "value" not in review:
                raise ProblemError(
                    422,
                    "Correction required",
                    "Corrected fields require an explicit value",
                    "bill_field_correction_missing",
                )
            item.corrected_value = review["value"]
            item.review_state = "corrected"
            item.extraction_method = "administrator"
            _set_path(
                {
                    "account": revision.normalized_account_data,
                    "rate_plan": revision.normalized_rate_data,
                    "billing_cycle": revision.normalized_cycle_data,
                }[item.output_kind],
                item.field_key,
                review["value"],
            )
        elif action == "confirm":
            item.review_state = "confirmed"
        else:
            item.review_state = "rejected"
        item.confidence = "administrator_confirmed" if action != "reject" else "missing"
        item.confirmed_by = user_id
        item.confirmed_at = now
    conflicts_by_id = {
        item.id: item
        for item in await session.scalars(
            select(UtilityBillFieldConflict).where(
                UtilityBillFieldConflict.bill_import_id == bill.id
            )
        )
    }
    for resolution in conflict_resolutions:
        conflict = conflicts_by_id.get(str(resolution.get("conflict_id")))
        if conflict is None:
            raise ProblemError(
                422,
                "Invalid conflict",
                "One conflict does not belong to this import",
                "bill_conflict_invalid",
            )
        decision = str(resolution.get("decision"))
        if decision not in {"accepted_bill", "accepted_configured", "dismissed"}:
            raise ProblemError(
                422,
                "Invalid conflict resolution",
                "Conflict decision is not supported",
                "bill_conflict_resolution_invalid",
            )
        conflict.status = decision
        conflict.resolution_note = str(resolution.get("note") or "")[:1000]
        conflict.resolved_by = user_id
        conflict.resolved_at = now
    cycle = await session.scalar(
        select(UtilityBillCycleDraft).where(UtilityBillCycleDraft.bill_import_id == bill.id)
    )
    if cycle is None:
        raise ProblemError(
            404,
            "Billing-cycle draft missing",
            "The separate billing-cycle draft no longer exists",
            "bill_cycle_draft_missing",
        )
    cycle.threshold_interpretation = threshold_interpretation
    cycle.reviewed_by = user_id
    cycle.updated_at = now
    cycle.revision += 1
    bill.source_role = source_role
    unresolved_fields = [
        item.field_key
        for item in fields_by_id.values()
        if item.field_key in REQUIRED_REVIEW_FIELDS
        and item.review_state not in {"confirmed", "corrected"}
    ]
    unresolved_conflicts = [
        item.field_key
        for item in conflicts_by_id.values()
        if item.blocking and item.status == "unresolved"
    ]
    blockers: list[dict[str, Any]] = []
    if unresolved_fields:
        blockers.append(
            {
                "code": "required_field_review",
                "fields": sorted(unresolved_fields),
                "message": "Required extracted fields still need administrator review",
            }
        )
    if unresolved_conflicts:
        blockers.append(
            {
                "code": "source_conflict",
                "fields": sorted(unresolved_conflicts),
                "message": "Source conflicts must be resolved before publication",
            }
        )
    if threshold_interpretation == "unknown":
        blockers.append(
            {
                "code": "threshold_interpretation_required",
                "message": "Tier-threshold interpretation must be selected",
            }
        )
    version = await session.get(RateVersion, bill.rate_version_id) if bill.rate_version_id else None
    if version is None or not version.normalized_payload:
        blockers.append(
            {
                "code": "rate_draft_missing",
                "message": "The separate linked rate-plan draft is unavailable",
            }
        )
    else:
        document = RatePlanDocument.model_validate(version.normalized_payload)
        extracted_model = str(revision.normalized_rate_data.get("pricing_model") or "")
        if extracted_model and document.pricing_model != extracted_model:
            blockers.append(
                {
                    "code": "rate_model_review_required",
                    "message": (
                        f"The bill suggests {extracted_model}, while the editable draft is "
                        f"{document.pricing_model}; complete the linked draft explicitly"
                    ),
                }
            )
        report = validate_document(document)
        if not report.valid:
            blockers.append(
                {
                    "code": "rate_draft_validation_failed",
                    "fields": [issue.path for issue in report.errors],
                    "message": "The linked rate-plan draft has blocking validation errors",
                }
            )
    bill.blocking_warnings = blockers
    bill.status = "ready_to_publish" if not blockers else "review_required"
    bill.reviewed_by = user_id
    bill.approved_at = now if not blockers else None
    bill.updated_at = now
    bill.revision += 1
    revision.status = "approved" if not blockers else "review_required"
    await synchronize_rate_draft_from_extraction(session, bill=bill)
    return await import_payload(session, bill)


async def validate_bill_rate_draft(
    session: AsyncSession, bill: UtilityBillImport
) -> dict[str, Any]:
    version = await session.get(RateVersion, bill.rate_version_id) if bill.rate_version_id else None
    if version is None or not version.normalized_payload:
        raise ProblemError(
            404,
            "Rate draft missing",
            "The linked rate-plan draft no longer exists",
            "bill_rate_draft_missing",
        )
    document = RatePlanDocument.model_validate(version.normalized_payload)
    report = validate_document(document)
    return {
        "bill_status": bill.status,
        "blocking_warnings": bill.blocking_warnings,
        "validation": report.model_dump(mode="json"),
    }


async def publish_and_assign(
    session: AsyncSession,
    *,
    bill: UtilityBillImport,
    account: UtilityAccount,
    user_id: str,
    effective_from: datetime,
) -> tuple[RateVersion, RateAssignment, str]:
    if bill.status != "ready_to_publish" or bill.blocking_warnings:
        raise ProblemError(
            409,
            "Bill review is incomplete",
            "Resolve all required fields and conflicts before publication",
            "bill_review_incomplete",
            extra={"blocking_warnings": bill.blocking_warnings},
        )
    version = await session.get(RateVersion, bill.rate_version_id) if bill.rate_version_id else None
    if version is None:
        raise ProblemError(
            404,
            "Rate draft missing",
            "The linked rate-plan draft no longer exists",
            "bill_rate_draft_missing",
        )
    status, _report = await activate_version(session, version, user_id)
    current = list(
        await session.scalars(
            select(RateAssignment).where(
                RateAssignment.utility_account_id == account.id,
                RateAssignment.effective_to.is_(None),
            )
        )
    )
    assignment_effective_from = max(effective_from, datetime.now(UTC))
    future_assignment = next(
        (item for item in current if _aware_utc(item.effective_from) >= assignment_effective_from),
        None,
    )
    if future_assignment is not None:
        raise ProblemError(
            409,
            "A scheduled rate assignment already exists",
            "Choose an effective time after the existing scheduled assignment",
            "rate_assignment_future_conflict",
            extra={
                "existing_assignment_id": future_assignment.id,
                "existing_effective_from": _aware_utc(future_assignment.effective_from).isoformat(),
            },
        )
    for assignment in current:
        assignment.effective_to = assignment_effective_from
    assignment = RateAssignment(
        utility_account_id=account.id,
        rate_version_id=version.id,
        effective_from=assignment_effective_from,
        effective_to=None,
        assignment_reason=f"Administrator-approved utility bill import {bill.id}",
        assigned_by=user_id,
        created_at=datetime.now(UTC),
    )
    session.add(assignment)
    account.active_rate_version_id = version.id
    bill.status = "published"
    bill.updated_at = datetime.now(UTC)
    if bill.retention_mode == "delete_after_approval":
        await delete_original_artifact(session, bill=bill)
    return version, assignment, status


async def approve_cycle_draft(
    session: AsyncSession,
    *,
    bill: UtilityBillImport,
    account: UtilityAccount,
    user_id: str,
) -> UtilityBillCycleDraft:
    if bill.status not in {"ready_to_publish", "published"}:
        raise ProblemError(
            409,
            "Bill review is incomplete",
            "Review all required fields and conflicts before importing billing-cycle data",
            "bill_review_incomplete",
        )
    draft = await session.scalar(
        select(UtilityBillCycleDraft).where(UtilityBillCycleDraft.bill_import_id == bill.id)
    )
    if draft is None:
        raise ProblemError(
            404,
            "Billing-cycle draft missing",
            "The separate billing-cycle draft no longer exists",
            "bill_cycle_draft_missing",
        )
    if draft.status == "imported":
        return draft
    if draft.starts_at is None or draft.ends_at is None or draft.total_usage_kwh is None:
        raise ProblemError(
            422,
            "Billing-cycle draft incomplete",
            "Cycle dates and utility-reported usage are required",
            "bill_cycle_draft_incomplete",
        )
    if draft.threshold_interpretation == "unknown":
        raise ProblemError(
            422,
            "Threshold interpretation required",
            "Review the tier threshold before importing the billing cycle",
            "bill_threshold_unresolved",
        )
    existing = await session.scalar(
        select(BillingCycle).where(
            BillingCycle.utility_account_id == account.id,
            BillingCycle.starts_at == draft.starts_at,
            BillingCycle.ends_at == draft.ends_at,
        )
    )
    if existing and existing.finalized_at is not None:
        raise ProblemError(
            409,
            "Billing cycle is finalized",
            "Finalized billing evidence cannot be overwritten",
            "billing_cycle_finalized",
        )
    now = datetime.now(UTC)
    cycle = existing or BillingCycle(
        utility_account_id=account.id,
        starts_at=draft.starts_at,
        ends_at=draft.ends_at,
        explicit_meter_dates=True,
        status="confirmed",
        boundary_source="utility_import",
        override_revision=0,
        recalculation_version=0,
        created_by=user_id,
        updated_by=user_id,
        created_at=now,
        updated_at=now,
    )
    if existing is None:
        session.add(cycle)
    else:
        cycle.explicit_meter_dates = True
        cycle.status = "confirmed"
        cycle.boundary_source = "utility_import"
        cycle.override_revision += 1
        cycle.updated_by = user_id
        cycle.updated_at = now
    await session.flush()
    usage_import = UtilityUsageImport(
        utility_account_id=account.id,
        import_kind="cycle_cumulative",
        status="committed",
        timezone=account.timezone,
        source_name=f"Private utility bill import {bill.id}",
        content_sha256=bill.content_sha256,
        field_mapping={
            "source": "utility_bill_pdf",
            "artifact_id": bill.artifact_id,
            "bill_import_id": bill.id,
        },
        row_count=1,
        conflict_count=0,
        normalized_rows=[
            {
                "effective_at": draft.ends_at.isoformat(),
                "cumulative_kwh": str(draft.total_usage_kwh),
                "billing_cycle_id": cycle.id,
            }
        ],
        created_by=user_id,
        created_at=now,
    )
    session.add(usage_import)
    await session.flush()
    session.add(
        ManualAccountUsage(
            utility_account_id=account.id,
            billing_cycle_id=cycle.id,
            effective_at=draft.ends_at,
            cumulative_kwh=draft.total_usage_kwh,
            source_note="Administrator-approved utility-bill usage",
            evidence_reference=f"utility-bill:{bill.id}:sha256:{bill.content_sha256}",
            idempotency_key=f"utility-bill-{bill.id}",
            verification_status="verified",
            created_by=user_id,
            created_at=now,
        )
    )
    if bill.source_role == "authoritative_account_specific":
        authority = await session.scalar(
            select(AccountUsageAuthority).where(
                AccountUsageAuthority.utility_account_id == account.id
            )
        )
        if authority is None:
            authority = AccountUsageAuthority(
                utility_account_id=account.id,
                authority_type="manual_cycle_usage",
                device_ids=[],
                source_reference=f"utility-bill:{bill.id}",
                confidence="utility_verified",
                complete_account=True,
                revision=1,
                updated_by=user_id,
                updated_at=now,
            )
            session.add(authority)
        else:
            authority.authority_type = "manual_cycle_usage"
            authority.source_reference = f"utility-bill:{bill.id}"
            authority.confidence = "utility_verified"
            authority.complete_account = True
            authority.revision += 1
            authority.updated_by = user_id
            authority.updated_at = now
    draft.status = "imported"
    draft.billing_cycle_id = cycle.id
    draft.utility_usage_import_id = usage_import.id
    draft.reviewed_by = user_id
    draft.approved_at = now
    draft.updated_at = now
    draft.revision += 1
    return draft


async def bill_comparison(
    session: AsyncSession,
    *,
    bill: UtilityBillImport,
) -> dict[str, Any]:
    draft = await session.scalar(
        select(UtilityBillCycleDraft).where(UtilityBillCycleDraft.bill_import_id == bill.id)
    )
    version = await session.get(RateVersion, bill.rate_version_id) if bill.rate_version_id else None
    if (
        draft is None
        or version is None
        or not version.normalized_payload
        or draft.total_usage_kwh is None
        or draft.starts_at is None
        or draft.ends_at is None
    ):
        return {
            "available": False,
            "reason": "Cycle dates, usage, and a linked rate draft are required",
        }
    document = RatePlanDocument.model_validate(version.normalized_payload)
    report = validate_document(document)
    if not report.valid:
        return {
            "available": False,
            "reason": "The linked rate draft has blocking validation errors",
            "validation": report.model_dump(mode="json"),
        }
    cost_scope = cast(
        Literal[
            "energy_only",
            "allocated_account",
            "full_account",
            "allocated_account_estimate",
            "full_account_estimate",
        ],
        document.cost_scope_default,
    )
    cycle_start = _aware_utc(draft.starts_at)
    cycle_end = _aware_utc(draft.ends_at)
    result = RateEngine(engine_plan(document)).calculate(
        start=cycle_start,
        end=cycle_end,
        energy_kwh=draft.total_usage_kwh,
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        cost_scope=cost_scope,
        billing_days=draft.cycle_days,
    )
    utility_energy = draft.energy_subtotal
    utility_total = draft.full_bill_total
    energy_difference = (
        utility_energy - result.energy_charge if utility_energy is not None else None
    )
    total_difference = utility_total - result.total if utility_total is not None else None
    tiers = [
        {
            "tier_id": str(item["tier_id"]),
            "name": str(item["name"]),
            "lower_bound_kwh": str(item["lower_bound_kwh"]),
            "upper_bound_kwh": (
                str(item["upper_bound_kwh"]) if item["upper_bound_kwh"] is not None else None
            ),
            "display_range": format_tier_range(
                str(item["lower_bound_kwh"]),
                str(item["upper_bound_kwh"]) if item["upper_bound_kwh"] is not None else None,
            ),
        }
        for item in result.tier_thresholds
    ]
    return {
        "available": True,
        "calculation_correctness": "rate_engine_validated",
        "extraction_confidence": "administrator_reviewed"
        if bill.status in {"ready_to_publish", "published"}
        else "review_required",
        "exact": {
            "usage_kwh": str(draft.total_usage_kwh),
            "calculated_energy_subtotal": str(result.energy_charge),
            "calculated_total": str(result.total),
            "utility_energy_subtotal": (
                str(utility_energy) if utility_energy is not None else None
            ),
            "utility_full_bill_total": (str(utility_total) if utility_total is not None else None),
            "energy_subtotal_difference": (
                str(energy_difference) if energy_difference is not None else None
            ),
            "complete_bill_difference": (
                str(total_difference) if total_difference is not None else None
            ),
            "unexplained_difference": (
                str(total_difference) if total_difference is not None else None
            ),
        },
        "display": {
            "usage": format_energy(draft.total_usage_kwh),
            "calculated_energy_subtotal": format_currency(result.energy_charge, document.currency),
            "blended_energy_rate": format_energy_rate(
                result.energy_charge / draft.total_usage_kwh,
                document.currency,
                derived=True,
            )
            if draft.total_usage_kwh > 0
            else None,
            "calculated_total": format_currency(result.total, document.currency),
            "utility_energy_subtotal": (
                format_currency(utility_energy, document.currency)
                if utility_energy is not None
                else None
            ),
            "utility_full_bill_total": (
                format_currency(utility_total, document.currency)
                if utility_total is not None
                else None
            ),
            "energy_subtotal_difference": (
                format_currency(energy_difference, document.currency)
                if energy_difference is not None
                else None
            ),
            "complete_bill_difference": (
                format_currency(total_difference, document.currency)
                if total_difference is not None
                else None
            ),
        },
        "tiers": tiers,
        "disclosure": (
            "The energy subtotal comparison is separate from the complete bill total. "
            "Taxes, credits, fixed charges, provider charges, and unexplained differences "
            "remain separate evidence."
        ),
    }


async def delete_original_artifact(
    session: AsyncSession,
    *,
    bill: UtilityBillImport,
) -> bool:
    if bill.status not in {"ready_to_publish", "published"}:
        raise ProblemError(
            409,
            "Approved extraction required",
            "Review and approve the extraction before deleting the original PDF",
            "bill_original_delete_blocked",
        )
    artifact = await session.get(RateSourceArtifact, bill.artifact_id)
    removed = False
    if artifact is not None:
        path = Path(artifact.storage_path)
        if path.is_file():
            path.unlink()
            removed = True
    bill.original_deleted_at = bill.original_deleted_at or datetime.now(UTC)
    bill.updated_at = datetime.now(UTC)
    return removed


async def due_retention_deletions(session: AsyncSession) -> int:
    now = datetime.now(UTC)
    bills = list(
        await session.scalars(
            select(UtilityBillImport).where(
                UtilityBillImport.retention_mode == "retain_until",
                UtilityBillImport.retain_until <= now,
                UtilityBillImport.original_deleted_at.is_(None),
                UtilityBillImport.status.in_(["ready_to_publish", "published"]),
            )
        )
    )
    for bill in bills:
        await delete_original_artifact(session, bill=bill)
    return len(bills)


async def import_count(session: AsyncSession, account_id: str) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(UtilityBillImport)
            .where(UtilityBillImport.utility_account_id == account_id)
        )
        or 0
    )


async def synchronize_rate_draft_from_extraction(
    session: AsyncSession,
    *,
    bill: UtilityBillImport,
) -> None:
    version = await session.get(RateVersion, bill.rate_version_id) if bill.rate_version_id else None
    account = await session.get(UtilityAccount, bill.utility_account_id)
    revision = await latest_extraction_revision(session, bill.id)
    if version is None or account is None:
        return
    extraction = BillExtraction(
        page_count=bill.page_count,
        extraction_method=bill.extraction_method,
        ocr_version=revision.ocr_version,
        regions=[],
        raw_text="",
        normalized_text="",
        normalization_history=[],
        account_data=revision.normalized_account_data,
        rate_data=revision.normalized_rate_data,
        cycle_data=revision.normalized_cycle_data,
        fields=[],
        warnings=[],
        blocking_warnings=bill.blocking_warnings,
    )
    document = _plan_document(extraction, account, bill.content_sha256)
    await update_draft_version(session, version, document)
