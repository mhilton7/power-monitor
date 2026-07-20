from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditEvent,
    RateCandidateDifference,
    RateChangeCandidate,
    RateExtractionResult,
    RatePlan,
    RateSource,
    RateSourceArtifact,
    RateSourceCheckRun,
    RateVersion,
    RateVersionSource,
)
from app.rates.documents import RatePlanDocument, document_hash, validate_document
from app.rates.notifications import emit_rate_alert
from app.rates.service import _replace_version_rows, activate_version, version_document
from app.rates.sources import APPROVED_SOURCE_URLS, PARSER_VERSION

APPROVED_PARSER_VERSIONS = {
    "sce_public_tou_html_v1": {PARSER_VERSION},
    "sce_rate_advisory_html_v1": {PARSER_VERSION},
    "sce_tariff_index_html_v1": {PARSER_VERSION},
    "sce_tariff_pdf_v1": {PARSER_VERSION},
}


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key in sorted(value):
            output.update(_flatten(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return output
    if isinstance(value, list):
        output = {}
        for index, item in enumerate(value):
            output.update(_flatten(item, f"{prefix}.{index}"))
        return output
    return {prefix: value}


def document_differences(
    before: dict[str, Any] | None, after: dict[str, Any]
) -> list[dict[str, Any]]:
    old = _flatten(before or {})
    new = _flatten(after)
    differences: list[dict[str, Any]] = []
    for path in sorted(old.keys() | new.keys()):
        if old.get(path) == new.get(path) and path in old and path in new:
            continue
        change_type = "added" if path not in old else "removed" if path not in new else "changed"
        differences.append(
            {
                "path": path,
                "change_type": change_type,
                "before_value": old.get(path),
                "after_value": new.get(path),
                "material": any(
                    token in path
                    for token in (
                        "effective_",
                        "price_per_kwh",
                        "start_minute",
                        "end_minute",
                        "adjustments",
                        "provider_mode",
                    )
                ),
            }
        )
    return differences


def _percent_change_is_safe(differences: list[dict[str, Any]], maximum_percent: Decimal) -> bool:
    for difference in differences:
        path = str(difference["path"])
        if not (path.endswith("price_per_kwh") or path.endswith(".value")):
            continue
        try:
            before = abs(Decimal(str(difference["before_value"])))
            after = abs(Decimal(str(difference["after_value"])))
        except (InvalidOperation, TypeError):
            return False
        if before == 0:
            if after != 0:
                return False
            continue
        if abs(after - before) / before * Decimal("100") > maximum_percent:
            return False
    return True


async def auto_activation_reasons(
    session: AsyncSession,
    *,
    document: RatePlanDocument,
    active: RateVersion | None,
    extraction: RateExtractionResult,
    artifact: RateSourceArtifact,
    differences: list[dict[str, Any]],
    maximum_percent_change: Decimal,
    retroactive_days: int,
) -> list[str]:
    reasons: list[str] = []
    check = await session.get(RateSourceCheckRun, artifact.source_check_id)
    source = await session.get(RateSource, check.rate_source_id) if check else None
    if source is None or source.url not in APPROVED_SOURCE_URLS:
        reasons.append("source_not_official_allowlisted")
    if extraction.parser_version not in APPROVED_PARSER_VERSIONS.get(extraction.parser_id, set()):
        reasons.append("parser_version_not_approved")
    if not artifact.sha256 or not Path(artifact.storage_path).is_file():
        reasons.append("source_artifact_not_archived")
    if extraction.warnings:
        reasons.append("parser_warning_present")
    if extraction.errors or extraction.status != "succeeded":
        reasons.append("parser_or_extraction_failed")
    report = validate_document(
        document,
        require_source_evidence=True,
        source_evidence={
            "artifact_id": artifact.id,
            "sha256": artifact.sha256,
            "parser_id": extraction.parser_id,
            "parser_version": extraction.parser_version,
        },
    )
    if not report.valid:
        reasons.append("schema_or_schedule_validation_failed")
    if report.warnings:
        reasons.append("validation_warning_present")
    if (date.today() - document.effective_from).days > retroactive_days:
        reasons.append("effective_date_too_retroactive")
    if not _percent_change_is_safe(differences, maximum_percent_change):
        reasons.append("change_threshold_exceeded")
    if active is not None:
        active_document = await version_document(session, active)
        if active_document.provider_mode != document.provider_mode:
            reasons.append("provider_assumption_changed")
    return sorted(set(reasons))


async def create_candidate_from_document(
    session: AsyncSession,
    document: RatePlanDocument,
    extraction: RateExtractionResult,
    artifact: RateSourceArtifact,
    *,
    approval_mode: str = "manual_review",
    auto_activate_verified: bool = False,
    maximum_percent_change: Decimal = Decimal("25"),
    retroactive_days: int = 0,
) -> RateChangeCandidate | None:
    plan = await session.scalar(select(RatePlan).where(RatePlan.code == document.plan_code))
    if plan is None:
        candidate = RateChangeCandidate(
            rate_plan_id=None,
            extraction_result_id=extraction.id,
            status="validation_failed",
            risk_level="blocking",
            summary={
                "plan_code": document.plan_code,
                "error": "recognized_plan_required",
            },
            created_at=datetime.now(UTC),
        )
        session.add(candidate)
        await session.flush()
        return candidate
    active = await session.scalar(
        select(RateVersion)
        .where(RateVersion.rate_plan_id == plan.id, RateVersion.is_active.is_(True))
        .order_by(RateVersion.version.desc())
    )
    normalized = document.model_dump(mode="json")
    before_document = await version_document(session, active) if active else None
    if before_document and document_hash(before_document) == document_hash(document):
        return None
    existing_version = await session.scalar(
        select(RateVersion.id).where(
            RateVersion.rate_plan_id == plan.id,
            RateVersion.content_hash == document_hash(document),
            RateVersion.status.in_(["candidate", "approved", "active"]),
        )
    )
    if existing_version:
        return None
    before = before_document.model_dump(mode="json") if before_document else None
    differences = document_differences(before, normalized)
    material_count = sum(bool(item["material"]) for item in differences)
    if approval_mode == "notify_only":
        await emit_rate_alert(
            session,
            "rate_source_changed",
            {
                "plan_code": document.plan_code,
                "material_differences": material_count,
                "policy": "notify_only",
            },
        )
        return None

    evidence = {
        "artifact_id": artifact.id,
        "sha256": artifact.sha256,
        "parser_id": extraction.parser_id,
        "parser_version": extraction.parser_version,
    }
    validation = validate_document(document, require_source_evidence=True, source_evidence=evidence)
    latest_number = await session.scalar(
        select(RateVersion.version)
        .where(RateVersion.rate_plan_id == plan.id)
        .order_by(RateVersion.version.desc())
        .limit(1)
    )
    version = RateVersion(
        rate_plan_id=plan.id,
        version=int(latest_number or 0) + 1,
        effective_from=document.effective_from,
        effective_to=document.effective_through,
        timezone=document.timezone,
        currency=document.currency,
        source_url="archived-sce-evidence",
        source_checked_on=date.today(),
        source_checked_at=datetime.now(UTC),
        source_notes=document.source_note,
        source_label=document.source_label,
        source_kind="official_sce_candidate",
        content_hash=document_hash(document),
        status="candidate" if validation.valid else "rejected",
        change_summary={},
        normalized_payload=normalized,
        immutable_after_use=False,
        is_active=False,
        automatically_activated=False,
        created_at=datetime.now(UTC),
    )
    session.add(version)
    await session.flush()
    await _replace_version_rows(session, version, document)
    candidate = RateChangeCandidate(
        rate_plan_id=plan.id,
        extraction_result_id=extraction.id,
        base_rate_version_id=active.id if active else None,
        candidate_rate_version_id=version.id,
        status="pending_review" if validation.valid else "validation_failed",
        risk_level="manual_review" if validation.valid else "blocking",
        summary={
            "plan_code": document.plan_code,
            "differences": len(differences),
            "material_differences": material_count,
            "validation": validation.model_dump(mode="json"),
        },
        created_at=datetime.now(UTC),
    )
    session.add(candidate)
    await session.flush()
    for difference in differences:
        session.add(RateCandidateDifference(candidate_id=candidate.id, **difference))
    session.add(
        RateVersionSource(
            rate_version_id=version.id,
            artifact_id=artifact.id,
            extraction_result_id=extraction.id,
            relationship="primary",
        )
    )

    conflicts: list[str] = []
    other_candidates = list(
        await session.scalars(
            select(RateChangeCandidate).where(
                RateChangeCandidate.rate_plan_id == plan.id,
                RateChangeCandidate.id != candidate.id,
                RateChangeCandidate.status == "pending_review",
            )
        )
    )
    for other in other_candidates:
        if not other.candidate_rate_version_id:
            continue
        other_version = await session.get(RateVersion, other.candidate_rate_version_id)
        if (
            other_version
            and other_version.effective_from == version.effective_from
            and other_version.content_hash != version.content_hash
        ):
            conflicts.append(other.id)
            other.risk_level = "blocking"
            other.summary = {
                **other.summary,
                "source_conflict": True,
                "conflicting_candidate_ids": sorted(
                    set(other.summary.get("conflicting_candidate_ids", [])) | {candidate.id}
                ),
            }
    if conflicts:
        candidate.risk_level = "blocking"
        candidate.summary = {
            **candidate.summary,
            "source_conflict": True,
            "conflicting_candidate_ids": conflicts,
        }
        await emit_rate_alert(
            session,
            "rate_source_conflict",
            {"candidate_id": candidate.id, "conflicting_candidate_ids": conflicts},
            dedupe_key=candidate.id,
        )

    auto_reasons: list[str] = []
    if approval_mode == "auto_activate_verified" and auto_activate_verified:
        if conflicts:
            auto_reasons.append("source_conflict_present")
        auto_reasons.extend(
            await auto_activation_reasons(
                session,
                document=document,
                active=active,
                extraction=extraction,
                artifact=artifact,
                differences=differences,
                maximum_percent_change=maximum_percent_change,
                retroactive_days=retroactive_days,
            )
        )
        auto_reasons = sorted(set(auto_reasons))
        if validation.valid and not auto_reasons:
            status, _ = await activate_version(session, version, None, automatically=True)
            candidate.status = "automatically_activated" if status == "active" else "scheduled"
            candidate.risk_level = "verified"
            candidate.reviewed_at = datetime.now(UTC)
            candidate.summary = {**candidate.summary, "automatic_activation": status}
            session.add(
                AuditEvent(
                    occurred_at=datetime.now(UTC),
                    actor_type="system",
                    actor_id=None,
                    action="rate_candidate.automatically_activated",
                    object_type="rate_candidate",
                    object_id=candidate.id,
                    outcome="success",
                    correlation_id=None,
                    details={"version_id": version.id, "status": status},
                )
            )
            await emit_rate_alert(
                session,
                "rate_version_auto_activated",
                {"candidate_id": candidate.id, "rate_version_id": version.id},
            )
        else:
            candidate.summary = {
                **candidate.summary,
                "automatic_activation_blocked": auto_reasons,
            }

    await emit_rate_alert(
        session,
        "rate_source_changed",
        {
            "candidate_id": candidate.id,
            "plan_code": document.plan_code,
            "material_differences": material_count,
        },
    )
    if candidate.status not in {"automatically_activated", "scheduled"}:
        await emit_rate_alert(
            session,
            "rate_candidate_pending" if validation.valid else "rate_candidate_validation_failed",
            {
                "candidate_id": candidate.id,
                "plan_code": document.plan_code,
                "blocking_errors": len(validation.errors),
            },
            dedupe_key=candidate.id,
        )
    return candidate
