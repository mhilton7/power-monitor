from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.data_reset.service import (
    NO_BACKUP_CONFIRMATION_PHRASE,
    create_reset_operation,
    create_reset_plan,
    perform_central_reset,
    public_plan_payload,
)
from app.db.models import (
    BackgroundJob,
    DataResetPricingBaseline,
    RateApprovalDecision,
    RateAssignment,
    RateCandidateDifference,
    RateChangeCandidate,
    RateExtractionResult,
    RatePeriod,
    RatePlan,
    RateSource,
    RateSourceArtifact,
    RateSourceCheckRun,
    RateVersion,
    RateVersionSource,
    Site,
    User,
    Utility,
    UtilityAccount,
    UtilityAccountAdjustment,
    UtilityBillExtractionRevision,
    UtilityBillImport,
    new_uuid,
)
from app.problem import ProblemError

ALL_RESET_CATEGORIES = [
    "measurement_history",
    "cost_history",
    "pricing_history",
    "generated_outputs",
]


def _rate_document(code: str, name: str, effective_from: date) -> dict[str, Any]:
    return {
        "schema_version": "power-monitor-rate-plan/1.0",
        "plan_name": name,
        "plan_code": code,
        "utility": "Test utility",
        "description": "Data-reset pricing fixture",
        "currency": "USD",
        "timezone": "America/Los_Angeles",
        "ownership_scope": "global",
        "owner_id": None,
        "effective_from": effective_from.isoformat(),
        "effective_through": None,
        "cost_scope_default": "energy_only",
        "source_label": "Reset test evidence",
        "source_note": "Deterministic test fixture",
        "provider_mode": "custom_combined",
        "seasons": [
            {
                "name": "all-year",
                "start": "01-01",
                "end": "12-31",
                "priority": 0,
                "leap_day_behavior": "include",
                "schedules": [
                    {
                        "day_type": "all-days",
                        "dates": [],
                        "periods": [
                            {
                                "label": "flat",
                                "start_minute": 0,
                                "end_minute": 1440,
                                "price_per_kwh": "0.25000000",
                                "delivery_per_kwh": "0",
                                "generation_per_kwh": "0",
                                "adjustment_per_kwh": "0",
                                "display_order": 0,
                            }
                        ],
                    }
                ],
            }
        ],
        "adjustments": [],
        "custom_notes": "",
        "cloned_from_rate_version_id": None,
    }


