from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, func, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.data_reset.sensor_client import (
    SensorResetCommunicationError,
    probe_sensor_storage,
    validate_sensor_storage_snapshot,
)
from app.db.models import (
    AccountReconciliationAdjustment,
    AggregateMember,
    AggregateSet,
    AlertInstance,
    AlertRule,
    AuditEvent,
    BackgroundJob,
    BackupRun,
    BaselineRule,
    BillingCycle,
    CostCalculationRun,
    CostIntervalResult,
    CycleTierSummary,
    DailyCostRollup,
    DailyDeviceRollup,
    DataResetOperation,
    DataResetParticipant,
    DataResetPlan,
    DataResetPricingBaseline,
    Device,
    DeviceCapability,
    DeviceConfigVersion,
    DeviceDataState,
    DeviceEvent,
    DeviceHeartbeat,
    DeviceLifecycleEvent,
    DeviceSiteAssignment,
    DeviceStatusSnapshot,
    ExportJob,
    FirmwareDeployment,
    FixedChargeRule,
    GeneratedReport,
    LogExportJob,
    ManualAccountUsage,
    ManualBillAdjustment,
    MonthlyDeviceRollup,
    NormalizedInterval,
    NotificationAttempt,
    NotificationEvent,
    RateAdjustment,
    RateApprovalDecision,
    RateAssignment,
    RateCandidateDifference,
    RateChangeCandidate,
    RateDayType,
    RateExtractionResult,
    RatePeriod,
    RatePlan,
    RateSeason,
    RateSeasonalBaseline,
    RateSourceArtifact,
    RateSourceCheckRun,
    RateThresholdRule,
    RateTierDefinition,
    RateVersion,
    RateVersionSource,
    RawReading,
    SequenceGap,
    Site,
    SiteDataState,
    SiteRollup,
    SyncCursor,
    TierAllocationSegment,
    TierProjectionSnapshot,
    UtilityAccount,
    UtilityAccountAdjustment,
    UtilityBillCycleDraft,
    UtilityBillExtractedField,
    UtilityBillExtractionRevision,
    UtilityBillFieldConflict,
    UtilityBillImport,
    UtilityUsageImport,
    new_uuid,
)
from app.ota import ACTIVE_DEPLOYMENT_STATES
from app.problem import ProblemError
from app.rates.reset_barrier import lock_rate_plans
from app.rates.service import version_document
from app.rates.tiered import expected_cycle_bounds

DATA_RESET_PROTOCOL = "data-reset/1.0.0"
VERIFIED_BACKUP_CONFIRMATION_PHRASE = "RESET ALL READINGS AND PRICING HISTORY"
NO_BACKUP_CONFIRMATION_PHRASE = "PERMANENTLY RESET ALL READINGS AND PRICING HISTORY WITHOUT BACKUP"
PLAN_TTL = timedelta(minutes=15)
MAX_RESET_BOUNDARY = 2**63 - 3
RESET_CATEGORIES = frozenset(
    {
        "measurement_history",
        "cost_history",
        "pricing_history",
        "generated_outputs",
    }
)
TERMINAL_OPERATION_STATES = frozenset({"completed", "cancelled", "failed_before_commit"})
TERMINAL_RATE_CANDIDATE_STATES = frozenset(
    {"activated", "automatically_activated", "rejected", "validation_failed"}
)
MEASUREMENT_ALERT_TYPES = frozenset(
    {
        "power_surge",
        "reading_stale",
        "sequence_gap",
        "sync_backlog",
        "ct_limit_80",
        "ct_limit_90",
        "voltage_range",
        "frequency_range",
    }
)
SENSITIVE_HISTORY_KEYS = frozenset(
    {
        "power",
        "power_w",
        "power_watts",
        "power_avg",
        "power_avg_w",
        "average_power",
        "average_power_w",
        "power_min",
        "power_min_w",
        "power_max",
        "power_max_w",
        "maximum_power",
        "peak_power_w",
        "last_known_power_watts",
        "current_load_w",
        "current_power",
        "current_power_w",
        "current_watts",
        "integrated_power",
        "power_values",
        "active_power_w",
        "reactive_power_var",
        "apparent_power_va",
        "current_amps",
        "current_avg",
        "current_a",
        "current_avg_a",
        "current_min",
        "current_min_a",
        "current_max",
        "current_max_a",
        "voltage_v",
        "voltage",
        "voltage_volts",
        "voltage_avg",
        "voltage_avg_v",
        "voltage_min",
        "voltage_min_v",
        "voltage_max",
        "voltage_max_v",
        "power_factor",
        "frequency_hz",
        "energy",
        "energy_wh",
        "energy_kwh",
        "actual_energy_kwh",
        "allocated_energy",
        "billing_cycle_energy_kwh",
        "cycle_energy",
        "daily_average_usage_kwh",
        "device_energy",
        "effective_energy",
        "eligible_energy",
        "energy_by_bucket",
        "energy_by_bucket_kwh",
        "energy_by_tier_kwh",
        "energy_kwh_value",
        "energy_today_kwh",
        "highest_usage_bucket_kwh",
        "interval_energy_wh",
        "interval_energy_kwh",
        "interval_energy",
        "device_interval_energy_wh",
        "device_energy_wh",
        "server_energy_wh",
        "selected_energy_wh",
        "device_lifetime_energy_wh",
        "raw_energy_wh",
        "raw_energy_start_wh",
        "raw_energy_end_wh",
        "meter_energy_total_wh",
        "pzem_energy_wh",
        "pzem_cumulative_energy_wh",
        "pzem_energy_start_wh",
        "pzem_energy_end_wh",
        "prepared_pzem_energy_wh",
        "commit_pzem_energy_wh",
        "verified_pzem_energy_wh",
        "software_energy_baseline_before_wh",
        "software_energy_baseline_after_wh",
        "total_energy_wh",
        "total_energy_kwh",
        "total_energy",
        "monthly_energy",
        "monitored_usage_kwh",
        "projected_energy_kwh",
        "segment_energy",
        "segment_energy_kwh",
        "selected_energy",
        "server_energy",
        "sensor_measured_usage",
        "summary_energy_kwh",
        "utility_energy",
        "usage",
        "usage_kwh",
        "total_usage_kwh",
        "total_usage",
        "summary_total_usage",
        "utility_usage_kwh",
        "tier_1_usage_kwh",
        "tier_2_usage_kwh",
        "cumulative_usage_kwh",
        "usage_by_tier",
        "usage_by_tou",
        "energy_by_tier",
        "energy_by_tou",
        "tier_usage_kwh",
        "tier_progress_kwh",
        "cumulative_kwh",
        "cost",
        "cost_usd",
        "total_cost",
        "total_cost_usd",
        "total_amount",
        "actual_energy_charge",
        "allocated_cost",
        "billing_cost",
        "calculated_energy_subtotal",
        "cycle_cost",
        "estimated_billing_cycle_cost",
        "estimated_cost_today",
        "estimated_energy_cost",
        "energy_cost",
        "usage_cost",
        "interval_energy_cost",
        "delivery_cost",
        "generation_cost",
        "adjustment_cost",
        "energy_subtotal",
        "projected_energy_charge",
        "projected_energy_subtotal",
        "summary_energy_cost",
        "today_cost",
        "subtotal",
        "fixed_charge",
        "baseline_credit",
        "tax_amount",
        "bill_amount",
        "amount",
        "amount_per_day",
        "percentage_amount",
        "price",
        "price_per_kwh",
        "raw_price",
        "current_energy_price",
        "current_price",
        "current_price_per_kwh",
        "next_price",
        "next_price_per_kwh",
        "blended_energy_rate",
        "account_energy_amount",
        "account_energy_rate",
        "unrounded_cost",
        "unrounded_energy_charge",
        "unrounded_energy_cost",
        "backlog",
        "backlog_estimate",
        "latest",
        "readings",
        "original_payload",
    }
)
SENSITIVE_HISTORY_DESCRIPTOR_FIELDS = frozenset(
    {"field", "metric", "measurement", "measurement_name", "reading_name", "series"}
)
SENSITIVE_HISTORY_VALUE_FIELDS = frozenset(
    {
        "avg",
        "end",
        "maximum",
        "max",
        "minimum",
        "min",
        "start",
        "total",
        "value",
        "values",
    }
)
SENSITIVE_HISTORY_TEXT = re.compile(
    r"(?i)\b(?:power|energy|usage|cost|price|voltage|current|watt|kwh|pzem)"
    r"[a-z0-9_. -]{0,40}\s*[:=]\s*-?\d"
)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def reset_plan_fingerprint(snapshot: dict[str, Any]) -> str:
    """Hash material reset state while ignoring harmless liveness timestamps."""

    material = dict(snapshot)
    material["participants"] = [
        {key: value for key, value in item.items() if key != "last_seen_at"}
        if isinstance(item, dict)
        else item
        for item in snapshot.get("participants", [])
    ]
    return canonical_sha256(material)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return default
    return int(value)


def _valid_reset_firmware_identity(device: Device) -> bool:
    version = device.firmware_version
    build_hash = device.firmware_build_hash
    return (
        isinstance(version, str)
        and 1 <= len(version) <= 80
        and isinstance(build_hash, str)
        and len(build_hash) == 64
        and all(character in "0123456789abcdef" for character in build_hash)
    )


