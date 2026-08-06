from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from worker.app.data_reset import process_data_reset_operations
from worker.app.tasks import process_cost_jobs, process_tier_recalculations

from app.config import Settings
from app.data_reset.service import (
    DATA_RESET_PROTOCOL,
    NO_BACKUP_CONFIRMATION_PHRASE,
    _active_pricing_snapshot,
    create_reset_operation,
    create_reset_plan,
)
from app.db.models import (
    AccountUsageAuthority,
    AggregateMember,
    AggregateSet,
    BillingCycle,
    CostCalculationRun,
    CostIntervalResult,
    DataResetParticipant,
    DataResetPricingBaseline,
    Device,
    DeviceCapability,
    NormalizedInterval,
    RateAssignment,
    RatePlan,
    RateTierDefinition,
    RateVersion,
    Site,
    SyncCursor,
    TierAllocationSegment,
    User,
    Utility,
    UtilityAccount,
    new_uuid,
)
from app.ingestion.service import ingest_readings
from app.rates.tiered import _rate_contexts, current_billing_cycle, expected_cycle_bounds
from app.schemas import Reading

ALL_RESET_CATEGORIES = [
    "measurement_history",
    "cost_history",
    "pricing_history",
    "generated_outputs",
]


def _tiered_rate_document(
    *,
    plan_name: str,
    plan_code: str,
    effective_from: date,
    first_tier_price: str,
    owner_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": "power-monitor-rate-plan/1.0",
        "plan_name": plan_name,
        "plan_code": plan_code,
        "utility": "Reset cost test utility",
        "description": "Deterministic post-reset cost verification fixture",
        "currency": "USD",
        "timezone": "America/Los_Angeles",
        "pricing_model": "tiered",
        "flat_rate_per_kwh": None,
        "billing_cycle": {
            "expected_start_day": 1,
            "threshold": {
                "basis": "fixed_cycle_kwh",
                "daily_baseline_kwh": None,
                "baseline_region": None,
                "baseline_category": None,
                "rounding_policy": "none",
                "seasonal_baselines": [],
                "source_citation": "Deterministic reset cost fixture",
            },
        },
        "tiers": [
            {
                "tier_id": "tier-1",
                "name": "Tier 1",
                "order": 0,
                "lower_bound_inclusive_kwh": "0",
                "upper_bound_exclusive_kwh": "1",
                "lower_bound_multiplier": None,
                "upper_bound_multiplier": None,
                "price_per_kwh": first_tier_price,
                "tou_prices": {},
                "season": None,
                "source_citation": "Deterministic reset cost fixture",
            },
            {
                "tier_id": "tier-2",
                "name": "Tier 2",
                "order": 1,
                "lower_bound_inclusive_kwh": "1",
                "upper_bound_exclusive_kwh": None,
                "lower_bound_multiplier": None,
                "upper_bound_multiplier": None,
                "price_per_kwh": "0.50000000",
                "tou_prices": {},
                "season": None,
                "source_citation": "Deterministic reset cost fixture",
            },
        ],
        "hybrid_pricing": None,
        "ownership_scope": "utility_account",
        "owner_id": owner_id,
        "effective_from": effective_from.isoformat(),
        "effective_through": None,
        "cost_scope_default": "energy_only",
        "source_label": "Reset cost test evidence",
        "source_note": "Not a production utility tariff",
        "provider_mode": "custom_combined",
        "seasons": [],
        "adjustments": [],
        "custom_notes": "",
        "cloned_from_rate_version_id": None,
    }


