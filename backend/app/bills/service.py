from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing_sources import REFERENCE_ONLY, bill_field_calculation_role
from app.bills.extraction import (
    PARSER_ID,
    PARSER_VERSION,
    BillExtraction,
    ExtractedField,
    extract_bill,
    redact_sensitive_text,
)
from app.config import Settings
from app.data_reset.service import ensure_site_reset_mutations_allowed
from app.db.models import (
    BackgroundJob,
    BillingCycle,
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
)
from app.formatting import (
    format_currency,
    format_energy,
    format_energy_rate,
    format_tier_range,
)
from app.problem import ProblemError
from app.rates.documents import (
    RatePlanDocument,
    document_hash,
    engine_plan,
    validate_document,
)
from app.rates.engine import RateEngine
from app.rates.reset_barrier import (
    ensure_account_rate_mutations_allowed,
    ensure_rate_plans_reset_mutations_allowed,
    rate_owner_site_ids,
)
from app.rates.service import activate_version, create_custom_plan, update_draft_version

logger = structlog.get_logger(__name__)

REQUIRED_REVIEW_FIELDS = {
    "utility",
    "plan_code",
    "pricing_model",
    "threshold_interpretation",
    "rate_plan_code",
    "baseline_allowance_kwh",
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


def _safe_display_filename(value: str | None, digest: str) -> str:
    candidate = Path(value or "").name.strip()
    if not candidate:
        return f"utility-bill-{digest[:12]}.pdf"
    candidate = re.sub(r"[^A-Za-z0-9._() -]+", "-", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" .-")[:180]
    if not candidate:
        return f"utility-bill-{digest[:12]}.pdf"
    return candidate if candidate.lower().endswith(".pdf") else f"{candidate}.pdf"


def _document_type(
    parser_id: str,
    adapter_result: dict[str, Any] | None = None,
    account_data: dict[str, Any] | None = None,
) -> str | None:
    classified = (adapter_result or {}).get("document_class") or (account_data or {}).get(
        "document_class"
    )
    if classified:
        return str(classified)
    if parser_id == "sce_residential_bill_v1":
        return "residential_electric_bill"
    return None


def _normalized_bill_artifact(
    *,
    artifact: RateSourceArtifact,
    extraction: BillExtraction,
    fields: list[ExtractedField],
    imported_at: datetime,
) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    missing_fields: list[dict[str, Any]] = []
    for item in fields:
        if item.normalized_value is None:
            warning = item.warnings[0] if item.warnings else {}
            state = "not_applicable" if item.confidence == "not_applicable" else "not_found_on_bill"
            missing_fields.append(
                {
                    "field": item.field_key,
                    "output_kind": item.output_kind,
                    "calculation_role": bill_field_calculation_role(
                        item.output_kind, item.field_key
                    ),
                    "value": None,
                    "state": state,
                    "required": item.field_key in REQUIRED_REVIEW_FIELDS,
                    "reason": warning.get("message") or "The bill did not contain this field.",
                }
            )
            continue
        confidence = item.confidence
        if item.validation_result and item.validation_result.get("status") == "pass":
            confidence = "arithmetic_confirmed"
        elif confidence == "high":
            confidence = "parser_confirmed"
        evidence.append(
            {
                "field": item.field_key,
                "output_kind": item.output_kind,
                "calculation_role": bill_field_calculation_role(item.output_kind, item.field_key),
                "value": item.normalized_value,
                "confidence": confidence,
                "source_page": item.page_number,
                "source_region": item.text_region,
                "source_text": item.source_excerpt,
                "extraction_method": item.extraction_method,
                "parser_rule": item.parser_rule,
                "parser_version": extraction.parser_version,
                "validation_result": item.validation_result,
            }
        )
    return {
        "schema_version": "normalized-utility-bill/1.0",
        "parser_id": extraction.parser_id,
        "parser_version": extraction.parser_version,
        "artifact": {
            "artifact_id": artifact.id,
            "display_filename": artifact.original_filename,
            "sha256": artifact.sha256,
            "mime_type": artifact.content_type,
            "byte_size": artifact.byte_size,
            "page_count": extraction.page_count,
            "extraction_method": extraction.extraction_method,
            "imported_at": imported_at.isoformat(),
        },
        "utility": {
            "name": extraction.account_data.get("utility"),
            "document_type": _document_type(
                extraction.parser_id,
                extraction.adapter_result,
                extraction.account_data,
            ),
            "rate_plan_code": extraction.rate_data.get("plan_code"),
        },
        "billing_cycle": extraction.cycle_data,
        "plan_candidate": extraction.rate_data,
        "line_items": list(extraction.cycle_data.get("line_items") or []),
        "calculation_policy": {
            "tariff_evidence_role": "tariff_rule",
            "reference_bill_evidence_role": REFERENCE_ONLY,
            "reported_usage_used_in_monitored_calculation": False,
            "bill_total_used_in_monitored_calculation": False,
        },
        "evidence": evidence,
        "validation": extraction.validation,
        "warnings": extraction.warnings,
        "missing_fields": missing_fields,
        "ignored_sections": extraction.ignored_sections,
        "page_classifications": extraction.page_classifications,
        "processing_status": "review_required",
    }


async def _bill_source(
    session: AsyncSession,
    account: UtilityAccount | None,
    user_id: str,
    now: datetime,
) -> RateSource:
    owner_scope = account.id if account is not None else f"user:{user_id}"
    url = f"urn:power-monitor:utility-bill:{owner_scope}"
    source = await session.scalar(select(RateSource).where(RateSource.url == url))
    if source is None:
        source = RateSource(
            name=(
                f"Private utility bills for account {account.id[:8]}"
                if account is not None
                else "Private unassigned utility-bill imports"
            ),
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


def _field_payload(
    field: ExtractedField, *, parser_version: str = PARSER_VERSION
) -> dict[str, Any]:
    return {
        "output_kind": field.output_kind,
        "calculation_role": bill_field_calculation_role(field.output_kind, field.field_key),
        "field_key": field.field_key,
        "raw_value": field.raw_value,
        "normalized_value": field.normalized_value,
        "page_number": field.page_number,
        "source_excerpt": field.source_excerpt,
        "text_region": field.text_region,
        "extraction_method": field.extraction_method,
        "parser_version": parser_version,
        "confidence": field.confidence,
        "warnings": field.warnings,
        "normalization_history": field.normalization_history,
        "parser_rule": field.parser_rule,
        "validation_result": field.validation_result,
    }


def _provider_mode(account: UtilityAccount | None) -> str:
    if account is None:
        return "custom_combined"
    return {
        "sce_bundled": "sce_delivery_generation",
        "sce_delivery_generation": "sce_delivery_generation",
        "sce_delivery_cca": "sce_delivery_cca",
        "sce_delivery_direct_access": "sce_delivery_direct_access",
        "custom_combined": "custom_combined",
    }.get(account.provider_mode, "custom_combined")


def _plan_document(
    extraction: BillExtraction,
    account: UtilityAccount | None,
    content_sha256: str,
    *,
    timezone: str,
    currency: str,
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
    # A single bill cannot prove that a charge is a recurring tariff rule.
    # Administrators may add such rules separately under Advanced Billing Rules.
    adjustments: list[dict[str, Any]] = []
    return RatePlanDocument.model_validate(
        {
            "schema_version": "power-monitor-rate-plan/1.0",
            "plan_name": str(rate.get("plan_name") or "Imported utility bill draft"),
            "plan_code": code,
            "utility": str(rate.get("utility") or "custom"),
            "description": "Administrator-reviewed custom plan drafted from a utility bill.",
            "currency": str(rate.get("currency") or currency),
            "timezone": timezone,
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
            "ownership_scope": "utility_account" if account is not None else "global",
            "owner_id": account.id if account is not None else None,
            "effective_from": effective_from,
            "effective_through": None,
            "cost_scope_default": (
                account.cost_scope_default if account is not None else "energy_only"
            ),
            "source_label": "Uploaded utility bill (supporting source)",
            "source_note": notes,
            "provider_mode": _provider_mode(account),
            "seasons": [],
            "adjustments": adjustments,
            "custom_notes": notes,
            "cloned_from_rate_version_id": None,
        }
    )


def _rate_draft_supported(rate_data: dict[str, Any]) -> bool:
    pricing_model = rate_data.get("pricing_model")
    return bool(
        rate_data.get("plan_code")
        and pricing_model
        and (pricing_model != "tiered" or len(rate_data.get("tiers") or []) >= 2)
    )


def _extraction_from_revision(
    bill: UtilityBillImport,
    revision: UtilityBillExtractionRevision,
) -> BillExtraction:
    return BillExtraction(
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


async def ensure_bill_rate_draft(
    session: AsyncSession,
    *,
    bill: UtilityBillImport,
    user_id: str,
) -> tuple[RateVersion | None, bool]:
    """Return the bill's editable draft, rebuilding legacy broken links when safe."""
    locked_bill = await session.scalar(
        select(UtilityBillImport).where(UtilityBillImport.id == bill.id).with_for_update()
    )
    if locked_bill is not None:
        bill = locked_bill
    version = await session.get(RateVersion, bill.rate_version_id) if bill.rate_version_id else None
    plan = await session.get(RatePlan, version.rate_plan_id) if version is not None else None
    if version is not None and plan is not None and plan.status not in {"removed", "retired"}:
        return version, False

    revision = await latest_extraction_revision(session, bill.id)
    if not _rate_draft_supported(revision.normalized_rate_data):
        return None, False
    account = (
        await session.get(UtilityAccount, bill.utility_account_id)
        if bill.utility_account_id is not None
        else None
    )
    fallback_timezone = (
        account.timezone
        if account is not None
        else (version.timezone if version is not None else "America/Los_Angeles")
    )
    fallback_currency = (
        account.currency
        if account is not None
        else (version.currency if version is not None else "USD")
    )
    document = _plan_document(
        _extraction_from_revision(bill, revision),
        account,
        bill.content_sha256,
        timezone=fallback_timezone,
        currency=fallback_currency,
    )

    reusable_plan = (
        await session.get(RatePlan, bill.rate_plan_id) if bill.rate_plan_id is not None else None
    )
    if reusable_plan is not None and reusable_plan.status == "draft":
        await ensure_rate_plans_reset_mutations_allowed(
            session,
            [reusable_plan],
            extra_site_ids=await rate_owner_site_ids(
                session,
                ownership_scope=document.ownership_scope,
                owner_id=document.owner_id,
            ),
        )
        next_version = (
            int(
                await session.scalar(
                    select(func.max(RateVersion.version)).where(
                        RateVersion.rate_plan_id == reusable_plan.id
                    )
                )
                or 0
            )
            + 1
        )
        now = datetime.now(UTC)
        version = RateVersion(
            rate_plan_id=reusable_plan.id,
            version=next_version,
            effective_from=document.effective_from,
            effective_to=document.effective_through,
            timezone=document.timezone,
            currency=document.currency,
            pricing_model=document.pricing_model,
            source_url=f"urn:power-monitor:utility-bill:{bill.id}",
            source_checked_on=date.today(),
            source_checked_at=now,
            source_notes=document.source_note,
            source_label=document.source_label,
            source_kind="utility_bill_candidate",
            content_hash=document_hash(document),
            status="draft",
            normalized_payload=document.model_dump(mode="json"),
            immutable_after_use=False,
            is_active=False,
            created_at=now,
            created_by=user_id,
        )
        session.add(version)
        await session.flush()
        await update_draft_version(session, version, document)
        reusable_plan.name = f"{document.plan_name} (bill draft)"
        plan = reusable_plan
    else:
        plan, version = await create_custom_plan(
            session,
            document,
            user_id,
            duplicate_suffix=True,
        )
        version.source_kind = "utility_bill_candidate"
        version.source_url = f"urn:power-monitor:utility-bill:{bill.id}"
        plan.name = f"{plan.name} (bill draft)"

    extraction_result = await session.scalar(
        select(RateExtractionResult)
        .where(RateExtractionResult.artifact_id == bill.artifact_id)
        .order_by(RateExtractionResult.extracted_at.desc())
        .limit(1)
    )
    session.add(
        RateVersionSource(
            rate_version_id=version.id,
            artifact_id=bill.artifact_id,
            extraction_result_id=extraction_result.id if extraction_result else None,
            relationship="supporting",
        )
    )
    bill.rate_plan_id = plan.id
    bill.rate_version_id = version.id
    bill.updated_at = datetime.now(UTC)
    return version, True


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
    account: UtilityAccount | None,
    extraction: BillExtraction,
) -> list[UtilityBillFieldConflict]:
    if account is None:
        return []
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
    account: UtilityAccount | None,
    content: bytes,
    user_id: str,
    settings: Settings,
    correlation_id: str,
    retention_mode: str,
    retain_until: datetime | None,
    source_role: str,
    timezone: str,
    currency: str,
    original_filename: str | None = None,
    runner: Any | None = None,
) -> tuple[UtilityBillImport, bool]:
    digest = hashlib.sha256(content).hexdigest()
    duplicate_query = select(UtilityBillImport).where(
        UtilityBillImport.content_sha256 == digest,
    )
    if account is not None:
        duplicate_query = duplicate_query.where(UtilityBillImport.utility_account_id == account.id)
    else:
        duplicate_query = duplicate_query.where(
            UtilityBillImport.utility_account_id.is_(None),
            UtilityBillImport.created_by == user_id,
        )
    existing = await session.scalar(duplicate_query)
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
        original_filename=_safe_display_filename(original_filename, digest),
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
        utility_account_id=account.id if account is not None else None,
        artifact_id=artifact.id,
        content_sha256=digest,
        status="review_required",
        source_role=source_role,
        extraction_method=extraction.extraction_method,
        parser_version=extraction.parser_version,
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
        "adapter_result": extraction.adapter_result,
    }
    extraction_record = RateExtractionResult(
        artifact_id=artifact.id,
        parser_id=extraction.parser_id,
        parser_version=extraction.parser_version,
        status="succeeded",
        normalized_payload=normalized_payload,
        warnings=extraction.warnings,
        errors=[],
        extracted_at=now,
    )
    session.add(extraction_record)
    normalized_artifact = _normalized_bill_artifact(
        artifact=artifact,
        extraction=extraction,
        fields=extraction.fields,
        imported_at=now,
    )
    revision = UtilityBillExtractionRevision(
        bill_import_id=bill.id,
        revision=1,
        status="review_required",
        parser_version=extraction.parser_version,
        ocr_version=extraction.ocr_version,
        normalized_account_data=extraction.account_data,
        normalized_rate_data=extraction.rate_data,
        normalized_cycle_data=extraction.cycle_data,
        normalized_artifact=normalized_artifact,
        raw_text_sha256=hashlib.sha256(extraction.raw_text.encode("utf-8")).hexdigest(),
        normalized_text_sha256=hashlib.sha256(
            extraction.normalized_text.encode("utf-8")
        ).hexdigest(),
        sanitized_text_path=str(sanitized_path),
        extraction_metadata={
            "page_count": extraction.page_count,
            "method": extraction.extraction_method,
            "normalization_history": extraction.normalization_history,
            "parser_id": extraction.parser_id,
            "page_classifications": extraction.page_classifications,
            "ignored_sections": extraction.ignored_sections,
            "validation": extraction.validation,
            "adapter_result": extraction.adapter_result,
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
                **_field_payload(item, parser_version=extraction.parser_version),
            )
        )
    plan = None
    version = None
    can_create_rate_draft = _rate_draft_supported(extraction.rate_data)
    if can_create_rate_draft:
        document = _plan_document(
            extraction,
            account,
            digest,
            timezone=timezone,
            currency=currency,
        )
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
        utility_account_id=account.id if account is not None else None,
        status="draft",
        calculation_role=REFERENCE_ONLY,
        starts_at=_as_utc_date(cycle_data.get("starts_at"), timezone),
        ends_at=_as_utc_date(cycle_data.get("ends_at"), timezone),
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
        threshold_interpretation=str(
            cycle_data.get("threshold_interpretation")
            or extraction.rate_data.get("threshold_interpretation")
            or "unknown"
        ),
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
        "parser_id": extraction.parser_id,
        "parser_version": extraction.parser_version,
        "extraction_method": extraction.extraction_method,
        "normalized": normalized_payload,
        "fields": [
            _field_payload(item, parser_version=extraction.parser_version)
            for item in extraction.fields
        ],
        "page_classifications": extraction.page_classifications,
        "ignored_sections": extraction.ignored_sections,
        "validation": extraction.validation,
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
        "rate_plan_id": plan.id if plan is not None else None,
        "rate_version_id": version.id if version is not None else None,
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


async def reprocess_bill_import(
    session: AsyncSession,
    *,
    bill: UtilityBillImport,
    settings: Settings,
    user_id: str,
    runner: Any | None = None,
) -> tuple[UtilityBillExtractionRevision, bool]:
    artifact = await session.get(RateSourceArtifact, bill.artifact_id)
    path = Path(artifact.storage_path) if artifact else None
    if (
        artifact is None
        or path is None
        or not path.is_file()
        or bill.original_deleted_at is not None
    ):
        raise ProblemError(
            410,
            "Original utility bill is unavailable",
            "Reprocessing requires the retained private PDF",
            "bill_original_removed",
        )
    extraction = extract_bill(
        path.read_bytes(),
        settings,
        pdf_path=path,
        **({"runner": runner} if runner is not None else {}),
    )
    previous = await latest_extraction_revision(session, bill.id)
    next_revision = (
        int(
            await session.scalar(
                select(func.max(UtilityBillExtractionRevision.revision)).where(
                    UtilityBillExtractionRevision.bill_import_id == bill.id
                )
            )
            or 0
        )
        + 1
    )
    now = datetime.now(UTC)
    sanitized_text = redact_sensitive_text(extraction.normalized_text)
    sanitized_path = _safe_path(
        settings.utility_bill_artifact_path,
        bill.content_sha256,
        f"sanitized-evidence-r{next_revision}.txt",
    )
    _write_private(sanitized_path, sanitized_text.encode("utf-8"))
    normalized_artifact = _normalized_bill_artifact(
        artifact=artifact,
        extraction=extraction,
        fields=extraction.fields,
        imported_at=now,
    )
    revision = UtilityBillExtractionRevision(
        bill_import_id=bill.id,
        revision=next_revision,
        status="review_required",
        parser_version=extraction.parser_version,
        ocr_version=extraction.ocr_version,
        normalized_account_data=extraction.account_data,
        normalized_rate_data=extraction.rate_data,
        normalized_cycle_data=extraction.cycle_data,
        normalized_artifact=normalized_artifact,
        raw_text_sha256=hashlib.sha256(extraction.raw_text.encode("utf-8")).hexdigest(),
        normalized_text_sha256=hashlib.sha256(
            extraction.normalized_text.encode("utf-8")
        ).hexdigest(),
        sanitized_text_path=str(sanitized_path),
        extraction_metadata={
            "page_count": extraction.page_count,
            "method": extraction.extraction_method,
            "normalization_history": extraction.normalization_history,
            "parser_id": extraction.parser_id,
            "page_classifications": extraction.page_classifications,
            "ignored_sections": extraction.ignored_sections,
            "validation": extraction.validation,
            "adapter_result": extraction.adapter_result,
            "reprocessed_from_revision": previous.revision,
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
                **_field_payload(item, parser_version=extraction.parser_version),
            )
        )
    session.add(
        RateExtractionResult(
            artifact_id=artifact.id,
            parser_id=extraction.parser_id,
            parser_version=extraction.parser_version,
            status="succeeded",
            normalized_payload=normalized_artifact,
            warnings=extraction.warnings,
            errors=[],
            extracted_at=now,
        )
    )
    adopted = bill.status not in {"ready_to_publish", "published"}
    if adopted:
        previous.status = "superseded"
        bill.parser_version = extraction.parser_version
        bill.extraction_method = extraction.extraction_method
        bill.page_count = extraction.page_count
        bill.blocking_warnings = extraction.blocking_warnings
        bill.extraction_warnings = extraction.warnings
        bill.status = "review_required"
        bill.revision += 1
        bill.updated_at = now
    return revision, adopted


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
    account = (
        await session.get(UtilityAccount, bill.utility_account_id)
        if bill.utility_account_id is not None
        else None
    )
    artifact = await session.get(RateSourceArtifact, bill.artifact_id)
    normalized_artifact = dict(revision.normalized_artifact or {})
    if not normalized_artifact:
        parser_id = str(revision.extraction_metadata.get("parser_id", "utility_bill_generic"))
        normalized_artifact = {
            "schema_version": "normalized-utility-bill/1.0",
            "parser_id": parser_id,
            "parser_version": revision.parser_version,
            "artifact": {
                "artifact_id": bill.artifact_id,
                "display_filename": (
                    artifact.original_filename
                    if artifact and artifact.original_filename
                    else f"utility-bill-{bill.content_sha256[:12]}.pdf"
                ),
                "sha256": bill.content_sha256,
                "mime_type": artifact.content_type if artifact else "application/pdf",
                "byte_size": artifact.byte_size if artifact else None,
                "page_count": bill.page_count,
                "extraction_method": bill.extraction_method,
                "imported_at": bill.created_at.isoformat(),
            },
            "utility": {
                "name": revision.normalized_account_data.get("utility"),
                "document_type": _document_type(
                    parser_id,
                    revision.extraction_metadata.get("adapter_result"),
                    revision.normalized_account_data,
                ),
                "rate_plan_code": revision.normalized_rate_data.get("plan_code"),
            },
            "billing_cycle": revision.normalized_cycle_data,
            "plan_candidate": revision.normalized_rate_data,
            "line_items": list(revision.normalized_cycle_data.get("line_items") or []),
            "evidence": [],
            "validation": revision.extraction_metadata.get("validation", {}),
            "warnings": bill.extraction_warnings,
            "missing_fields": [],
            "ignored_sections": revision.extraction_metadata.get("ignored_sections", []),
            "page_classifications": revision.extraction_metadata.get("page_classifications", []),
            "processing_status": bill.status,
        }
    else:
        normalized_artifact["processing_status"] = bill.status
    return {
        "id": bill.id,
        "job_id": bill.job_id,
        "utility_account_id": bill.utility_account_id,
        "utility_account_name": account.name if account else "Not assigned yet",
        "artifact_id": bill.artifact_id,
        "content_sha256": bill.content_sha256,
        "status": bill.status,
        "source_role": bill.source_role,
        "extraction_method": bill.extraction_method,
        "parser_id": revision.extraction_metadata.get("parser_id", "utility_bill_generic"),
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
        "normalized_artifact": normalized_artifact,
        "adapter_result": revision.extraction_metadata.get("adapter_result"),
        "page_classifications": revision.extraction_metadata.get("page_classifications", []),
        "ignored_sections": revision.extraction_metadata.get("ignored_sections", []),
        "validation": revision.extraction_metadata.get("validation", {}),
        "fields": [
            {
                "id": item.id,
                "output_kind": item.output_kind,
                "calculation_role": item.calculation_role,
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
                "parser_rule": item.parser_rule,
                "validation_result": item.validation_result,
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
                "utility_account_id": cycle.utility_account_id,
                "status": cycle.status,
                "calculation_role": cycle.calculation_role,
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


def _apply_cycle_revision(
    cycle: UtilityBillCycleDraft,
    revision: UtilityBillExtractionRevision,
    *,
    timezone: str,
    now: datetime,
) -> None:
    data = revision.normalized_cycle_data
    cycle.starts_at = _as_utc_date(data.get("starts_at"), timezone)
    cycle.ends_at = _as_utc_date(data.get("ends_at"), timezone)
    cycle.cycle_days = data.get("cycle_days")
    cycle.meter_read_date = (
        date.fromisoformat(str(data["meter_read_date"])) if data.get("meter_read_date") else None
    )
    cycle.total_usage_kwh = _as_decimal(data.get("total_usage_kwh"))
    cycle.usage_by_tier = list(data.get("usage_by_tier") or [])
    cycle.usage_by_tou = list(data.get("usage_by_tou") or [])
    cycle.meter_records = list(data.get("meter_records") or [])
    cycle.current_tier = data.get("current_tier")
    cycle.projected_tier = data.get("projected_tier")
    cycle.energy_subtotal = _as_decimal(data.get("energy_subtotal"))
    cycle.full_bill_total = _as_decimal(data.get("full_bill_total"))
    cycle.fixed_charges = _as_decimal(data.get("fixed_charges"))
    cycle.taxes_fees = _as_decimal(data.get("taxes_fees"))
    cycle.credits = _as_decimal(data.get("credits"))
    cycle.adjustments = _as_decimal(data.get("adjustments"))
    cycle.extraction_revision_id = revision.id
    cycle.updated_at = now


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


def _refresh_review_artifact(
    *,
    revision: UtilityBillExtractionRevision,
    bill: UtilityBillImport,
    fields: list[UtilityBillExtractedField],
) -> None:
    artifact = dict(revision.normalized_artifact or {})
    artifact["processing_status"] = bill.status
    artifact["billing_cycle"] = revision.normalized_cycle_data
    artifact["plan_candidate"] = revision.normalized_rate_data
    artifact["line_items"] = list(revision.normalized_cycle_data.get("line_items") or [])
    artifact["calculation_policy"] = {
        "tariff_evidence_role": "tariff_rule",
        "reference_bill_evidence_role": REFERENCE_ONLY,
        "reported_usage_used_in_monitored_calculation": False,
        "bill_total_used_in_monitored_calculation": False,
    }
    evidence: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for item in fields:
        value = item.corrected_value if item.corrected_value is not None else item.normalized_value
        if value is None:
            warning = item.warnings[0] if item.warnings else {}
            missing.append(
                {
                    "field": item.field_key,
                    "output_kind": item.output_kind,
                    "calculation_role": item.calculation_role,
                    "value": None,
                    "state": (
                        "not_applicable"
                        if item.confidence == "not_applicable"
                        else "not_found_on_bill"
                    ),
                    "required": item.field_key in REQUIRED_REVIEW_FIELDS,
                    "reason": warning.get("message") or "The bill did not contain this field.",
                }
            )
            continue
        confidence = item.confidence
        if item.validation_result and item.validation_result.get("status") == "pass":
            confidence = "arithmetic_confirmed"
        elif confidence == "high":
            confidence = "parser_confirmed"
        evidence.append(
            {
                "field": item.field_key,
                "output_kind": item.output_kind,
                "calculation_role": item.calculation_role,
                "value": value,
                "confidence": confidence,
                "source_page": item.page_number,
                "source_region": item.text_region,
                "source_text": item.source_excerpt,
                "extraction_method": item.extraction_method,
                "parser_rule": item.parser_rule,
                "parser_version": item.parser_version,
                "validation_result": item.validation_result,
                "manual_confirmation": (
                    {
                        "actor_id": item.confirmed_by,
                        "confirmed_at": item.confirmed_at.isoformat()
                        if item.confirmed_at
                        else None,
                        "review_state": item.review_state,
                    }
                    if item.confidence == "manual_confirmed"
                    else None
                ),
            }
        )
    artifact["evidence"] = evidence
    artifact["missing_fields"] = missing
    revision.normalized_artifact = artifact


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
            effective_value = (
                item.corrected_value if item.corrected_value is not None else item.normalized_value
            )
            if effective_value is None:
                raise ProblemError(
                    422,
                    "Missing fields cannot be confirmed",
                    "Leave optional missing fields unreviewed or enter a correction",
                    "bill_missing_field_confirmation",
                    extra={"field_key": item.field_key},
                )
            item.review_state = "confirmed"
        else:
            item.review_state = "rejected"
        item.confidence = "manual_confirmed" if action != "reject" else "missing"
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
    account = (
        await session.get(UtilityAccount, bill.utility_account_id)
        if bill.utility_account_id
        else None
    )
    _apply_cycle_revision(
        cycle,
        revision,
        timezone=account.timezone if account else "America/Los_Angeles",
        now=now,
    )
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
    version, _draft_recreated = await ensure_bill_rate_draft(
        session,
        bill=bill,
        user_id=user_id,
    )
    if version is not None:
        await synchronize_rate_draft_from_extraction(session, bill=bill)
        version = await session.get(RateVersion, bill.rate_version_id)
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
    _refresh_review_artifact(
        revision=revision,
        bill=bill,
        fields=list(fields_by_id.values()),
    )
    return await import_payload(session, bill)


async def validate_bill_rate_draft(
    session: AsyncSession,
    bill: UtilityBillImport,
    *,
    user_id: str,
) -> dict[str, Any]:
    version, recreated = await ensure_bill_rate_draft(
        session,
        bill=bill,
        user_id=user_id,
    )
    if version is None or not version.normalized_payload:
        raise ProblemError(
            404,
            "Rate draft missing",
            "The linked rate-plan draft no longer exists",
            "bill_rate_draft_missing",
        )
    document = RatePlanDocument.model_validate(version.normalized_payload)
    report = validate_document(document)
    if recreated and report.valid:
        bill.blocking_warnings = [
            warning
            for warning in bill.blocking_warnings
            if warning.get("code") != "rate_draft_missing"
        ]
        if not bill.blocking_warnings:
            bill.status = "ready_to_publish"
            bill.approved_at = datetime.now(UTC)
        bill.revision += 1
        bill.updated_at = datetime.now(UTC)
    return {
        "bill_status": bill.status,
        "blocking_warnings": bill.blocking_warnings,
        "validation": report.model_dump(mode="json"),
        "rate_draft_recreated": recreated,
        "rate_plan_id": bill.rate_plan_id,
        "rate_version_id": bill.rate_version_id,
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
    plan = await session.get(RatePlan, version.rate_plan_id)
    if plan is None or plan.status in {"removed", "retired"}:
        raise ProblemError(
            409,
            "Rate plan unavailable",
            "Restore the linked draft before publishing or assigning it",
            "rate_plan_removed",
        )
    account, _locked_plans = await ensure_account_rate_mutations_allowed(
        session,
        account.id,
        extra_version_ids=[version.id],
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
    logger.info(
        "billing.rate_rules_imported",
        account_id=account.id,
        rate_version_id=version.id,
        bill_import_id=bill.id,
        rate_source_type="reviewed_bill",
        bill_usage_calculation_role=REFERENCE_ONLY,
    )
    logger.info(
        "billing.bill_usage_ignored_for_calculation",
        account_id=account.id,
        bill_import_id=bill.id,
        reported_usage_present=bool(
            (
                await session.scalar(
                    select(UtilityBillCycleDraft.total_usage_kwh).where(
                        UtilityBillCycleDraft.bill_import_id == bill.id
                    )
                )
            )
            is not None
        ),
    )
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
            "Review all required fields and conflicts before applying billing-cycle dates",
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
    if draft.starts_at is None or draft.ends_at is None:
        raise ProblemError(
            422,
            "Billing-cycle draft incomplete",
            "Reviewed cycle start and end dates are required",
            "bill_cycle_draft_incomplete",
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
        usage_source_type="sensor_measurements",
        projection_source_type="sensor_trend",
        tier_progress_source_type="sensor_measurements",
        recalculation_required=True,
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
        cycle.recalculation_required = True
    await session.flush()
    draft.status = "imported"
    draft.calculation_role = REFERENCE_ONLY
    draft.billing_cycle_id = cycle.id
    draft.utility_usage_import_id = None
    draft.reviewed_by = user_id
    draft.approved_at = now
    draft.updated_at = now
    draft.revision += 1
    logger.info(
        "billing.bill_usage_retained_reference_only",
        account_id=account.id,
        cycle_id=cycle.id,
        bill_import_id=bill.id,
        bill_usage_present=draft.total_usage_kwh is not None,
        cycle_dates_applied=True,
    )
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
    account_ids = sorted(
        {bill.utility_account_id for bill in bills if bill.utility_account_id is not None}
    )
    account_sites = {
        account_id: site_id
        for account_id, site_id in (
            await session.execute(
                select(UtilityAccount.id, UtilityAccount.site_id).where(
                    UtilityAccount.id.in_(account_ids)
                )
            )
        ).all()
    }
    blocked_sites: set[str] = set()
    for site_id in sorted(set(account_sites.values())):
        try:
            await ensure_site_reset_mutations_allowed(session, [site_id])
        except ProblemError as exc:
            if exc.code != "data_reset_site_mutation_blocked":
                raise
            blocked_sites.add(site_id)

    deleted = 0
    for bill in bills:
        if bill.utility_account_id is not None:
            site_id = account_sites.get(bill.utility_account_id)
            # Missing account context is an integrity fault. Retention must fail
            # closed instead of deleting evidence without a reset scope lock.
            if site_id is None or site_id in blocked_sites:
                continue
        await delete_original_artifact(session, bill=bill)
        deleted += 1
    return deleted


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
    account = (
        await session.get(UtilityAccount, bill.utility_account_id)
        if bill.utility_account_id is not None
        else None
    )
    revision = await latest_extraction_revision(session, bill.id)
    if version is None:
        return
    extraction = _extraction_from_revision(bill, revision)
    document = _plan_document(
        extraction,
        account,
        bill.content_sha256,
        timezone=account.timezone if account is not None else version.timezone,
        currency=account.currency if account is not None else version.currency,
    )
    plan = await session.get(RatePlan, version.rate_plan_id)
    if plan is None:
        raise ProblemError(
            404, "Rate plan not found", "Rate plan does not exist", "rate_plan_missing"
        )
    await ensure_rate_plans_reset_mutations_allowed(
        session,
        [plan],
        extra_site_ids=await rate_owner_site_ids(
            session,
            ownership_scope=document.ownership_scope,
            owner_id=document.owner_id,
        ),
    )
    await update_draft_version(session, version, document)
