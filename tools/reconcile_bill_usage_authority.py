#!/usr/bin/env python3
"""Audit and repair legacy bill-derived usage authority.

Dry-run is the default. This tool never prints bill text, account numbers, or
private evidence. Run only after migration 20260730_0021.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.billing_sources import (  # noqa: E402
    REFERENCE_ONLY,
    SENSOR_AUTHORITY_TYPES,
    SENSOR_MEASUREMENTS,
    UNAVAILABLE,
)
from app.config import get_settings  # noqa: E402
from app.db.models import (  # noqa: E402
    AccountUsageAuthority,
    AuditEvent,
    BillingCycle,
    Device,
    ManualAccountUsage,
    UtilityAccount,
    UtilityBillCycleDraft,
    UtilityUsageImport,
)
from app.problem import ProblemError  # noqa: E402
from app.rates.tiered import (  # noqa: E402
    _authority_device_ids,
    _monitored_intervals,
    calculate_cycle_tier_status,
)

logger = structlog.get_logger(__name__)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile legacy utility-bill usage authority to sensors."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report only (default)")
    mode.add_argument("--apply", action="store_true", help="apply audited corrections")
    return parser.parse_args()


async def candidate_authority(
    session: Any,
    account: UtilityAccount,
    current: AccountUsageAuthority | None,
) -> tuple[str | None, list[str]]:
    devices = list(
        await session.scalars(
            select(Device)
            .where(
                Device.utility_account_id == account.id,
                Device.lifecycle_status == "active",
            )
            .order_by(Device.id)
        )
    )
    active_ids = {item.id for item in devices}
    if (
        current is not None
        and current.calculation_role == SENSOR_MEASUREMENTS
        and current.authority_type in SENSOR_AUTHORITY_TYPES
    ):
        if current.authority_type == "complete_site_aggregate":
            selected, _warnings = await _authority_device_ids(session, account, current)
            return current.authority_type, selected
        selected = [item for item in current.device_ids if item in active_ids]
        if selected:
            return current.authority_type, selected
    mains = [item.id for item in devices if item.measurement_role == "main"]
    if len(mains) == 1:
        return "whole_account_meter", mains
    legs = [item.id for item in devices if item.measurement_role == "service-leg"]
    if len(legs) == 2:
        return "service_leg_pair", legs
    return None, []


async def reconcile(*, apply: bool) -> dict[str, Any]:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    report: list[dict[str, Any]] = []
    try:
        async with factory() as session:
            cycles = list(
                await session.scalars(
                    select(BillingCycle)
                    .where(BillingCycle.legacy_bill_authority_review_required.is_(True))
                    .order_by(BillingCycle.utility_account_id, BillingCycle.starts_at)
                )
            )
            for cycle in cycles:
                account = await session.get(UtilityAccount, cycle.utility_account_id)
                if account is None:
                    continue
                draft = await session.scalar(
                    select(UtilityBillCycleDraft).where(
                        UtilityBillCycleDraft.billing_cycle_id == cycle.id
                    )
                )
                authority = await session.scalar(
                    select(AccountUsageAuthority).where(
                        AccountUsageAuthority.utility_account_id == account.id
                    )
                )
                manual_rows = list(
                    await session.scalars(
                        select(ManualAccountUsage).where(
                            ManualAccountUsage.billing_cycle_id == cycle.id,
                            ManualAccountUsage.calculation_role == REFERENCE_ONLY,
                        )
                    )
                )
                import_rows = list(
                    await session.scalars(
                        select(UtilityUsageImport).where(
                            UtilityUsageImport.utility_account_id == account.id,
                            UtilityUsageImport.calculation_role == REFERENCE_ONLY,
                            UtilityUsageImport.status == "committed",
                        )
                    )
                )
                authority_type, device_ids = await candidate_authority(
                    session, account, authority
                )
                intervals = await _monitored_intervals(
                    session,
                    device_ids=device_ids,
                    start=cycle.starts_at,
                    end=cycle.ends_at,
                )
                sensor_usage = sum((item.energy_kwh for item in intervals), start=0)
                finalized = (
                    cycle.finalized_at is not None or cycle.status == "finalized"
                )
                result: dict[str, Any] = {
                    "account_id": account.id,
                    "cycle_id": cycle.id,
                    "finalized": finalized,
                    "bill_reported_usage_kwh": (
                        str(draft.total_usage_kwh)
                        if draft and draft.total_usage_kwh is not None
                        else None
                    ),
                    "sensor_derived_usage_kwh": str(sensor_usage),
                    "current_authority": authority.authority_type
                    if authority
                    else None,
                    "current_calculation_role": (
                        authority.calculation_role if authority else None
                    ),
                    "proposed_authority": authority_type,
                    "proposed_device_count": len(device_ids),
                    "expected_recalculation": not finalized and bool(authority_type),
                    "manual_reference_rows": len(manual_rows),
                    "usage_import_reference_rows": len(import_rows),
                    "action": (
                        "review_only"
                        if finalized
                        else (
                            "recalculate_from_sensors"
                            if authority_type
                            else "configure_full_account_sensor"
                        )
                    ),
                }
                if apply:
                    now = datetime.now(UTC)
                    if finalized:
                        result["applied"] = False
                        result["reason"] = "finalized cycle preserved"
                    else:
                        for manual_item in manual_rows:
                            if manual_item.superseded_at is None:
                                manual_item.superseded_at = now
                                manual_item.verification_status = "reconciled"
                        for import_item in import_rows:
                            import_item.status = "reversed"
                            import_item.reversed_at = now
                        if authority_type:
                            aggregate_set_id = (
                                authority.aggregate_set_id
                                if authority is not None
                                and authority_type == "complete_site_aggregate"
                                else None
                            )
                            if authority is None:
                                authority = AccountUsageAuthority(
                                    utility_account_id=account.id,
                                    revision=1,
                                    updated_at=now,
                                )
                                session.add(authority)
                            else:
                                authority.revision += 1
                            authority.authority_type = authority_type
                            authority.calculation_role = SENSOR_MEASUREMENTS
                            authority.aggregate_set_id = aggregate_set_id
                            authority.device_ids = device_ids
                            authority.source_reference = (
                                "reconciliation:legacy-bill-authority-to-sensors"
                            )
                            authority.confidence = "high"
                            authority.complete_account = True
                            authority.updated_at = now
                        cycle.status = "recalculating"
                        cycle.usage_source_type = (
                            SENSOR_MEASUREMENTS if authority_type else UNAVAILABLE
                        )
                        cycle.projection_source_type = (
                            "sensor_trend" if authority_type else UNAVAILABLE
                        )
                        cycle.tier_progress_source_type = (
                            SENSOR_MEASUREMENTS if authority_type else UNAVAILABLE
                        )
                        cycle.recalculation_required = True
                        cycle.updated_at = now
                        await session.flush()
                        calculation: dict[str, Any] | None = None
                        if authority_type:
                            try:
                                calculation = await calculate_cycle_tier_status(
                                    session,
                                    account,
                                    cycle,
                                    persist=True,
                                    actor_id=None,
                                )
                            except ProblemError as exc:
                                result["recalculation_error"] = exc.code
                        session.add(
                            AuditEvent(
                                occurred_at=now,
                                actor_type="system",
                                actor_id=None,
                                action="billing.legacy_bill_authority_reconciled",
                                object_type="billing_cycle",
                                object_id=cycle.id,
                                source_ip=None,
                                outcome="success",
                                correlation_id=None,
                                details={
                                    "utility_account_id": account.id,
                                    "old_calculation_role": (
                                        result["current_calculation_role"]
                                    ),
                                    "new_calculation_role": (
                                        SENSOR_MEASUREMENTS
                                        if authority_type
                                        else "unavailable"
                                    ),
                                    "proposed_authority": authority_type,
                                    "sensor_count": len(device_ids),
                                    "sensor_usage_kwh": str(sensor_usage),
                                    "bill_usage_retained_reference_only": True,
                                    "calculation_available": (
                                        calculation.get("available")
                                        if calculation is not None
                                        else False
                                    ),
                                },
                            )
                        )
                        logger.info(
                            "billing.legacy_bill_authority_reconciled",
                            account_id=account.id,
                            cycle_id=cycle.id,
                            finalized=False,
                            new_calculation_role=(
                                SENSOR_MEASUREMENTS if authority_type else UNAVAILABLE
                            ),
                            sensor_count=len(device_ids),
                            sensor_usage_kwh=str(sensor_usage),
                        )
                        result["applied"] = True
                report.append(result)
            if apply:
                await session.commit()
            else:
                await session.rollback()
    finally:
        await engine.dispose()
    return {
        "mode": "apply" if apply else "dry-run",
        "affected_cycle_count": len(report),
        "cycles": report,
    }


def main() -> None:
    args = arguments()
    print(
        json.dumps(asyncio.run(reconcile(apply=args.apply)), indent=2, sort_keys=True)
    )


if __name__ == "__main__":
    main()
