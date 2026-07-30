from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import smtplib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
import structlog
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    AccountUsageAuthority,
    AggregateMember,
    AggregateSet,
    AlertInstance,
    AlertRule,
    BaselineRule,
    BillingCycle,
    CostCalculationRun,
    CostIntervalResult,
    DailyDeviceRollup,
    Device,
    DeviceHeartbeat,
    ExportJob,
    FixedChargeRule,
    GeneratedReport,
    ManualBillAdjustment,
    MonthlyDeviceRollup,
    NormalizedInterval,
    NotificationAttempt,
    NotificationChannel,
    RateAdjustment,
    RatePeriod,
    RateVersion,
    RawReading,
    ReportDefinition,
    SequenceGap,
    Site,
    SiteRollup,
    TierAllocationSegment,
    UtilityAccount,
    UtilityAccountAdjustment,
    WorkerState,
)
from app.ingestion.service import normalize_energy
from app.polling.ssrf import validate_poll_target
from app.rates.documents import engine_plan
from app.rates.engine import RateEngine
from app.rates.service import version_document
from app.rates.tiered import calculate_cycle_tier_status, current_billing_cycle
from app.schemas import Reading
from app.security.protocol import SecretCipher

logger = structlog.get_logger(__name__)


def _csv_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _reading_from_raw(raw: RawReading) -> Reading:
    if raw.original_payload:
        return Reading.model_validate(raw.original_payload)
    return Reading(
        sequence=raw.sequence,
        boot_id=raw.boot_id,
        interval_start=_aware(raw.interval_start),
        interval_end=_aware(raw.interval_end),
        time_trusted=raw.time_trusted,
        voltage_avg=raw.voltage_avg,
        voltage_min=raw.voltage_min,
        voltage_max=raw.voltage_max,
        current_avg=raw.current_avg,
        current_min=raw.current_min,
        current_max=raw.current_max,
        power_avg=raw.power_avg,
        power_min=raw.power_min,
        power_max=raw.power_max,
        power_factor=raw.power_factor,
        frequency_hz=raw.frequency_hz,
        pzem_energy_start_wh=raw.pzem_energy_start_wh,
        pzem_energy_end_wh=raw.pzem_energy_end_wh,
        device_lifetime_energy_wh=raw.device_lifetime_energy_wh,
        interval_energy_wh=raw.device_interval_energy_wh,
        energy_method=raw.energy_method,
        ct_rating_amps=raw.ct_rating_amps,
        quality_flags=raw.quality_flags or [],
        firmware_version=raw.firmware_version,
        record_hash=raw.record_hash,
    )