def _version(
    plan: RatePlan,
    *,
    number: int,
    code: str,
    name: str,
    effective_from: date,
    effective_to: date | None,
    active: bool = False,
    status: str = "published",
) -> RateVersion:
    return RateVersion(
        id=new_uuid(),
        rate_plan_id=plan.id,
        version=number,
        effective_from=effective_from,
        effective_to=effective_to,
        timezone="America/Los_Angeles",
        currency="USD",
        pricing_model="flat",
        source_url="https://example.test/reset-rate",
        source_checked_on=effective_from,
        source_notes="Deterministic reset test",
        content_hash=f"{number:064x}",
        immutable_after_use=active,
        is_active=active,
        status=status,
        source_kind="custom",
        normalized_payload=_rate_document(code, name, effective_from),
        automatically_activated=False,
        lifecycle_revision=1,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


async def _source_graph(
    session: AsyncSession,
    *,
    source: RateSource,
    version: RateVersion,
    plan: RatePlan,
    user: User,
    label: str,
    root: Path,
    now: datetime,
    terminal_candidate: bool,
) -> tuple[BackgroundJob, RateSourceArtifact, RateExtractionResult]:
    job = BackgroundJob(
        id=new_uuid(),
        job_type="rate_source_sync",
        status="completed",
        requested_at=now - timedelta(days=2),
        started_at=now - timedelta(days=2),
        completed_at=now - timedelta(days=2),
        correlation_id=f"pricing-reset-{label}",
        progress={},
        result={},
    )
    check = RateSourceCheckRun(
        id=new_uuid(),
        job_id=job.id,
        rate_source_id=source.id,
        checked_at=now - timedelta(days=2),
        finished_at=now - timedelta(days=2),
        http_status=200,
        outcome="changed",
        artifact_count=1,
    )
    relative_path = Path("pricing-reset") / f"{label}.html"
    artifact_path = root / relative_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(f"pricing evidence {label}", encoding="utf-8")
    artifact = RateSourceArtifact(
        id=new_uuid(),
        source_check_id=check.id,
        sha256=f"{len(label):064x}",
        content_type="text/html",
        byte_size=artifact_path.stat().st_size,
        storage_path=str(relative_path),
        original_filename=f"{label}.html",
        captured_at=now - timedelta(days=2),
    )
    extraction = RateExtractionResult(
        id=new_uuid(),
        artifact_id=artifact.id,
        parser_id="reset-test",
        parser_version="1.0.0",
        status="succeeded",
        normalized_payload={},
        warnings=[],
        errors=[],
        extracted_at=now - timedelta(days=2),
    )
    session.add_all([job, check, artifact, extraction])
    if terminal_candidate:
        candidate = RateChangeCandidate(
            id=new_uuid(),
            rate_plan_id=plan.id,
            extraction_result_id=extraction.id,
            base_rate_version_id=None,
            candidate_rate_version_id=version.id,
            status="rejected",
            risk_level="manual_review",
            summary={},
            created_at=now - timedelta(days=2),
            reviewed_at=now - timedelta(days=1),
            reviewed_by=user.id,
        )
        session.add_all(
            [
                candidate,
                RateCandidateDifference(
                    id=new_uuid(),
                    candidate_id=candidate.id,
                    path="seasons[0].price",
                    change_type="changed",
                    before_value="0.20",
                    after_value="0.25",
                    material=True,
                ),
                RateApprovalDecision(
                    id=new_uuid(),
                    candidate_id=candidate.id,
                    decision="reject",
                    comment="Historical candidate",
                    decided_by=user.id,
                    decided_at=now - timedelta(days=1),
                ),
            ]
        )
    session.add(
        RateVersionSource(
            rate_version_id=version.id,
            artifact_id=artifact.id,
            extraction_result_id=extraction.id,
            relationship="primary",
        )
    )
    return job, artifact, extraction


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_bill_documents", [False, True])
async def test_pricing_plan_and_commit_share_exact_dependency_closed_scope(
    session: AsyncSession,
    test_settings: Settings,
    delete_bill_documents: bool,
) -> None:
    now = datetime(2026, 8, 6, 19, 0, tzinfo=UTC)
    suffix = "DELETE" if delete_bill_documents else "PRESERVE"
    user = User(
        id=new_uuid(),
        email=f"pricing-reset-{delete_bill_documents}@example.com",
        display_name="Pricing Reset",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Pricing reset site",
        code=f"pricing-reset-{delete_bill_documents}",
        timezone="America/Los_Angeles",
    )
    other_site = Site(
        id=new_uuid(),
        name="Other pricing site",
        code=f"other-pricing-{delete_bill_documents}",
        timezone="America/Los_Angeles",
    )
    utility = Utility(id=new_uuid(), name=f"Reset Utility {delete_bill_documents}")
    session.add_all([user, site, other_site, utility])
    await session.flush()
    account = UtilityAccount(
        id=new_uuid(),
        site_id=site.id,
        utility_id=utility.id,
        name="Reset account",
        timezone="America/Los_Angeles",
    )
    other_account = UtilityAccount(
        id=new_uuid(),
        site_id=other_site.id,
        utility_id=utility.id,
        name="Other account",
        timezone="America/Los_Angeles",
    )
    session.add_all([account, other_account])
    await session.flush()
    plan = RatePlan(
        id=new_uuid(),
        utility_id=utility.id,
        code=f"RESET-{suffix}",
        name="Scoped reset plan",
        ownership_scope="utility_account",
        owner_site_id=site.id,
        owner_utility_account_id=account.id,
        status="active",
    )
    other_plan = RatePlan(
        id=new_uuid(),
        utility_id=utility.id,
        code=f"OTHER-{suffix}",
        name="Cross-site plan",
        ownership_scope="utility_account",
        owner_site_id=other_site.id,
        owner_utility_account_id=other_account.id,
        status="active",
    )
    session.add_all([plan, other_plan])
    await session.flush()
    old = _version(
        plan,
        number=1,
        code=plan.code,
        name=plan.name,
        effective_from=date(2024, 1, 1),
        effective_to=date(2024, 12, 31),
    )
    bill_backed_old = _version(
        plan,
        number=2,
        code=plan.code,
        name=plan.name,
        effective_from=date(2025, 1, 1),
        effective_to=date(2025, 6, 30),
    )
    current = _version(
        plan,
        number=3,
        code=plan.code,
        name=plan.name,
        effective_from=date(2025, 7, 1),
        effective_to=None,
        active=True,
        status="active",
    )
    future = _version(
        plan,
        number=4,
        code=plan.code,
        name=plan.name,
        effective_from=date(2026, 9, 1),
        effective_to=None,
        status="approved",
    )
    cross_site_referenced_old = _version(
        plan,
        number=5,
        code=plan.code,
        name=plan.name,
        effective_from=date(2023, 1, 1),
        effective_to=date(2023, 12, 31),
    )
    cross_site_old = _version(
        other_plan,
        number=1,
        code=other_plan.code,
        name=other_plan.name,
        effective_from=date(2024, 1, 1),
        effective_to=date(2024, 12, 31),
    )
    session.add_all(
        [old, bill_backed_old, current, future, cross_site_referenced_old, cross_site_old]
    )
    await session.flush()
    account.active_rate_version_id = current.id
    other_account.active_rate_version_id = cross_site_old.id
    future_at = now + timedelta(days=30)
    historical_assignment = RateAssignment(
        id=new_uuid(),
        utility_account_id=account.id,
        rate_version_id=old.id,
        effective_from=datetime(2024, 1, 1, tzinfo=UTC),
        effective_to=datetime(2025, 1, 1, tzinfo=UTC),
        revision=1,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    current_assignment = RateAssignment(
        id=new_uuid(),
        utility_account_id=account.id,
        rate_version_id=current.id,
        effective_from=datetime(2025, 7, 1, tzinfo=UTC),
        effective_to=future_at,
        revision=1,
        created_at=datetime(2025, 7, 1, tzinfo=UTC),
    )
    future_assignment = RateAssignment(
        id=new_uuid(),
        utility_account_id=account.id,
        rate_version_id=future.id,
        effective_from=future_at,
        effective_to=None,
        revision=1,
        created_at=now - timedelta(days=1),
    )
    historical_adjustment = UtilityAccountAdjustment(
        id=new_uuid(),
        utility_account_id=account.id,
        component="service_charge",
        value=Decimal("3.00"),
        unit="fixed",
        provenance="reset-test",
        effective_from=datetime(2024, 1, 1, tzinfo=UTC),
        effective_to=datetime(2024, 12, 31, tzinfo=UTC),
        enabled=False,
        status="removed",
        revision=1,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    active_adjustment = UtilityAccountAdjustment(
        id=new_uuid(),
        utility_account_id=account.id,
        component="cca_generation",
        value=Decimal("0.01"),
        unit="per_kwh",
        provenance="reset-test",
        effective_from=datetime(2025, 1, 1, tzinfo=UTC),
        effective_to=None,
        enabled=True,
        status="active",
        revision=1,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    old_period = RatePeriod(
        id=new_uuid(),
        rate_version_id=old.id,
        season_name="all-year",
        day_type="all-days",
        start_minute=0,
        end_minute=1440,
        bucket="flat",
        price_per_kwh=Decimal("0.20"),
        delivery_per_kwh=Decimal("0"),
        generation_per_kwh=Decimal("0"),
        adjustment_per_kwh=Decimal("0"),
        display_order=0,
    )
    session.add_all(
        [
            historical_assignment,
            current_assignment,
            future_assignment,
            historical_adjustment,
            active_adjustment,
            old_period,
        ]
    )

    runtime_root = (
        test_settings.report_path.parent
        / "pricing-history-reset"
        / str(delete_bill_documents).lower()
    )
    rate_root = runtime_root / "rate-artifacts"
    bill_root = runtime_root / "bill-artifacts"
    report_root = runtime_root / "reports"
    log_root = runtime_root / "logs"
    backup_root = runtime_root / "backups"
    for root in (rate_root, bill_root, report_root, log_root, backup_root):
        root.mkdir(parents=True, exist_ok=True)
    source = RateSource(
        id=new_uuid(),
        name=f"Reset source {delete_bill_documents}",
        url=f"https://example.test/source/{delete_bill_documents}",
        parser_id="reset-test",
        enabled=True,
        consecutive_failures=0,
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=1),
    )
    session.add(source)
    await session.flush()
    old_job, old_artifact, old_extraction = await _source_graph(
        session,
        source=source,
        version=old,
        plan=plan,
        user=user,
        label="old",
        root=rate_root,
        now=now,
        terminal_candidate=True,
    )
    _, active_artifact, _ = await _source_graph(
        session,
        source=source,
        version=current,
        plan=plan,
        user=user,
        label="active",
        root=rate_root,
        now=now,
        terminal_candidate=False,
    )
    bill_source_version = current if delete_bill_documents else bill_backed_old
    bill_source_job, bill_artifact, bill_extraction = await _source_graph(
        session,
        source=source,
        version=bill_source_version,
        plan=plan,
        user=user,
        label="bill",
        root=rate_root,
        now=now,
        terminal_candidate=False,
    )
    bill_job = BackgroundJob(
        id=new_uuid(),
        job_type="utility_bill_import",
        status="completed",
        requested_by=user.id,
        requested_at=now - timedelta(days=3),
        started_at=now - timedelta(days=3),
        completed_at=now - timedelta(days=3),
        correlation_id=f"bill-reset-{delete_bill_documents}",
        progress={},
        result={},
    )
    bill_path = bill_root / "bill-evidence.pdf"
    bill_text_path = bill_root / "bill-evidence.txt"
    bill_path.write_bytes(b"sanitized bill")
    bill_text_path.write_text("sanitized bill text", encoding="utf-8")
    bill = UtilityBillImport(
        id=new_uuid(),
        job_id=bill_job.id,
        utility_account_id=account.id,
        artifact_id=bill_artifact.id,
        content_sha256="b" * 64,
        status="published",
        source_role="supporting",
        extraction_method="text",
        parser_version="1.0.0",
        page_count=1,
        retention_mode="retain",
        sanitized_evidence_path=bill_path.name,
        rate_plan_id=plan.id,
        rate_version_id=bill_source_version.id,
        revision=1,
        created_by=user.id,
        reviewed_by=user.id,
        created_at=now - timedelta(days=3),
        updated_at=now - timedelta(days=1),
        approved_at=now - timedelta(days=1),
    )
    bill_revision = UtilityBillExtractionRevision(
        id=new_uuid(),
        bill_import_id=bill.id,
        revision=1,
        status="approved",
        parser_version="1.0.0",
        normalized_account_data={},
        normalized_rate_data={},
        normalized_cycle_data={},
        normalized_artifact={},
        raw_text_sha256="c" * 64,
        normalized_text_sha256="d" * 64,
        sanitized_text_path=bill_text_path.name,
        extraction_metadata={},
        created_by=user.id,
        created_at=now - timedelta(days=3),
    )
    cross_bill_job = BackgroundJob(
        id=new_uuid(),
        job_type="utility_bill_import",
        status="completed",
        requested_by=user.id,
        requested_at=now - timedelta(days=4),
        started_at=now - timedelta(days=4),
        completed_at=now - timedelta(days=4),
        correlation_id=f"cross-site-bill-reset-{delete_bill_documents}",
        progress={},
        result={},
    )
    cross_bill = UtilityBillImport(
        id=new_uuid(),
        job_id=cross_bill_job.id,
        utility_account_id=other_account.id,
        artifact_id=active_artifact.id,
        content_sha256="e" * 64,
        status="published",
        source_role="supporting",
        extraction_method="text",
        parser_version="1.0.0",
        page_count=1,
        retention_mode="retain",
        sanitized_evidence_path="cross-site-bill.pdf",
        rate_plan_id=plan.id,
        rate_version_id=cross_site_referenced_old.id,
        revision=1,
        created_by=user.id,
        reviewed_by=user.id,
        created_at=now - timedelta(days=4),
        updated_at=now - timedelta(days=1),
        approved_at=now - timedelta(days=1),
    )
    session.add_all([bill_job, bill, bill_revision, cross_bill_job, cross_bill])
    await session.commit()

    reset_plan = await create_reset_plan(
        session,
        site_id=site.id,
        requested_by=user.id,
        categories=ALL_RESET_CATEGORIES,
        delete_imported_bill_documents=delete_bill_documents,
        disconnected_sensor_policy="defer_until_reconnect",
        offline_after_seconds=30,
        now=now,
    )
    planned = dict(reset_plan.plan_snapshot["counts"])
    assert "pricing_history_scope" not in public_plan_payload(reset_plan)
    assert planned["historical_pricing_rows"] > planned["rate_assignments"]
    assert planned["rate_periods"] == 1
    assert planned["rate_change_candidates"] == 1
    assert planned["rate_candidate_differences"] == 1
    assert planned["rate_approval_decisions"] == 1
    assert planned["imported_bill_documents_preserved"] == (0 if delete_bill_documents else 1)
    assert planned["imported_bill_documents_selected_for_deletion"] == (
        1 if delete_bill_documents else 0
    )

    operation = await create_reset_operation(
        session,
        plan_id=reset_plan.id,
        plan_revision=reset_plan.revision,
        requested_by=user.id,
        idempotency_key=f"pricing-reset-{delete_bill_documents}",
        reason="Verify exact pricing-history dependency scope",
        backup_mode="permanent_without_backup",
        confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=True,
        offline_after_seconds=30,
        now=now,
    )
    operation.state = "backup_verified"
    await session.commit()

    if not delete_bill_documents:
        changed_candidate = await session.scalar(
            select(RateChangeCandidate).where(
                RateChangeCandidate.extraction_result_id == old_extraction.id
            )
        )
        assert changed_candidate is not None
        changed_candidate.status = "pending_review"
        await session.commit()
        with pytest.raises(ProblemError) as stale:
            await perform_central_reset(
                session,
                operation=operation,
                report_root=report_root,
                log_root=log_root,
                bill_artifact_root=bill_root,
                rate_artifact_root=rate_root,
                backup_root=backup_root,
                now=now,
            )
        assert stale.value.code == "data_reset_plan_stale"
        assert await session.get(RateVersion, old.id) is not None
        changed_candidate.status = "rejected"
        await session.commit()

    deleted = await perform_central_reset(
        session,
        operation=operation,
        report_root=report_root,
        log_root=log_root,
        bill_artifact_root=bill_root,
        rate_artifact_root=rate_root,
        backup_root=backup_root,
        now=now,
    )
    pricing_keys = {
        key
        for key in planned
        if key.startswith("rate_")
        or key.startswith("historical_")
        or key.startswith("imported_bill_")
        or key in {"baseline_rules", "fixed_charge_rules"}
    }
    assert {key: deleted[key] for key in pricing_keys} == {
        key: planned[key] for key in pricing_keys
    }
    assert await session.get(RateVersion, old.id) is None
    assert await session.get(RatePeriod, old_period.id) is None
    assert await session.get(RateSourceArtifact, old_artifact.id) is None
    assert await session.get(RateExtractionResult, old_extraction.id) is None
    assert await session.get(BackgroundJob, old_job.id) is None
    assert not (rate_root / old_artifact.storage_path).exists()

    assert await session.get(RateVersion, current.id) is not None
    assert await session.get(RateVersion, future.id) is not None
    assert await session.get(RateVersion, cross_site_referenced_old.id) is not None
    assert await session.get(RateVersion, cross_site_old.id) is not None
    assert await session.get(RateSourceArtifact, active_artifact.id) is not None
    assert (rate_root / active_artifact.storage_path).is_file()
    assert await session.get(RateAssignment, future_assignment.id) is not None
    assert await session.get(UtilityAccountAdjustment, active_adjustment.id) is not None
    assert await session.get(UtilityAccountAdjustment, historical_adjustment.id) is None
    assert (
        await session.scalar(
            select(func.count(RateAssignment.id)).where(
                RateAssignment.utility_account_id == account.id,
                RateAssignment.effective_from <= now,
                RateAssignment.effective_to > now,
            )
        )
        == 1
    )
    assert (
        await session.scalar(
            select(func.count(DataResetPricingBaseline.id)).where(
                DataResetPricingBaseline.operation_id == operation.id
            )
        )
        == 1
    )

    if delete_bill_documents:
        assert await session.get(UtilityBillImport, bill.id) is None
        assert await session.get(UtilityBillExtractionRevision, bill_revision.id) is None
        assert await session.get(RateVersion, bill_backed_old.id) is None
        assert await session.get(RateSourceArtifact, bill_artifact.id) is None
        assert await session.get(RateExtractionResult, bill_extraction.id) is None
        assert await session.get(BackgroundJob, bill_job.id) is None
        assert await session.get(BackgroundJob, bill_source_job.id) is None
        assert not bill_path.exists()
        assert not bill_text_path.exists()
    else:
        assert await session.get(UtilityBillImport, bill.id) is not None
        assert await session.get(UtilityBillExtractionRevision, bill_revision.id) is not None
        assert await session.get(RateVersion, bill_backed_old.id) is not None
        assert await session.get(RateSourceArtifact, bill_artifact.id) is not None
        assert await session.get(RateExtractionResult, bill_extraction.id) is not None
        assert await session.get(BackgroundJob, bill_job.id) is not None
        assert await session.get(BackgroundJob, bill_source_job.id) is not None
        assert bill_path.is_file()
        assert bill_text_path.is_file()

        # A prior reset baseline deliberately retains the old clean assignment
        # identifier as audit evidence.  It must not FK-pin that assignment or
        # the preceding clean billing cycle during a later data-only reset.
        prior_baseline = await session.scalar(
            select(DataResetPricingBaseline).where(
                DataResetPricingBaseline.operation_id == operation.id
            )
        )
        assert prior_baseline is not None
        prior_clean_assignment_id = prior_baseline.rate_assignment_id
        operation.state = "completed"
        operation.completed_at = now
        await session.commit()
        second_now = now + timedelta(hours=1)
        second_plan = await create_reset_plan(
            session,
            site_id=site.id,
            requested_by=user.id,
            categories=ALL_RESET_CATEGORIES,
            delete_imported_bill_documents=False,
            disconnected_sensor_policy="defer_until_reconnect",
            offline_after_seconds=30,
            now=second_now,
        )
        assert second_plan.plan_snapshot["counts"]["rate_assignments"] == 1
        second_operation = await create_reset_operation(
            session,
            plan_id=second_plan.id,
            plan_revision=second_plan.revision,
            requested_by=user.id,
            idempotency_key="pricing-reset-repeat",
            reason="Verify a prior clean baseline does not pin reset history",
            backup_mode="permanent_without_backup",
            confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
            permanent_without_backup_acknowledged=True,
            offline_after_seconds=30,
            now=second_now,
        )
        second_operation.state = "backup_verified"
        await session.commit()
        second_deleted = await perform_central_reset(
            session,
            operation=second_operation,
            report_root=report_root,
            log_root=log_root,
            bill_artifact_root=bill_root,
            rate_artifact_root=rate_root,
            backup_root=backup_root,
            now=second_now,
        )
        assert second_deleted["rate_assignments"] == 1
        assert await session.get(RateAssignment, prior_clean_assignment_id) is None
        assert await session.get(RateVersion, current.id) is not None
        assert await session.get(RateAssignment, future_assignment.id) is not None
        assert (
            await session.scalar(
                select(func.count(DataResetPricingBaseline.id)).where(
                    DataResetPricingBaseline.utility_account_id == account.id
                )
            )
            == 2
        )