def _nested(payload: Any, *keys: str) -> Any:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def redact_history_values(value: Any) -> Any:
    """Recursively remove values that can reconstruct cleared usage/history."""

    if isinstance(value, dict):
        descriptor_is_sensitive = any(
            str(key).lower() in SENSITIVE_HISTORY_DESCRIPTOR_FIELDS
            and isinstance(item, str)
            and (
                item.lower() in SENSITIVE_HISTORY_KEYS
                or any(
                    marker in item.lower()
                    for marker in (
                        "power",
                        "energy",
                        "usage",
                        "cost",
                        "price",
                        "voltage",
                        "current",
                        "watt",
                        "kwh",
                        "pzem",
                    )
                )
            )
            for key, item in value.items()
        )
        return {
            str(key): (
                "[redacted-by-data-reset]"
                if str(key).lower() in SENSITIVE_HISTORY_KEYS
                or (descriptor_is_sensitive and str(key).lower() in SENSITIVE_HISTORY_VALUE_FIELDS)
                else redact_history_values(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_history_values(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")) and stripped.endswith(("}", "]")):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict | list):
                return json.dumps(
                    redact_history_values(decoded),
                    sort_keys=True,
                    separators=(",", ":"),
                )
        if SENSITIVE_HISTORY_TEXT.search(value):
            return "[redacted-by-data-reset]"
    return value


def reset_backup_verification_is_conclusive(backup: BackupRun | None) -> bool:
    """Require durable evidence from the isolated restore verifier."""

    if (
        backup is None
        or backup.status != "verified"
        or backup.verified_at is None
        or backup.deleted_at is not None
        or not isinstance(backup.path, str)
        or not backup.path.strip()
        or isinstance(backup.size_bytes, bool)
        or not isinstance(backup.size_bytes, int)
        or backup.size_bytes <= 0
        or not isinstance(backup.manifest_hash, str)
        or len(backup.manifest_hash) != 64
        or any(character not in "0123456789abcdef" for character in backup.manifest_hash)
    ):
        return False
    details = backup.verification_details
    if not isinstance(details, dict):
        return False
    revision = details.get("migration_revision")
    if (
        not isinstance(revision, str)
        or len(revision) != 13
        or revision[8] != "_"
        or not revision[:8].isdigit()
        or not revision[9:].isdigit()
    ):
        return False

    def integer_at_least(key: str, minimum: int) -> bool:
        value = details.get(key)
        return not isinstance(value, bool) and isinstance(value, int) and value >= minimum

    def integer_equals(key: str, expected: int) -> bool:
        value = details.get(key)
        return not isinstance(value, bool) and isinstance(value, int) and value == expected

    return (
        integer_at_least("table_count", 21)
        and integer_equals("required_table_count", 5)
        and integer_at_least("status_layout_revisions", 0)
        and integer_at_least("postgres_major", 14)
    )


async def _count(session: AsyncSession, model: Any, *criteria: Any) -> int:
    query = select(func.count()).select_from(model)
    if criteria:
        query = query.where(*criteria)
    return int(await session.scalar(query) or 0)


async def _site_measurement_scope(
    session: AsyncSession,
    *,
    site: Site,
) -> tuple[set[str], set[tuple[str, date]], set[tuple[str, date]], set[str]]:
    """Resolve historical device/rollup scope without relying on current assignment."""

    current_ids = set(await session.scalars(select(Device.id).where(Device.site_id == site.id)))
    raw_ids = set(
        await session.scalars(
            select(RawReading.device_id).where(RawReading.site_id == site.id).distinct()
        )
    )
    assignments = list(
        await session.scalars(
            select(DeviceSiteAssignment).where(DeviceSiteAssignment.site_id == site.id)
        )
    )
    assigned_ids = {item.device_id for item in assignments}
    device_ids = current_ids | raw_ids | assigned_ids
    unassigned_ids = device_ids - assigned_ids
    if not device_ids:
        return set(), set(), set(), set()

    timezone = ZoneInfo(site.timezone)
    windows: dict[str, list[tuple[date, date | None]]] = {}
    for assignment in assignments:
        starts_on = _aware(assignment.effective_from).astimezone(timezone).date()
        ends_before = (
            (_aware(assignment.effective_to) - timedelta(microseconds=1))
            .astimezone(timezone)
            .date()
            if assignment.effective_to is not None
            else None
        )
        windows.setdefault(assignment.device_id, []).append((starts_on, ends_before))

    raw_days: set[tuple[str, date]] = set()
    grouped_raw = (
        await session.execute(
            select(
                RawReading.device_id,
                func.min(RawReading.interval_start),
                func.max(RawReading.interval_end),
            )
            .where(RawReading.site_id == site.id)
            .group_by(RawReading.device_id, func.date(RawReading.interval_start))
        )
    ).all()
    for device_id, first_start, last_end in grouped_raw:
        if first_start is None or last_end is None:
            continue
        first_day = _aware(first_start).astimezone(timezone).date()
        last_day = _aware(last_end).astimezone(timezone).date()
        day = first_day
        while day <= last_day:
            raw_days.add((str(device_id), day))
            day += timedelta(days=1)

    def assigned_on(device_id: str, local_day: date) -> bool:
        return any(
            starts_on <= local_day and (ends_on is None or local_day <= ends_on)
            for starts_on, ends_on in windows.get(device_id, [])
        )

    daily_keys: set[tuple[str, date]] = set(raw_days)
    for daily_rollup in await session.scalars(
        select(DailyDeviceRollup).where(DailyDeviceRollup.device_id.in_(device_ids))
    ):
        if daily_rollup.device_id in unassigned_ids or assigned_on(
            daily_rollup.device_id, daily_rollup.local_date
        ):
            daily_keys.add((daily_rollup.device_id, daily_rollup.local_date))

    monthly_keys: set[tuple[str, date]] = {
        (device_id, date(local_day.year, local_day.month, 1)) for device_id, local_day in daily_keys
    }
    for monthly_rollup in await session.scalars(
        select(MonthlyDeviceRollup).where(MonthlyDeviceRollup.device_id.in_(device_ids))
    ):
        if monthly_rollup.device_id in unassigned_ids:
            monthly_keys.add((monthly_rollup.device_id, monthly_rollup.month_start))
            continue
        month_end = (
            date(monthly_rollup.month_start.year + 1, 1, 1)
            if monthly_rollup.month_start.month == 12
            else date(
                monthly_rollup.month_start.year,
                monthly_rollup.month_start.month + 1,
                1,
            )
        ) - timedelta(days=1)
        if any(
            starts_on <= month_end and (ends_on is None or monthly_rollup.month_start <= ends_on)
            for starts_on, ends_on in windows.get(monthly_rollup.device_id, [])
        ):
            monthly_keys.add((monthly_rollup.device_id, monthly_rollup.month_start))
    return device_ids, daily_keys, monthly_keys, unassigned_ids


def _site_assignment_at(
    *,
    site_id: str,
    device_column: Any,
    timestamp_column: Any,
    unassigned_device_ids: set[str],
) -> Any:
    assigned = exists(
        select(DeviceSiteAssignment.id).where(
            DeviceSiteAssignment.site_id == site_id,
            DeviceSiteAssignment.device_id == device_column,
            DeviceSiteAssignment.effective_from <= timestamp_column,
            or_(
                DeviceSiteAssignment.effective_to.is_(None),
                timestamp_column < DeviceSiteAssignment.effective_to,
            ),
        )
    )
    return (
        or_(device_column.in_(unassigned_device_ids), assigned)
        if unassigned_device_ids
        else assigned
    )


async def ensure_device_reset_mutations_allowed(
    session: AsyncSession,
    device_ids: Iterable[str],
) -> None:
    """Serialize device mutations with reset activation and reject unsafe overlap."""

    ordered_ids = sorted(set(device_ids))
    if not ordered_ids:
        return
    locked_ids = list(
        await session.scalars(
            select(Device.id)
            .where(Device.id.in_(ordered_ids))
            .order_by(Device.id)
            .with_for_update()
        )
    )
    if set(locked_ids) != set(ordered_ids):
        raise ProblemError(
            404,
            "Sensor not found",
            "One or more sensors do not exist",
            "device_missing",
        )
    states = list(
        await session.scalars(
            select(DeviceDataState)
            .where(DeviceDataState.device_id.in_(ordered_ids))
            .order_by(DeviceDataState.device_id)
            .with_for_update()
        )
    )
    blocked_ids = {
        state.device_id
        for state in states
        if state.active_operation_id is not None
        or state.ingestion_gate != "open"
        or state.reset_required_on_reconnect
    }
    participant_blocked = set(
        await session.scalars(
            select(DataResetParticipant.device_id)
            .join(
                DataResetOperation,
                DataResetOperation.id == DataResetParticipant.operation_id,
            )
            .where(
                DataResetParticipant.device_id.in_(ordered_ids),
                DataResetParticipant.state.not_in(["verified", "not_applicable"]),
                DataResetOperation.state.not_in(TERMINAL_OPERATION_STATES),
            )
        )
    )
    blocked_ids.update(participant_blocked)
    if blocked_ids:
        raise ProblemError(
            409,
            "Sensor reset in progress",
            "Configuration, credential, storage, and OTA changes are locked "
            "until the sensor reset checkpoint finishes",
            "data_reset_device_mutation_blocked",
            extra={"device_ids": sorted(blocked_ids)},
        )


async def ensure_site_reset_mutations_allowed(
    session: AsyncSession,
    site_ids: Iterable[str],
) -> None:
    """Serialize site-scope mutations with reset activation."""

    ordered_ids = sorted(set(site_ids))
    if not ordered_ids:
        return
    locked_ids = list(
        await session.scalars(
            select(Site.id).where(Site.id.in_(ordered_ids)).order_by(Site.id).with_for_update()
        )
    )
    if set(locked_ids) != set(ordered_ids):
        raise ProblemError(
            404,
            "Site not found",
            "One or more sites do not exist",
            "site_missing",
        )
    active_sites = set(
        await session.scalars(
            select(DataResetOperation.site_id).where(
                DataResetOperation.site_id.in_(ordered_ids),
                DataResetOperation.state.not_in(TERMINAL_OPERATION_STATES),
            )
        )
    )
    if active_sites:
        raise ProblemError(
            409,
            "Site reset in progress",
            "Topology, pricing, account, report, export, and log mutations are "
            "locked until the coordinated reset finishes",
            "data_reset_site_mutation_blocked",
            extra={"site_ids": sorted(active_sites)},
        )


def _supports_data_reset(capability: DeviceCapability | None) -> bool:
    if capability is None:
        return False
    features = dict(capability.features or {})
    direct = features.get("data_reset") or features.get("data-reset")
    if direct is True or direct == DATA_RESET_PROTOCOL:
        return True
    values: list[Any] = []
    for key in ("supported_endpoints", "capabilities", "protocols", "features"):
        candidate = features.get(key)
        if isinstance(candidate, list):
            values.extend(candidate)
        elif isinstance(candidate, dict):
            values.extend(candidate.keys())
            values.extend(candidate.values())
    return DATA_RESET_PROTOCOL in values


def _configuration_transition_state(
    *,
    desired_version: int,
    effective_version: int,
    pending_versions: Iterable[tuple[str, int]],
    uninitialized_baseline: bool = False,
) -> tuple[bool, list[str], list[str]]:
    """Classify pending rows against authoritative desired/effective revisions.

    A signed sensor report can advance the effective revision even when an
    earlier command-result response was lost.  Rows at or below that revision
    are stale status records, not active configuration transitions.  A version
    mismatch or any newer pending row must continue to block a data reset.
    """

    unresolved_ids: list[str] = []
    stale_ids: list[str] = []
    for config_id, version in pending_versions:
        target = stale_ids if int(version) <= effective_version else unresolved_ids
        target.append(config_id)
    transition_pending = desired_version != effective_version or bool(unresolved_ids)
    # Devices created before their first explicit configuration revision use
    # the model's 1/0 baseline but have no configuration artifact to deliver.
    # That sentinel is not an active transition; every initialized mismatch
    # and every unresolved pending row remains a reset blocker.
    if (
        uninitialized_baseline
        and desired_version == 1
        and effective_version == 0
        and not unresolved_ids
        and not stale_ids
    ):
        transition_pending = False
    return transition_pending, unresolved_ids, stale_ids


async def _device_plan_snapshot(
    session: AsyncSession,
    device: Device,
    *,
    observed_at: datetime,
    offline_after_seconds: int,
    settings: Settings | None = None,
) -> dict[str, Any]:
    capability = await session.get(DeviceCapability, device.id)
    cursor = await session.get(SyncCursor, device.id)
    data_state = await session.get(DeviceDataState, device.id)
    latest_config = await session.scalar(
        select(DeviceConfigVersion)
        .where(DeviceConfigVersion.device_id == device.id)
        .order_by(DeviceConfigVersion.version.desc())
        .limit(1)
    )
    pending_configs = list(
        await session.scalars(
            select(DeviceConfigVersion)
            .where(
                DeviceConfigVersion.device_id == device.id,
                DeviceConfigVersion.status == "pending",
            )
            .order_by(DeviceConfigVersion.version)
        )
    )
    (
        pending_configuration_change,
        pending_config_ids,
        stale_pending_config_ids,
    ) = _configuration_transition_state(
        desired_version=int(device.desired_config_version),
        effective_version=int(device.effective_config_version),
        pending_versions=((item.id, int(item.version)) for item in pending_configs),
        uninitialized_baseline=latest_config is None,
    )
    active_deployment = await session.scalar(
        select(FirmwareDeployment)
        .where(
            FirmwareDeployment.device_id == device.id,
            FirmwareDeployment.state.in_(ACTIVE_DEPLOYMENT_STATES),
        )
        .order_by(FirmwareDeployment.created_at, FirmwareDeployment.id)
        .limit(1)
    )
    heartbeat = await session.scalar(
        select(DeviceHeartbeat)
        .where(DeviceHeartbeat.device_id == device.id)
        .order_by(DeviceHeartbeat.received_at.desc())
        .limit(1)
    )
    raw_max = _safe_int(
        await session.scalar(
            select(func.max(RawReading.sequence)).where(RawReading.device_id == device.id)
        )
    )
    payload = dict(heartbeat.payload or {}) if heartbeat is not None else {}
    storage = _nested(payload, "sd", "details")
    if not isinstance(storage, dict):
        storage = {}
    newest = max(
        _safe_int(getattr(heartbeat, "newest_sequence", None)),
        _safe_int(payload.get("newest_stored_sequence")),
        _safe_int(payload.get("newest_syncable_sequence")),
    )
    projected_newest_syncable = _safe_int(payload.get("newest_syncable_sequence"))
    durable_newest_syncable = projected_newest_syncable
    next_sequence = _safe_int(storage.get("next_sequence"), newest + 1)
    if next_sequence == 0:
        next_sequence = newest + 1
    local_floor = max(
        _safe_int(storage.get("sequence_floor")),
        _safe_int(storage.get("prepared_removal_floor")),
    )
    server_ack = _safe_int(payload.get("server_ack_sequence"))
    sensor_max = _safe_int(payload.get("server_maximum_seen_sequence"))
    highest = _safe_int(getattr(cursor, "highest_contiguous_sequence", None))
    maximum = _safe_int(getattr(cursor, "maximum_seen_sequence", None))
    existing_boundary = max(
        _safe_int(getattr(cursor, "reset_boundary", None)),
        _safe_int(getattr(data_state, "reset_boundary", None)),
    )
    supported = _supports_data_reset(capability)
    heartbeat_seen = _aware(heartbeat.received_at) if heartbeat is not None else None
    last_seen = (
        heartbeat_seen
        if settings is not None
        else _aware(device.last_seen_at)
        if device.last_seen_at is not None
        else None
    )
    fresh_heartbeat = bool(
        heartbeat_seen and heartbeat_seen >= observed_at - timedelta(seconds=offline_after_seconds)
    )
    connected = bool(
        last_seen and last_seen >= observed_at - timedelta(seconds=offline_after_seconds)
    )
    probe_status = "not_attempted"
    probe_snapshot: dict[str, Any] | None = None
    card_identity_status: str | None = None
    sd_status: str | None = None
    sensor_data_generation = _safe_int(getattr(heartbeat, "data_generation", None))
    if device.revoked_at is not None:
        classification = "revoked"
    elif device.lifecycle_status != "active":
        classification = "removed"
    elif not supported:
        classification = "unsupported"
    elif not _valid_reset_firmware_identity(device):
        classification = "unsupported"
        supported = False
        probe_status = "firmware_identity_incomplete"
    elif settings is not None and not fresh_heartbeat:
        classification = "disconnected"
        probe_status = "skipped_stale_heartbeat"
    elif settings is not None:
        try:
            if device.protocol_version == "pm-agent/2.0.0":
                probe = validate_sensor_storage_snapshot(payload.get("reset_projection"))
            else:
                probe = await probe_sensor_storage(
                    session,
                    device=device,
                    settings=settings,
                )
        except SensorResetCommunicationError as exc:
            probe_status = exc.code
            if exc.code == "sensor_probe_projection_busy":
                raise ProblemError(
                    409,
                    "Sensor inventory is changing",
                    "A connected sensor could not provide one coherent exact prepare "
                    "projection; retry the read-only plan",
                    "data_reset_sensor_inventory_busy",
                    extra={"device_id": device.id},
                ) from exc
            if exc.code == "sensor_probe_authentication_failed":
                classification = "authentication_failed"
            elif exc.code == "sensor_probe_unsupported":
                classification = "unsupported"
                supported = False
            elif not exc.retryable:
                # A fresh sensor that returns invalid or policy-rejected evidence
                # must not be reclassified as safely disconnected and deferred.
                classification = "authentication_failed"
            else:
                classification = "disconnected"
        else:
            probe_snapshot = probe
            classification = "connected"
            probe_status = "authenticated"
            server_ack = int(probe["server_ack_sequence"])
            newest = max(
                int(probe["newest_sequence"]),
                int(probe["newest_syncable_sequence"]),
            )
            projected_newest_syncable = int(probe["newest_syncable_sequence"])
            durable_newest_syncable = int(probe["durable_newest_syncable_sequence"])
            local_floor = int(probe["sequence_floor"])
            next_sequence = int(probe["next_sequence"])
            local_record_count = int(probe["local_record_count"])
            backlog_estimate = int(probe["backlog_estimate"])
            card_generation = (
                str(probe["card_generation"]) if probe["card_generation"] is not None else None
            )
            card_identity_status = str(probe["card_identity_status"])
            sd_status = str(probe["sd_status"])
            sensor_data_generation = int(probe["data_generation"])
    elif connected:
        classification = "connected"
    else:
        classification = "disconnected"
    card_generation = storage.get("card_generation")
    if not isinstance(card_generation, str | int) or isinstance(card_generation, bool):
        card_generation = None
    cached_local_record_count = storage.get("local_record_count")
    cached_record_count_available = (
        isinstance(cached_local_record_count, int)
        and not isinstance(cached_local_record_count, bool)
        and cached_local_record_count >= 0
    )
    local_record_count = _safe_int(cached_local_record_count)
    backlog_estimate = _safe_int(getattr(heartbeat, "backlog_estimate", None))
    prepare_drain_records_projected = 0
    prepare_drain_first_sequence_projected: int | None = None
    prepare_drain_last_sequence_projected: int | None = None
    prepare_drain_syncable_records_projected = 0
    if probe_snapshot is not None:
        # The authenticated probe above is authoritative over cached heartbeat
        # storage details. Reapply its exact values after initializing fallbacks.
        card_generation = (
            str(probe_snapshot["card_generation"])
            if probe_snapshot["card_generation"] is not None
            else None
        )
        local_record_count = int(probe_snapshot["local_record_count"])
        backlog_estimate = int(probe_snapshot["backlog_estimate"])
        prepare_drain_records_projected = int(probe_snapshot["prepare_drain_records_projected"])
        prepare_drain_first_sequence_projected = probe_snapshot[
            "prepare_drain_first_sequence_projected"
        ]
        prepare_drain_last_sequence_projected = probe_snapshot[
            "prepare_drain_last_sequence_projected"
        ]
        prepare_drain_syncable_records_projected = int(
            probe_snapshot["prepare_drain_syncable_records_projected"]
        )
    boundary = max(
        existing_boundary,
        highest,
        maximum,
        raw_max,
        server_ack,
        sensor_max,
        newest,
        local_floor,
        max(0, next_sequence - 1),
    )
    if classification not in {"revoked", "removed"} and boundary > MAX_RESET_BOUNDARY:
        raise ProblemError(
            409,
            "Sensor sequence space is exhausted",
            "The sensor boundary is too high to guarantee post-reset readings in signed storage",
            "data_reset_sequence_space_exhausted",
            extra={"device_id": device.id, "boundary": boundary},
        )
    data_generation = max(
        int(data_state.data_generation) if data_state is not None else 0,
        sensor_data_generation,
    )
    configuration_snapshot = {
        "desired_version": int(device.desired_config_version),
        "effective_version": int(device.effective_config_version),
        "latest_version": int(latest_config.version) if latest_config is not None else None,
        "latest_hash": latest_config.config_hash if latest_config is not None else None,
        "latest_status": latest_config.status if latest_config is not None else None,
    }
    return {
        "device_id": device.id,
        "name": device.name,
        "classification": classification,
        "supported": supported,
        "last_seen_at": last_seen.isoformat() if last_seen is not None else None,
        "firmware_version": device.firmware_version,
        "firmware_build_hash": device.firmware_build_hash,
        "data_generation": data_generation,
        "configuration_snapshot": configuration_snapshot,
        "configuration_snapshot_digest": canonical_sha256(configuration_snapshot),
        "pending_configuration_change": pending_configuration_change,
        "pending_configuration_ids": pending_config_ids,
        "stale_pending_configuration_ids": stale_pending_config_ids,
        "active_firmware_deployment_id": (
            active_deployment.id if active_deployment is not None else None
        ),
        "active_firmware_deployment_state": (
            active_deployment.state if active_deployment is not None else None
        ),
        "sensor_data_generation": sensor_data_generation,
        "boundary": boundary,
        "server_highest_contiguous": highest,
        "server_maximum_seen": maximum,
        "sensor_ack_sequence": server_ack,
        "sensor_newest_sequence": newest,
        "sensor_newest_syncable_sequence": projected_newest_syncable,
        "sensor_durable_newest_syncable_sequence": durable_newest_syncable,
        "old_sequence_floor": local_floor,
        "old_next_sequence": max(next_sequence, boundary + 1),
        "card_generation": str(card_generation) if card_generation is not None else None,
        "card_identity_status": card_identity_status,
        "sd_status": sd_status,
        "probe_status": probe_status,
        "local_record_count": local_record_count,
        "backlog_estimate": backlog_estimate,
        "record_count_status": (
            "exact_prepare_projection"
            if probe_status == "authenticated"
            else "not_applicable"
            if classification in {"revoked", "removed"}
            else "last_reported"
            if cached_record_count_available
            else "unavailable"
        ),
        "prepare_drain_records_projected": prepare_drain_records_projected,
        "prepare_drain_first_sequence_projected": prepare_drain_first_sequence_projected,
        "prepare_drain_last_sequence_projected": prepare_drain_last_sequence_projected,
        "prepare_drain_syncable_records_projected": prepare_drain_syncable_records_projected,
        # Retained as a wire-compatible alias. It is a record count, never a
        # sequence-span backlog estimate.
        "estimated_sensor_records": local_record_count,
    }


async def _active_pricing_snapshot(
    session: AsyncSession,
    account: UtilityAccount,
    *,
    reset_at: datetime,
) -> dict[str, Any] | None:
    assignment = await session.scalar(
        select(RateAssignment)
        .where(
            RateAssignment.utility_account_id == account.id,
            RateAssignment.effective_from <= reset_at,
            or_(
                RateAssignment.effective_to.is_(None),
                RateAssignment.effective_to > reset_at,
            ),
            RateAssignment.cancelled_at.is_(None),
        )
        .order_by(RateAssignment.effective_from.desc(), RateAssignment.revision.desc())
        .limit(1)
    )
    version_id = assignment.rate_version_id if assignment else account.active_rate_version_id
    if version_id is None:
        return None
    version = await session.get(RateVersion, version_id)
    if version is None:
        raise ProblemError(
            409,
            "Pricing configuration invalid",
            "The current utility account references a missing rate version",
            "data_reset_pricing_invalid",
        )
    plan = await session.get(RatePlan, version.rate_plan_id)
    if plan is None:
        raise ProblemError(
            409,
            "Pricing configuration invalid",
            "The current rate version references a missing plan",
            "data_reset_pricing_invalid",
        )
    document = (await version_document(session, version)).model_dump(mode="json")
    future_assignments = list(
        await session.scalars(
            select(RateAssignment)
            .where(
                RateAssignment.utility_account_id == account.id,
                RateAssignment.effective_from > reset_at,
                RateAssignment.cancelled_at.is_(None),
            )
            .order_by(RateAssignment.effective_from, RateAssignment.id)
        )
    )
    cycle = await session.scalar(
        select(BillingCycle)
        .where(
            BillingCycle.utility_account_id == account.id,
            BillingCycle.starts_at <= reset_at,
            BillingCycle.ends_at > reset_at,
        )
        .order_by(BillingCycle.explicit_meter_dates.desc(), BillingCycle.override_revision.desc())
        .limit(1)
    )
    account_adjustments = list(
        await session.scalars(
            select(UtilityAccountAdjustment)
            .where(
                UtilityAccountAdjustment.utility_account_id == account.id,
                UtilityAccountAdjustment.enabled.is_(True),
                UtilityAccountAdjustment.status == "active",
                UtilityAccountAdjustment.effective_from <= reset_at,
                or_(
                    UtilityAccountAdjustment.effective_to.is_(None),
                    UtilityAccountAdjustment.effective_to > reset_at,
                ),
            )
            .order_by(
                UtilityAccountAdjustment.component,
                UtilityAccountAdjustment.effective_from,
                UtilityAccountAdjustment.id,
            )
        )
    )
    canonical = {
        "account_id": account.id,
        "account_revision": account.revision,
        "rate_plan_id": plan.id,
        "rate_plan_code": plan.code,
        "rate_plan_name": plan.name,
        "rate_version_id": version.id,
        "rate_version_number": version.version,
        "document": document,
        "account_adjustment_config": account.adjustment_config or {},
        "active_account_adjustments": [
            {
                "component": item.component,
                "value": str(item.value),
                "unit": item.unit,
                "provenance": item.provenance,
                "effective_from": _aware(item.effective_from).isoformat(),
                "effective_to": (
                    _aware(item.effective_to).isoformat() if item.effective_to is not None else None
                ),
                "revision": item.revision,
            }
            for item in account_adjustments
        ],
        "future_assignments": [
            {
                "id": item.id,
                "rate_version_id": item.rate_version_id,
                "effective_from": _aware(item.effective_from).isoformat(),
                "effective_to": (
                    _aware(item.effective_to).isoformat() if item.effective_to is not None else None
                ),
                "revision": item.revision,
            }
            for item in future_assignments
        ],
    }
    return {
        "utility_account_id": account.id,
        "utility_account_name": account.name,
        "rate_plan_id": plan.id,
        "rate_plan_code": plan.code,
        "rate_plan_name": plan.name,
        "rate_version_id": version.id,
        "rate_version_number": version.version,
        "rate_assignment_id": assignment.id if assignment else None,
        "current_cycle_end": (_aware(cycle.ends_at).isoformat() if cycle is not None else None),
        "future_assignment_ids": [item.id for item in future_assignments],
        "pricing_configuration_hash": canonical_sha256(canonical),
        "canonical_configuration": canonical,
    }


def _output_matches_site(value: Any, site_id: str, device_ids: set[str]) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "site_id" and item == site_id:
                return True
            if key in {"device_id", "sensor_id"} and item in device_ids:
                return True
            if (
                key in {"device_ids", "sensor_ids"}
                and isinstance(item, list)
                and device_ids.intersection(str(candidate) for candidate in item)
            ):
                return True
            if _output_matches_site(item, site_id, device_ids):
                return True
    elif isinstance(value, list):
        return any(_output_matches_site(item, site_id, device_ids) for item in value)
    return False


def _output_has_explicit_scope(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"site_id", "device_id", "sensor_id", "device_ids", "sensor_ids"}:
                return True
            if _output_has_explicit_scope(item):
                return True
    elif isinstance(value, list):
        return any(_output_has_explicit_scope(item) for item in value)
    return False


async def _scoped_output_snapshot(
    session: AsyncSession,
    site_id: str,
    device_ids: set[str],
    *,
    report_root: Path | None = None,
    log_root: Path | None = None,
    quarantine_journal: Iterable[dict[str, str]] = (),
) -> dict[str, list[str]]:
    exports = [
        item
        for item in await session.scalars(select(ExportJob))
        if _output_matches_site(item.query or {}, site_id, device_ids)
        or not _output_has_explicit_scope(item.query or {})
    ]
    reports = [
        item
        for item in await session.scalars(select(GeneratedReport))
        if _output_matches_site(item.data_coverage or {}, site_id, device_ids)
        or not _output_has_explicit_scope(item.data_coverage or {})
    ]
    export_artifact_paths: set[str] = set()
    report_artifact_paths: set[str] = set()
    recovery_sources = {
        Path(entry["original"]).resolve(): Path(entry["quarantine"]).resolve()
        for entry in quarantine_journal
        if Path(entry["quarantine"]).resolve().is_file()
    }
    if report_root is not None:
        resolved_root = report_root.resolve()
        for item in exports:
            path = safe_artifact_path(resolved_root, item.file_path)
            if path is not None and (path.is_file() or path.resolve() in recovery_sources):
                export_artifact_paths.add(path.relative_to(resolved_root).as_posix())
        for report in reports:
            path = safe_artifact_path(resolved_root, report.file_path)
            if path is not None and (path.is_file() or path.resolve() in recovery_sources):
                report_artifact_paths.add(path.relative_to(resolved_root).as_posix())
    log_export_paths: list[str] = []
    sanitized_log_paths: list[str] = []
    if log_root is not None:
        resolved_log_root = log_root.resolve()
        log_export_root = resolved_log_root / ".exports"
        try:
            if log_export_root.is_dir():
                log_export_paths = sorted(
                    path.resolve().relative_to(resolved_log_root).as_posix()
                    for path in log_export_root.iterdir()
                    if path.is_file()
                )
            log_export_paths = sorted(
                set(log_export_paths)
                | {
                    original.relative_to(resolved_log_root).as_posix()
                    for original in recovery_sources
                    if original.parent == log_export_root
                }
            )
            log_candidates = (
                {
                    path.resolve()
                    for path in resolved_log_root.iterdir()
                    if path.is_file()
                    and (path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz"))
                }
                if resolved_log_root.is_dir()
                else set()
            )
            log_candidates.update(
                original
                for original in recovery_sources
                if original.parent == resolved_log_root
                and (original.name.endswith(".jsonl") or original.name.endswith(".jsonl.gz"))
            )
            for path in sorted(log_candidates):
                source = path if path.is_file() else recovery_sources.get(path)
                if source is None:
                    continue
                for line in _read_log_lines(source):
                    try:
                        payload = json.loads(line)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if _scope_present(payload, site_id, device_ids):
                        sanitized_log_paths.append(
                            path.resolve().relative_to(resolved_log_root).as_posix()
                        )
                        break
        except OSError as exc:
            raise ProblemError(
                409,
                "Generated-output inventory unavailable",
                "The reset plan could not inspect all configured log artifacts",
                "data_reset_output_inventory_unavailable",
            ) from exc
    return {
        "export_job_ids": sorted(item.id for item in exports),
        "generated_report_ids": sorted(item.id for item in reports),
        "export_artifact_paths": sorted(export_artifact_paths),
        "report_artifact_paths": sorted(report_artifact_paths),
        "log_export_job_ids": sorted(
            await session.scalars(select(LogExportJob.id).order_by(LogExportJob.id))
        ),
        "active_export_job_ids": sorted(
            item.id for item in exports if item.status in {"queued", "running", "preparing"}
        ),
        "active_report_job_ids": sorted(
            item.id for item in reports if item.status in {"queued", "running", "preparing"}
        ),
        "active_log_export_job_ids": sorted(
            await session.scalars(
                select(LogExportJob.id)
                .where(LogExportJob.status.in_({"queued", "running", "preparing"}))
                .order_by(LogExportJob.id)
            )
        ),
        "log_export_file_paths": log_export_paths,
        "sanitized_log_file_paths": sorted(sanitized_log_paths),
    }


async def _measurement_alert_scope(
    session: AsyncSession, site_id: str, device_ids: set[str]
) -> tuple[set[str], set[str]]:
    if not device_ids:
        return set(), set()
    measurement_rule_ids = set(
        await session.scalars(
            select(AlertRule.id).where(AlertRule.rule_type.in_(MEASUREMENT_ALERT_TYPES))
        )
    )
    if not measurement_rule_ids:
        return set(), set()
    alert_ids = set(
        await session.scalars(
            select(AlertInstance.id).where(
                AlertInstance.rule_id.in_(measurement_rule_ids),
                or_(
                    AlertInstance.site_id == site_id,
                    AlertInstance.device_id.in_(device_ids),
                ),
            )
        )
    )
    notification_attempt_ids = (
        set(
            await session.scalars(
                select(NotificationAttempt.id).where(
                    NotificationAttempt.alert_instance_id.in_(alert_ids)
                )
            )
        )
        if alert_ids
        else set()
    )
    return alert_ids, notification_attempt_ids


def _protected_pricing_version_ids(pricing: Iterable[dict[str, Any]]) -> set[str]:
    protected: set[str] = set()
    for item in pricing:
        version_id = item.get("rate_version_id")
        if version_id:
            protected.add(str(version_id))
        canonical = item.get("canonical_configuration")
        if not isinstance(canonical, dict):
            continue
        future = canonical.get("future_assignments", [])
        if not isinstance(future, list):
            continue
        for assignment in future:
            if isinstance(assignment, dict) and assignment.get("rate_version_id"):
                protected.add(str(assignment["rate_version_id"]))
    return protected


async def _pricing_history_scope(
    session: AsyncSession,
    *,
    site_id: str,
    account_ids: set[str],
    protected_version_ids: set[str],
    reset_at: datetime,
    delete_imported_bill_documents: bool,
) -> dict[str, Any]:
    """Return the exact, dependency-closed pricing rows eligible for this reset.

    The returned identifiers are stored only in the private plan snapshot.  Both
    planning and central commit use this function so a dependency appearing after
    approval makes the plan stale instead of broadening the destructive scope.
    """

    assignment_ids = (
        set(
            await session.scalars(
                select(RateAssignment.id).where(
                    RateAssignment.utility_account_id.in_(account_ids),
                    RateAssignment.effective_from <= reset_at,
                )
            )
        )
        if account_ids
        else set()
    )
    account_adjustment_ids = (
        set(
            await session.scalars(
                select(UtilityAccountAdjustment.id).where(
                    UtilityAccountAdjustment.utility_account_id.in_(account_ids),
                    UtilityAccountAdjustment.effective_from < reset_at,
                    or_(
                        UtilityAccountAdjustment.effective_to <= reset_at,
                        UtilityAccountAdjustment.status != "active",
                        UtilityAccountAdjustment.enabled.is_(False),
                    ),
                )
            )
        )
        if account_ids
        else set()
    )
    all_bill_rows = (
        list(
            await session.scalars(
                select(UtilityBillImport).where(
                    UtilityBillImport.utility_account_id.in_(account_ids)
                )
            )
        )
        if account_ids
        else []
    )
    bill_ids = {item.id for item in all_bill_rows if delete_imported_bill_documents}
    bill_revision_ids = (
        set(
            await session.scalars(
                select(UtilityBillExtractionRevision.id).where(
                    UtilityBillExtractionRevision.bill_import_id.in_(bill_ids)
                )
            )
        )
        if bill_ids
        else set()
    )
    bill_field_ids = (
        set(
            await session.scalars(
                select(UtilityBillExtractedField.id).where(
                    UtilityBillExtractedField.extraction_revision_id.in_(bill_revision_ids)
                )
            )
        )
        if bill_revision_ids
        else set()
    )
    bill_conflict_ids = (
        set(
            await session.scalars(
                select(UtilityBillFieldConflict.id).where(
                    UtilityBillFieldConflict.bill_import_id.in_(bill_ids)
                )
            )
        )
        if bill_ids
        else set()
    )

    plan_ids = set(
        await session.scalars(
            select(RatePlan.id).where(
                or_(
                    RatePlan.owner_site_id == site_id,
                    RatePlan.owner_utility_account_id.in_(account_ids),
                )
            )
        )
    )
    scoped_version_ids = (
        set(
            await session.scalars(
                select(RateVersion.id).where(RateVersion.rate_plan_id.in_(plan_ids))
            )
        )
        if plan_ids
        else set()
    )
    all_candidates = list(await session.scalars(select(RateChangeCandidate)))
    candidate_ids = {
        item.id
        for item in all_candidates
        if _aware(item.created_at) < reset_at
        and item.status in TERMINAL_RATE_CANDIDATE_STATES
        and (
            item.rate_plan_id in plan_ids
            or item.base_rate_version_id in scoped_version_ids
            or item.candidate_rate_version_id in scoped_version_ids
        )
    }

    version_rows = (
        list(
            await session.scalars(
                select(RateVersion).where(
                    RateVersion.rate_plan_id.in_(plan_ids),
                    RateVersion.effective_to.is_not(None),
                    RateVersion.effective_to < reset_at.date(),
                )
            )
        )
        if plan_ids
        else []
    )
    version_ids = {
        item.id
        for item in version_rows
        if item.id not in protected_version_ids and not item.is_active
    }
    if version_ids:
        blocked = set(
            await session.scalars(
                select(UtilityAccount.active_rate_version_id).where(
                    UtilityAccount.active_rate_version_id.in_(version_ids)
                )
            )
        )
        blocked.update(
            item.rate_version_id
            for item in await session.scalars(
                select(RateAssignment).where(RateAssignment.rate_version_id.in_(version_ids))
            )
            if item.id not in assignment_ids
        )
        blocked.update(
            str(item.rate_version_id)
            for item in await session.scalars(
                select(UtilityBillImport).where(UtilityBillImport.rate_version_id.in_(version_ids))
            )
            if item.id not in bill_ids
        )
        blocked.update(
            await session.scalars(
                select(DataResetPricingBaseline.rate_version_id).where(
                    DataResetPricingBaseline.rate_version_id.in_(version_ids)
                )
            )
        )
        blocked.update(
            item.rate_version_id
            for item in await session.scalars(
                select(CostCalculationRun).where(
                    CostCalculationRun.rate_version_id.in_(version_ids)
                )
            )
            if item.utility_account_id not in account_ids
        )
        blocked.update(
            item.rate_version_id
            for item in await session.scalars(
                select(TierAllocationSegment).where(
                    TierAllocationSegment.rate_version_id.in_(version_ids)
                )
            )
            if item.utility_account_id not in account_ids
        )
        blocked.update(
            await session.scalars(
                select(RatePlan.cloned_from_rate_version_id).where(
                    RatePlan.cloned_from_rate_version_id.in_(version_ids)
                )
            )
        )
        for candidate in all_candidates:
            if candidate.id in candidate_ids:
                continue
            if candidate.base_rate_version_id in version_ids:
                blocked.add(str(candidate.base_rate_version_id))
            if candidate.candidate_rate_version_id in version_ids:
                blocked.add(str(candidate.candidate_rate_version_id))
        version_ids.difference_update(str(item) for item in blocked if item is not None)

        # A preserved child version must not have its parent pointer silently
        # nulled.  Iterate because preserving one child can in turn preserve its
        # parent elsewhere in the same historical chain.
        while version_ids:
            parent_ids = set(
                await session.scalars(
                    select(RateVersion.parent_version_id).where(
                        RateVersion.parent_version_id.in_(version_ids),
                        RateVersion.id.not_in(version_ids),
                    )
                )
            )
            parent_ids.discard(None)
            if not parent_ids:
                break
            before = len(version_ids)
            version_ids.difference_update(str(item) for item in parent_ids)
            if len(version_ids) == before:
                break

    async def version_child_ids(model: Any) -> set[str]:
        if not version_ids:
            return set()
        return set(
            await session.scalars(select(model.id).where(model.rate_version_id.in_(version_ids)))
        )

    rate_season_ids = await version_child_ids(RateSeason)
    rate_day_type_ids = await version_child_ids(RateDayType)
    rate_period_ids = await version_child_ids(RatePeriod)
    rate_tier_definition_ids = await version_child_ids(RateTierDefinition)
    rate_threshold_rule_ids = await version_child_ids(RateThresholdRule)
    rate_seasonal_baseline_ids = await version_child_ids(RateSeasonalBaseline)
    baseline_rule_ids = await version_child_ids(BaselineRule)
    fixed_charge_rule_ids = await version_child_ids(FixedChargeRule)
    rate_adjustment_ids = await version_child_ids(RateAdjustment)

    version_source_rows = list(await session.scalars(select(RateVersionSource)))
    privacy_source_detachable_version_ids: set[str] = set()
    if bill_ids and scoped_version_ids:
        privacy_source_detachable_version_ids = set(scoped_version_ids)
        privacy_source_detachable_version_ids.difference_update(
            item.active_rate_version_id
            for item in await session.scalars(
                select(UtilityAccount).where(
                    UtilityAccount.active_rate_version_id.in_(scoped_version_ids)
                )
            )
            if item.site_id != site_id and item.active_rate_version_id is not None
        )
        privacy_source_detachable_version_ids.difference_update(
            item.rate_version_id
            for item in await session.scalars(
                select(RateAssignment).where(RateAssignment.rate_version_id.in_(scoped_version_ids))
            )
            if item.utility_account_id not in account_ids
        )
        privacy_source_detachable_version_ids.difference_update(
            str(item.rate_version_id)
            for item in await session.scalars(
                select(UtilityBillImport).where(
                    UtilityBillImport.rate_version_id.in_(scoped_version_ids)
                )
            )
            if item.id not in bill_ids
        )
        privacy_source_detachable_version_ids.difference_update(
            item.rate_version_id
            for item in await session.scalars(
                select(CostCalculationRun).where(
                    CostCalculationRun.rate_version_id.in_(scoped_version_ids)
                )
            )
            if item.utility_account_id not in account_ids
        )
        privacy_source_detachable_version_ids.difference_update(
            item.rate_version_id
            for item in await session.scalars(
                select(TierAllocationSegment).where(
                    TierAllocationSegment.rate_version_id.in_(scoped_version_ids)
                )
            )
            if item.utility_account_id not in account_ids
        )
    all_extractions = list(await session.scalars(select(RateExtractionResult)))
    extraction_artifact = {item.id: item.artifact_id for item in all_extractions}
    candidate_ids_by_artifact: dict[str, set[str]] = {}
    for candidate in all_candidates:
        artifact_id = extraction_artifact.get(candidate.extraction_result_id)
        if artifact_id is not None:
            candidate_ids_by_artifact.setdefault(artifact_id, set()).add(candidate.id)
    version_ids_by_artifact: dict[str, set[str]] = {}
    for item in version_source_rows:
        version_ids_by_artifact.setdefault(item.artifact_id, set()).add(item.rate_version_id)
    bill_ids_by_artifact: dict[str, set[str]] = {}
    for bill in await session.scalars(select(UtilityBillImport)):
        bill_ids_by_artifact.setdefault(bill.artifact_id, set()).add(bill.id)

    artifact_ids: set[str] = set()
    artifacts = list(
        await session.scalars(
            select(RateSourceArtifact).where(RateSourceArtifact.captured_at < reset_at)
        )
    )
    for artifact in artifacts:
        artifact_candidate_ids = candidate_ids_by_artifact.get(artifact.id, set())
        artifact_version_ids = version_ids_by_artifact.get(artifact.id, set())
        artifact_bill_ids = bill_ids_by_artifact.get(artifact.id, set())
        associated = bool(
            artifact_candidate_ids.intersection(candidate_ids)
            or artifact_version_ids.intersection(version_ids)
            or artifact_bill_ids.intersection(bill_ids)
        )
        allowed_version_ids = set(version_ids)
        if artifact_bill_ids.intersection(bill_ids):
            allowed_version_ids.update(privacy_source_detachable_version_ids)
        if (
            associated
            and artifact_candidate_ids.issubset(candidate_ids)
            and artifact_version_ids.issubset(allowed_version_ids)
            and artifact_bill_ids.issubset(bill_ids)
        ):
            artifact_ids.add(artifact.id)

    version_source_keys = [
        {"artifact_id": item.artifact_id, "rate_version_id": item.rate_version_id}
        for item in version_source_rows
        if item.rate_version_id in version_ids or item.artifact_id in artifact_ids
    ]

    extraction_ids = {item.id for item in all_extractions if item.artifact_id in artifact_ids}
    difference_ids = (
        set(
            await session.scalars(
                select(RateCandidateDifference.id).where(
                    RateCandidateDifference.candidate_id.in_(candidate_ids)
                )
            )
        )
        if candidate_ids
        else set()
    )
    decision_ids = (
        set(
            await session.scalars(
                select(RateApprovalDecision.id).where(
                    RateApprovalDecision.candidate_id.in_(candidate_ids)
                )
            )
        )
        if candidate_ids
        else set()
    )
    check_ids: set[str] = set()
    checks = list(
        await session.scalars(
            select(RateSourceCheckRun).where(RateSourceCheckRun.checked_at < reset_at)
        )
    )
    artifact_ids_by_check: dict[str, set[str]] = {}
    for artifact in await session.scalars(select(RateSourceArtifact)):
        artifact_ids_by_check.setdefault(artifact.source_check_id, set()).add(artifact.id)
    for check in checks:
        owned_artifacts = artifact_ids_by_check.get(check.id, set())
        if owned_artifacts and owned_artifacts.issubset(artifact_ids):
            check_ids.add(check.id)

    all_checks = list(await session.scalars(select(RateSourceCheckRun)))
    check_ids_by_job: dict[str, set[str]] = {}
    for check in all_checks:
        check_ids_by_job.setdefault(check.job_id, set()).add(check.id)
    bill_ids_by_job: dict[str, set[str]] = {}
    for bill in await session.scalars(select(UtilityBillImport)):
        bill_ids_by_job.setdefault(bill.job_id, set()).add(bill.id)
    job_ids: set[str] = set()
    for job in await session.scalars(select(BackgroundJob)):
        owned_checks = check_ids_by_job.get(job.id, set())
        owned_bills = bill_ids_by_job.get(job.id, set())
        associated = bool(
            owned_checks.intersection(check_ids) or owned_bills.intersection(bill_ids)
        )
        if associated and owned_checks.issubset(check_ids) and owned_bills.issubset(bill_ids):
            job_ids.add(job.id)

    return {
        "rate_assignment_ids": sorted(assignment_ids),
        "utility_account_adjustment_ids": sorted(account_adjustment_ids),
        "historical_rate_version_ids": sorted(version_ids),
        "rate_season_ids": sorted(rate_season_ids),
        "rate_day_type_ids": sorted(rate_day_type_ids),
        "rate_period_ids": sorted(rate_period_ids),
        "rate_tier_definition_ids": sorted(rate_tier_definition_ids),
        "rate_threshold_rule_ids": sorted(rate_threshold_rule_ids),
        "rate_seasonal_baseline_ids": sorted(rate_seasonal_baseline_ids),
        "baseline_rule_ids": sorted(baseline_rule_ids),
        "fixed_charge_rule_ids": sorted(fixed_charge_rule_ids),
        "rate_adjustment_ids": sorted(rate_adjustment_ids),
        "rate_version_source_keys": sorted(
            version_source_keys,
            key=lambda item: (item["rate_version_id"], item["artifact_id"]),
        ),
        "rate_change_candidate_ids": sorted(candidate_ids),
        "rate_candidate_difference_ids": sorted(difference_ids),
        "rate_approval_decision_ids": sorted(decision_ids),
        "rate_extraction_result_ids": sorted(extraction_ids),
        "rate_source_artifact_ids": sorted(artifact_ids),
        "rate_source_check_run_ids": sorted(check_ids),
        "rate_source_background_job_ids": sorted(job_ids),
        "imported_bill_document_ids": sorted(bill_ids),
        "imported_bill_extraction_revision_ids": sorted(bill_revision_ids),
        "imported_bill_extracted_field_ids": sorted(bill_field_ids),
        "imported_bill_field_conflict_ids": sorted(bill_conflict_ids),
        "imported_bill_documents_preserved": len(all_bill_rows) - len(bill_ids),
    }


def _pricing_history_counts(scope: dict[str, Any]) -> dict[str, int]:
    mapping = {
        "rate_assignments": "rate_assignment_ids",
        "historical_utility_account_adjustments": "utility_account_adjustment_ids",
        "historical_rate_versions": "historical_rate_version_ids",
        "rate_seasons": "rate_season_ids",
        "rate_day_types": "rate_day_type_ids",
        "rate_periods": "rate_period_ids",
        "rate_tier_definitions": "rate_tier_definition_ids",
        "rate_threshold_rules": "rate_threshold_rule_ids",
        "rate_seasonal_baselines": "rate_seasonal_baseline_ids",
        "baseline_rules": "baseline_rule_ids",
        "fixed_charge_rules": "fixed_charge_rule_ids",
        "rate_adjustments": "rate_adjustment_ids",
        "rate_version_sources": "rate_version_source_keys",
        "rate_change_candidates": "rate_change_candidate_ids",
        "rate_candidate_differences": "rate_candidate_difference_ids",
        "rate_approval_decisions": "rate_approval_decision_ids",
        "rate_extraction_results": "rate_extraction_result_ids",
        "rate_source_artifacts": "rate_source_artifact_ids",
        "rate_source_check_runs": "rate_source_check_run_ids",
        "rate_source_background_jobs": "rate_source_background_job_ids",
        "imported_bill_documents": "imported_bill_document_ids",
        "imported_bill_extraction_revisions": "imported_bill_extraction_revision_ids",
        "imported_bill_extracted_fields": "imported_bill_extracted_field_ids",
        "imported_bill_field_conflicts": "imported_bill_field_conflict_ids",
    }
    counts = {key: len(scope.get(scope_key, [])) for key, scope_key in mapping.items()}
    counts["imported_bill_documents_selected_for_deletion"] = counts["imported_bill_documents"]
    counts["imported_bill_documents_preserved"] = int(
        scope.get("imported_bill_documents_preserved", 0)
    )
    counts["historical_rate_source_records"] = sum(
        counts[key]
        for key in (
            "rate_version_sources",
            "rate_change_candidates",
            "rate_candidate_differences",
            "rate_approval_decisions",
            "rate_extraction_results",
            "rate_source_artifacts",
            "rate_source_check_runs",
            "rate_source_background_jobs",
        )
    )
    counts["historical_pricing_rows"] = (
        sum(
            counts[key]
            for key in (
                "rate_assignments",
                "historical_utility_account_adjustments",
                "historical_rate_versions",
                "rate_seasons",
                "rate_day_types",
                "rate_periods",
                "rate_tier_definitions",
                "rate_threshold_rules",
                "rate_seasonal_baselines",
                "baseline_rules",
                "fixed_charge_rules",
                "rate_adjustments",
            )
        )
        + counts["historical_rate_source_records"]
    )
    return counts


async def calculate_plan_snapshot(
    session: AsyncSession,
    *,
    site: Site,
    categories: Iterable[str],
    delete_imported_bill_documents: bool,
    disconnected_sensor_policy: str,
    reset_at: datetime,
    observed_at: datetime,
    offline_after_seconds: int,
    settings: Settings | None = None,
    report_root: Path | None = None,
    log_root: Path | None = None,
    quarantine_journal: Iterable[dict[str, str]] = (),
) -> dict[str, Any]:
    selected = sorted(set(categories))
    devices = list(
        await session.scalars(
            select(Device).where(Device.site_id == site.id).order_by(Device.name, Device.id)
        )
    )
    device_ids = {item.id for item in devices}
    (
        measurement_device_ids,
        daily_rollup_keys,
        monthly_rollup_keys,
        unassigned_measurement_device_ids,
    ) = await _site_measurement_scope(session, site=site)
    historical_devices_outside_current_scope = sorted(measurement_device_ids - device_ids)
    if historical_devices_outside_current_scope:
        raise ProblemError(
            409,
            "Historical sensor scope cannot be reset safely",
            "One or more sensors that recorded data for this site are now assigned "
            "elsewhere. Sensor storage is device-wide and does not record a site "
            "epoch, so the server cannot prove which local backlog belongs to each "
            "site. Move the sensor back and resolve its backlog before transfer, or "
            "use a future transfer-boundary migration workflow.",
            "data_reset_historical_device_scope_unsafe",
            extra={"device_ids": historical_devices_outside_current_scope},
        )
    aggregate_ids = set(
        await session.scalars(select(AggregateSet.id).where(AggregateSet.site_id == site.id))
    )
    accounts = list(
        await session.scalars(
            select(UtilityAccount)
            .where(UtilityAccount.site_id == site.id)
            .order_by(UtilityAccount.id)
        )
    )
    active_accounts = [item for item in accounts if item.status == "active"]
    account_ids = {item.id for item in accounts}
    cycle_ids = (
        set(
            await session.scalars(
                select(BillingCycle.id).where(
                    BillingCycle.utility_account_id.in_(account_ids),
                    BillingCycle.starts_at < reset_at,
                )
            )
        )
        if account_ids
        else set()
    )
    cost_run_ids = (
        set(
            await session.scalars(
                select(CostCalculationRun.id).where(
                    CostCalculationRun.utility_account_id.in_(account_ids)
                )
            )
        )
        if account_ids
        else set()
    )
    participants = [
        await _device_plan_snapshot(
            session,
            device,
            observed_at=observed_at,
            offline_after_seconds=offline_after_seconds,
            settings=settings,
        )
        for device in devices
    ]
    pending_configuration_devices = [
        str(item["device_id"]) for item in participants if item.get("pending_configuration_change")
    ]
    if pending_configuration_devices:
        raise ProblemError(
            409,
            "Sensor configuration change pending",
            "Resolve every desired/effective configuration transition before "
            "approving a data reset",
            "data_reset_configuration_change_pending",
            extra={"device_ids": pending_configuration_devices},
        )
    active_deployment_devices = [
        str(item["device_id"])
        for item in participants
        if item.get("active_firmware_deployment_id") is not None
    ]
    if active_deployment_devices:
        raise ProblemError(
            409,
            "Sensor firmware change active",
            "Complete, cancel, or repair every active firmware deployment before "
            "approving a data reset",
            "data_reset_firmware_change_active",
            extra={"device_ids": active_deployment_devices},
        )
    pricing = [
        snapshot
        for account in active_accounts
        if (snapshot := await _active_pricing_snapshot(session, account, reset_at=reset_at))
        is not None
    ]
    device_circuit_ids = {item.circuit_id for item in devices if item.circuit_id is not None}
    member_filter: Any = AggregateMember.device_id.in_(device_ids)
    if device_circuit_ids:
        member_filter = or_(
            member_filter,
            AggregateMember.circuit_id.in_(device_circuit_ids),
        )
    aggregate_account_rows = (
        await session.execute(
            select(AggregateSet.utility_account_id, AggregateSet.id)
            .join(
                AggregateMember,
                AggregateMember.aggregate_set_id == AggregateSet.id,
            )
            .where(
                AggregateSet.site_id == site.id,
                AggregateSet.utility_account_id.is_not(None),
                member_filter,
            )
            .order_by(AggregateSet.utility_account_id, AggregateSet.id)
        )
    ).tuples()
    post_reset_cost_aggregates: dict[str, str] = {}
    for account_id, aggregate_id in aggregate_account_rows:
        if account_id is not None:
            post_reset_cost_aggregates.setdefault(str(account_id), str(aggregate_id))
    priced_account_ids = {str(item["utility_account_id"]) for item in pricing}
    direct_account_ids = {
        str(item.utility_account_id) for item in devices if item.utility_account_id is not None
    }
    required_post_reset_cost_accounts = (
        direct_account_ids | set(post_reset_cost_aggregates)
    ) & priced_account_ids
    missing_post_reset_cost_aggregates = required_post_reset_cost_accounts - set(
        post_reset_cost_aggregates
    )
    if missing_post_reset_cost_aggregates:
        raise ProblemError(
            409,
            "Post-reset cost scope is incomplete",
            "Every priced sensor account must have an aggregate that can calculate "
            "the first clean reading",
            "data_reset_post_reset_cost_scope_invalid",
            extra={"utility_account_ids": sorted(missing_post_reset_cost_aggregates)},
        )
    pricing_history_scope = (
        await _pricing_history_scope(
            session,
            site_id=site.id,
            account_ids=account_ids,
            protected_version_ids=_protected_pricing_version_ids(pricing),
            reset_at=reset_at,
            delete_imported_bill_documents=delete_imported_bill_documents,
        )
        if "pricing_history" in selected
        else {}
    )
    outputs = await _scoped_output_snapshot(
        session,
        site.id,
        measurement_device_ids,
        report_root=report_root,
        log_root=log_root,
        quarantine_journal=quarantine_journal,
    )
    alert_ids, notification_attempt_ids = await _measurement_alert_scope(
        session, site.id, measurement_device_ids
    )
    site_data_state = await session.get(SiteDataState, site.id)
    counts: dict[str, int] = {
        "raw_readings": 0,
        "normalized_intervals": 0,
        "daily_device_rollups": 0,
        "monthly_device_rollups": 0,
        "site_rollups": 0,
        "device_heartbeats": 0,
        "device_status_snapshots": 0,
        "sequence_gaps": 0,
        "alert_instances": 0,
        "notification_attempts": 0,
        "cost_calculation_runs": 0,
        "cost_interval_results": 0,
        "daily_cost_rollups": 0,
        "tier_allocation_segments": 0,
        "cycle_tier_summaries": 0,
        "tier_projection_snapshots": 0,
        "billing_cycles": 0,
        "account_reconciliation_adjustments": 0,
        "manual_bill_adjustments": 0,
        "utility_bill_cycle_drafts": 0,
        "manual_account_usage": 0,
        "utility_usage_imports": 0,
        "rate_assignments": 0,
        "imported_bill_documents": 0,
        "exports": len(outputs["export_job_ids"]),
        "reports": len(outputs["generated_report_ids"]),
        "export_files": len(outputs["export_artifact_paths"]),
        "report_files": len(outputs["report_artifact_paths"]),
        "log_exports": len(outputs["log_export_job_ids"]),
        "log_export_files": len(outputs["log_export_file_paths"]),
        "sanitized_log_files": len(outputs["sanitized_log_file_paths"]),
    }
    if "measurement_history" in selected:
        counts.update(
            {
                "raw_readings": await _count(session, RawReading, RawReading.site_id == site.id),
                "normalized_intervals": await _count(
                    session,
                    NormalizedInterval,
                    NormalizedInterval.raw_reading_id.in_(
                        select(RawReading.id).where(RawReading.site_id == site.id)
                    ),
                ),
                "daily_device_rollups": await _count(
                    session,
                    DailyDeviceRollup,
                    tuple_(DailyDeviceRollup.device_id, DailyDeviceRollup.local_date).in_(
                        daily_rollup_keys
                    ),
                )
                if daily_rollup_keys
                else 0,
                "monthly_device_rollups": await _count(
                    session,
                    MonthlyDeviceRollup,
                    tuple_(MonthlyDeviceRollup.device_id, MonthlyDeviceRollup.month_start).in_(
                        monthly_rollup_keys
                    ),
                )
                if monthly_rollup_keys
                else 0,
                "site_rollups": await _count(
                    session, SiteRollup, SiteRollup.aggregate_set_id.in_(aggregate_ids)
                )
                if aggregate_ids
                else 0,
                "device_heartbeats": await _count(
                    session,
                    DeviceHeartbeat,
                    DeviceHeartbeat.device_id.in_(measurement_device_ids),
                    _site_assignment_at(
                        site_id=site.id,
                        device_column=DeviceHeartbeat.device_id,
                        timestamp_column=DeviceHeartbeat.received_at,
                        unassigned_device_ids=unassigned_measurement_device_ids,
                    ),
                    or_(
                        DeviceHeartbeat.received_at <= reset_at,
                        DeviceHeartbeat.current_watts.is_not(None),
                    ),
                ),
                "device_status_snapshots": await _count(
                    session,
                    DeviceStatusSnapshot,
                    DeviceStatusSnapshot.device_id.in_(measurement_device_ids),
                    _site_assignment_at(
                        site_id=site.id,
                        device_column=DeviceStatusSnapshot.device_id,
                        timestamp_column=DeviceStatusSnapshot.captured_at,
                        unassigned_device_ids=unassigned_measurement_device_ids,
                    ),
                    DeviceStatusSnapshot.captured_at <= reset_at,
                ),
                "sequence_gaps": await _count(
                    session,
                    SequenceGap,
                    SequenceGap.device_id.in_(measurement_device_ids),
                    _site_assignment_at(
                        site_id=site.id,
                        device_column=SequenceGap.device_id,
                        timestamp_column=SequenceGap.detected_at,
                        unassigned_device_ids=unassigned_measurement_device_ids,
                    ),
                    SequenceGap.detected_at <= reset_at,
                ),
                "alert_instances": len(alert_ids),
                "notification_attempts": len(notification_attempt_ids),
            }
        )
    if account_ids and "cost_history" in selected:
        counts.update(
            {
                "cost_calculation_runs": len(cost_run_ids),
                "cost_interval_results": await _count(
                    session, CostIntervalResult, CostIntervalResult.run_id.in_(cost_run_ids)
                )
                if cost_run_ids
                else 0,
                "daily_cost_rollups": await _count(
                    session, DailyCostRollup, DailyCostRollup.run_id.in_(cost_run_ids)
                )
                if cost_run_ids
                else 0,
                "tier_allocation_segments": await _count(
                    session,
                    TierAllocationSegment,
                    TierAllocationSegment.utility_account_id.in_(account_ids),
                ),
                "cycle_tier_summaries": await _count(
                    session,
                    CycleTierSummary,
                    CycleTierSummary.billing_cycle_id.in_(cycle_ids),
                )
                if cycle_ids
                else 0,
                "tier_projection_snapshots": await _count(
                    session,
                    TierProjectionSnapshot,
                    TierProjectionSnapshot.billing_cycle_id.in_(cycle_ids),
                )
                if cycle_ids
                else 0,
                "billing_cycles": len(cycle_ids),
                "account_reconciliation_adjustments": await _count(
                    session,
                    AccountReconciliationAdjustment,
                    AccountReconciliationAdjustment.billing_cycle_id.in_(cycle_ids),
                )
                if cycle_ids
                else 0,
                "manual_bill_adjustments": await _count(
                    session,
                    ManualBillAdjustment,
                    ManualBillAdjustment.utility_account_id.in_(account_ids),
                    or_(
                        ManualBillAdjustment.billing_cycle_id.is_(None),
                        ManualBillAdjustment.billing_cycle_id.in_(cycle_ids),
                    ),
                ),
                "utility_bill_cycle_drafts": await _count(
                    session,
                    UtilityBillCycleDraft,
                    UtilityBillCycleDraft.utility_account_id.in_(account_ids),
                ),
                "manual_account_usage": await _count(
                    session,
                    ManualAccountUsage,
                    ManualAccountUsage.utility_account_id.in_(account_ids),
                    or_(
                        ManualAccountUsage.effective_at < reset_at,
                        ManualAccountUsage.billing_cycle_id.in_(cycle_ids),
                    ),
                ),
                "utility_usage_imports": await _count(
                    session,
                    UtilityUsageImport,
                    UtilityUsageImport.utility_account_id.in_(account_ids),
                ),
            }
        )
    if "pricing_history" in selected:
        counts.update(_pricing_history_counts(pricing_history_scope))
    return {
        "protocol": DATA_RESET_PROTOCOL,
        "site": {
            "id": site.id,
            "name": site.name,
            "revision": site.revision,
            "timezone": site.timezone,
        },
        "categories": selected,
        "delete_imported_bill_documents": delete_imported_bill_documents,
        "disconnected_sensor_policy": disconnected_sensor_policy,
        "reset_timestamp": reset_at.isoformat(),
        "reset_generation": max(
            [item["data_generation"] for item in participants]
            + [int(site_data_state.data_generation) if site_data_state is not None else 0]
        )
        + 1,
        "participants": participants,
        "counts": counts,
        "estimated_database_bytes": sum(
            value
            for key, value in counts.items()
            if key
            not in {
                "historical_pricing_rows",
                "historical_rate_source_records",
                "imported_bill_documents_selected_for_deletion",
                "imported_bill_documents_preserved",
                "export_files",
                "report_files",
                "log_export_files",
                "sanitized_log_files",
            }
        )
        * 1024,
        "sensor_records_to_delete_now": sum(
            int(item["local_record_count"])
            for item in participants
            if item["classification"] == "connected" and item["probe_status"] == "authenticated"
        ),
        # Retained for older clients; this is now the exact connected-sensor
        # deletion count rather than max(records, sequence-span backlog).
        "estimated_sensor_records": sum(
            int(item["local_record_count"])
            for item in participants
            if item["classification"] == "connected" and item["probe_status"] == "authenticated"
        ),
        "pricing": pricing,
        "post_reset_cost_aggregates": post_reset_cost_aggregates,
        "required_post_reset_cost_account_ids": sorted(required_post_reset_cost_accounts),
        "account_scope_ids": sorted(account_ids),
        "measurement_device_scope_ids": sorted(measurement_device_ids),
        "pricing_history_scope": pricing_history_scope,
        "outputs": outputs,
        "preserved": [
            "users_roles_sessions_mfa",
            "site_circuits_aggregates_devices",
            "device_uuid_credentials_network_configuration",
            "device_desired_effective_configuration",
            "notification_and_smtp_configuration",
            "firmware_ota_events_coredumps",
            "current_utility_accounts_and_active_pricing",
        ],
    }


async def create_reset_plan(
    session: AsyncSession,
    *,
    site_id: str,
    requested_by: str,
    categories: Iterable[str],
    delete_imported_bill_documents: bool,
    disconnected_sensor_policy: str,
    offline_after_seconds: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> DataResetPlan:
    now = _aware(now or datetime.now(UTC))
    selected = set(categories)
    if selected != RESET_CATEGORIES:
        raise ProblemError(
            422,
            "Invalid reset categories",
            (
                "A data-only factory reset must include measurement, cost, pricing, "
                "and generated-output history; imported bill documents remain the "
                "only optional privacy scope"
            ),
            "data_reset_categories_invalid",
        )
    if delete_imported_bill_documents and "pricing_history" not in selected:
        raise ProblemError(
            422,
            "Pricing scope required",
            "Imported bill document deletion requires the pricing-history category",
            "data_reset_bill_scope_invalid",
        )
    if disconnected_sensor_policy not in {"block", "defer_until_reconnect"}:
        raise ProblemError(
            422,
            "Invalid disconnected-sensor policy",
            "Choose block or defer_until_reconnect",
            "data_reset_sensor_policy_invalid",
        )
    site = await session.get(Site, site_id)
    if site is None or site.lifecycle_state != "active":
        raise ProblemError(
            404, "Site not found", "The active reset site does not exist", "site_missing"
        )
    active = await session.scalar(
        select(DataResetOperation.id).where(
            DataResetOperation.site_id == site.id,
            DataResetOperation.state.not_in(TERMINAL_OPERATION_STATES),
        )
    )
    if active is not None:
        raise ProblemError(
            409,
            "Data reset already active",
            "Wait for the existing site reset and every pending sensor to finish",
            "data_reset_active",
            extra={"operation_id": active},
        )
    snapshot = await calculate_plan_snapshot(
        session,
        site=site,
        categories=selected,
        delete_imported_bill_documents=delete_imported_bill_documents,
        disconnected_sensor_policy=disconnected_sensor_policy,
        reset_at=now,
        observed_at=now,
        offline_after_seconds=offline_after_seconds,
        settings=settings,
        report_root=settings.report_path if settings is not None else None,
        log_root=settings.log_path if settings is not None else None,
    )
    active_output_jobs = sorted(
        {
            str(job_id)
            for key in (
                "active_export_job_ids",
                "active_report_job_ids",
                "active_log_export_job_ids",
            )
            for job_id in snapshot.get("outputs", {}).get(key, [])
        }
    )
    if active_output_jobs:
        raise ProblemError(
            409,
            "Generated outputs are still running",
            "Wait for scoped report and export jobs to become quiescent, then create a new plan",
            "data_reset_output_jobs_active",
            extra={"job_ids": active_output_jobs},
        )
    plan = DataResetPlan(
        id=new_uuid(),
        site_id=site.id,
        requested_by=requested_by,
        requested_categories=sorted(selected),
        delete_imported_bill_documents=delete_imported_bill_documents,
        disconnected_sensor_policy=disconnected_sensor_policy,
        plan_snapshot=snapshot,
        plan_fingerprint=reset_plan_fingerprint(snapshot),
        revision=1,
        created_at=now,
        expires_at=now + PLAN_TTL,
    )
    session.add(plan)
    await session.flush()
    return plan


def public_plan_payload(plan: DataResetPlan) -> dict[str, Any]:
    snapshot = dict(plan.plan_snapshot or {})
    snapshot.pop("outputs", None)
    snapshot.pop("account_scope_ids", None)
    snapshot.pop("measurement_device_scope_ids", None)
    snapshot.pop("pricing_history_scope", None)
    pricing = []
    for item in snapshot.get("pricing", []):
        safe = dict(item)
        safe.pop("canonical_configuration", None)
        pricing.append(safe)
    snapshot["pricing"] = pricing
    return {
        "plan_id": plan.id,
        "revision": plan.revision,
        "created_at": plan.created_at,
        "expires_at": plan.expires_at,
        "fingerprint": plan.plan_fingerprint,
        "confirmation_phrases": {
            "verified_backup": VERIFIED_BACKUP_CONFIRMATION_PHRASE,
            "permanent_without_backup": NO_BACKUP_CONFIRMATION_PHRASE,
        },
        **snapshot,
    }


async def _revalidate_plan(
    session: AsyncSession,
    plan: DataResetPlan,
    *,
    offline_after_seconds: int,
    now: datetime,
    settings: Settings | None = None,
) -> None:
    if plan.invalidated_at is not None or _aware(plan.expires_at) <= now:
        raise ProblemError(
            409,
            "Reset plan expired",
            "Generate and review a new data-only reset plan",
            "data_reset_plan_expired",
        )
    site = await session.get(Site, plan.site_id)
    if site is None:
        raise ProblemError(404, "Site not found", "The reset site no longer exists", "site_missing")
    reset_at = _aware(datetime.fromisoformat(str(plan.plan_snapshot["reset_timestamp"])))
    current = await calculate_plan_snapshot(
        session,
        site=site,
        categories=plan.requested_categories,
        delete_imported_bill_documents=plan.delete_imported_bill_documents,
        disconnected_sensor_policy=plan.disconnected_sensor_policy,
        reset_at=reset_at,
        observed_at=now,
        offline_after_seconds=offline_after_seconds,
        settings=settings,
        report_root=settings.report_path if settings is not None else None,
        log_root=settings.log_path if settings is not None else None,
    )
    current_fingerprint = reset_plan_fingerprint(current)
    if current_fingerprint != plan.plan_fingerprint:
        plan.invalidated_at = now
        plan.invalidation_reason = "material_state_changed"
        await session.flush()
        raise ProblemError(
            409,
            "Reset plan changed",
            "Site, sensor boundary, pricing, or deletion counts changed; review a new plan",
            "data_reset_plan_stale",
        )


async def create_reset_operation(
    session: AsyncSession,
    *,
    plan_id: str,
    plan_revision: int,
    requested_by: str,
    idempotency_key: str,
    reason: str,
    backup_mode: str,
    confirmation_phrase: str,
    permanent_without_backup_acknowledged: bool,
    offline_after_seconds: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> DataResetOperation:
    now = _aware(now or datetime.now(UTC))
    plan = await session.get(DataResetPlan, plan_id, with_for_update=True)
    if plan is None:
        raise ProblemError(
            404, "Reset plan not found", "Generate a new reset plan", "data_reset_plan_missing"
        )
    if plan.revision != plan_revision:
        raise ProblemError(
            409,
            "Reset plan revision changed",
            "Review the latest plan revision",
            "data_reset_plan_revision_mismatch",
        )
    site = await session.get(Site, plan.site_id)
    assert site is not None
    normalized_reason = " ".join(reason.split())
    if not 8 <= len(normalized_reason) <= 500:
        raise ProblemError(
            422,
            "Reset reason required",
            "Enter an operational reason of 8 to 500 characters",
            "data_reset_reason_invalid",
        )
    if backup_mode not in {"verified_backup", "permanent_without_backup"}:
        raise ProblemError(
            422,
            "Invalid backup mode",
            "Choose a verified backup or permanent execution without backup",
            "data_reset_backup_mode_invalid",
        )
    expected_phrase = (
        VERIFIED_BACKUP_CONFIRMATION_PHRASE
        if backup_mode == "verified_backup"
        else NO_BACKUP_CONFIRMATION_PHRASE
    )
    if confirmation_phrase != expected_phrase:
        raise ProblemError(
            422,
            "Confirmation phrase does not match",
            "Enter the exact site-specific confirmation phrase",
            "data_reset_confirmation_mismatch",
        )
    if backup_mode == "permanent_without_backup" and not permanent_without_backup_acknowledged:
        raise ProblemError(
            422,
            "Permanent reset acknowledgement required",
            "A reset without backup is irreversible and needs separate acknowledgement",
            "data_reset_no_backup_ack_required",
        )
    fingerprint = canonical_sha256(
        {
            "plan_id": plan.id,
            "plan_revision": plan_revision,
            "requested_by": requested_by,
            "reason": normalized_reason,
            "backup_mode": backup_mode,
            "confirmation_phrase": confirmation_phrase,
            "permanent_without_backup_acknowledged": (permanent_without_backup_acknowledged),
        }
    )
    existing = await session.scalar(
        select(DataResetOperation).where(
            DataResetOperation.site_id == plan.site_id,
            DataResetOperation.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise ProblemError(
                409,
                "Idempotency conflict",
                "The idempotency key belongs to a different reset request",
                "idempotency_conflict",
            )
        return existing
    # This is the shared serialization point with configuration, credential,
    # storage, and OTA mutations. A mutation that commits first invalidates the
    # approved snapshot; one that arrives later observes the active reset gate.
    await session.scalar(select(Site.id).where(Site.id == plan.site_id).with_for_update())
    list(
        await session.scalars(
            select(Device.id)
            .where(Device.site_id == plan.site_id)
            .order_by(Device.id)
            .with_for_update()
        )
    )
    await _revalidate_plan(
        session,
        plan,
        offline_after_seconds=offline_after_seconds,
        now=now,
        settings=settings,
    )
    participant_snapshots = list(plan.plan_snapshot.get("participants", []))
    authentication_failures = [
        item
        for item in participant_snapshots
        if item.get("classification") == "authentication_failed"
    ]
    if authentication_failures:
        raise ProblemError(
            409,
            "Sensor authentication must be repaired",
            "A recently connected sensor rejected the authenticated read-only inventory probe",
            "data_reset_sensor_authentication_failed",
            extra={"device_ids": [item.get("device_id") for item in authentication_failures]},
        )
    blocking = [
        item
        for item in participant_snapshots
        if item.get("classification") in {"disconnected", "unsupported"}
    ]
    if blocking and plan.disconnected_sensor_policy == "block":
        raise ProblemError(
            409,
            "All sensors must be ready",
            ("The plan blocks execution while an active sensor is disconnected or unsupported"),
            "data_reset_sensor_unavailable",
            extra={"device_ids": [item.get("device_id") for item in blocking]},
        )
    operation = DataResetOperation(
        id=new_uuid(),
        plan_id=plan.id,
        site_id=plan.site_id,
        requested_by=requested_by,
        state="preparing_sensors",
        revision=1,
        reset_generation=int(plan.plan_snapshot["reset_generation"]),
        reset_timestamp=_aware(datetime.fromisoformat(str(plan.plan_snapshot["reset_timestamp"]))),
        requested_categories=list(plan.requested_categories),
        delete_imported_bill_documents=plan.delete_imported_bill_documents,
        disconnected_sensor_policy=plan.disconnected_sensor_policy,
        backup_mode=backup_mode,
        reason=normalized_reason,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        plan_revision=plan_revision,
        started_at=now,
        created_at=now,
        updated_at=now,
        final_evidence={"plan_fingerprint": plan.plan_fingerprint},
    )
    session.add(operation)
    await session.flush()
    for snapshot in participant_snapshots:
        classification = str(snapshot["classification"])
        participant_state = (
            "pending"
            if classification == "connected"
            else "not_applicable"
            if classification in {"revoked", "removed"}
            else "unsupported"
            if classification == "unsupported"
            else "unreachable"
        )
        participant = DataResetParticipant(
            operation_id=operation.id,
            device_id=str(snapshot["device_id"]),
            state=participant_state,
            planned_classification=classification,
            reset_generation=operation.reset_generation,
            reset_boundary=int(snapshot["boundary"]),
            old_sequence_floor=int(snapshot["old_sequence_floor"]),
            old_next_sequence=int(snapshot["old_next_sequence"]),
            server_highest_contiguous=int(snapshot["server_highest_contiguous"]),
            server_maximum_seen=int(snapshot["server_maximum_seen"]),
            sensor_ack_sequence=int(snapshot["sensor_ack_sequence"]),
            sensor_newest_sequence=int(snapshot["sensor_newest_sequence"]),
            firmware_version=snapshot.get("firmware_version"),
            firmware_build_hash=snapshot.get("firmware_build_hash"),
            card_generation=snapshot.get("card_generation"),
            updated_at=now,
        )
        session.add(participant)
        state = await session.get(DeviceDataState, participant.device_id, with_for_update=True)
        if state is None:
            device = await session.get(Device, participant.device_id)
            assert device is not None
            state = DeviceDataState(
                device_id=device.id,
                site_id=device.site_id,
                data_generation=int(snapshot["data_generation"]),
                reset_boundary=0,
                ingestion_gate="open",
                reset_required_on_reconnect=False,
                generation_updated_at=now,
                updated_at=now,
            )
            session.add(state)
            await session.flush()
        if participant_state == "not_applicable":
            state.active_operation_id = None
            state.ingestion_gate = "open"
            state.reset_required_on_reconnect = False
        else:
            state.active_operation_id = operation.id
            state.ingestion_gate = (
                "preparing" if classification == "connected" else "pending_reconnect"
            )
            state.reset_required_on_reconnect = classification != "connected"
        state.updated_at = now
    await session.flush()
    return operation


async def operation_payload(session: AsyncSession, operation: DataResetOperation) -> dict[str, Any]:
    participants = list(
        await session.scalars(
            select(DataResetParticipant)
            .where(DataResetParticipant.operation_id == operation.id)
            .order_by(DataResetParticipant.device_id)
        )
    )
    device_name_rows = (
        await session.execute(
            select(Device.id, Device.name).where(
                Device.id.in_([item.device_id for item in participants])
            )
        )
    ).tuples()
    device_names: dict[str, str] = {device_id: name for device_id, name in device_name_rows}
    backup = (
        await session.get(BackupRun, operation.backup_run_id)
        if operation.backup_run_id is not None
        else None
    )
    backup_was_verified = operation.backup_verified_at is not None
    backup_is_recoverable = bool(
        operation.backup_mode == "verified_backup"
        and backup_was_verified
        and reset_backup_verification_is_conclusive(backup)
        and backup is not None
        and backup.manifest_hash == operation.backup_checksum
    )
    return {
        "operation_id": operation.id,
        "plan_id": operation.plan_id,
        "site_id": operation.site_id,
        "state": operation.state,
        "stage": operation.state,
        "revision": operation.revision,
        "reset_generation": operation.reset_generation,
        "reset_timestamp": operation.reset_timestamp,
        "backup": {
            "mode": operation.backup_mode,
            "backup_id": operation.backup_run_id,
            # BackupRun.path is trusted worker-only recovery state and may be
            # an absolute host path. The browser receives the opaque run ID,
            # never a filesystem location.
            "reference": operation.backup_run_id,
            "manifest_hash": operation.backup_checksum,
            "verified_at": operation.backup_verified_at,
            "was_verified": backup_was_verified,
            "recoverable": backup_is_recoverable,
        },
        "participants": [
            {
                "device_id": item.device_id,
                "name": device_names[item.device_id],
                "state": item.state,
                "reset_generation": item.reset_generation,
                "reset_boundary": item.reset_boundary,
                "new_sequence_floor": item.new_sequence_floor,
                "new_next_sequence": item.new_next_sequence,
                "firmware_version": item.firmware_version,
                "last_attempt_at": item.last_attempt_at,
                "prepared_at": item.prepared_at,
                "committed_at": item.committed_at,
                "verified_at": item.verified_at,
                "failure_code": item.failure_code,
                "failure_summary": item.failure_summary,
            }
            for item in participants
        ],
        "started_at": operation.started_at,
        "central_commit_at": operation.central_commit_at,
        "completed_at": operation.completed_at,
        "failure_code": operation.failure_code,
        "failure_summary": operation.failure_summary,
        "recoverability": (
            "verified_backup"
            if backup_is_recoverable
            else "verified_backup_unavailable"
            if backup_was_verified
            else "irreversible_no_backup"
            if operation.backup_mode == "permanent_without_backup"
            else "backup_pending"
        ),
        "final_evidence": {
            key: value
            for key, value in dict(operation.final_evidence or {}).items()
            if not str(key).startswith("_")
        },
    }


async def queue_reset_backup(
    session: AsyncSession,
    operation: DataResetOperation,
    *,
    now: datetime,
) -> BackupRun:
    if operation.backup_run_id is not None:
        existing = await session.get(BackupRun, operation.backup_run_id)
        if existing is not None:
            return existing
    active = await session.scalar(
        select(BackgroundJob).where(
            BackgroundJob.job_type.in_(
                [
                    "backup_create",
                    "backup_verify",
                    "backup_restore_preflight",
                    "backup_delete",
                    "backup_replace_all",
                ]
            ),
            BackgroundJob.status.in_(["queued", "running"]),
        )
    )
    if active is not None:
        raise ProblemError(
            409,
            "Backup operation already active",
            "The data reset is waiting for the isolated backup service",
            "backup_operation_active",
        )
    backup = BackupRun(
        id=new_uuid(),
        started_at=now,
        status="queued",
        path=None,
        manifest_hash=None,
        verified_at=None,
        verification_details={},
        requested_by=operation.requested_by,
        trigger_type="data_reset",
        encrypted=False,
        verification_attempt_count=0,
        updated_at=now,
    )
    job = BackgroundJob(
        id=new_uuid(),
        job_type="backup_create",
        status="queued",
        requested_by=operation.requested_by,
        requested_at=now,
        scheduled_for=now,
        correlation_id=f"data-reset:{operation.id}",
        dedupe_key="backup:global",
        idempotency_key=f"data-reset:{operation.id}:backup",
        trigger_type="data_reset",
        progress={"backup_run_id": backup.id, "data_reset_operation_id": operation.id},
        result={},
    )
    session.add_all([backup, job])
    operation.backup_run_id = backup.id
    operation.backup_reference = backup.id
    operation.state = "backup_running"
    operation.revision += 1
    operation.updated_at = now
    await session.flush()
    return backup


async def mark_cancel_requested(
    session: AsyncSession, operation: DataResetOperation, *, now: datetime
) -> None:
    if operation.central_commit_at is not None:
        raise ProblemError(
            409,
            "Cancellation is no longer safe",
            "The reset has crossed its irreversible commit boundary",
            "data_reset_cancel_unsafe",
        )
    if operation.state in TERMINAL_OPERATION_STATES:
        raise ProblemError(
            409,
            "Cancellation is unavailable",
            "The reset operation is already terminal",
            "data_reset_cancel_unavailable",
        )
    committed_participant = await session.scalar(
        select(DataResetParticipant.device_id).where(
            DataResetParticipant.operation_id == operation.id,
            DataResetParticipant.commit_authorized_at.is_not(None),
        )
    )
    if committed_participant is not None:
        raise ProblemError(
            409,
            "Cancellation is no longer safe",
            "A sensor has durably authorized commit",
            "data_reset_cancel_unsafe",
        )
    evidence = dict(operation.final_evidence or {})
    evidence["cancel_requested"] = True
    evidence["cancel_requested_at"] = now.isoformat()
    operation.final_evidence = evidence
    operation.updated_at = now
    operation.revision += 1


async def retry_reset_operation(
    session: AsyncSession, operation: DataResetOperation, *, now: datetime
) -> None:
    if operation.state in {"completed", "cancelled", "failed_before_commit"}:
        raise ProblemError(
            409,
            "Reset cannot be retried",
            "Completed, cancelled, and pre-commit-failed operations are terminal",
            "data_reset_retry_unavailable",
        )
    if operation.central_commit_at is None:
        operation.state = "backup_running" if operation.backup_run_id else "preparing_sensors"
    else:
        operation.state = "sensor_commit_running"
    operation.failure_code = None
    operation.failure_summary = None
    operation.updated_at = now
    operation.revision += 1


def safe_artifact_path(root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    resolved_root = root.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    candidate = candidate.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ProblemError(
            409,
            "Reset artifact path rejected",
            "A generated artifact escaped its configured storage root",
            "data_reset_artifact_path_invalid",
        )
    return candidate


def verify_pinned_backup_artifacts(
    backup_directory: Path,
    *,
    expected_manifest_hash: str,
) -> None:
    """Re-verify the complete checksum inventory at the irreversible boundary."""

    invalid = ProblemError(
        409,
        "Verified reset backup unavailable",
        "The pinned backup artifact inventory is missing or no longer matches "
        "its verified checksums",
        "data_reset_backup_artifact_invalid",
    )
    root = backup_directory.resolve()
    manifest_path = root / "manifest.json"
    checksums_path = root / "checksums.sha256"
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        checksums_bytes = checksums_path.read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        raise invalid from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "power-monitor-backup/v2"
        or hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_hash
        or manifest.get("checksums_sha256") != hashlib.sha256(checksums_bytes).hexdigest()
    ):
        raise invalid
    try:
        checksum_lines = checksums_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise invalid from exc
    inventory: dict[str, str] = {}
    for line in checksum_lines:
        if not line or len(line) < 67:
            raise invalid
        expected_hash = line[:64]
        separator = line[64:66]
        relative_value = line[66:]
        if (
            any(character not in "0123456789abcdef" for character in expected_hash)
            or separator not in {"  ", " *"}
            or not relative_value
            or relative_value in inventory
        ):
            raise invalid
        relative = Path(relative_value)
        unresolved_candidate = root / relative
        candidate = unresolved_candidate.resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or root not in candidate.parents
            or unresolved_candidate.is_symlink()
            or not candidate.is_file()
        ):
            raise invalid
        digest = hashlib.sha256()
        try:
            with candidate.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise invalid from exc
        if digest.hexdigest() != expected_hash:
            raise invalid
        inventory[relative_value] = expected_hash
    if not inventory or not ({"database.dump", "database.dump.enc"} & set(inventory)):
        raise invalid


def _scope_present(value: Any, site_id: str, device_ids: set[str]) -> bool:
    if isinstance(value, dict):
        return any(
            _scope_present(key, site_id, device_ids) or _scope_present(item, site_id, device_ids)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_scope_present(item, site_id, device_ids) for item in value)
    if isinstance(value, str):
        return value == site_id or value in device_ids
    return False


def _quarantine_path(root: Path, operation_id: str, original: Path) -> Path:
    relative = original.resolve().relative_to(root.resolve())
    digest = hashlib.sha256(relative.as_posix().encode()).hexdigest()[:16]
    return root.resolve() / ".data-reset-quarantine" / operation_id / digest / relative.name


def _quarantine_operation_root(root: Path, operation_id: str) -> Path:
    return root.resolve() / ".data-reset-quarantine" / operation_id


def _persist_quarantine_journal(
    *, root: Path, operation_id: str, journal: Iterable[dict[str, str]]
) -> None:
    """Persist recovery metadata before the corresponding filesystem move.

    The database and filesystem cannot share one transaction. This sidecar is
    therefore the crash-recovery authority until the database commit records
    the same journal.
    """

    operation_root = _quarantine_operation_root(root, operation_id)
    operation_root.mkdir(parents=True, exist_ok=True)
    entries = [
        dict(entry)
        for entry in journal
        if Path(entry["quarantine"]).resolve() == operation_root
        or operation_root in Path(entry["quarantine"]).resolve().parents
    ]
    sidecar = operation_root / "journal.json"
    temporary = operation_root / ".journal.json.tmp"
    temporary.write_text(
        json.dumps(entries, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(sidecar)


def load_quarantine_journal(*, root: Path, operation_id: str) -> list[dict[str, str]]:
    operation_root = _quarantine_operation_root(root, operation_id)
    sidecar = operation_root / "journal.json"
    if not sidecar.is_file():
        return []
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProblemError(
            409,
            "Reset quarantine journal is invalid",
            "Crash-recovery metadata for a staged reset artifact is unreadable",
            "data_reset_quarantine_journal_invalid",
        ) from exc
    if not isinstance(value, list):
        raise ProblemError(
            409,
            "Reset quarantine journal is invalid",
            "Crash-recovery metadata for a staged reset artifact has an invalid shape",
            "data_reset_quarantine_journal_invalid",
        )
    resolved_root = root.resolve()
    entries: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, dict)
            or not {"original", "quarantine"}.issubset(item)
            or not set(item).issubset(
                {
                    "original",
                    "quarantine",
                    "replacement_kind",
                    "replacement_sha256",
                }
            )
        ):
            raise ProblemError(
                409,
                "Reset quarantine journal is invalid",
                "Crash-recovery metadata contains an unrecognized entry",
                "data_reset_quarantine_journal_invalid",
            )
        original = Path(str(item["original"])).resolve()
        quarantined = Path(str(item["quarantine"])).resolve()
        if (
            resolved_root not in original.parents
            or operation_root not in quarantined.parents
            or resolved_root not in quarantined.parents
        ):
            raise ProblemError(
                409,
                "Reset quarantine journal escaped its storage root",
                "Crash-recovery metadata contains an unsafe artifact path",
                "data_reset_quarantine_journal_invalid",
            )
        entry = {"original": str(original), "quarantine": str(quarantined)}
        replacement_kind = item.get("replacement_kind")
        replacement_sha256 = item.get("replacement_sha256")
        if replacement_kind is not None or replacement_sha256 is not None:
            if (
                replacement_kind != "sanitized_log"
                or not isinstance(replacement_sha256, str)
                or len(replacement_sha256) != 64
                or any(character not in "0123456789abcdef" for character in replacement_sha256)
            ):
                raise ProblemError(
                    409,
                    "Reset quarantine journal is invalid",
                    "Crash-recovery metadata contains invalid replacement evidence",
                    "data_reset_quarantine_journal_invalid",
                )
            entry["replacement_kind"] = replacement_kind
            entry["replacement_sha256"] = replacement_sha256
        entries.append(entry)
    return entries


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _recognized_reset_replacement(path: Path, entry: dict[str, str]) -> bool:
    return bool(
        path.is_file()
        and entry.get("replacement_kind") == "sanitized_log"
        and isinstance(entry.get("replacement_sha256"), str)
        and _file_sha256(path) == entry["replacement_sha256"]
    )


def stage_file_for_reset(
    *,
    root: Path,
    path: Path,
    operation_id: str,
    journal: list[dict[str, str]],
) -> None:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ProblemError(
            409,
            "Reset artifact path rejected",
            "A reset artifact escaped its configured storage root",
            "data_reset_artifact_path_invalid",
        )
    existing_entry = next(
        (entry for entry in journal if Path(entry["original"]).resolve() == resolved),
        None,
    )
    if existing_entry is not None:
        destination = Path(existing_entry["quarantine"]).resolve()
        if destination.exists():
            if resolved.exists() and destination.read_bytes() != resolved.read_bytes():
                if _recognized_reset_replacement(resolved, existing_entry):
                    resolved.unlink()
                else:
                    raise ProblemError(
                        409,
                        "Reset quarantine conflict",
                        "A staged artifact conflicts with its crash-recovery journal",
                        "data_reset_quarantine_conflict",
                    )
            if resolved.exists():
                resolved.unlink()
            return
        if not resolved.exists():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved.replace(destination)
        return
    if not resolved.exists():
        return
    if not resolved.is_file():
        raise ProblemError(
            409,
            "Reset artifact is not a file",
            "Only positively identified generated files can be removed",
            "data_reset_artifact_type_invalid",
        )
    destination = _quarantine_path(resolved_root, operation_id, resolved)
    destination.parent.mkdir(parents=True, exist_ok=True)
    journal.append({"original": str(resolved), "quarantine": str(destination)})
    _persist_quarantine_journal(
        root=resolved_root,
        operation_id=operation_id,
        journal=journal,
    )
    if destination.exists():
        if destination.read_bytes() != resolved.read_bytes():
            raise ProblemError(
                409,
                "Reset quarantine conflict",
                "A different staged artifact already uses this reset path",
                "data_reset_quarantine_conflict",
            )
        resolved.unlink()
    else:
        resolved.replace(destination)


def restore_staged_files(journal: Iterable[dict[str, str]]) -> None:
    entries = list(journal)
    operation_roots: set[Path] = set()
    for entry in reversed(entries):
        original = Path(entry["original"])
        quarantined = Path(entry["quarantine"])
        if len(quarantined.parents) >= 2:
            operation_roots.add(quarantined.parents[1])
        if not quarantined.exists():
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        if original.exists():
            if (
                not original.is_file()
                or not quarantined.is_file()
                or original.read_bytes() != quarantined.read_bytes()
            ):
                if _recognized_reset_replacement(original, entry):
                    original.unlink()
                    quarantined.replace(original)
                    continue
                raise ProblemError(
                    409,
                    "Reset quarantine conflict",
                    "A new artifact conflicts with the pre-commit reset recovery copy",
                    "data_reset_quarantine_conflict",
                )
            quarantined.unlink()
            continue
        quarantined.replace(original)
    for operation_root in operation_roots:
        with suppress(OSError):
            (operation_root / "journal.json").unlink()
        with suppress(OSError):
            (operation_root / ".journal.json.tmp").unlink()


def restore_precommit_quarantine(
    *,
    operation: DataResetOperation,
    roots: Iterable[Path],
) -> int:
    """Restore crash-staged artifacts before a pre-commit operation becomes terminal."""

    if operation.central_commit_at is not None:
        raise ProblemError(
            409,
            "Reset artifact restore is unsafe",
            "Quarantined artifacts cannot be restored after the central commit",
            "data_reset_quarantine_restore_unsafe",
        )
    journal: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for root in roots:
        for entry in load_quarantine_journal(root=root, operation_id=operation.id):
            key = (entry["original"], entry["quarantine"])
            if key not in seen:
                seen.add(key)
                journal.append(entry)
    restore_staged_files(journal)
    return len(journal)


def purge_staged_files(journal: Iterable[dict[str, str]]) -> None:
    parents: set[Path] = set()
    operation_roots: set[Path] = set()
    for entry in journal:
        quarantined = Path(entry["quarantine"])
        parents.add(quarantined.parent)
        if len(quarantined.parents) >= 2:
            operation_roots.add(quarantined.parents[1])
        if quarantined.is_file():
            quarantined.unlink()
    for operation_root in operation_roots:
        with suppress(OSError):
            (operation_root / "journal.json").unlink()
        with suppress(OSError):
            (operation_root / ".journal.json.tmp").unlink()
    for parent in sorted(parents, key=lambda item: len(item.parts), reverse=True):
        with suppress(OSError):
            parent.rmdir()


def _read_log_lines(path: Path) -> list[str]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as source:
            return source.readlines()
    return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)


def _prepare_log_replacement(path: Path, lines: list[str]) -> tuple[Path, str]:
    temporary = path.with_name(f".{path.name}.data-reset-tmp")
    if path.suffix == ".gz":
        with gzip.open(temporary, "wt", encoding="utf-8") as target:
            target.writelines(lines)
    else:
        temporary.write_text("".join(lines), encoding="utf-8")
    return temporary, _file_sha256(temporary)


def sanitize_scoped_logs(
    *,
    log_root: Path,
    operation_id: str,
    site_id: str,
    device_ids: set[str],
    journal: list[dict[str, str]],
) -> int:
    root = log_root.resolve()
    if not root.is_dir():
        return 0
    recovery_sources = {
        Path(entry["original"]).resolve(): Path(entry["quarantine"]).resolve()
        for entry in journal
        if Path(entry["original"]).resolve().parent == root
        and Path(entry["quarantine"]).resolve().is_file()
    }
    candidates = {
        path.resolve()
        for path in root.iterdir()
        if path.is_file() and (path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz"))
    }
    candidates.update(
        path
        for path in recovery_sources
        if path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz")
    )
    sanitized = 0
    for path in sorted(candidates):
        source = path if path.is_file() else recovery_sources.get(path)
        if source is None or not source.is_file():
            continue
        original_lines = _read_log_lines(source)
        replacement: list[str] = []
        changed = False
        for line in original_lines:
            try:
                payload = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                replacement.append(line)
                continue
            if _scope_present(payload, site_id, device_ids):
                redacted = redact_history_values(payload)
                rendered = json.dumps(redacted, sort_keys=True, separators=(",", ":")) + "\n"
                replacement.append(rendered)
                changed = changed or rendered != line
            else:
                replacement.append(line)
        if changed:
            temporary, replacement_sha256 = _prepare_log_replacement(path, replacement)
            stage_file_for_reset(
                root=root,
                path=path,
                operation_id=operation_id,
                journal=journal,
            )
            entry = next(
                item for item in journal if Path(item["original"]).resolve() == path.resolve()
            )
            entry["replacement_kind"] = "sanitized_log"
            entry["replacement_sha256"] = replacement_sha256
            _persist_quarantine_journal(
                root=root,
                operation_id=operation_id,
                journal=journal,
            )
            temporary.replace(path)
            sanitized += 1
        elif path in recovery_sources:
            # A prior process may have crashed after writing the sanitized
            # replacement but before committing the database transaction.
            sanitized += 1
    return sanitized


async def _delete_count(session: AsyncSession, statement: Any) -> int:
    result = await session.execute(statement)
    return int(result.rowcount or 0)


async def _delete_in_bounded_batches(
    session: AsyncSession,
    model: Any,
    id_column: Any,
    *criteria: Any,
    batch_size: int = 5_000,
) -> int:
    """Bound individual delete statements while retaining one atomic reset commit."""

    deleted = 0
    while True:
        identifiers = list(
            await session.scalars(
                select(id_column).where(*criteria).order_by(id_column).limit(batch_size)
            )
        )
        if not identifiers:
            return deleted
        deleted += await _delete_count(
            session,
            delete(model).where(id_column.in_(identifiers)),
        )
        await session.flush()


async def _stage_generated_outputs(
    session: AsyncSession,
    *,
    operation: DataResetOperation,
    plan: DataResetPlan,
    device_ids: set[str],
    report_root: Path,
    log_root: Path,
    journal: list[dict[str, str]],
) -> dict[str, int]:
    output_snapshot = dict(plan.plan_snapshot.get("outputs", {}))
    current_output_snapshot = await _scoped_output_snapshot(
        session,
        operation.site_id,
        device_ids,
        report_root=report_root,
        log_root=log_root,
        quarantine_journal=journal,
    )
    if current_output_snapshot != output_snapshot:
        raise ProblemError(
            409,
            "Generated-output scope changed",
            "Generated reports, exports, or scoped logs changed after plan approval",
            "data_reset_plan_stale",
        )
    export_ids = sorted(set(output_snapshot.get("export_job_ids", [])))
    report_ids = sorted(set(output_snapshot.get("generated_report_ids", [])))
    exports = (
        list(await session.scalars(select(ExportJob).where(ExportJob.id.in_(export_ids))))
        if export_ids
        else []
    )
    reports = (
        list(
            await session.scalars(select(GeneratedReport).where(GeneratedReport.id.in_(report_ids)))
        )
        if report_ids
        else []
    )
    for export in exports:
        path = safe_artifact_path(report_root, export.file_path)
        if path is not None:
            stage_file_for_reset(
                root=report_root,
                path=path,
                operation_id=operation.id,
                journal=journal,
            )
    for report in reports:
        path = safe_artifact_path(report_root, report.file_path)
        if path is not None:
            stage_file_for_reset(
                root=report_root,
                path=path,
                operation_id=operation.id,
                journal=journal,
            )
    deleted_exports = (
        await _delete_count(
            session,
            delete(ExportJob).where(ExportJob.id.in_([item.id for item in exports])),
        )
        if exports
        else 0
    )
    deleted_reports = (
        await _delete_count(
            session,
            delete(GeneratedReport).where(GeneratedReport.id.in_([item.id for item in reports])),
        )
        if reports
        else 0
    )
    log_export_job_ids = {str(value) for value in output_snapshot.get("log_export_job_ids", [])}
    deleted_log_exports = (
        await _delete_count(
            session,
            delete(LogExportJob).where(LogExportJob.id.in_(log_export_job_ids)),
        )
        if log_export_job_ids
        else 0
    )
    for relative in output_snapshot.get("log_export_file_paths", []):
        path = safe_artifact_path(log_root, str(relative))
        if path is not None:
            stage_file_for_reset(
                root=log_root,
                path=path,
                operation_id=operation.id,
                journal=journal,
            )
    sanitized_log_files = sanitize_scoped_logs(
        log_root=log_root,
        operation_id=operation.id,
        site_id=operation.site_id,
        device_ids=device_ids,
        journal=journal,
    )
    return {
        "exports": deleted_exports,
        "reports": deleted_reports,
        "export_files": len(output_snapshot.get("export_artifact_paths", [])),
        "report_files": len(output_snapshot.get("report_artifact_paths", [])),
        "log_exports": deleted_log_exports,
        "log_export_files": len(output_snapshot.get("log_export_file_paths", [])),
        "sanitized_log_files": sanitized_log_files,
    }


async def _lock_planned_output_jobs(session: AsyncSession, output_snapshot: dict[str, Any]) -> None:
    """Lock every approved output row so a worker cannot recreate artifacts."""

    specs = (
        (ExportJob, "export_job_ids"),
        (GeneratedReport, "generated_report_ids"),
        (LogExportJob, "log_export_job_ids"),
    )
    for model, key in specs:
        identifiers = {str(value) for value in output_snapshot.get(key, [])}
        if not identifiers:
            continue
        rows = list(
            await session.scalars(select(model).where(model.id.in_(identifiers)).with_for_update())
        )
        if any(getattr(row, "status", None) in {"queued", "running", "preparing"} for row in rows):
            raise ProblemError(
                409,
                "Generated outputs are still running",
                "A scoped report or export job is not quiescent; review a new plan",
                "data_reset_output_jobs_active",
            )


async def _delete_pricing_history_scope(
    session: AsyncSession,
    *,
    scope: dict[str, Any],
    operation: DataResetOperation,
    bill_artifact_root: Path,
    rate_artifact_root: Path,
    journal: list[dict[str, str]],
) -> dict[str, int]:
    """Delete exactly the dependency-closed IDs approved in the plan."""

    def ids(key: str) -> set[str]:
        return {str(value) for value in scope.get(key, [])}

    async def delete_expected(model: Any, key: str) -> int:
        expected = ids(key)
        if not expected:
            return 0
        deleted = await _delete_count(session, delete(model).where(model.id.in_(expected)))
        if deleted != len(expected):
            raise ProblemError(
                409,
                "Pricing history changed",
                "The exact approved pricing-history rows are no longer available",
                "data_reset_plan_stale",
            )
        return deleted

    bill_ids = ids("imported_bill_document_ids")
    revision_ids = ids("imported_bill_extraction_revision_ids")
    artifact_ids = ids("rate_source_artifact_ids")
    bills = (
        list(
            await session.scalars(
                select(UtilityBillImport).where(UtilityBillImport.id.in_(bill_ids))
            )
        )
        if bill_ids
        else []
    )
    revisions = (
        list(
            await session.scalars(
                select(UtilityBillExtractionRevision).where(
                    UtilityBillExtractionRevision.id.in_(revision_ids)
                )
            )
        )
        if revision_ids
        else []
    )
    artifacts = (
        list(
            await session.scalars(
                select(RateSourceArtifact).where(RateSourceArtifact.id.in_(artifact_ids))
            )
        )
        if artifact_ids
        else []
    )
    if len(bills) != len(bill_ids) or len(revisions) != len(revision_ids):
        raise ProblemError(
            409,
            "Pricing history changed",
            "Imported bill evidence changed after the reset plan was approved",
            "data_reset_plan_stale",
        )
    if len(artifacts) != len(artifact_ids):
        raise ProblemError(
            409,
            "Pricing history changed",
            "Rate-source evidence changed after the reset plan was approved",
            "data_reset_plan_stale",
        )
    for bill in bills:
        path = safe_artifact_path(bill_artifact_root, bill.sanitized_evidence_path)
        if path is not None:
            stage_file_for_reset(
                root=bill_artifact_root,
                path=path,
                operation_id=operation.id,
                journal=journal,
            )
    for revision in revisions:
        path = safe_artifact_path(bill_artifact_root, revision.sanitized_text_path)
        if path is not None:
            stage_file_for_reset(
                root=bill_artifact_root,
                path=path,
                operation_id=operation.id,
                journal=journal,
            )
    for artifact in artifacts:
        path = safe_artifact_path(rate_artifact_root, artifact.storage_path)
        if path is not None:
            stage_file_for_reset(
                root=rate_artifact_root,
                path=path,
                operation_id=operation.id,
                journal=journal,
            )

    counts: dict[str, int] = {}
    counts["rate_assignments"] = await delete_expected(RateAssignment, "rate_assignment_ids")
    counts["historical_utility_account_adjustments"] = await delete_expected(
        UtilityAccountAdjustment, "utility_account_adjustment_ids"
    )
    counts["rate_candidate_differences"] = await delete_expected(
        RateCandidateDifference, "rate_candidate_difference_ids"
    )
    counts["rate_approval_decisions"] = await delete_expected(
        RateApprovalDecision, "rate_approval_decision_ids"
    )
    counts["rate_change_candidates"] = await delete_expected(
        RateChangeCandidate, "rate_change_candidate_ids"
    )
    counts["imported_bill_extracted_fields"] = await delete_expected(
        UtilityBillExtractedField, "imported_bill_extracted_field_ids"
    )
    counts["imported_bill_field_conflicts"] = await delete_expected(
        UtilityBillFieldConflict, "imported_bill_field_conflict_ids"
    )

    version_source_keys = list(scope.get("rate_version_source_keys", []))
    version_source_pairs = {
        (str(item["rate_version_id"]), str(item["artifact_id"]))
        for item in version_source_keys
        if isinstance(item, dict) and item.get("rate_version_id") and item.get("artifact_id")
    }
    counts["rate_version_sources"] = (
        await _delete_count(
            session,
            delete(RateVersionSource).where(
                tuple_(
                    RateVersionSource.rate_version_id,
                    RateVersionSource.artifact_id,
                ).in_(version_source_pairs)
            ),
        )
        if version_source_pairs
        else 0
    )
    if counts["rate_version_sources"] != len(version_source_pairs):
        raise ProblemError(
            409,
            "Pricing history changed",
            "Rate-version source relationships changed after plan approval",
            "data_reset_plan_stale",
        )

    counts["imported_bill_extraction_revisions"] = await delete_expected(
        UtilityBillExtractionRevision, "imported_bill_extraction_revision_ids"
    )
    counts["imported_bill_documents"] = await delete_expected(
        UtilityBillImport, "imported_bill_document_ids"
    )
    counts["rate_periods"] = await delete_expected(RatePeriod, "rate_period_ids")
    counts["rate_tier_definitions"] = await delete_expected(
        RateTierDefinition, "rate_tier_definition_ids"
    )
    counts["rate_threshold_rules"] = await delete_expected(
        RateThresholdRule, "rate_threshold_rule_ids"
    )
    counts["rate_seasonal_baselines"] = await delete_expected(
        RateSeasonalBaseline, "rate_seasonal_baseline_ids"
    )
    counts["baseline_rules"] = await delete_expected(BaselineRule, "baseline_rule_ids")
    counts["fixed_charge_rules"] = await delete_expected(FixedChargeRule, "fixed_charge_rule_ids")
    counts["rate_adjustments"] = await delete_expected(RateAdjustment, "rate_adjustment_ids")
    counts["rate_day_types"] = await delete_expected(RateDayType, "rate_day_type_ids")
    counts["rate_seasons"] = await delete_expected(RateSeason, "rate_season_ids")
    counts["historical_rate_versions"] = await delete_expected(
        RateVersion, "historical_rate_version_ids"
    )
    counts["rate_extraction_results"] = await delete_expected(
        RateExtractionResult, "rate_extraction_result_ids"
    )
    counts["rate_source_artifacts"] = await delete_expected(
        RateSourceArtifact, "rate_source_artifact_ids"
    )
    counts["rate_source_check_runs"] = await delete_expected(
        RateSourceCheckRun, "rate_source_check_run_ids"
    )
    counts["rate_source_background_jobs"] = await delete_expected(
        BackgroundJob, "rate_source_background_job_ids"
    )
    counts["imported_bill_documents_selected_for_deletion"] = counts["imported_bill_documents"]
    counts["imported_bill_documents_preserved"] = int(
        scope.get("imported_bill_documents_preserved", 0)
    )
    counts["historical_rate_source_records"] = sum(
        counts[key]
        for key in (
            "rate_version_sources",
            "rate_change_candidates",
            "rate_candidate_differences",
            "rate_approval_decisions",
            "rate_extraction_results",
            "rate_source_artifacts",
            "rate_source_check_runs",
            "rate_source_background_jobs",
        )
    )
    counts["historical_pricing_rows"] = (
        sum(
            counts[key]
            for key in (
                "rate_assignments",
                "historical_utility_account_adjustments",
                "historical_rate_versions",
                "rate_seasons",
                "rate_day_types",
                "rate_periods",
                "rate_tier_definitions",
                "rate_threshold_rules",
                "rate_seasonal_baselines",
                "baseline_rules",
                "fixed_charge_rules",
                "rate_adjustments",
            )
        )
        + counts["historical_rate_source_records"]
    )
    return counts


async def perform_central_reset(
    session: AsyncSession,
    *,
    operation: DataResetOperation,
    report_root: Path,
    log_root: Path,
    bill_artifact_root: Path,
    rate_artifact_root: Path,
    backup_root: Path,
    now: datetime | None = None,
) -> dict[str, int]:
    """Execute the central commit transaction and its file quarantine journal."""

    now = _aware(now or datetime.now(UTC))
    if operation.central_commit_at is not None:
        existing_evidence = dict(operation.final_evidence or {})
        committed_journal = list(existing_evidence.get("_quarantine_journal", []))
        if committed_journal:
            try:
                purge_staged_files(committed_journal)
            except OSError as exc:
                raise ProblemError(
                    500,
                    "Reset artifact cleanup needs attention",
                    "Central reset committed; retry the artifact-purge checkpoint",
                    "data_reset_quarantine_purge_failed",
                ) from exc
            existing_evidence.pop("_quarantine_journal", None)
            existing_evidence["artifact_quarantine_purged"] = True
            operation.final_evidence = existing_evidence
            operation.updated_at = now
            await session.commit()
        return {
            key: int(value)
            for key, value in dict(operation.final_evidence or {}).get("deleted_counts", {}).items()
        }
    if operation.state not in {"backup_verified", "database_reset_running"}:
        raise ProblemError(
            409,
            "Reset is not ready for central commit",
            "Sensors and the selected backup must complete first",
            "data_reset_transition_invalid",
        )
    if operation.backup_mode == "verified_backup" and operation.backup_verified_at is None:
        raise ProblemError(
            409,
            "Verified backup required",
            "Central deletion cannot begin before backup restore verification succeeds",
            "data_reset_backup_not_verified",
        )
    if operation.backup_mode == "verified_backup":
        backup = (
            await session.get(BackupRun, operation.backup_run_id, with_for_update=True)
            if operation.backup_run_id is not None
            else None
        )
        if (
            backup is None
            or not reset_backup_verification_is_conclusive(backup)
            or backup.manifest_hash != operation.backup_checksum
        ):
            raise ProblemError(
                409,
                "Verified reset backup changed",
                "The pinned reset backup is no longer verified and available",
                "data_reset_backup_invalidated",
            )
        assert backup.path is not None
        assert backup.manifest_hash is not None
        backup_directory = safe_artifact_path(backup_root, backup.path)
        if backup_directory is None:
            raise ProblemError(
                409,
                "Verified reset backup unavailable",
                "The pinned backup artifact inventory is missing or no longer matches "
                "its verified checksums",
                "data_reset_backup_artifact_invalid",
            )
        verify_pinned_backup_artifacts(
            backup_directory,
            expected_manifest_hash=backup.manifest_hash,
        )
    plan = await session.get(DataResetPlan, operation.plan_id)
    # Every reset-sensitive writer acquires the same site row before mutation.
    # Hold it from inventory revalidation through the central commit so no
    # account, pricing, bill, output, or topology write can enter the final
    # exact-plan window.
    site = await session.get(Site, operation.site_id, with_for_update=True)
    if plan is None or site is None:
        raise ProblemError(
            409,
            "Reset scope unavailable",
            "The durable reset plan or site is missing",
            "data_reset_scope_missing",
        )
    participants = list(
        await session.scalars(
            select(DataResetParticipant)
            .where(DataResetParticipant.operation_id == operation.id)
            .with_for_update()
        )
    )
    unsafe_participants = [
        item.device_id
        for item in participants
        if not (
            item.state in {"prepared", "not_applicable"}
            or (
                item.planned_classification != "connected"
                and item.state in {"pending_reconnect", "unreachable", "unsupported"}
            )
        )
    ]
    if unsafe_participants:
        raise ProblemError(
            409,
            "Sensors are not prepared",
            "Every connected participant must be prepared before central commit",
            "data_reset_sensor_not_prepared",
            extra={"device_ids": unsafe_participants},
        )
    (
        measurement_device_ids,
        daily_rollup_keys,
        monthly_rollup_keys,
        unassigned_measurement_device_ids,
    ) = await _site_measurement_scope(session, site=site)
    approved_measurement_device_ids = {
        str(value) for value in plan.plan_snapshot.get("measurement_device_scope_ids", [])
    }
    if measurement_device_ids != approved_measurement_device_ids:
        raise ProblemError(
            409,
            "Measurement scope changed",
            "The site's current or historical sensor scope changed after plan approval",
            "data_reset_plan_stale",
        )
    reset_at = _aware(operation.reset_timestamp)
    current_account_ids = set(
        await session.scalars(
            select(UtilityAccount.id).where(UtilityAccount.site_id == operation.site_id)
        )
    )
    account_ids = {str(value) for value in plan.plan_snapshot.get("account_scope_ids", [])}
    if current_account_ids != account_ids:
        raise ProblemError(
            409,
            "Utility-account scope changed",
            "The site's utility-account scope changed after the reset plan was approved",
            "data_reset_plan_stale",
        )
    pricing_snapshots = list(plan.plan_snapshot.get("pricing", []))
    approved_pricing_scope = dict(plan.plan_snapshot.get("pricing_history_scope", {}))
    affected_rate_plan_ids = {
        str(item["rate_plan_id"]) for item in pricing_snapshots if item.get("rate_plan_id")
    }
    affected_rate_plan_ids.update(
        str(value) for value in approved_pricing_scope.get("rate_plan_ids", [])
    )
    # The target Site row is already held. Lock every plan the reset may
    # preserve, rewrite assignments for, or delete before revalidating the
    # approved pricing snapshot. Assignment writers use the same Site ->
    # RatePlan order, so no dependency can change across the central commit.
    await lock_rate_plans(session, affected_rate_plan_ids)
    for pricing in pricing_snapshots:
        account = await session.get(UtilityAccount, str(pricing["utility_account_id"]))
        current_pricing = (
            await _active_pricing_snapshot(session, account, reset_at=reset_at)
            if account is not None
            else None
        )
        if (
            current_pricing is None
            or current_pricing["rate_plan_id"] != pricing["rate_plan_id"]
            or current_pricing["rate_version_id"] != pricing["rate_version_id"]
            or current_pricing["rate_assignment_id"] != pricing["rate_assignment_id"]
            or current_pricing["pricing_configuration_hash"]
            != pricing["pricing_configuration_hash"]
            or current_pricing["future_assignment_ids"] != pricing.get("future_assignment_ids", [])
        ):
            raise ProblemError(
                409,
                "Pricing configuration changed",
                "Active or future pricing changed after the reset plan was approved",
                "data_reset_pricing_changed",
            )
    current_pricing_scope = await _pricing_history_scope(
        session,
        site_id=operation.site_id,
        account_ids=account_ids,
        protected_version_ids=_protected_pricing_version_ids(pricing_snapshots),
        reset_at=reset_at,
        delete_imported_bill_documents=operation.delete_imported_bill_documents,
    )
    if current_pricing_scope != approved_pricing_scope:
        raise ProblemError(
            409,
            "Pricing history changed",
            "Pricing-history rows or preserved dependencies changed after plan approval",
            "data_reset_plan_stale",
        )
    aggregate_ids = set(
        await session.scalars(
            select(AggregateSet.id).where(AggregateSet.site_id == operation.site_id)
        )
    )
    journal: list[dict[str, str]] = list(
        dict(operation.final_evidence or {}).get("_quarantine_journal", [])
    )
    known_entries = {
        (str(Path(item["original"]).resolve()), str(Path(item["quarantine"]).resolve()))
        for item in journal
    }
    for artifact_root in (report_root, log_root, bill_artifact_root, rate_artifact_root):
        for item in load_quarantine_journal(root=artifact_root, operation_id=operation.id):
            key = (item["original"], item["quarantine"])
            if key not in known_entries:
                journal.append(item)
                known_entries.add(key)
    await _lock_planned_output_jobs(session, dict(plan.plan_snapshot.get("outputs", {})))
    current_inventory = await calculate_plan_snapshot(
        session,
        site=site,
        categories=operation.requested_categories,
        delete_imported_bill_documents=operation.delete_imported_bill_documents,
        disconnected_sensor_policy=operation.disconnected_sensor_policy,
        reset_at=reset_at,
        observed_at=now,
        offline_after_seconds=30,
        report_root=report_root,
        log_root=log_root,
        quarantine_journal=journal,
    )
    approved_counts = {
        str(key): int(value) for key, value in dict(plan.plan_snapshot.get("counts", {})).items()
    }
    current_counts = {str(key): int(value) for key, value in current_inventory["counts"].items()}
    if current_counts != approved_counts:
        raise ProblemError(
            409,
            "Deletion inventory changed",
            "Central deletion counts changed after plan approval; review a new plan",
            "data_reset_plan_stale",
        )
    counts: dict[str, int] = {key: 0 for key in approved_counts}
    operation.state = "database_reset_running"
    operation.updated_at = now
    operation.revision += 1
    try:
        if "generated_outputs" in operation.requested_categories:
            counts.update(
                await _stage_generated_outputs(
                    session,
                    operation=operation,
                    plan=plan,
                    device_ids=measurement_device_ids,
                    report_root=report_root,
                    log_root=log_root,
                    journal=journal,
                )
            )
        evidence = dict(operation.final_evidence or {})
        evidence["_quarantine_journal"] = journal
        operation.final_evidence = evidence
        await session.flush()

        if measurement_device_ids:
            alert_ids, notification_attempt_ids = await _measurement_alert_scope(
                session, operation.site_id, measurement_device_ids
            )
            if notification_attempt_ids:
                counts["notification_attempts"] = await _delete_count(
                    session,
                    delete(NotificationAttempt).where(
                        NotificationAttempt.id.in_(notification_attempt_ids)
                    ),
                )
            if alert_ids:
                counts["alert_instances"] = await _delete_count(
                    session,
                    delete(AlertInstance).where(AlertInstance.id.in_(alert_ids)),
                )

        cycle_ids = (
            set(
                await session.scalars(
                    select(BillingCycle.id).where(
                        BillingCycle.utility_account_id.in_(account_ids),
                        BillingCycle.starts_at < reset_at,
                    )
                )
            )
            if account_ids
            else set()
        )
        cost_run_ids = (
            set(
                await session.scalars(
                    select(CostCalculationRun.id).where(
                        CostCalculationRun.utility_account_id.in_(account_ids)
                    )
                )
            )
            if account_ids
            else set()
        )
        if "cost_history" in operation.requested_categories and account_ids:
            if cost_run_ids:
                counts["daily_cost_rollups"] = await _delete_count(
                    session,
                    delete(DailyCostRollup).where(DailyCostRollup.run_id.in_(cost_run_ids)),
                )
                counts["cost_interval_results"] = await _delete_in_bounded_batches(
                    session,
                    CostIntervalResult,
                    CostIntervalResult.id,
                    CostIntervalResult.run_id.in_(cost_run_ids),
                )
                counts["cost_calculation_runs"] = await _delete_count(
                    session,
                    delete(CostCalculationRun).where(CostCalculationRun.id.in_(cost_run_ids)),
                )
            counts["tier_allocation_segments"] = await _delete_in_bounded_batches(
                session,
                TierAllocationSegment,
                TierAllocationSegment.id,
                TierAllocationSegment.utility_account_id.in_(account_ids),
            )
            if cycle_ids:
                counts["cycle_tier_summaries"] = await _delete_count(
                    session,
                    delete(CycleTierSummary).where(
                        CycleTierSummary.billing_cycle_id.in_(cycle_ids)
                    ),
                )
                counts["tier_projection_snapshots"] = await _delete_count(
                    session,
                    delete(TierProjectionSnapshot).where(
                        TierProjectionSnapshot.billing_cycle_id.in_(cycle_ids)
                    ),
                )
                counts["account_reconciliation_adjustments"] = await _delete_count(
                    session,
                    delete(AccountReconciliationAdjustment).where(
                        AccountReconciliationAdjustment.billing_cycle_id.in_(cycle_ids)
                    ),
                )
            counts["manual_bill_adjustments"] = await _delete_count(
                session,
                delete(ManualBillAdjustment).where(
                    ManualBillAdjustment.utility_account_id.in_(account_ids),
                    or_(
                        ManualBillAdjustment.billing_cycle_id.is_(None),
                        ManualBillAdjustment.billing_cycle_id.in_(cycle_ids),
                    ),
                ),
            )
            counts["utility_bill_cycle_drafts"] = await _delete_count(
                session,
                delete(UtilityBillCycleDraft).where(
                    UtilityBillCycleDraft.utility_account_id.in_(account_ids)
                ),
            )
            counts["manual_account_usage"] = await _delete_count(
                session,
                delete(ManualAccountUsage).where(
                    ManualAccountUsage.utility_account_id.in_(account_ids),
                    or_(
                        ManualAccountUsage.effective_at < reset_at,
                        ManualAccountUsage.billing_cycle_id.in_(cycle_ids),
                    ),
                ),
            )
            counts["utility_usage_imports"] = await _delete_count(
                session,
                delete(UtilityUsageImport).where(
                    UtilityUsageImport.utility_account_id.in_(account_ids)
                ),
            )
            if cycle_ids:
                counts["billing_cycles"] = await _delete_count(
                    session, delete(BillingCycle).where(BillingCycle.id.in_(cycle_ids))
                )

        if "measurement_history" in operation.requested_categories:
            counts["normalized_intervals"] = await _delete_in_bounded_batches(
                session,
                NormalizedInterval,
                NormalizedInterval.id,
                NormalizedInterval.raw_reading_id.in_(
                    select(RawReading.id).where(RawReading.site_id == operation.site_id)
                ),
            )
            counts["raw_readings"] = await _delete_in_bounded_batches(
                session,
                RawReading,
                RawReading.id,
                RawReading.site_id == operation.site_id,
            )
            counts["daily_device_rollups"] = (
                await _delete_count(
                    session,
                    delete(DailyDeviceRollup).where(
                        tuple_(DailyDeviceRollup.device_id, DailyDeviceRollup.local_date).in_(
                            daily_rollup_keys
                        )
                    ),
                )
                if daily_rollup_keys
                else 0
            )
            counts["monthly_device_rollups"] = (
                await _delete_count(
                    session,
                    delete(MonthlyDeviceRollup).where(
                        tuple_(MonthlyDeviceRollup.device_id, MonthlyDeviceRollup.month_start).in_(
                            monthly_rollup_keys
                        )
                    ),
                )
                if monthly_rollup_keys
                else 0
            )
            if aggregate_ids:
                counts["site_rollups"] = await _delete_count(
                    session,
                    delete(SiteRollup).where(SiteRollup.aggregate_set_id.in_(aggregate_ids)),
                )
            counts["device_heartbeats"] = await _delete_count(
                session,
                delete(DeviceHeartbeat).where(
                    DeviceHeartbeat.device_id.in_(measurement_device_ids),
                    _site_assignment_at(
                        site_id=operation.site_id,
                        device_column=DeviceHeartbeat.device_id,
                        timestamp_column=DeviceHeartbeat.received_at,
                        unassigned_device_ids=unassigned_measurement_device_ids,
                    ),
                    or_(
                        DeviceHeartbeat.received_at <= reset_at,
                        DeviceHeartbeat.current_watts.is_not(None),
                    ),
                ),
            )
            counts["device_status_snapshots"] = await _delete_count(
                session,
                delete(DeviceStatusSnapshot).where(
                    DeviceStatusSnapshot.device_id.in_(measurement_device_ids),
                    _site_assignment_at(
                        site_id=operation.site_id,
                        device_column=DeviceStatusSnapshot.device_id,
                        timestamp_column=DeviceStatusSnapshot.captured_at,
                        unassigned_device_ids=unassigned_measurement_device_ids,
                    ),
                    DeviceStatusSnapshot.captured_at <= reset_at,
                ),
            )
            counts["sequence_gaps"] = await _delete_count(
                session,
                delete(SequenceGap).where(
                    SequenceGap.device_id.in_(measurement_device_ids),
                    _site_assignment_at(
                        site_id=operation.site_id,
                        device_column=SequenceGap.device_id,
                        timestamp_column=SequenceGap.detected_at,
                        unassigned_device_ids=unassigned_measurement_device_ids,
                    ),
                    SequenceGap.detected_at <= reset_at,
                ),
            )

        baseline_count = 0
        if "pricing_history" in operation.requested_categories:
            counts.update(
                await _delete_pricing_history_scope(
                    session,
                    scope=approved_pricing_scope,
                    operation=operation,
                    bill_artifact_root=bill_artifact_root,
                    rate_artifact_root=rate_artifact_root,
                    journal=journal,
                )
            )
            if not operation.delete_imported_bill_documents and account_ids:
                await session.execute(
                    update(UtilityBillImport)
                    .where(UtilityBillImport.utility_account_id.in_(account_ids))
                    .values(
                        history_cleared_at=reset_at,
                        history_cleared_by=operation.requested_by,
                    )
                )

        actual_approved_counts = {key: int(counts.get(key, 0)) for key in approved_counts}
        if actual_approved_counts != approved_counts:
            raise ProblemError(
                409,
                "Deletion count mismatch",
                "The rows or files deleted did not match the exact approved plan",
                "data_reset_plan_stale",
            )

        accounts = (
            {
                item.id: item
                for item in await session.scalars(
                    select(UtilityAccount).where(UtilityAccount.id.in_(account_ids))
                )
            }
            if account_ids
            else {}
        )
        for pricing in pricing_snapshots:
            account = accounts.get(str(pricing["utility_account_id"]))
            if account is None:
                raise ProblemError(
                    409,
                    "Pricing account changed",
                    "A utility account from the reset plan is no longer available",
                    "data_reset_pricing_changed",
                )
            version = await session.get(RateVersion, str(pricing["rate_version_id"]))
            plan_row = await session.get(RatePlan, str(pricing["rate_plan_id"]))
            if version is None or plan_row is None:
                raise ProblemError(
                    409,
                    "Pricing configuration changed",
                    "The active rate selected in the reset plan is no longer available",
                    "data_reset_pricing_changed",
                )
            future_start = await session.scalar(
                select(func.min(RateAssignment.effective_from)).where(
                    RateAssignment.utility_account_id == account.id,
                    RateAssignment.effective_from > reset_at,
                    RateAssignment.cancelled_at.is_(None),
                )
            )
            assignment = RateAssignment(
                id=new_uuid(),
                utility_account_id=account.id,
                rate_version_id=version.id,
                effective_from=reset_at,
                effective_to=_aware(future_start) if future_start is not None else None,
                assignment_reason=f"Data-only reset baseline {operation.id}",
                assigned_by=operation.requested_by,
                revision=1,
                idempotency_key=f"data-reset:{operation.id}:assignment:{account.id}",
                created_at=now,
            )
            session.add(assignment)
            await session.flush()
            _expected_start, expected_end = expected_cycle_bounds(account, reset_at)
            cycle_end = expected_end
            planned_end = pricing.get("current_cycle_end")
            if planned_end:
                parsed_end = _aware(datetime.fromisoformat(str(planned_end)))
                if parsed_end > reset_at:
                    cycle_end = parsed_end
            cycle = BillingCycle(
                id=new_uuid(),
                utility_account_id=account.id,
                starts_at=reset_at,
                ends_at=cycle_end,
                explicit_meter_dates=False,
                status="expected",
                boundary_source="data_reset",
                override_revision=0,
                recalculation_version=0,
                usage_source_type="sensor_measurements",
                projection_source_type="sensor_trend",
                tier_progress_source_type="sensor_measurements",
                recalculation_required=False,
                created_by=operation.requested_by,
                updated_by=operation.requested_by,
                created_at=now,
                updated_at=now,
            )
            session.add(cycle)
            await session.flush()
            after = await _active_pricing_snapshot(session, account, reset_at=reset_at)
            if (
                after is None
                or after["pricing_configuration_hash"] != pricing["pricing_configuration_hash"]
            ):
                raise ProblemError(
                    409,
                    "Pricing preservation verification failed",
                    "The exact active rate configuration changed during reset",
                    "data_reset_pricing_hash_mismatch",
                )
            session.add(
                DataResetPricingBaseline(
                    id=new_uuid(),
                    operation_id=operation.id,
                    utility_account_id=account.id,
                    rate_plan_id=plan_row.id,
                    rate_version_id=version.id,
                    rate_assignment_id=assignment.id,
                    billing_cycle_id=cycle.id,
                    data_generation=operation.reset_generation,
                    effective_at=reset_at,
                    pricing_configuration_hash=pricing["pricing_configuration_hash"],
                    pricing_snapshot={
                        "rate_plan_code": plan_row.code,
                        "rate_version_number": version.version,
                        "future_assignment_ids": pricing.get("future_assignment_ids", []),
                    },
                    created_at=now,
                )
            )
            baseline_count += 1
        counts["pricing_baselines"] = baseline_count

        if measurement_device_ids:
            for device_event in await session.scalars(
                select(DeviceEvent).where(
                    DeviceEvent.device_id.in_(measurement_device_ids),
                    _site_assignment_at(
                        site_id=operation.site_id,
                        device_column=DeviceEvent.device_id,
                        timestamp_column=DeviceEvent.occurred_at,
                        unassigned_device_ids=unassigned_measurement_device_ids,
                    ),
                )
            ):
                device_event.evidence = redact_history_values(device_event.evidence or {})
            for lifecycle_event in await session.scalars(
                select(DeviceLifecycleEvent).where(
                    DeviceLifecycleEvent.site_id == operation.site_id
                )
            ):
                lifecycle_event.details = redact_history_values(lifecycle_event.details or {})
            for notification_event in await session.scalars(
                select(NotificationEvent).where(NotificationEvent.site_id == operation.site_id)
            ):
                notification_event.details = redact_history_values(notification_event.details or {})
            for audit_record in await session.scalars(select(AuditEvent)):
                if audit_record.object_id in measurement_device_ids | {
                    operation.site_id
                } or _scope_present(
                    audit_record.details, operation.site_id, measurement_device_ids
                ):
                    audit_record.details = redact_history_values(audit_record.details or {})

        site_state = await session.get(SiteDataState, operation.site_id, with_for_update=True)
        if site_state is None:
            site_state = SiteDataState(
                site_id=operation.site_id,
                data_generation=0,
                history_revision=0,
                updated_at=now,
            )
            session.add(site_state)
            await session.flush()
        if site_state.data_generation >= operation.reset_generation:
            raise ProblemError(
                409,
                "Reset generation conflict",
                "The site has already advanced to this or a newer data generation",
                "data_reset_generation_conflict",
            )
        site_state.data_generation = operation.reset_generation
        site_state.history_revision += 1
        site_state.last_reset_operation_id = operation.id
        site_state.last_reset_at = reset_at
        site_state.updated_at = now
        for participant in participants:
            device_state = await session.get(
                DeviceDataState, participant.device_id, with_for_update=True
            )
            if device_state is None:
                raise ProblemError(
                    409,
                    "Sensor reset state missing",
                    "A participant's durable ingestion gate is missing",
                    "data_reset_device_state_missing",
                )
            device_state.data_generation = operation.reset_generation
            device_state.reset_boundary = max(
                int(device_state.reset_boundary), int(participant.reset_boundary)
            )
            device_state.generation_updated_at = now
            device_state.last_reset_at = reset_at
            if participant.state == "not_applicable":
                device_state.ingestion_gate = "open"
                device_state.reset_required_on_reconnect = False
                device_state.active_operation_id = None
            else:
                device_state.ingestion_gate = (
                    "committing" if participant.state == "prepared" else "pending_reconnect"
                )
                device_state.reset_required_on_reconnect = participant.state != "prepared"
            device_state.updated_at = now
            cursor = await session.get(SyncCursor, participant.device_id, with_for_update=True)
            if cursor is None:
                cursor = SyncCursor(
                    device_id=participant.device_id,
                    highest_contiguous_sequence=participant.reset_boundary,
                    maximum_seen_sequence=participant.reset_boundary,
                    data_generation=operation.reset_generation,
                    reset_boundary=participant.reset_boundary,
                    updated_at=now,
                )
                session.add(cursor)
            else:
                cursor.highest_contiguous_sequence = max(
                    cursor.highest_contiguous_sequence, participant.reset_boundary
                )
                cursor.maximum_seen_sequence = max(
                    cursor.maximum_seen_sequence, participant.reset_boundary
                )
                cursor.data_generation = operation.reset_generation
                cursor.reset_boundary = max(cursor.reset_boundary, participant.reset_boundary)
                cursor.updated_at = now

        evidence = dict(operation.final_evidence or {})
        evidence["deleted_counts"] = counts
        evidence["history_revision"] = site_state.history_revision
        evidence["pricing_hashes"] = {
            str(item["utility_account_id"]): item["pricing_configuration_hash"]
            for item in pricing_snapshots
        }
        evidence["backup_mode"] = operation.backup_mode
        operation.final_evidence = evidence
        operation.state = "database_reset_committed"
        operation.central_commit_at = now
        operation.updated_at = now
        operation.revision += 1
        session.add(
            AuditEvent(
                id=new_uuid(),
                occurred_at=now,
                actor_type="system",
                actor_id=None,
                action="data_reset.central_committed",
                object_type="data_reset_operation",
                object_id=operation.id,
                source_ip=None,
                outcome="success",
                correlation_id=f"data-reset:{operation.id}",
                details={
                    "site_id": operation.site_id,
                    "reset_generation": operation.reset_generation,
                    "deleted_counts": counts,
                    "pricing_baseline_count": baseline_count,
                },
            )
        )
        await session.commit()
    except Exception:
        await session.rollback()
        restore_staged_files(journal)
        raise

    operation_id = operation.id
    try:
        purge_staged_files(journal)
        reloaded_operation = await session.get(
            DataResetOperation, operation_id, with_for_update=True
        )
        assert reloaded_operation is not None
        operation = reloaded_operation
        evidence = dict(operation.final_evidence or {})
        evidence.pop("_quarantine_journal", None)
        evidence["artifact_quarantine_purged"] = True
        operation.final_evidence = evidence
        operation.updated_at = datetime.now(UTC)
        await session.commit()
    except OSError as exc:
        await session.rollback()
        reloaded_operation = await session.get(
            DataResetOperation, operation_id, with_for_update=True
        )
        assert reloaded_operation is not None
        operation = reloaded_operation
        operation.state = "attention_required"
        operation.failure_code = "data_reset_quarantine_purge_failed"
        operation.failure_summary = "Central data committed, but quarantined artifacts need cleanup"
        operation.updated_at = datetime.now(UTC)
        await session.commit()
        raise ProblemError(
            500,
            "Reset artifact cleanup needs attention",
            "Central reset committed; retry the artifact-purge checkpoint",
            "data_reset_quarantine_purge_failed",
        ) from exc
    return counts