async def reconcile_missing_normalized_intervals(
    session: AsyncSession, limit: int = 500
) -> dict[str, int]:
    rows = list(
        await session.scalars(
            select(RawReading)
            .where(
                ~select(NormalizedInterval.id)
                .where(NormalizedInterval.raw_reading_id == RawReading.id)
                .exists()
            )
            .order_by(RawReading.ingested_at, RawReading.device_id, RawReading.sequence)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    if rows:
        logger.info(
            "history.normalization_queued",
            record_count=len(rows),
            device_count=len({row.device_id for row in rows}),
            first_sequence=min(row.sequence for row in rows),
            last_sequence=max(row.sequence for row in rows),
        )
    completed = 0
    failed = 0
    for raw in rows:
        try:
            reading = _reading_from_raw(raw)
            selected = normalize_energy(reading)
            session.add(
                NormalizedInterval(
                    raw_reading_id=raw.id,
                    device_id=raw.device_id,
                    interval_start=raw.interval_start,
                    interval_end=raw.interval_end,
                    device_energy_wh=selected.device_energy_wh,
                    server_energy_wh=selected.server_energy_wh,
                    selected_energy_wh=selected.selected_energy_wh,
                    selected_method=selected.selected_method,
                    validation_result=selected.validation_result,
                    validation_reason=selected.validation_reason,
                )
            )
            await session.flush()
            completed += 1
        except (ValidationError, ValueError, TypeError) as exc:
            failed += 1
            logger.warning(
                "history.normalization_failed",
                device_id=raw.device_id,
                sequence=raw.sequence,
                error_type=type(exc).__name__,
            )
    if rows:
        logger.info(
            "history.normalization_completed",
            queued_count=len(rows),
            normalized_interval_count=completed,
            failed_count=failed,
        )
    return {"queued": len(rows), "completed": completed, "failed": failed}


def _window_fraction(
    window_start: datetime,
    window_end: datetime,
    effective_from: datetime,
    effective_to: datetime | None,
) -> Decimal:
    start = max(_aware(window_start), _aware(effective_from))
    end = (
        min(_aware(window_end), _aware(effective_to))
        if effective_to
        else _aware(window_end)
    )
    if end <= start:
        return Decimal("0")
    total_seconds = Decimal(
        str((_aware(window_end) - _aware(window_start)).total_seconds())
    )
    return Decimal(str((end - start).total_seconds())) / total_seconds


def _effective_interval_energy_kwh(
    intervals: list[NormalizedInterval],
    effective_from: datetime,
    effective_to: datetime | None,
) -> Decimal:
    energy = Decimal("0")
    for interval in intervals:
        fraction = _window_fraction(
            interval.interval_start,
            interval.interval_end,
            effective_from,
            effective_to,
        )
        energy += (
            (interval.selected_energy_wh or Decimal("0")) / Decimal("1000") * fraction
        )
    return energy


async def _calculation_plan(
    session: AsyncSession, version: RateVersion
) -> dict[str, Any]:
    if version.normalized_payload:
        return engine_plan(await version_document(session, version))
    periods = list(
        await session.scalars(
            select(RatePeriod)
            .where(RatePeriod.rate_version_id == version.id)
            .order_by(
                RatePeriod.season_name, RatePeriod.day_type, RatePeriod.start_minute
            )
        )
    )
    grouped: dict[str, dict[str, list[list[Any]]]] = {}
    for period in periods:
        grouped.setdefault(period.season_name, {}).setdefault(
            period.day_type, []
        ).append(
            [
                period.start_minute,
                period.end_minute,
                period.bucket,
                str(period.price_per_kwh),
            ]
        )
    fixed = await session.scalar(
        select(FixedChargeRule)
        .where(FixedChargeRule.rate_version_id == version.id)
        .limit(1)
    )
    baseline = await session.scalar(
        select(BaselineRule).where(BaselineRule.rate_version_id == version.id).limit(1)
    )
    return {
        "timezone": version.timezone,
        "periods": grouped,
        "base_service_charge_per_day": str(fixed.amount_per_day if fixed else 0),
        "baseline_credit_per_kwh": str(baseline.credit_per_kwh) if baseline else None,
    }


async def process_cost_jobs(session: AsyncSession, limit: int = 2) -> int:
    jobs = list(
        await session.scalars(
            select(CostCalculationRun)
            .where(CostCalculationRun.status == "queued")
            .order_by(CostCalculationRun.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    completed = 0
    for job in jobs:
        job.status = "running"
        await session.flush()
        account = await session.get(UtilityAccount, job.utility_account_id)
        aggregate = await session.get(AggregateSet, job.aggregate_set_id)
        version = await session.get(RateVersion, job.rate_version_id)
        if account is None or aggregate is None or version is None:
            job.status = "failed"
            continue
        members = list(
            await session.scalars(
                select(AggregateMember).where(
                    AggregateMember.aggregate_set_id == aggregate.id
                )
            )
        )
        device_ids = {member.device_id for member in members if member.device_id}
        circuit_ids = {member.circuit_id for member in members if member.circuit_id}
        if circuit_ids:
            device_ids.update(
                await session.scalars(
                    select(Device.id).where(
                        Device.site_id == aggregate.site_id,
                        Device.circuit_id.in_(circuit_ids),
                    )
                )
            )
        intervals = (
            list(
                await session.scalars(
                    select(NormalizedInterval)
                    .where(
                        NormalizedInterval.device_id.in_(device_ids),
                        NormalizedInterval.interval_start >= job.input_start,
                        NormalizedInterval.interval_end <= job.input_end,
                        NormalizedInterval.selected_energy_wh.is_not(None),
                    )
                    .order_by(NormalizedInterval.interval_start)
                )
            )
            if device_ids
            else []
        )
        engine = RateEngine(await _calculation_plan(session, version))
        tier_segments = (
            list(
                (
                    await session.scalars(
                        select(TierAllocationSegment)
                        .join(
                            BillingCycle,
                            BillingCycle.id == TierAllocationSegment.billing_cycle_id,
                        )
                        .where(
                            TierAllocationSegment.utility_account_id == account.id,
                            TierAllocationSegment.rate_version_id == version.id,
                            TierAllocationSegment.interval_start < job.input_end,
                            TierAllocationSegment.interval_end > job.input_start,
                            TierAllocationSegment.recalculation_version
                            == BillingCycle.recalculation_version,
                        )
                        .order_by(
                            TierAllocationSegment.interval_start,
                            TierAllocationSegment.segment_order,
                        )
                    )
                )
            )
            if version.pricing_model in {"tiered", "time_of_use_tiered"}
            else []
        )
        total_energy_kwh = Decimal("0")
        tiered_energy_charge = Decimal("0")
        covered_seconds = Decimal("0")
        tier_cost_missing = False
        for interval in intervals:
            energy_kwh = (interval.selected_energy_wh or Decimal("0")) / Decimal("1000")
            total_energy_kwh += energy_kwh
            covered_seconds += Decimal(
                str((interval.interval_end - interval.interval_start).total_seconds())
            )
            if version.pricing_model in {"tiered", "time_of_use_tiered"}:
                matching = [
                    segment
                    for segment in tier_segments
                    if segment.normalized_interval_id == interval.id
                ]
                exact = bool(matching)
                if not matching:
                    matching = [
                        segment
                        for segment in tier_segments
                        if segment.interval_start < interval.interval_end
                        and segment.interval_end > interval.interval_start
                    ]
                weighted_seconds = sum(
                    (
                        Decimal(
                            str(
                                (
                                    min(interval.interval_end, segment.interval_end)
                                    - max(
                                        interval.interval_start, segment.interval_start
                                    )
                                ).total_seconds()
                            )
                        )
                        for segment in matching
                    ),
                    Decimal("0"),
                )
                if not matching or weighted_seconds <= 0:
                    tier_cost_missing = True
                    break
                for segment in matching:
                    overlap_start = max(interval.interval_start, segment.interval_start)
                    overlap_end = min(interval.interval_end, segment.interval_end)
                    overlap_seconds = Decimal(
                        str((overlap_end - overlap_start).total_seconds())
                    )
                    segment_seconds = Decimal(
                        str(
                            (
                                segment.interval_end - segment.interval_start
                            ).total_seconds()
                        )
                    )
                    if overlap_seconds <= 0 or segment_seconds <= 0:
                        continue
                    if exact:
                        fraction = overlap_seconds / segment_seconds
                        allocated_energy = segment.segment_energy_kwh * fraction
                        allocated_cost = segment.unrounded_energy_charge * fraction
                    else:
                        allocated_energy = (
                            energy_kwh * overlap_seconds / weighted_seconds
                        )
                        allocated_cost = allocated_energy * segment.price_per_kwh
                    tiered_energy_charge += allocated_cost
                    session.add(
                        CostIntervalResult(
                            run_id=job.id,
                            normalized_interval_id=interval.id,
                            interval_start=overlap_start,
                            interval_end=overlap_end,
                            bucket=(
                                f"{segment.tier_name} / {segment.tou_period}"
                                if segment.tou_period
                                else segment.tier_name
                            ),
                            energy_kwh=allocated_energy,
                            price_per_kwh=segment.price_per_kwh,
                            unrounded_cost=allocated_cost,
                            component="energy",
                            adjustment_breakdown={
                                "tier_id": segment.tier_stable_id,
                                "billing_cycle_id": segment.billing_cycle_id,
                                "recalculation_version": segment.recalculation_version,
                                "usage_authority_type": segment.usage_authority_type,
                            },
                            calculation_version=engine.algorithm_version,
                        )
                    )
                continue
            calculation = engine.calculate(
                start=interval.interval_start,
                end=interval.interval_end,
                energy_kwh=energy_kwh,
                cost_scope="energy_only",
            )
            for item in calculation.slices:
                session.add(
                    CostIntervalResult(
                        run_id=job.id,
                        normalized_interval_id=interval.id,
                        interval_start=item.start,
                        interval_end=item.end,
                        bucket=item.bucket,
                        energy_kwh=item.energy_kwh,
                        price_per_kwh=item.price_per_kwh,
                        unrounded_cost=item.cost,
                        component="energy",
                        adjustment_breakdown={},
                        calculation_version=engine.algorithm_version,
                    )
                )
        if tier_cost_missing:
            job.status = "failed"
            job.completed_at = datetime.now(UTC)
            continue
        adjustments = list(
            await session.scalars(
                select(RateAdjustment).where(
                    RateAdjustment.rate_version_id == version.id
                )
            )
        )
        cca_per_kwh = (
            Decimal("0")
            if version.normalized_payload
            else sum(
                (
                    item.amount
                    for item in adjustments
                    if item.component
                    in {"cca_generation", "cca", "direct_access", "generation_provider"}
                    and (item.operation == "per_kwh" or item.unit == "per_kwh")
                ),
                Decimal("0"),
            )
        )
        manual_total = await session.scalar(
            select(func.coalesce(func.sum(ManualBillAdjustment.amount), 0)).where(
                ManualBillAdjustment.utility_account_id == account.id,
                ManualBillAdjustment.created_at >= job.input_start,
                ManualBillAdjustment.created_at < job.input_end,
            )
        )
        effective_scope: Literal[
            "energy_only",
            "allocated_account_estimate",
            "full_account_estimate",
        ] = "energy_only"
        if (
            account.cost_scope_default == "allocated_account_estimate"
            and aggregate.cost_scope
            in {
                "allocated_account",
                "full_account",
            }
        ):
            effective_scope = "allocated_account_estimate"
        elif (
            account.cost_scope_default == "full_account_estimate"
            and aggregate.cost_scope == "full_account"
        ):
            effective_scope = "full_account_estimate"
        configured_adjustments = list(
            await session.scalars(
                select(UtilityAccountAdjustment).where(
                    UtilityAccountAdjustment.utility_account_id == account.id,
                    UtilityAccountAdjustment.enabled.is_(True),
                    UtilityAccountAdjustment.effective_from < job.input_end,
                    or_(
                        UtilityAccountAdjustment.effective_to.is_(None),
                        UtilityAccountAdjustment.effective_to > job.input_start,
                    ),
                )
            )
        )
        account_energy_amount = Decimal("0")
        account_fixed = (
            Decimal(str(manual_total or 0))
            if effective_scope == "full_account_estimate"
            else Decimal("0")
        )
        account_percent: list[tuple[Decimal, Decimal]] = []
        configured_breakdown: dict[str, str] = {}

        def add_breakdown(component: str, amount: Decimal) -> None:
            prior = Decimal(configured_breakdown.get(component, "0"))
            configured_breakdown[component] = str(prior + amount)

        for configured in configured_adjustments:
            effective_energy = _effective_interval_energy_kwh(
                intervals, configured.effective_from, configured.effective_to
            )
            effective_fraction = _window_fraction(
                job.input_start,
                job.input_end,
                configured.effective_from,
                configured.effective_to,
            )
            if (
                configured.component
                in {
                    "cca_generation",
                    "direct_access",
                    "custom_per_kwh",
                }
                and configured.unit == "per_kwh"
            ):
                amount = effective_energy * configured.value
                account_energy_amount += amount
                add_breakdown(configured.component, amount)
            elif effective_scope != "energy_only" and configured.unit == "percent":
                account_percent.append((configured.value, effective_fraction))
            elif (
                effective_scope == "full_account_estimate"
                and configured.unit == "per_kwh"
            ):
                amount = effective_energy * configured.value
                if configured.component == "baseline_credit":
                    amount = -abs(amount)
                account_fixed += amount
                add_breakdown(configured.component, amount)
            elif (
                effective_scope == "full_account_estimate"
                and configured.unit == "fixed"
            ):
                amount = configured.value * effective_fraction
                if configured.component == "baseline_credit":
                    amount = -abs(amount)
                account_fixed += amount
                add_breakdown(configured.component, amount)
        account_energy_rate = (
            account_energy_amount / total_energy_kwh
            if total_energy_kwh
            else Decimal("0")
        )
        preliminary = engine.calculate(
            start=job.input_start,
            end=job.input_end,
            energy_kwh=total_energy_kwh,
            cost_scope=effective_scope,
            baseline_allocation_kwh=account.baseline_allocation_kwh,
            cca_adjustment_per_kwh=cca_per_kwh + account_energy_rate,
            other_adjustment=account_fixed,
        )
        preliminary_total = preliminary.total
        if version.pricing_model in {"tiered", "time_of_use_tiered"}:
            preliminary_total = (
                preliminary.total - preliminary.energy_charge + tiered_energy_charge
            )
        percentage_amount = sum(
            (
                preliminary_total * percent / Decimal("100") * effective_fraction
                for percent, effective_fraction in account_percent
            ),
            Decimal("0"),
        )
        if percentage_amount:
            add_breakdown("tax_fee", percentage_amount)
        account_components = engine.calculate(
            start=job.input_start,
            end=job.input_end,
            energy_kwh=total_energy_kwh,
            cost_scope=effective_scope,
            baseline_allocation_kwh=account.baseline_allocation_kwh,
            cca_adjustment_per_kwh=cca_per_kwh + account_energy_rate,
            other_adjustment=account_fixed + percentage_amount,
        )
        for component, amount in (
            ("fixed_charge", account_components.fixed_charge),
            ("baseline_credit", -account_components.baseline_credit),
            ("cca_adjustment", account_components.cca_adjustment),
            ("manual_adjustment", account_components.other_adjustment),
        ):
            if amount:
                session.add(
                    CostIntervalResult(
                        run_id=job.id,
                        normalized_interval_id=None,
                        interval_start=job.input_start,
                        interval_end=job.input_end,
                        bucket="account",
                        energy_kwh=Decimal("0"),
                        price_per_kwh=Decimal("0"),
                        unrounded_cost=amount,
                        component=component,
                        adjustment_breakdown={
                            **{
                                key: str(value)
                                for key, value in account_components.adjustment_breakdown.items()
                            },
                            **configured_breakdown,
                        }
                        or {component: str(amount)},
                        calculation_version=engine.algorithm_version,
                    )
                )
        expected_seconds = Decimal(
            str((job.input_end - job.input_start).total_seconds())
        )
        job.coverage_percent = min(
            Decimal("100"), covered_seconds / expected_seconds * Decimal("100")
        )
        job.status = "completed"
        job.completed_at = datetime.now(UTC)
        version.immutable_after_use = True
        completed += 1
    await session.commit()
    return completed


async def process_tier_recalculations(session: AsyncSession, limit: int = 4) -> int:
    configured_accounts = list(
        await session.scalars(
            select(UtilityAccount)
            .join(
                AccountUsageAuthority,
                AccountUsageAuthority.utility_account_id == UtilityAccount.id,
            )
            .where(UtilityAccount.status == "active")
        )
    )
    for configured_account in configured_accounts:
        cycle = await current_billing_cycle(
            session,
            configured_account,
            datetime.now(UTC),
            create=True,
        )
        if cycle.recalculation_version == 0 and cycle.finalized_at is None:
            cycle.status = "recalculating"
    await session.flush()
    cycles = list(
        await session.scalars(
            select(BillingCycle)
            .where(
                BillingCycle.status == "recalculating",
                BillingCycle.finalized_at.is_(None),
            )
            .order_by(BillingCycle.updated_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    completed = 0
    for cycle in cycles:
        account = await session.get(UtilityAccount, cycle.utility_account_id)
        if account is None:
            cycle.status = "expected"
            continue
        result = await calculate_cycle_tier_status(
            session,
            account,
            cycle,
            persist=True,
        )
        if not result["available"]:
            cycle.status = "confirmed" if cycle.explicit_meter_dates else "expected"
            cycle.updated_at = datetime.now(UTC)
        completed += 1
    return completed


async def process_export_jobs(
    session: AsyncSession, settings: Settings, limit: int = 4
) -> int:
    jobs = list(
        await session.scalars(
            select(ExportJob)
            .where(ExportJob.status == "queued")
            .order_by(ExportJob.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    root = settings.report_path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    completed = 0
    for job in jobs:
        job.status = "running"
        await session.flush()
        query = select(RawReading).order_by(RawReading.device_id, RawReading.sequence)
        device_id = job.query.get("device_id")
        if device_id:
            query = query.where(RawReading.device_id == device_id)
        start = job.query.get("start")
        end = job.query.get("end")
        if start:
            query = query.where(
                RawReading.interval_start >= datetime.fromisoformat(start)
            )
        if end:
            query = query.where(RawReading.interval_start < datetime.fromisoformat(end))
        rows = list(await session.scalars(query.limit(1_000_000)))
        path = (root / f"export-{job.id}.{job.format}").resolve()
        if root not in path.parents:
            raise RuntimeError("unsafe export path")
        if job.format == "csv":
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "device_id",
                        "sequence",
                        "interval_start_utc",
                        "interval_end_utc",
                        "power_avg_w",
                        "interval_energy_wh",
                        "voltage_avg_v",
                        "current_avg_a",
                        "power_factor",
                        "frequency_hz",
                        "quality_flags",
                    ]
                )
                for row in rows:
                    writer.writerow(
                        [
                            _csv_safe(row.device_id),
                            row.sequence,
                            row.interval_start.isoformat(),
                            row.interval_end.isoformat(),
                            row.power_avg,
                            row.device_interval_energy_wh,
                            row.voltage_avg,
                            row.current_avg,
                            row.power_factor,
                            row.frequency_hz,
                            "|".join(row.quality_flags),
                        ]
                    )
        else:
            with path.open("w", encoding="utf-8") as handle:
                handle.write("[")
                for index, row in enumerate(rows):
                    if index:
                        handle.write(",")
                    handle.write(
                        json.dumps(
                            {
                                "device_id": row.device_id,
                                "sequence": row.sequence,
                                "interval_start": row.interval_start.isoformat(),
                                "interval_end": row.interval_end.isoformat(),
                                "power_avg_w": str(row.power_avg)
                                if row.power_avg is not None
                                else None,
                                "interval_energy_wh": (
                                    str(row.device_interval_energy_wh)
                                    if row.device_interval_energy_wh is not None
                                    else None
                                ),
                                "quality_flags": row.quality_flags,
                            },
                            separators=(",", ":"),
                        )
                    )
                handle.write("]")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        job.file_path = str(path)
        job.content_hash = digest
        job.status = "completed"
        completed += 1
    await session.commit()
    return completed


async def process_report_jobs(
    session: AsyncSession, settings: Settings, limit: int = 2
) -> int:
    jobs = list(
        await session.scalars(
            select(GeneratedReport)
            .where(GeneratedReport.status == "queued")
            .order_by(GeneratedReport.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    root = settings.report_path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    completed = 0
    disclosure = (
        "Estimate, not utility bill. Results depend on monitored coverage, configured rates, "
        "meter accuracy, missing circuits, provider adjustments, taxes, credits, rounding, "
        "tariff changes, and billing adjustments."
    )
    for job in jobs:
        job.status = "running"
        await session.flush()
        definition = (
            await session.get(ReportDefinition, job.definition_id)
            if job.definition_id
            else None
        )
        if definition is None:
            job.status = "failed"
            job.data_coverage = {"error": "definition_missing"}
            continue
        config = definition.configuration
        query = select(NormalizedInterval).order_by(NormalizedInterval.interval_start)
        start = (
            datetime.fromisoformat(str(config["start"]))
            if config.get("start")
            else None
        )
        end = datetime.fromisoformat(str(config["end"])) if config.get("end") else None
        if start:
            query = query.where(NormalizedInterval.interval_start >= start)
        if end:
            query = query.where(NormalizedInterval.interval_start < end)
        if config.get("device_id"):
            query = query.where(
                NormalizedInterval.device_id == str(config["device_id"])
            )
        if config.get("site_id"):
            site_device_ids = select(Device.id).where(
                Device.site_id == str(config["site_id"])
            )
            query = query.where(NormalizedInterval.device_id.in_(site_device_ids))
        rows = list(await session.scalars(query.limit(1_000_000)))
        energy_wh = sum(
            (row.selected_energy_wh or Decimal("0") for row in rows), Decimal("0")
        )
        covered_seconds = sum(
            (
                Decimal(str((row.interval_end - row.interval_start).total_seconds()))
                for row in rows
            ),
            Decimal("0"),
        )
        observed_start = min((row.interval_start for row in rows), default=start)
        observed_end = max((row.interval_end for row in rows), default=end)
        expected_seconds = (
            Decimal(str((end - start).total_seconds()))
            if start and end
            else covered_seconds
        )
        coverage = (
            min(Decimal("100"), covered_seconds / expected_seconds * Decimal("100"))
            if expected_seconds
            else Decimal("0")
        )
        cost_runs = list(
            await session.scalars(
                select(CostCalculationRun)
                .where(CostCalculationRun.status == "completed")
                .order_by(CostCalculationRun.completed_at.desc())
                .limit(100)
            )
        )
        quality_flags = sorted(
            {
                row.validation_result
                for row in rows
                if row.validation_result not in {"valid", "accepted"}
            }
        )
        report: dict[str, Any] = {
            "schema_version": "power-monitor-report/1.0.0",
            "report_id": job.id,
            "name": definition.name,
            "report_type": definition.report_type,
            "generated_at": datetime.now(UTC).isoformat(),
            "selection": config,
            "summary": {
                "energy_kwh": str(energy_wh / Decimal("1000")),
                "interval_count": len(rows),
                "observed_start": observed_start.isoformat()
                if observed_start
                else None,
                "observed_end": observed_end.isoformat() if observed_end else None,
                "coverage_percent": str(coverage),
            },
            "calculation": {
                "normalization_version": "energy-normalizer/1",
                "cost_algorithm_version": "sce-rate-engine/1",
                "rate_version_ids": sorted({run.rate_version_id for run in cost_runs}),
            },
            "quality": {
                "estimated": True,
                "quality_flags": quality_flags,
                "disclosure": disclosure,
            },
        }
        path = (root / f"report-{job.id}.json").resolve()
        if root not in path.parents:
            raise RuntimeError("unsafe report path")
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        job.file_path = str(path)
        job.status = "completed"
        job.data_coverage = {
            "coverage_percent": str(coverage),
            "interval_count": len(rows),
            "quality_flags": quality_flags,
        }
        completed += 1
    await session.commit()
    return completed


def _send_smtp(config: dict[str, Any], subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = str(config["from"])
    recipients = [str(value) for value in config["recipients"]]
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    host = str(config["host"])
    port = int(config["port"])
    if config.get("implicit_tls"):
        client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=10)
    else:
        client = smtplib.SMTP(host, port, timeout=10)
    with client:
        if config.get("starttls", True) and not config.get("implicit_tls"):
            client.starttls()
        if config.get("username"):
            client.login(str(config["username"]), str(config.get("password", "")))
        client.send_message(message, to_addrs=recipients)


async def _deliver_notification(
    channel: NotificationChannel,
    config: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    if channel.channel_type == "in_app":
        return "recorded in application"
    if channel.channel_type == "https_webhook":
        parsed = urlsplit(str(config["url"]))
        port = parsed.port or 443
        await validate_poll_target(
            host=str(parsed.hostname),
            port=port,
            scheme="https",
            allowed_cidrs=[str(item) for item in config.get("allowed_cidrs", [])],
            allowed_domains=[str(parsed.hostname)],
            allowed_ports=(port,),
            allow_public=bool(config.get("allow_public", True)),
        )
        headers = {
            str(key): str(value)
            for key, value in dict(config.get("headers", {})).items()
        }
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                str(config["url"]), json=payload, headers=headers
            )
            response.raise_for_status()
        return f"HTTPS {response.status_code}"
    host = str(config["host"])
    port = int(config["port"])
    await validate_poll_target(
        host=host,
        port=port,
        scheme="https",
        allowed_cidrs=[str(item) for item in config.get("allowed_cidrs", [])],
        allowed_domains=[host],
        allowed_ports=(port,),
        allow_public=bool(config.get("allow_public", True)),
    )
    subject = str(payload["title"])
    body = json.dumps(payload, indent=2)
    await asyncio.to_thread(_send_smtp, config, subject, body)
    return "SMTP accepted"


async def process_notification_jobs(
    session: AsyncSession, settings: Settings, limit: int = 20
) -> dict[str, int]:
    now = datetime.now(UTC)
    cipher = SecretCipher(settings.app_master_key)
    channels = list(
        await session.scalars(
            select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
        )
    )
    alerts = list(
        await session.scalars(
            select(AlertInstance).where(
                AlertInstance.status.in_(["active", "acknowledged"]),
                (AlertInstance.silenced_until.is_(None))
                | (AlertInstance.silenced_until <= now),
            )
        )
    )
    rule_types = {
        rule.id: rule.rule_type for rule in await session.scalars(select(AlertRule))
    }
    channel_configs: dict[str, dict[str, Any]] = {}
    for channel in channels:
        try:
            channel_configs[channel.id] = json.loads(
                cipher.decrypt(channel.encrypted_config)
            )
        except (RuntimeError, ValueError, json.JSONDecodeError):
            channel_configs[channel.id] = {}
    for alert in alerts:
        rule_type = rule_types.get(alert.rule_id)
        for channel in channels:
            event_types = channel_configs[channel.id].get("event_types", [])
            if event_types and rule_type not in event_types:
                continue
            existing = await session.scalar(
                select(NotificationAttempt.id).where(
                    NotificationAttempt.alert_instance_id == alert.id,
                    NotificationAttempt.channel_id == channel.id,
                )
            )
            if existing is None:
                session.add(
                    NotificationAttempt(
                        alert_instance_id=alert.id,
                        channel_id=channel.id,
                        attempted_at=now,
                        status="queued",
                        attempt_number=0,
                        response_summary=None,
                        next_attempt_at=now,
                        is_test=False,
                    )
                )
    await session.flush()
    attempts = list(
        await session.scalars(
            select(NotificationAttempt)
            .where(
                NotificationAttempt.status == "queued",
                NotificationAttempt.next_attempt_at <= now,
            )
            .order_by(NotificationAttempt.next_attempt_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    delivered = 0
    failed = 0
    for attempt in attempts:
        current_channel = await session.get(NotificationChannel, attempt.channel_id)
        current_alert = (
            await session.get(AlertInstance, attempt.alert_instance_id)
            if attempt.alert_instance_id
            else None
        )
        if current_channel is None or not current_channel.enabled:
            attempt.status = "cancelled"
            attempt.response_summary = "channel unavailable"
            continue
        try:
            config = json.loads(cipher.decrypt(current_channel.encrypted_config))
            current_rule = (
                await session.get(AlertRule, current_alert.rule_id)
                if current_alert
                else None
            )
            payload = {
                "schema_version": "power-monitor-notification/1.0.0",
                "title": "Power Monitor notification test"
                if attempt.is_test
                else f"Power Monitor: {current_rule.name if current_rule else 'alert'}",
                "test": attempt.is_test,
                "alert_id": current_alert.id if current_alert else None,
                "alert_name": current_rule.name if current_rule else None,
                "event_type": current_rule.rule_type if current_rule else None,
                "status": current_alert.status if current_alert else "test",
                "severity": current_alert.severity if current_alert else "info",
                "site_id": current_alert.site_id if current_alert else None,
                "device_id": current_alert.device_id if current_alert else None,
                "evidence": current_alert.evidence if current_alert else {},
                "occurred_at": (
                    current_alert.opened_at if current_alert else now
                ).isoformat(),
            }
            attempt.response_summary = await _deliver_notification(
                current_channel, config, payload
            )
            attempt.status = "delivered"
            attempt.attempted_at = datetime.now(UTC)
            attempt.next_attempt_at = None
            delivered += 1
        except Exception as exc:
            attempt.status = "failed"
            attempt.attempted_at = datetime.now(UTC)
            attempt.response_summary = f"delivery failed ({type(exc).__name__})"
            attempt.next_attempt_at = None
            failed += 1
            if attempt.attempt_number < 4:
                delays = (60, 300, 900, 3600, 14400)
                session.add(
                    NotificationAttempt(
                        alert_instance_id=attempt.alert_instance_id,
                        channel_id=attempt.channel_id,
                        attempted_at=now,
                        status="queued",
                        attempt_number=attempt.attempt_number + 1,
                        response_summary=None,
                        next_attempt_at=now
                        + timedelta(seconds=delays[attempt.attempt_number]),
                        is_test=attempt.is_test,
                    )
                )
    await session.commit()
    return {"delivered": delivered, "failed": failed, "processed": len(attempts)}


async def evaluate_alerts(session: AsyncSession, settings: Settings) -> dict[str, int]:
    now = datetime.now(UTC)
    devices = list(
        await session.scalars(select(Device).where(Device.revoked_at.is_(None)))
    )
    rules: dict[str, list[AlertRule]] = {}
    for rule in await session.scalars(
        select(AlertRule).where(AlertRule.enabled.is_(True))
    ):
        rules.setdefault(rule.rule_type, []).append(rule)
    opened = 0
    resolved = 0

    async def set_condition(
        device: Device, rule: AlertRule, active: bool, evidence: dict[str, Any]
    ) -> None:
        nonlocal opened, resolved
        instance = await session.scalar(
            select(AlertInstance).where(
                AlertInstance.rule_id == rule.id,
                AlertInstance.device_id == device.id,
                AlertInstance.status.in_(
                    ["debouncing", "active", "acknowledged", "resolving"]
                ),
            )
        )
        if active and instance is None:
            session.add(
                AlertInstance(
                    rule_id=rule.id,
                    device_id=device.id,
                    site_id=device.site_id,
                    status="debouncing" if rule.debounce_seconds else "active",
                    severity=rule.severity,
                    opened_at=now,
                    evidence=evidence,
                )
            )
            if not rule.debounce_seconds:
                opened += 1
        elif active and instance is not None:
            if instance.status == "debouncing":
                opened_at = instance.opened_at
                if opened_at.tzinfo is None:
                    opened_at = opened_at.replace(tzinfo=UTC)
                if opened_at <= now - timedelta(seconds=rule.debounce_seconds):
                    instance.status = "active"
                    instance.evidence = evidence
                    opened += 1
            elif instance.status == "resolving":
                prior = instance.evidence.get("status_before_resolve", "active")
                instance.status = (
                    str(prior) if prior in {"active", "acknowledged"} else "active"
                )
                instance.evidence = evidence
        elif not active and instance is not None:
            if instance.status == "debouncing":
                await session.delete(instance)
            elif not rule.resolve_seconds:
                instance.status = "resolved"
                instance.resolved_at = now
                resolved += 1
            elif instance.status != "resolving":
                instance.evidence = {
                    **instance.evidence,
                    "resolve_observed_at": now.isoformat(),
                    "status_before_resolve": instance.status,
                }
                instance.status = "resolving"
            else:
                observed_text = instance.evidence.get("resolve_observed_at")
                observed = datetime.fromisoformat(str(observed_text))
                if observed <= now - timedelta(seconds=rule.resolve_seconds):
                    instance.status = "resolved"
                    instance.resolved_at = now
                    resolved += 1

    async def set_system_condition(
        rule: AlertRule, active: bool, evidence: dict[str, Any]
    ) -> None:
        """Apply debounce/resolve semantics to a server-level alert rule."""

        nonlocal opened, resolved
        instance = await session.scalar(
            select(AlertInstance).where(
                AlertInstance.rule_id == rule.id,
                AlertInstance.device_id.is_(None),
                AlertInstance.status.in_(
                    ["debouncing", "active", "acknowledged", "resolving"]
                ),
            )
        )
        if active and instance is None:
            session.add(
                AlertInstance(
                    rule_id=rule.id,
                    device_id=None,
                    site_id=rule.site_id,
                    status="debouncing" if rule.debounce_seconds else "active",
                    severity=rule.severity,
                    opened_at=now,
                    evidence=evidence,
                )
            )
            if not rule.debounce_seconds:
                opened += 1
        elif active and instance is not None:
            if instance.status == "debouncing":
                opened_at = instance.opened_at
                if opened_at.tzinfo is None:
                    opened_at = opened_at.replace(tzinfo=UTC)
                if opened_at <= now - timedelta(seconds=rule.debounce_seconds):
                    instance.status = "active"
                    instance.evidence = evidence
                    opened += 1
            elif instance.status == "resolving":
                prior = instance.evidence.get("status_before_resolve", "active")
                instance.status = (
                    str(prior) if prior in {"active", "acknowledged"} else "active"
                )
                instance.evidence = evidence
        elif not active and instance is not None:
            if instance.status == "debouncing":
                await session.delete(instance)
            elif not rule.resolve_seconds:
                instance.status = "resolved"
                instance.resolved_at = now
                resolved += 1
            elif instance.status != "resolving":
                instance.evidence = {
                    **instance.evidence,
                    "resolve_observed_at": now.isoformat(),
                    "status_before_resolve": instance.status,
                }
                instance.status = "resolving"
            else:
                observed = datetime.fromisoformat(
                    str(instance.evidence.get("resolve_observed_at"))
                )
                if observed <= now - timedelta(seconds=rule.resolve_seconds):
                    instance.status = "resolved"
                    instance.resolved_at = now
                    resolved += 1

    def matching_rules(device: Device, rule_type: str) -> list[AlertRule]:
        return [
            rule
            for rule in rules.get(rule_type, [])
            if (rule.device_id is None or rule.device_id == device.id)
            and (rule.site_id is None or rule.site_id == device.site_id)
        ]

    worker = await session.get(WorkerState, "main")
    worker_last_success = worker.last_success_at if worker else None
    if worker_last_success is not None and worker_last_success.tzinfo is None:
        worker_last_success = worker_last_success.replace(tzinfo=UTC)
    for rule in rules.get("worker_failure", []):
        stale_seconds = int(rule.configuration.get("stale_seconds", 45))
        unhealthy = (
            worker is None
            or worker.status != "healthy"
            or worker_last_success is None
            or worker_last_success < now - timedelta(seconds=stale_seconds)
        )
        await set_system_condition(
            rule,
            unhealthy,
            {
                "worker_name": worker.worker_name if worker else "main",
                "worker_status": worker.status if worker else "missing",
                "last_success_at": (
                    worker_last_success.isoformat() if worker_last_success else None
                ),
                "stale_after_seconds": stale_seconds,
            },
        )

    for device in devices:
        if device.maintenance_until:
            maintenance_until = device.maintenance_until
            if maintenance_until.tzinfo is None:
                maintenance_until = maintenance_until.replace(tzinfo=UTC)
            if maintenance_until > now:
                continue
            device.maintenance_until = None
            if device.status == "maintenance":
                device.status = "offline_last_known"
        heartbeat = await session.scalar(
            select(DeviceHeartbeat)
            .where(DeviceHeartbeat.device_id == device.id)
            .order_by(DeviceHeartbeat.received_at.desc())
            .limit(1)
        )
        heartbeat_received_at = heartbeat.received_at if heartbeat else None
        if heartbeat_received_at is not None and heartbeat_received_at.tzinfo is None:
            heartbeat_received_at = heartbeat_received_at.replace(tzinfo=UTC)
        stale = (
            heartbeat_received_at is None
            or heartbeat_received_at
            < now - timedelta(seconds=settings.heartbeat_expectation_seconds * 2)
        )
        for rule in matching_rules(device, "heartbeat_stale"):
            stale_seconds = int(
                rule.configuration.get(
                    "stale_seconds", settings.heartbeat_expectation_seconds * 2
                )
            )
            rule_stale = (
                heartbeat_received_at is None
                or heartbeat_received_at < now - timedelta(seconds=stale_seconds)
            )
            await set_condition(
                device,
                rule,
                rule_stale,
                {
                    "last_seen_at": device.last_seen_at.isoformat()
                    if device.last_seen_at
                    else None,
                    "stale_after_seconds": stale_seconds,
                },
            )
        if stale and device.status not in {"maintenance", "revoked"}:
            device.status = "offline_last_known"
        if heartbeat:
            for rule in matching_rules(device, "power_surge"):
                threshold_watts = Decimal(str(rule.configuration["threshold_watts"]))
                await set_condition(
                    device,
                    rule,
                    not stale
                    and heartbeat.current_watts is not None
                    and heartbeat.current_watts >= threshold_watts,
                    {
                        "current_watts": str(heartbeat.current_watts)
                        if heartbeat.current_watts is not None
                        else None,
                        "threshold_watts": str(threshold_watts),
                        "heartbeat_id": heartbeat.id,
                    },
                )
            for rule_type, active, evidence in (
                ("pzem_failure", not heartbeat.pzem_ok, {"heartbeat_id": heartbeat.id}),
                ("sd_failure", not heartbeat.sd_ok, {"heartbeat_id": heartbeat.id}),
                (
                    "time_untrusted",
                    not heartbeat.time_trusted,
                    {"heartbeat_id": heartbeat.id},
                ),
                (
                    "low_rssi",
                    heartbeat.rssi_dbm is not None and heartbeat.rssi_dbm < -75,
                    {"rssi_dbm": heartbeat.rssi_dbm},
                ),
            ):
                for rule in matching_rules(device, rule_type):
                    await set_condition(device, rule, active, evidence)
        gap_count = await session.scalar(
            select(func.count())
            .select_from(SequenceGap)
            .where(
                SequenceGap.device_id == device.id, SequenceGap.resolved_at.is_(None)
            )
        )
        for rule in matching_rules(device, "sequence_gap"):
            await set_condition(
                device, rule, bool(gap_count), {"open_gap_count": gap_count or 0}
            )
    await session.commit()
    return {"opened": opened, "resolved": resolved}


async def recompute_recent_rollups(session: AsyncSession) -> int:
    now = datetime.now(UTC)
    devices = list(
        await session.scalars(select(Device).where(Device.revoked_at.is_(None)))
    )
    count = 0
    for device in devices:
        site = await session.get(Site, device.site_id)
        if site is None:
            continue
        zone = ZoneInfo(site.timezone)
        for offset in (0, 1):
            local_day = now.astimezone(zone).date() - timedelta(days=offset)
            local_start = datetime.combine(local_day, datetime.min.time(), zone)
            local_end = local_start + timedelta(days=1)
            rows = list(
                await session.scalars(
                    select(NormalizedInterval).where(
                        NormalizedInterval.device_id == device.id,
                        NormalizedInterval.interval_start
                        >= local_start.astimezone(UTC),
                        NormalizedInterval.interval_start < local_end.astimezone(UTC),
                    )
                )
            )
            energy = sum(
                (row.selected_energy_wh or Decimal("0") for row in rows), Decimal("0")
            )
            peak = max(
                (
                    (row.selected_energy_wh or Decimal("0"))
                    * Decimal("3600")
                    / Decimal(
                        str((row.interval_end - row.interval_start).total_seconds())
                    )
                    for row in rows
                    if row.interval_end > row.interval_start
                ),
                default=Decimal("0"),
            )
            duration = sum(
                (
                    Decimal(
                        str((row.interval_end - row.interval_start).total_seconds())
                    )
                    for row in rows
                ),
                Decimal("0"),
            )
            day_seconds = Decimal(
                str(
                    (
                        local_end.astimezone(UTC) - local_start.astimezone(UTC)
                    ).total_seconds()
                )
            )
            coverage = (
                min(Decimal("100"), duration / day_seconds * Decimal("100"))
                if day_seconds
                else Decimal("0")
            )
            device_rollup = await session.get(DailyDeviceRollup, (device.id, local_day))
            if device_rollup is None:
                device_rollup = DailyDeviceRollup(
                    device_id=device.id,
                    local_date=local_day,
                    timezone=site.timezone,
                    energy_wh=energy,
                    peak_watts=peak,
                    coverage_percent=coverage,
                    calculated_at=now,
                )
                session.add(device_rollup)
            else:
                device_rollup.energy_wh = energy
                device_rollup.peak_watts = peak
                device_rollup.coverage_percent = coverage
                device_rollup.calculated_at = now
            count += 1
        await session.flush()
        for month_offset in (0, 1):
            local_today = now.astimezone(zone).date()
            year = local_today.year
            month = local_today.month - month_offset
            if month == 0:
                year -= 1
                month = 12
            month_start = local_today.replace(year=year, month=month, day=1)
            next_month = (
                month_start.replace(year=year + 1, month=1)
                if month == 12
                else month_start.replace(month=month + 1)
            )
            daily_rows = list(
                await session.scalars(
                    select(DailyDeviceRollup).where(
                        DailyDeviceRollup.device_id == device.id,
                        DailyDeviceRollup.local_date >= month_start,
                        DailyDeviceRollup.local_date < next_month,
                    )
                )
            )
            monthly_energy = sum((row.energy_wh for row in daily_rows), Decimal("0"))
            monthly_peak = max(
                (row.peak_watts for row in daily_rows), default=Decimal("0")
            )
            monthly_coverage = (
                sum((row.coverage_percent for row in daily_rows), Decimal("0"))
                / Decimal(str(len(daily_rows)))
                if daily_rows
                else Decimal("0")
            )
            monthly = await session.get(MonthlyDeviceRollup, (device.id, month_start))
            if monthly is None:
                monthly = MonthlyDeviceRollup(
                    device_id=device.id,
                    month_start=month_start,
                    energy_wh=monthly_energy,
                    peak_watts=monthly_peak,
                    coverage_percent=monthly_coverage,
                    calculated_at=now,
                )
                session.add(monthly)
            else:
                monthly.energy_wh = monthly_energy
                monthly.peak_watts = monthly_peak
                monthly.coverage_percent = monthly_coverage
                monthly.calculated_at = now
            count += 1
    await session.flush()
    aggregates = list(await session.scalars(select(AggregateSet)))
    for aggregate in aggregates:
        site = await session.get(Site, aggregate.site_id)
        if site is None:
            continue
        members = list(
            await session.scalars(
                select(AggregateMember).where(
                    AggregateMember.aggregate_set_id == aggregate.id
                )
            )
        )
        allocations: dict[str, Decimal] = {}
        for member in members:
            member_device_ids: set[str] = set()
            if member.device_id:
                member_device_ids.add(member.device_id)
            elif member.circuit_id:
                member_device_ids.update(
                    await session.scalars(
                        select(Device.id).where(
                            Device.site_id == aggregate.site_id,
                            Device.circuit_id == member.circuit_id,
                            Device.revoked_at.is_(None),
                        )
                    )
                )
            for device_id in member_device_ids:
                allocations[device_id] = allocations.get(device_id, Decimal("0")) + (
                    member.allocation_percent / Decimal("100")
                )
        zone = ZoneInfo(site.timezone)
        for offset in (0, 1):
            local_day = now.astimezone(zone).date() - timedelta(days=offset)
            local_start = datetime.combine(
                local_day, datetime.min.time(), zone
            ).astimezone(UTC)
            energy = Decimal("0")
            peak = Decimal("0")
            coverage_weight = Decimal("0")
            allocation_total = Decimal("0")
            for device_id, allocation in allocations.items():
                daily_rollup = await session.get(
                    DailyDeviceRollup, (device_id, local_day)
                )
                if daily_rollup is None:
                    continue
                energy += daily_rollup.energy_wh * allocation
                peak += daily_rollup.peak_watts * allocation
                coverage_weight += daily_rollup.coverage_percent * allocation
                allocation_total += allocation
            coverage = (
                coverage_weight / allocation_total if allocation_total else Decimal("0")
            )
            site_rollup = await session.get(
                SiteRollup, (aggregate.id, local_start, "daily")
            )
            if site_rollup is None:
                session.add(
                    SiteRollup(
                        aggregate_set_id=aggregate.id,
                        interval_start=local_start,
                        resolution="daily",
                        energy_wh=energy,
                        peak_watts=peak,
                        coverage_percent=coverage,
                    )
                )
            else:
                site_rollup.energy_wh = energy
                site_rollup.peak_watts = peak
                site_rollup.coverage_percent = coverage
            count += 1
    await session.commit()
    logger.info(
        "history.rollup_updated",
        rollup_count=count,
        device_count=len(devices),
        aggregate_count=len(aggregates),
    )
    return count