def _rate_version(
    plan: RatePlan,
    *,
    number: int,
    effective_from: date,
    price: str,
    status: str,
    active: bool,
) -> RateVersion:
    return RateVersion(
        id=new_uuid(),
        rate_plan_id=plan.id,
        version=number,
        effective_from=effective_from,
        effective_to=None,
        timezone="America/Los_Angeles",
        currency="USD",
        pricing_model="tiered",
        source_url="https://example.test/data-reset-cost-rate",
        source_checked_on=effective_from,
        source_notes="Deterministic reset cost fixture",
        content_hash=f"{number:064x}",
        immutable_after_use=active,
        is_active=active,
        status=status,
        source_kind="custom",
        normalized_payload=_tiered_rate_document(
            plan_name=plan.name,
            plan_code=plan.code,
            effective_from=effective_from,
            first_tier_price=price,
            owner_id=str(plan.owner_utility_account_id),
        ),
        automatically_activated=False,
        lifecycle_revision=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _tier_definitions(version: RateVersion, *, first_tier_price: str) -> list[RateTierDefinition]:
    return [
        RateTierDefinition(
            id=new_uuid(),
            rate_version_id=version.id,
            stable_tier_id="tier-1",
            name="Tier 1",
            display_order=0,
            lower_bound_kwh=Decimal("0"),
            upper_bound_kwh=Decimal("1"),
            price_per_kwh=Decimal(first_tier_price),
            tou_prices={},
            source_citation="Deterministic reset cost fixture",
        ),
        RateTierDefinition(
            id=new_uuid(),
            rate_version_id=version.id,
            stable_tier_id="tier-2",
            name="Tier 2",
            display_order=1,
            lower_bound_kwh=Decimal("1"),
            upper_bound_kwh=None,
            price_per_kwh=Decimal("0.50000000"),
            tou_prices={},
            source_citation="Deterministic reset cost fixture",
        ),
    ]


def _post_reset_reading(
    *,
    generation: int,
    sequence: int,
    interval_start: datetime,
) -> Reading:
    return Reading(
        data_generation=generation,
        sequence=sequence,
        boot_id=new_uuid(),
        interval_start=interval_start,
        interval_end=interval_start + timedelta(hours=1),
        time_trusted=True,
        voltage_avg=Decimal("120"),
        current_avg=Decimal("8.333333"),
        power_avg=Decimal("1000"),
        power_factor=Decimal("1"),
        frequency_hz=Decimal("60"),
        pzem_energy_start_wh=Decimal("100000"),
        pzem_energy_end_wh=Decimal("101000"),
        interval_energy_wh=Decimal("1000"),
        energy_method="counter",
        ct_rating_amps=Decimal("100"),
        quality_flags=[],
        firmware_version="1.0.18",
    )


def _configure_runtime_paths(settings: Settings, root: Path) -> None:
    settings.report_path = root / "reports"
    settings.log_path = root / "logs"
    settings.utility_bill_artifact_path = root / "utility-bills"
    settings.rate_sync_artifact_path = root / "rate-artifacts"
    settings.backup_path = root / "backups"
    for path in (
        settings.report_path,
        settings.log_path,
        settings.utility_bill_artifact_path,
        settings.rate_sync_artifact_path,
        settings.backup_path,
    ):
        path.mkdir(parents=True, exist_ok=True)


@pytest.mark.asyncio
async def test_reset_requires_real_first_cost_for_aggregate_priced_sensor(
    session: AsyncSession,
    test_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_at = (datetime.now(UTC) - timedelta(hours=2)).replace(microsecond=0)
    future_at = reset_at + timedelta(days=14)
    user = User(
        id=new_uuid(),
        email="post-reset-cost@example.com",
        display_name="Post Reset Cost",
        password_hash="not-used",
    )
    site = Site(
        id=new_uuid(),
        name="Post-reset cost site",
        code="post-reset-cost-site",
        timezone="America/Los_Angeles",
    )
    utility = Utility(id=new_uuid(), name="Reset cost test utility")
    session.add_all([user, site, utility])
    await session.flush()
    account = UtilityAccount(
        id=new_uuid(),
        site_id=site.id,
        utility_id=utility.id,
        name="Reset cost account",
        timezone="America/Los_Angeles",
        billing_cycle_start_day=1,
        cost_scope_default="energy_only",
    )
    session.add(account)
    await session.flush()
    rate_plan = RatePlan(
        id=new_uuid(),
        utility_id=utility.id,
        code="RESET-COST-TIERED",
        name="Reset Cost Tiered",
        description="Post-reset cost verification fixture",
        plan_kind="custom",
        ownership_scope="utility_account",
        owner_site_id=site.id,
        owner_utility_account_id=account.id,
        currency="USD",
        timezone="America/Los_Angeles",
        status="active",
    )
    session.add(rate_plan)
    await session.flush()
    current_version = _rate_version(
        rate_plan,
        number=1,
        effective_from=date(2026, 1, 1),
        price="0.30000000",
        status="active",
        active=True,
    )
    future_version = _rate_version(
        rate_plan,
        number=2,
        effective_from=future_at.date(),
        price="0.40000000",
        status="approved",
        active=False,
    )
    session.add_all([current_version, future_version])
    await session.flush()
    account.active_rate_version_id = current_version.id
    current_assignment = RateAssignment(
        id=new_uuid(),
        utility_account_id=account.id,
        rate_version_id=current_version.id,
        effective_from=reset_at - timedelta(days=30),
        effective_to=future_at,
        assignment_reason="Current deterministic rate",
        revision=1,
        created_at=reset_at - timedelta(days=30),
    )
    future_assignment = RateAssignment(
        id=new_uuid(),
        utility_account_id=account.id,
        rate_version_id=future_version.id,
        effective_from=future_at,
        effective_to=None,
        assignment_reason="Future deterministic rate",
        revision=1,
        created_at=reset_at,
    )
    device = Device(
        id=new_uuid(),
        site_id=site.id,
        utility_account_id=None,
        hardware_id="post-reset-aggregate-sensor",
        name="Aggregate-priced sensor",
        firmware_version="1.0.18",
        firmware_build_hash="a" * 64,
        last_seen_at=reset_at,
    )
    aggregate = AggregateSet(
        id=new_uuid(),
        site_id=site.id,
        utility_account_id=account.id,
        name="Whole account aggregate",
        cost_scope="energy_only",
        is_default=True,
    )
    session.add_all(
        [
            current_assignment,
            future_assignment,
            device,
            aggregate,
            *_tier_definitions(current_version, first_tier_price="0.30000000"),
            *_tier_definitions(future_version, first_tier_price="0.40000000"),
        ]
    )
    await session.flush()
    session.add_all(
        [
            AggregateMember(
                id=new_uuid(),
                aggregate_set_id=aggregate.id,
                device_id=device.id,
                allocation_percent=Decimal("100"),
            ),
            AccountUsageAuthority(
                id=new_uuid(),
                utility_account_id=account.id,
                authority_type="complete_site_aggregate",
                calculation_role="sensor_measurements",
                aggregate_set_id=aggregate.id,
                device_ids=[],
                source_reference="Deterministic sensor aggregate",
                confidence="high",
                complete_account=True,
                revision=1,
                updated_at=reset_at,
            ),
            DeviceCapability(
                device_id=device.id,
                hardware_target="esp32-s3",
                pzem_model="PZEM-004T V4.0",
                sd_required=True,
                features={"data_reset": DATA_RESET_PROTOCOL},
                reported_at=reset_at,
            ),
            SyncCursor(
                device_id=device.id,
                highest_contiguous_sequence=6,
                maximum_seen_sequence=7,
                data_generation=0,
                reset_boundary=0,
                updated_at=reset_at,
            ),
        ]
    )
    await session.commit()

    plan = await create_reset_plan(
        session,
        site_id=site.id,
        requested_by=user.id,
        categories=ALL_RESET_CATEGORIES,
        delete_imported_bill_documents=False,
        disconnected_sensor_policy="defer_until_reconnect",
        offline_after_seconds=30,
        now=reset_at,
    )
    assert len(plan.plan_snapshot["pricing"]) == 1
    planned_pricing = dict(plan.plan_snapshot["pricing"][0])
    planned_hash = str(planned_pricing["pricing_configuration_hash"])
    assert planned_pricing["rate_version_id"] == current_version.id
    assert planned_pricing["rate_assignment_id"] == current_assignment.id
    assert planned_pricing["future_assignment_ids"] == [future_assignment.id]

    operation = await create_reset_operation(
        session,
        plan_id=plan.id,
        plan_revision=plan.revision,
        requested_by=user.id,
        idempotency_key="post-reset-real-cost-operation",
        reason="Verify real post-reset tier and cost calculation",
        backup_mode="permanent_without_backup",
        confirmation_phrase=NO_BACKUP_CONFIRMATION_PHRASE,
        permanent_without_backup_acknowledged=True,
        offline_after_seconds=30,
        now=reset_at,
    )
    await session.commit()
    _configure_runtime_paths(test_settings, tmp_path / "post-reset-cost-runtime")

    boot_id = new_uuid()
    card_generation = "post-reset-card-1"
    preservation_digest = hashlib.sha256(b"post-reset-preserved-config").hexdigest()

    async def sensor_reset_response(*_args: object, **kwargs: Any) -> dict[str, object]:
        requested_device = kwargs["device"]
        action = str(kwargs["action"])
        assert isinstance(requested_device, Device)
        assert requested_device.id == device.id
        participant = await session.get(
            DataResetParticipant,
            (operation.id, device.id),
        )
        assert participant is not None
        boundary = participant.reset_boundary
        common = {
            "protocol": DATA_RESET_PROTOCOL,
            "operation_id": operation.id,
            "device_id": device.id,
            "target_generation": operation.reset_generation,
            "plan_revision": operation.plan_revision,
            "plan_digest": plan.plan_fingerprint,
            "firmware_version": device.firmware_version,
            "firmware_build_hash": device.firmware_build_hash,
            "boot_id": boot_id,
            "card_generation": card_generation,
        }
        if action == "prepare":
            receipt = {
                **common,
                "state": "prepared",
                "checkpoint": "prepared",
                "reset_boundary": boundary,
                "sequence_floor": boundary,
                "next_sequence": boundary + 1,
                "server_ack_sequence": boundary,
                "server_maximum_seen": boundary,
                "newest_stored_sequence": boundary,
                "newest_syncable_sequence": boundary,
                "local_records_before": 0,
                "local_records_after": 0,
                "backlog_before": 0,
                "backlog_after": 0,
                "prepare_drain_records_added": 0,
                "prepare_drain_first_sequence": None,
                "prepare_drain_last_sequence": None,
                "prepare_drain_syncable_records_added": 0,
                "measurement_pause_started_utc_ms": 1_800_000_000_000,
                "prepared_pzem_energy_wh": 100_000,
                "software_energy_baseline_before_wh": 10_000,
                "pzem_baseline_captured": True,
                "configuration_preserved": True,
                "configuration_preservation_digest_before": preservation_digest,
                "sd_status": "verified",
            }
            return {
                "state": "prepared",
                "_prepared_receipt_parsed": receipt,
                "prepared_receipt_digest": hashlib.sha256(b"prepared").hexdigest(),
                "configuration_preservation_digest_before": preservation_digest,
            }
        assert action == "commit"
        receipt = {
            **common,
            "state": "completed",
            "checkpoint": "completed",
            "configuration_preserved": True,
            "pzem_baseline_captured": True,
            "local_records_before": 0,
            "local_records_after": 0,
            "backlog_before": 0,
            "backlog_after": 0,
            "records_deleted": 0,
            "prepared_receipt_digest": hashlib.sha256(b"prepared").hexdigest(),
            "prepared_pzem_energy_wh": 100_000,
            "commit_pzem_energy_wh": 100_001,
            "verified_pzem_energy_wh": 100_002,
            "measurement_pause_started_utc_ms": 1_800_000_000_000,
            "measurement_pause_ended_utc_ms": 1_800_000_000_001,
            "measurement_pause_evidenced": True,
            "configuration_preservation_digest_before": preservation_digest,
            "configuration_preservation_digest_after": preservation_digest,
            "queues_cleared": True,
            "exports_cleared": True,
            "indexes_rebuilt": True,
            "reset_boundary": boundary,
            "sequence_floor": boundary,
            "next_sequence": boundary + 1,
            "server_ack_sequence": boundary,
            "server_maximum_seen": boundary,
        }
        return {
            "state": "completed",
            "_commit_receipt_parsed": receipt,
            "commit_receipt_digest": hashlib.sha256(b"committed").hexdigest(),
            "configuration_preservation_digest_before": preservation_digest,
            "configuration_preservation_digest_after": preservation_digest,
        }

    monkeypatch.setattr("worker.app.data_reset.request_sensor_reset", sensor_reset_response)

    states = []
    for _ in range(5):
        result = await process_data_reset_operations(session, test_settings)
        states.append(str(result["state"]))
    assert states == [
        "sensors_prepared",
        "backup_verified",
        "database_reset_committed",
        "sensor_commit_running",
        "verification_running",
    ]
    await session.refresh(operation)
    assert operation.completed_at is None
    assert operation.final_evidence["new_cost_status"] == "pending"
    assert operation.final_evidence["required_cost_account_ids"] == [account.id]
    assert operation.final_evidence["queued_cost_account_ids"] == []
    assert operation.final_evidence["missing_cost_aggregate_ids"] == []

    baseline = await session.scalar(
        select(DataResetPricingBaseline).where(
            DataResetPricingBaseline.operation_id == operation.id,
            DataResetPricingBaseline.utility_account_id == account.id,
        )
    )
    assert baseline is not None
    assert baseline.rate_version_id == current_version.id
    assert baseline.pricing_configuration_hash == planned_hash
    assert operation.final_evidence["pricing_hashes"] == {account.id: planned_hash}
    assert await session.get(RateAssignment, current_assignment.id) is None
    assert await session.get(RateAssignment, future_assignment.id) is not None
    current_assignments = list(
        await session.scalars(
            select(RateAssignment).where(
                RateAssignment.utility_account_id == account.id,
                RateAssignment.effective_from <= reset_at,
                RateAssignment.effective_to > reset_at,
                RateAssignment.cancelled_at.is_(None),
            )
        )
    )
    assert [item.id for item in current_assignments] == [baseline.rate_assignment_id]
    after_reset_pricing = await _active_pricing_snapshot(
        session,
        account,
        reset_at=reset_at,
    )
    assert after_reset_pricing is not None
    assert after_reset_pricing["rate_version_id"] == current_version.id
    assert after_reset_pricing["future_assignment_ids"] == [future_assignment.id]
    assert after_reset_pricing["pricing_configuration_hash"] == planned_hash

    cycle = await session.scalar(
        select(BillingCycle).where(BillingCycle.utility_account_id == account.id)
    )
    assert cycle is not None
    cycle_start = (
        cycle.starts_at.replace(tzinfo=UTC) if cycle.starts_at.tzinfo is None else cycle.starts_at
    )
    cycle_end = cycle.ends_at.replace(tzinfo=UTC) if cycle.ends_at.tzinfo is None else cycle.ends_at
    expected_cycle_end = expected_cycle_bounds(account, reset_at)[1]
    assert cycle_start == reset_at
    assert future_at < expected_cycle_end
    assert cycle_end == expected_cycle_end
    cycle_after_rate_change = await current_billing_cycle(
        session,
        account,
        future_at + timedelta(minutes=1),
        create=True,
    )
    assert cycle_after_rate_change.id == cycle.id
    assert (
        await session.scalar(
            select(func.count())
            .select_from(BillingCycle)
            .where(BillingCycle.utility_account_id == account.id)
        )
        == 1
    )
    rate_contexts = await _rate_contexts(session, account, cycle)
    assert [context[2].id for context in rate_contexts] == [
        current_version.id,
        future_version.id,
    ]
    assert rate_contexts[0][1] == future_at
    assert rate_contexts[1][0] == future_at
    assert rate_contexts[1][1] == expected_cycle_end
    assert cycle.recalculation_version == 0
    assert await session.scalar(select(func.count()).select_from(TierAllocationSegment)) == 0
    assert await session.scalar(select(func.count()).select_from(CostCalculationRun)) == 0
    assert await session.scalar(select(func.count()).select_from(CostIntervalResult)) == 0

    participant = await session.get(
        DataResetParticipant,
        (operation.id, device.id),
    )
    assert participant is not None
    interval_start = reset_at + timedelta(minutes=1)
    reading = _post_reset_reading(
        generation=operation.reset_generation,
        sequence=participant.reset_boundary + 1,
        interval_start=interval_start,
    )
    ingested = await ingest_readings(
        session,
        device_id=device.id,
        readings=[reading],
        source="push",
        data_generation=operation.reset_generation,
    )
    assert ingested.accepted == [reading.sequence]
    await session.commit()
    normalized = await session.scalar(
        select(NormalizedInterval).where(NormalizedInterval.device_id == device.id)
    )
    assert normalized is not None
    assert normalized.selected_energy_wh == Decimal("1000")
    assert normalized.validation_result == "accepted"

    waiting_for_cost = await process_data_reset_operations(session, test_settings)
    assert waiting_for_cost["state"] == "verification_running"
    await session.refresh(operation)
    assert operation.completed_at is None
    assert operation.final_evidence["new_readings_status"] == "confirmed"
    assert operation.final_evidence["new_cost_status"] == "pending"
    assert operation.final_evidence["required_cost_account_ids"] == [account.id]
    assert operation.final_evidence["queued_cost_account_ids"] == [account.id]
    cost_run = await session.scalar(
        select(CostCalculationRun).where(
            CostCalculationRun.utility_account_id == account.id,
            CostCalculationRun.aggregate_set_id == aggregate.id,
            CostCalculationRun.rate_version_id == current_version.id,
            CostCalculationRun.status == "queued",
        )
    )
    assert cost_run is not None

    assert await process_tier_recalculations(session) == 1
    await session.commit()
    await session.refresh(cycle)
    first_tier_segment = await session.scalar(
        select(TierAllocationSegment)
        .where(TierAllocationSegment.billing_cycle_id == cycle.id)
        .order_by(TierAllocationSegment.interval_start, TierAllocationSegment.segment_order)
    )
    assert first_tier_segment is not None
    assert cycle.recalculation_version == 1
    assert first_tier_segment.normalized_interval_id == normalized.id
    assert first_tier_segment.cumulative_start_kwh == Decimal("0")
    assert first_tier_segment.cumulative_end_kwh == Decimal("1")
    assert first_tier_segment.price_per_kwh == Decimal("0.30000000")
    assert first_tier_segment.unrounded_energy_charge == Decimal("0.300000000000")

    assert await process_cost_jobs(session) == 1
    await session.refresh(cost_run)
    assert cost_run.status == "completed"
    assert cost_run.coverage_percent == Decimal("100")
    first_cost = await session.scalar(
        select(CostIntervalResult)
        .where(
            CostIntervalResult.run_id == cost_run.id,
            CostIntervalResult.component == "energy",
        )
        .order_by(CostIntervalResult.interval_start, CostIntervalResult.id)
    )
    assert first_cost is not None
    assert first_cost.normalized_interval_id == normalized.id
    assert first_cost.energy_kwh == Decimal("1")
    assert first_cost.price_per_kwh == Decimal("0.30000000")
    assert first_cost.unrounded_cost == Decimal("0.300000000000")

    completed = await process_data_reset_operations(session, test_settings)
    assert completed["state"] == "completed"
    await session.refresh(operation)
    assert operation.completed_at is not None
    assert operation.final_evidence["new_cost_status"] == "confirmed"
    assert operation.final_evidence["new_cost_calculation_confirmed"] is True
    assert operation.final_evidence["new_cost_account_ids"] == [account.id]
    assert operation.final_evidence["required_cost_account_ids"] == [account.id]

    final_pricing = await _active_pricing_snapshot(
        session,
        account,
        reset_at=reset_at,
    )
    assert final_pricing is not None
    assert final_pricing["rate_version_id"] == current_version.id
    assert final_pricing["future_assignment_ids"] == [future_assignment.id]
    assert final_pricing["pricing_configuration_hash"] == planned_hash
