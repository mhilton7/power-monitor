from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import hmac
import io
import json
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from time import perf_counter
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy import (
    DateTime,
    Integer,
    Numeric,
    String,
    and_,
    case,
    cast,
    column,
    func,
    literal,
    or_,
    select,
    text,
    true,
    union_all,
    values,
)
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import load_only

from app.db.models import (
    AggregateMember,
    AggregateSet,
    BillingCycle,
    Circuit,
    Device,
    NormalizedInterval,
    RateAssignment,
    RatePlan,
    RateVersion,
    RawReading,
    Site,
    SiteDataState,
    TierAllocationSegment,
    UtilityAccount,
)
from app.home_aggregate import resolve_home_aggregate_devices
from app.problem import ProblemError
from app.rates.documents import engine_plan
from app.rates.engine import RateEngine
from app.rates.service import version_document
from app.schemas import (
    HistoryBucket,
    HistoryIndividualSeries,
    HistoryQueryRequest,
    HistoryQueryResponse,
    HistoryRangeSummary,
    HistoryRateContribution,
    HistoryResolvedScope,
)
from app.security.browser import SessionPrincipal

MAX_HISTORY_RANGE = timedelta(days=366)
MAX_HISTORY_SENSORS = 32
MAX_HISTORY_BUCKETS = 2000
MAX_SOURCE_ROWS = 250_000
HISTORY_CONTINUATION_TTL = timedelta(minutes=10)
# Keep the common 30-day/hourly request in one exact aggregate query. The
# maximum request has 2,000 buckets (6,000 VALUES parameters), safely below
# PostgreSQL's bind limit; chunking still protects alternate/older drivers.
COARSE_WINDOW_CHUNK_SIZE = 1_000
COARSE_HISTORY_BUCKETS = {"1h", "1d"}
ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
logger = structlog.get_logger(__name__)
_POSTGRES_SNAPSHOT_RE = re.compile(r"[0-9A-Fa-f-]+")
# A History request may borrow one extra connection to execute its independent
# measurement and pricing aggregates against the same exported PostgreSQL
# snapshot. Bound that optimization to one request per API process so a burst
# cannot double every request's pool footprint and starve device ingestion.
_parallel_coarse_history_slot = asyncio.Semaphore(1)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _offset_text(value: datetime) -> str:
    offset = value.utcoffset() or timedelta(0)
    sign = "+" if offset >= timedelta(0) else "-"
    total_minutes = abs(int(offset.total_seconds() // 60))
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _csv_safe(value: object) -> object:
    if value is None:
        return ""
    text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


@dataclass
class ResolvedHistoryScope:
    scope_type: str
    display_name: str
    site: Site
    devices: list[Device]
    circuits: dict[str, Circuit]
    allocations: dict[str, Decimal]
    excluded_device_ids: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    overlap: bool = False


@dataclass(frozen=True)
class RateContext:
    account_id: str
    version: RateVersion
    plan: RatePlan
    engine: RateEngine
    start: datetime
    end: datetime
    adjustment_boundaries: tuple[datetime, ...]


@dataclass(frozen=True)
class HistoryExecutionPlan:
    """The minimum backend work needed for one History request."""

    metrics: frozenset[str]
    needs_power: bool
    needs_energy: bool
    needs_voltage: bool
    needs_current: bool
    needs_power_factor: bool
    needs_frequency: bool
    needs_cost: bool
    return_combined: bool
    build_individual: bool

    @classmethod
    def from_request(cls, request: HistoryQueryRequest) -> HistoryExecutionPlan:
        metrics = frozenset(request.metrics)
        needs_cost = bool(metrics & {"energy_cost", "usage_cost"})
        return cls(
            metrics=metrics,
            needs_power="power_w" in metrics,
            needs_energy="energy_kwh" in metrics or needs_cost,
            needs_voltage="voltage_v" in metrics,
            needs_current="current_a" in metrics,
            needs_power_factor="power_factor" in metrics,
            needs_frequency="frequency_hz" in metrics,
            needs_cost=needs_cost,
            return_combined=request.display_mode in {"combined", "combined_plus_individual"},
            build_individual=request.display_mode in {"individual", "combined_plus_individual"},
        )


@dataclass(frozen=True)
class IndexedTierSegment:
    segment: TierAllocationSegment
    start: datetime
    end: datetime


@dataclass(frozen=True)
class TierSegmentIndex:
    """O(1) exact lookup and O(log n + matches) schedule fallback."""

    by_interval: dict[tuple[str, str], tuple[IndexedTierSegment, ...]]
    by_version: dict[str, tuple[IndexedTierSegment, ...]]
    starts_by_version: dict[str, tuple[datetime, ...]]

    @classmethod
    def build(cls, segments: list[TierAllocationSegment]) -> TierSegmentIndex:
        by_interval_lists: dict[tuple[str, str], list[IndexedTierSegment]] = defaultdict(list)
        by_version_lists: dict[str, list[IndexedTierSegment]] = defaultdict(list)
        for segment in segments:
            indexed = IndexedTierSegment(
                segment=segment,
                start=_aware_utc(segment.interval_start),
                end=_aware_utc(segment.interval_end),
            )
            by_version_lists[segment.rate_version_id].append(indexed)
            if segment.normalized_interval_id:
                by_interval_lists[(segment.rate_version_id, segment.normalized_interval_id)].append(
                    indexed
                )
        by_version = {
            version_id: tuple(
                sorted(
                    values,
                    key=lambda item: (
                        item.start,
                        item.end,
                        item.segment.segment_order,
                    ),
                )
            )
            for version_id, values in by_version_lists.items()
        }
        return cls(
            by_interval={
                key: tuple(
                    sorted(
                        values,
                        key=lambda item: (
                            item.start,
                            item.end,
                            item.segment.segment_order,
                        ),
                    )
                )
                for key, values in by_interval_lists.items()
            },
            by_version=by_version,
            starts_by_version={
                version_id: tuple(item.start for item in values)
                for version_id, values in by_version.items()
            },
        )

    def overlapping(
        self,
        *,
        version_id: str,
        normalized_interval_id: str | None,
        start: datetime,
        end: datetime,
    ) -> tuple[tuple[IndexedTierSegment, ...], bool]:
        if normalized_interval_id:
            exact = tuple(
                item
                for item in self.by_interval.get((version_id, normalized_interval_id), ())
                if item.start < end and item.end > start
            )
            if exact:
                return exact, False
        values = self.by_version.get(version_id, ())
        starts = self.starts_by_version.get(version_id, ())
        if not values:
            return (), True
        stop = bisect_left(starts, end)
        cursor = max(0, bisect_right(starts, start) - 1)
        while cursor > 0 and values[cursor - 1].end > start:
            cursor -= 1
        return (
            tuple(item for item in values[cursor:stop] if item.end > start),
            True,
        )


@dataclass(frozen=True)
class CoarseWindow:
    bucket_index: int
    start: datetime
    end: datetime


@dataclass(frozen=True)
class CoarseCostWindow:
    window_id: int
    bucket_index: int
    account_id: str
    start: datetime
    end: datetime
    context: RateContext | None


@dataclass(frozen=True)
class CoarseLoadResult:
    accumulators: list[dict[str, DeviceBucketAccumulator]]
    aggregate_row_count: int
    scanned_reading_count: int
    quality_row_count: int


@dataclass(frozen=True)
class HistoryContinuation:
    snapshot_at: datetime
    pricing_input_fingerprint: str
    summary: HistoryRangeSummary
    selected_summary: HistoryRangeSummary | None
    rate_versions_used: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    mixed_rates: bool
    use_coarse: bool
    token: str


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _history_continuation_key(principal: SessionPrincipal) -> bytes:
    """Bind a continuation to the authenticated browser session.

    API requests always have a BrowserSession with a high-entropy CSRF hash.
    The fallback exists only for direct service-level benchmark/test calls that
    intentionally construct a principal without a browser session.
    """
    session_hash = getattr(principal.session, "csrf_hash", None)
    if session_hash:
        return hashlib.sha256(f"history:{session_hash}".encode()).digest()
    return hashlib.sha256(b"history:direct-service-call").digest()


def _history_request_fingerprint(
    request: HistoryQueryRequest,
    resolved: ResolvedHistoryScope,
    bucket: str,
    source_strategy: str,
    history_revision: int,
) -> str:
    canonical = {
        "request": request.model_dump(
            mode="json",
            exclude={"page", "continuation_token"},
        ),
        "bucket": bucket,
        "source_strategy": source_strategy,
        "site_id": resolved.site.id,
        "history_revision": history_revision,
        "device_ids": [device.id for device in resolved.devices],
        "allocations": {key: str(value) for key, value in sorted(resolved.allocations.items())},
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _encode_history_continuation(
    *,
    principal: SessionPrincipal,
    fingerprint: str,
    snapshot_at: datetime,
    pricing_input_fingerprint: str,
    summary: HistoryRangeSummary,
    selected_summary: HistoryRangeSummary | None,
    rate_versions_used: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    mixed_rates: bool,
    use_coarse: bool,
) -> str:
    payload = {
        "version": 2,
        "expires_at": int((datetime.now(UTC) + HISTORY_CONTINUATION_TTL).timestamp()),
        "fingerprint": fingerprint,
        "snapshot_at": snapshot_at.isoformat(),
        "pricing_input_fingerprint": pricing_input_fingerprint,
        "summary": summary.model_dump(mode="json"),
        "selected_summary": (
            selected_summary.model_dump(mode="json") if selected_summary is not None else None
        ),
        "rate_versions_used": rate_versions_used,
        "warnings": warnings,
        "mixed_rates": mixed_rates,
        "use_coarse": use_coarse,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    signature = hmac.new(_history_continuation_key(principal), encoded, hashlib.sha256).digest()
    return f"{_base64url_encode(encoded)}.{_base64url_encode(signature)}"


def _decode_history_continuation(
    *,
    token: str,
    principal: SessionPrincipal,
    fingerprint: str,
) -> HistoryContinuation:
    try:
        payload_part, signature_part = token.split(".", maxsplit=1)
        encoded = _base64url_decode(payload_part)
        supplied_signature = _base64url_decode(signature_part)
        expected_signature = hmac.new(
            _history_continuation_key(principal), encoded, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("signature mismatch")
        payload = json.loads(encoded)
        if payload.get("version") != 2:
            raise ValueError("unsupported version")
        if not hmac.compare_digest(str(payload.get("fingerprint", "")), fingerprint):
            raise ValueError("query mismatch")
        if int(payload["expires_at"]) < int(datetime.now(UTC).timestamp()):
            raise ValueError("expired")
        snapshot_at = _aware_utc(datetime.fromisoformat(str(payload["snapshot_at"])))
        pricing_input_fingerprint = str(payload["pricing_input_fingerprint"])
        if len(pricing_input_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in pricing_input_fingerprint
        ):
            raise ValueError("invalid pricing-input fingerprint")
        summary = HistoryRangeSummary.model_validate(payload["summary"])
        selected_payload = payload.get("selected_summary")
        selected_summary = (
            HistoryRangeSummary.model_validate(selected_payload)
            if selected_payload is not None
            else None
        )
        rate_versions_used = [dict(item) for item in payload.get("rate_versions_used", [])]
        warnings = [dict(item) for item in payload.get("warnings", [])]
        mixed_rates = bool(payload.get("mixed_rates", False))
        use_coarse = bool(payload.get("use_coarse", False))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProblemError(
            409,
            "History continuation is no longer valid",
            "Restart the History query from page 1",
            "history_continuation_invalid",
        ) from exc
    return HistoryContinuation(
        snapshot_at=snapshot_at,
        pricing_input_fingerprint=pricing_input_fingerprint,
        summary=summary,
        selected_summary=selected_summary,
        rate_versions_used=rate_versions_used,
        warnings=warnings,
        mixed_rates=mixed_rates,
        use_coarse=use_coarse,
        token=token,
    )


def _update_history_input_hash(hasher: Any, value: Any) -> None:
    """Add one unambiguous canonical record to the pricing-input digest."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    hasher.update(len(encoded).to_bytes(8, "big"))
    hasher.update(encoded)


async def _history_pricing_input_fingerprint(
    session: AsyncSession,
    *,
    contexts: dict[str, list[RateContext]],
    device_accounts: dict[str, str | None],
    start: datetime,
    end: datetime,
) -> str:
    """Hash every mutable pricing/allocation input used by a paged query.

    Raw readings are bounded separately by ``snapshot_at``. Published tariff
    components are represented by the exact normalized engine plan, while
    effective assignments are represented by the derived context windows. For
    tiered/hybrid pricing, the digest additionally covers each overlapping
    billing-cycle revision. Tier allocation rows are immutable facts appended
    under that revision in the same transaction that advances the cycle, so
    hashing every segment again would add range-linear work without adding a
    distinct revision signal. A continuation is therefore either calculated
    from the same inputs as page one or rejected before any page data is
    returned.
    """

    hasher = hashlib.sha256()
    _update_history_input_hash(
        hasher,
        {
            "schema": "power-monitor-history-pricing-inputs/1",
            "start": start,
            "end": end,
            "device_accounts": sorted(device_accounts.items()),
        },
    )
    tier_pairs: set[tuple[str, str]] = set()
    for account_id in sorted(contexts):
        for context in contexts[account_id]:
            _update_history_input_hash(
                hasher,
                {
                    "account_id": account_id,
                    "context_start": context.start,
                    "context_end": context.end,
                    "plan": {
                        "id": context.plan.id,
                        "code": context.plan.code,
                        "name": context.plan.name,
                        "currency": context.plan.currency,
                        "timezone": context.plan.timezone,
                        "status": context.plan.status,
                        "lifecycle_revision": context.plan.lifecycle_revision,
                    },
                    "version": {
                        "id": context.version.id,
                        "number": context.version.version,
                        "effective_from": context.version.effective_from,
                        "effective_to": context.version.effective_to,
                        "timezone": context.version.timezone,
                        "currency": context.version.currency,
                        "pricing_model": context.version.pricing_model,
                        "content_hash": context.version.content_hash,
                        "status": context.version.status,
                        "lifecycle_revision": context.version.lifecycle_revision,
                        "immutable_after_use": context.version.immutable_after_use,
                    },
                    "engine_plan": context.engine.plan,
                    "adjustment_boundaries": context.adjustment_boundaries,
                },
            )
            if context.engine.pricing_model in {"tiered", "time_of_use_tiered"}:
                tier_pairs.add((account_id, context.version.id))

    if not tier_pairs:
        return hasher.hexdigest()

    account_ids = sorted({account_id for account_id, _ in tier_pairs})
    cycles = await session.stream(
        select(
            BillingCycle.id,
            BillingCycle.utility_account_id,
            BillingCycle.starts_at,
            BillingCycle.ends_at,
            BillingCycle.status,
            BillingCycle.boundary_source,
            BillingCycle.override_revision,
            BillingCycle.recalculation_version,
            BillingCycle.usage_source_type,
            BillingCycle.tier_progress_source_type,
            BillingCycle.recalculation_required,
            BillingCycle.locked_snapshot_hash,
            BillingCycle.updated_at,
        )
        .where(
            BillingCycle.utility_account_id.in_(account_ids),
            BillingCycle.starts_at < end,
            BillingCycle.ends_at > start,
        )
        .order_by(
            BillingCycle.utility_account_id,
            BillingCycle.starts_at,
            BillingCycle.ends_at,
            BillingCycle.id,
        )
    )
    async for row in cycles:
        _update_history_input_hash(hasher, ("billing_cycle", *row))
    return hasher.hexdigest()


def _raise_history_pricing_snapshot_changed() -> None:
    raise ProblemError(
        409,
        "History pricing changed during pagination",
        "Restart the History query from page 1",
        "history_continuation_pricing_changed",
    )


@dataclass
class CostPart:
    account_id: str
    plan_id: str
    plan_name: str
    version_id: str
    version_number: int
    effective_from: date
    tou_period: str
    tier_id: str | None = None
    tier_name: str | None = None
    cumulative_start_kwh: Decimal | None = None
    cumulative_end_kwh: Decimal | None = None
    recalculation_version: int | None = None
    usage_authority_type: str | None = None
    energy_kwh: Decimal = ZERO
    cost: Decimal = ZERO


@dataclass
class DeviceBucketAccumulator:
    coverage_ranges: list[tuple[datetime, datetime]] = field(default_factory=list)
    power_weighted: Decimal = ZERO
    power_seconds: Decimal = ZERO
    peak_power_w: Decimal | None = None
    energy_kwh: Decimal = ZERO
    energy_available: bool = False
    voltage_weighted: Decimal = ZERO
    voltage_seconds: Decimal = ZERO
    voltage_min_v: Decimal | None = None
    voltage_max_v: Decimal | None = None
    current_weighted: Decimal = ZERO
    current_seconds: Decimal = ZERO
    factor_weighted: Decimal = ZERO
    factor_weight: Decimal = ZERO
    frequency_weighted: Decimal = ZERO
    frequency_seconds: Decimal = ZERO
    quality_flags: set[str] = field(default_factory=set)
    cost_parts: dict[tuple[str, str, str, str | None, Decimal, int | None], CostPart] = field(
        default_factory=dict
    )
    cost_missing: bool = False

    def add_cost(
        self,
        *,
        context: RateContext,
        tou_period: str,
        rate: Decimal,
        energy_kwh: Decimal,
        cost: Decimal,
        tier_id: str | None = None,
        tier_name: str | None = None,
        cumulative_start_kwh: Decimal | None = None,
        cumulative_end_kwh: Decimal | None = None,
        recalculation_version: int | None = None,
        usage_authority_type: str | None = None,
    ) -> None:
        key = (
            context.account_id,
            context.version.id,
            tou_period,
            tier_id,
            rate,
            recalculation_version,
        )
        part = self.cost_parts.get(key)
        if part is None:
            part = CostPart(
                account_id=context.account_id,
                plan_id=context.plan.id,
                plan_name=context.plan.name,
                version_id=context.version.id,
                version_number=context.version.version,
                effective_from=context.version.effective_from,
                tou_period=tou_period,
                tier_id=tier_id,
                tier_name=tier_name,
                cumulative_start_kwh=cumulative_start_kwh,
                cumulative_end_kwh=cumulative_end_kwh,
                recalculation_version=recalculation_version,
                usage_authority_type=usage_authority_type,
            )
            self.cost_parts[key] = part
        part.energy_kwh += energy_kwh
        part.cost += cost
        if cumulative_start_kwh is not None:
            part.cumulative_start_kwh = (
                cumulative_start_kwh
                if part.cumulative_start_kwh is None
                else min(part.cumulative_start_kwh, cumulative_start_kwh)
            )
        if cumulative_end_kwh is not None:
            part.cumulative_end_kwh = (
                cumulative_end_kwh
                if part.cumulative_end_kwh is None
                else max(part.cumulative_end_kwh, cumulative_end_kwh)
            )


def _merge_duration(ranges: list[tuple[datetime, datetime]]) -> Decimal:
    if not ranges:
        return ZERO
    ordered = sorted(ranges)
    start, end = ordered[0]
    total = ZERO
    for next_start, next_end in ordered[1:]:
        if next_start <= end:
            end = max(end, next_end)
            continue
        total += Decimal(str((end - start).total_seconds()))
        start, end = next_start, next_end
    return total + Decimal(str((end - start).total_seconds()))


def _circuit_ancestor(
    child_id: str | None, possible_ancestor: str | None, circuits: dict[str, Circuit]
) -> bool:
    if child_id is None or possible_ancestor is None or child_id == possible_ancestor:
        return child_id == possible_ancestor and child_id is not None
    current = circuits.get(child_id)
    visited: set[str] = set()
    while current and current.parent_id and current.parent_id not in visited:
        if current.parent_id == possible_ancestor:
            return True
        visited.add(current.parent_id)
        current = circuits.get(current.parent_id)
    return False


def _overlap_pairs(
    devices: list[Device], circuits: dict[str, Circuit]
) -> list[tuple[Device, Device]]:
    conflicts: list[tuple[Device, Device]] = []
    for index, left in enumerate(devices):
        for right in devices[index + 1 :]:
            if left.id == right.id:
                conflicts.append((left, right))
                continue
            if (
                left.circuit_id
                and right.circuit_id
                and (
                    _circuit_ancestor(left.circuit_id, right.circuit_id, circuits)
                    or _circuit_ancestor(right.circuit_id, left.circuit_id, circuits)
                )
            ):
                conflicts.append((left, right))
    return conflicts


async def resolve_history_scope(
    session: AsyncSession, principal: SessionPrincipal, request: HistoryQueryRequest
) -> ResolvedHistoryScope:
    scope = request.scope
    selected_ids: list[str] = []
    allocations: dict[str, Decimal] = {}
    site_id: str | None = None
    display_name = "History"
    warnings: list[dict[str, Any]] = []

    if scope.type == "device":
        selected_ids = [scope.device_id or ""]
    elif scope.type == "devices":
        selected_ids = list(scope.device_ids)
    elif scope.type == "circuit":
        circuit = await session.get(Circuit, scope.circuit_id or "")
        if circuit is None or not principal.can_access_site(circuit.site_id):
            raise ProblemError(
                404, "Resource not found", "Resource does not exist", "resource_missing"
            )
        site_id = circuit.site_id
        display_name = circuit.name
        selected_ids = list(
            await session.scalars(
                select(Device.id).where(
                    Device.site_id == circuit.site_id,
                    Device.circuit_id == circuit.id,
                    Device.lifecycle_status == "active",
                )
            )
        )
    elif scope.type == "site":
        site_id = scope.site_id
        if not site_id or not principal.can_access_site(site_id):
            raise ProblemError(
                404, "Resource not found", "Resource does not exist", "resource_missing"
            )
        site_devices = list(
            await session.scalars(
                select(Device)
                .where(
                    Device.site_id == site_id,
                    Device.lifecycle_status == "active",
                    Device.revoked_at.is_(None),
                )
                .order_by(Device.name, Device.id)
            )
        )
        site_selection = await resolve_home_aggregate_devices(session, site_devices)
        selected_ids = [device.id for device in site_selection.devices]
        if site_selection.mode == "single_sensor_fallback":
            warnings.append(
                {
                    "code": "single_sensor_site_fallback",
                    "message": (
                        "The only active measurement sensor was included even though no "
                        "explicit Whole Home selection is configured."
                    ),
                    "device_ids": selected_ids,
                }
            )
        elif site_selection.mode == "configuration_required":
            warnings.append(
                {
                    "code": "site_total_configuration_required",
                    "message": (
                        "Active sensors need an explicit, non-overlapping Whole Home "
                        "selection before they can be combined."
                    ),
                    "device_ids": [device.id for device in site_devices],
                }
            )
        for selection_warning in site_selection.warnings:
            warnings.append(
                {
                    "code": "site_total_topology_warning",
                    "message": selection_warning,
                    "device_ids": selected_ids,
                }
            )
    else:
        aggregate = await session.get(AggregateSet, scope.aggregate_set_id or "")
        if aggregate is None or not principal.can_access_site(aggregate.site_id):
            raise ProblemError(
                404, "Resource not found", "Resource does not exist", "resource_missing"
            )
        site_id = aggregate.site_id
        display_name = aggregate.name
        members = list(
            await session.scalars(
                select(AggregateMember).where(AggregateMember.aggregate_set_id == aggregate.id)
            )
        )
        for member in members:
            member_ids: list[str]
            if member.device_id:
                member_ids = [member.device_id]
            else:
                member_ids = list(
                    await session.scalars(
                        select(Device.id).where(
                            Device.site_id == aggregate.site_id,
                            Device.circuit_id == member.circuit_id,
                            Device.lifecycle_status == "active",
                        )
                    )
                )
            for device_id in member_ids:
                if device_id in allocations:
                    warnings.append(
                        {
                            "code": "duplicate_aggregate_member",
                            "message": "A saved aggregate resolves the same sensor more than once.",
                            "device_ids": [device_id],
                        }
                    )
                selected_ids.append(device_id)
                allocations[device_id] = member.allocation_percent / ONE_HUNDRED

    selected_ids = [value for value in selected_ids if value]
    if len(set(selected_ids)) > MAX_HISTORY_SENSORS:
        raise ProblemError(
            422,
            "Too many sensors",
            f"History supports at most {MAX_HISTORY_SENSORS} sensors per query",
            "history_sensor_limit",
        )
    unique_ids = list(dict.fromkeys(selected_ids))
    devices = (
        list(
            await session.scalars(
                select(Device).where(Device.id.in_(unique_ids)).order_by(Device.name, Device.id)
            )
        )
        if unique_ids
        else []
    )
    if len(devices) != len(unique_ids):
        raise ProblemError(404, "Resource not found", "Resource does not exist", "resource_missing")
    for device in devices:
        if not principal.can_access_site(device.site_id):
            raise ProblemError(
                404, "Resource not found", "Resource does not exist", "resource_missing"
            )
    device_sites = {device.site_id for device in devices}
    if site_id:
        device_sites.add(site_id)
    if len(device_sites) > 1:
        raise ProblemError(
            422,
            "Cross-site history is unavailable",
            "Select sensors from one authorized site at a time",
            "history_cross_site",
        )
    if not site_id:
        site_id = next(iter(device_sites), None)
    if site_id is None:
        raise ProblemError(
            422, "Empty history scope", "The selected scope has no site", "history_scope_empty"
        )
    site = await session.get(Site, site_id)
    if site is None or not principal.can_access_site(site.id):
        raise ProblemError(404, "Resource not found", "Resource does not exist", "resource_missing")
    if scope.type == "site":
        display_name = f"{site.name} total"
    elif scope.type in {"device", "devices"}:
        display_name = " + ".join(device.name for device in devices)

    circuit_rows = list(await session.scalars(select(Circuit).where(Circuit.site_id == site.id)))
    circuits = {item.id: item for item in circuit_rows}
    conflicts = _overlap_pairs(devices, circuits)
    excluded: list[str] = []
    if conflicts and scope.type == "site":
        excluded_set: set[str] = set()
        for left, right in conflicts:
            if _circuit_ancestor(left.circuit_id, right.circuit_id, circuits):
                excluded_set.add(left.id)
            elif _circuit_ancestor(right.circuit_id, left.circuit_id, circuits):
                excluded_set.add(right.id)
            else:
                excluded_set.add(max(left.id, right.id))
        devices = [device for device in devices if device.id not in excluded_set]
        excluded = sorted(excluded_set)
        warnings.append(
            {
                "code": "topology_items_excluded",
                "message": (
                    "Overlapping child or duplicate sensors were excluded from the site total."
                ),
                "device_ids": excluded,
            }
        )
        conflicts = _overlap_pairs(devices, circuits)
    if any(device.circuit_id is None for device in devices):
        warnings.append(
            {
                "code": "topology_incomplete",
                "message": (
                    "One or more sensors have no circuit assignment; "
                    "overlap cannot be fully verified."
                ),
                "device_ids": [device.id for device in devices if device.circuit_id is None],
            }
        )
    if conflicts:
        conflict_ids = sorted({device.id for pair in conflicts for device in pair})
        warnings.append(
            {
                "code": "topology_overlap",
                "message": "Selected parent, child, or duplicate circuit measurements overlap.",
                "device_ids": conflict_ids,
            }
        )
        if request.display_mode in {"combined", "combined_plus_individual"}:
            raise ProblemError(
                422,
                "Overlapping history selection",
                "Combined totals cannot include parent/child or duplicate circuit measurements",
                "history_topology_overlap",
                extra={"warnings": warnings},
            )
    for device in devices:
        allocations.setdefault(device.id, Decimal("1"))
    return ResolvedHistoryScope(
        scope_type=scope.type,
        display_name=display_name or site.name,
        site=site,
        devices=devices,
        circuits=circuits,
        allocations=allocations,
        excluded_device_ids=excluded,
        warnings=warnings,
        overlap=bool(conflicts),
    )


def _effective_bounds(version: RateVersion) -> tuple[datetime, datetime]:
    zone = ZoneInfo(version.timezone)
    start = datetime.combine(version.effective_from, time.min, tzinfo=zone).astimezone(UTC)
    end = (
        datetime.combine(
            version.effective_to + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(UTC)
        if version.effective_to
        else datetime.max.replace(tzinfo=UTC)
    )
    return start, end


def _adjustment_boundaries(plan: dict[str, Any], zone: ZoneInfo) -> tuple[datetime, ...]:
    values: set[datetime] = set()
    for adjustment in plan.get("adjustments", []):
        if adjustment.get("effective_from"):
            values.add(
                datetime.combine(
                    date.fromisoformat(str(adjustment["effective_from"])), time.min, tzinfo=zone
                ).astimezone(UTC)
            )
        if adjustment.get("effective_to"):
            values.add(
                datetime.combine(
                    date.fromisoformat(str(adjustment["effective_to"])) + timedelta(days=1),
                    time.min,
                    tzinfo=zone,
                ).astimezone(UTC)
            )
    return tuple(sorted(values))


async def _load_rate_contexts(
    session: AsyncSession,
    scope: ResolvedHistoryScope,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, list[RateContext]], dict[str, str | None]]:
    account_ids = {
        device.utility_account_id for device in scope.devices if device.utility_account_id
    }
    site_accounts = list(
        await session.scalars(select(UtilityAccount).where(UtilityAccount.site_id == scope.site.id))
    )
    fallback_account_id = site_accounts[0].id if len(site_accounts) == 1 else None
    device_accounts = {
        device.id: device.utility_account_id or fallback_account_id for device in scope.devices
    }
    account_ids.update(value for value in device_accounts.values() if value)
    accounts = {item.id: item for item in site_accounts if item.id in account_ids}
    assignments = (
        list(
            await session.scalars(
                select(RateAssignment).where(
                    RateAssignment.utility_account_id.in_(account_ids),
                    RateAssignment.effective_from < end,
                    (RateAssignment.effective_to.is_(None) | (RateAssignment.effective_to > start)),
                )
            )
        )
        if account_ids
        else []
    )
    version_ids = {item.rate_version_id for item in assignments}
    version_ids.update(
        account.active_rate_version_id
        for account in accounts.values()
        if account.active_rate_version_id
    )
    versions = {
        item.id: item
        for item in (
            list(await session.scalars(select(RateVersion).where(RateVersion.id.in_(version_ids))))
            if version_ids
            else []
        )
    }
    plan_ids = {version.rate_plan_id for version in versions.values()}
    plans = {
        item.id: item
        for item in (
            list(await session.scalars(select(RatePlan).where(RatePlan.id.in_(plan_ids))))
            if plan_ids
            else []
        )
    }
    contexts: dict[str, list[RateContext]] = defaultdict(list)
    assignments_by_account: dict[str, list[RateAssignment]] = defaultdict(list)
    for assignment in assignments:
        assignments_by_account[assignment.utility_account_id].append(assignment)

    engine_cache: dict[str, tuple[RateEngine, tuple[datetime, ...]]] = {}
    for account_id, account in accounts.items():
        account_assignments = assignments_by_account.get(account_id, [])
        if not account_assignments and account.active_rate_version_id:
            version = versions.get(account.active_rate_version_id)
            if version:
                effective_start, effective_end = _effective_bounds(version)
                account_assignments = [
                    RateAssignment(
                        utility_account_id=account_id,
                        rate_version_id=version.id,
                        effective_from=effective_start,
                        effective_to=effective_end,
                        created_at=effective_start,
                    )
                ]
        for assignment in account_assignments:
            version = versions.get(assignment.rate_version_id)
            if version is None:
                continue
            plan = plans.get(version.rate_plan_id)
            if plan is None:
                continue
            if version.id not in engine_cache:
                document = await version_document(session, version)
                calculated_plan = engine_plan(document)
                engine = RateEngine(calculated_plan)
                engine_cache[version.id] = (
                    engine,
                    _adjustment_boundaries(calculated_plan, engine.zone),
                )
            engine, adjustment_dates = engine_cache[version.id]
            version_start, version_end = _effective_bounds(version)
            context_start = max(_aware_utc(assignment.effective_from), version_start)
            context_end = min(
                _aware_utc(assignment.effective_to)
                if assignment.effective_to
                else datetime.max.replace(tzinfo=UTC),
                version_end,
            )
            if context_end <= start or context_start >= end or context_end <= context_start:
                continue
            contexts[account_id].append(
                RateContext(
                    account_id=account_id,
                    version=version,
                    plan=plan,
                    engine=engine,
                    start=context_start,
                    end=context_end,
                    adjustment_boundaries=adjustment_dates,
                )
            )
        contexts[account_id].sort(key=lambda item: (item.start, item.end, item.version.id))
    return contexts, device_accounts


async def _coarse_requires_raw_tier_fallback(
    session: AsyncSession,
    *,
    contexts: dict[str, list[RateContext]],
    start: datetime,
    end: datetime,
) -> bool:
    """Keep imported/manual tier allocations on their authoritative raw path.

    Segments without a normalized interval are account-level allocations. They
    cannot be joined to a device in the coarse SQL without either dropping them
    or multiplying them across devices. The existing raw TierSegmentIndex owns
    that fallback and preserves its historical allocation semantics.
    """
    tier_pairs = {
        (account_id, context.version.id)
        for account_id, account_contexts in contexts.items()
        for context in account_contexts
        if context.engine.pricing_model in {"tiered", "time_of_use_tiered"}
    }
    if not tier_pairs:
        return False
    pair_filter = or_(
        *(
            and_(
                TierAllocationSegment.utility_account_id == account_id,
                TierAllocationSegment.rate_version_id == version_id,
            )
            for account_id, version_id in sorted(tier_pairs)
        )
    )
    return (
        await session.scalar(
            select(literal(True))
            .select_from(TierAllocationSegment)
            .join(
                BillingCycle,
                and_(
                    BillingCycle.id == TierAllocationSegment.billing_cycle_id,
                    BillingCycle.recalculation_version
                    == TierAllocationSegment.recalculation_version,
                ),
            )
            .where(
                pair_filter,
                TierAllocationSegment.normalized_interval_id.is_(None),
                TierAllocationSegment.interval_start < end,
                TierAllocationSegment.interval_end > start,
            )
            .limit(1)
        )
        is True
    )


def _automatic_bucket(start: datetime, end: datetime) -> str:
    duration = end - start
    if duration <= timedelta(hours=12):
        return "5m"
    if duration <= timedelta(days=2):
        return "15m"
    if duration <= timedelta(days=31):
        return "1h"
    return "1d"


def _bucket_boundaries(
    start: datetime, end: datetime, bucket: str, zone: ZoneInfo
) -> list[datetime]:
    start = _aware_utc(start)
    end = _aware_utc(end)
    if bucket == "1d":
        local_start = start.astimezone(zone)
        boundaries = [start]
        day = local_start.date() + timedelta(days=1)
        while True:
            boundary = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
            if boundary >= end:
                break
            if boundary > start:
                boundaries.append(boundary)
            day += timedelta(days=1)
        boundaries.append(end)
        return sorted(set(boundaries))
    seconds = {"raw": 60, "5m": 300, "15m": 900, "1h": 3600}[bucket]
    start_epoch = int(start.timestamp())
    cursor_epoch = (start_epoch // seconds + 1) * seconds
    boundaries = [start]
    while cursor_epoch < int(end.timestamp()) and len(boundaries) <= MAX_HISTORY_BUCKETS:
        boundaries.append(datetime.fromtimestamp(cursor_epoch, UTC))
        cursor_epoch += seconds
    boundaries.append(end)
    return boundaries


def _find_cost_contexts(
    contexts: list[RateContext], start: datetime, end: datetime
) -> list[tuple[datetime, datetime, RateContext | None]]:
    boundaries = {start, end}
    for context in contexts:
        if start < context.start < end:
            boundaries.add(context.start)
        if start < context.end < end:
            boundaries.add(context.end)
        boundaries.update(value for value in context.adjustment_boundaries if start < value < end)
    ordered = sorted(boundaries)
    result: list[tuple[datetime, datetime, RateContext | None]] = []
    for left, right in pairwise(ordered):
        midpoint = left + (right - left) / 2
        matching = next((item for item in contexts if item.start <= midpoint < item.end), None)
        result.append((left, right, matching))
    return result


def _apply_cost(
    accumulator: DeviceBucketAccumulator,
    *,
    contexts: list[RateContext],
    start: datetime,
    end: datetime,
    energy_kwh: Decimal,
    normalized_interval_id: str | None,
    tier_segments: TierSegmentIndex | None,
) -> None:
    seconds = Decimal(str((end - start).total_seconds()))
    if seconds <= 0:
        return
    for left, right, context in _find_cost_contexts(contexts, start, end):
        part_seconds = Decimal(str((right - left).total_seconds()))
        part_energy = energy_kwh * part_seconds / seconds
        if context is None:
            accumulator.cost_missing = True
            continue
        if context.engine.pricing_model in {"tiered", "time_of_use_tiered"}:
            matching_segments, account_schedule_fallback = (
                tier_segments.overlapping(
                    version_id=context.version.id,
                    normalized_interval_id=normalized_interval_id,
                    start=left,
                    end=right,
                )
                if tier_segments
                else ((), True)
            )
            if not matching_segments:
                accumulator.cost_missing = True
                accumulator.quality_flags.add("tier_recalculation_required")
                continue
            weighted_seconds = sum(
                (
                    Decimal(
                        str((min(right, indexed.end) - max(left, indexed.start)).total_seconds())
                    )
                    for indexed in matching_segments
                ),
                ZERO,
            )
            for indexed in matching_segments:
                segment = indexed.segment
                segment_start = indexed.start
                segment_end = indexed.end
                overlap_start = max(left, segment_start)
                overlap_end = min(right, segment_end)
                segment_seconds = Decimal(str((segment_end - segment_start).total_seconds()))
                overlap_seconds = Decimal(str((overlap_end - overlap_start).total_seconds()))
                if segment_seconds <= 0 or overlap_seconds <= 0:
                    continue
                fraction = overlap_seconds / segment_seconds
                allocated_energy = (
                    part_energy * overlap_seconds / weighted_seconds
                    if account_schedule_fallback and weighted_seconds
                    else segment.segment_energy_kwh * fraction
                )
                allocated_cost = (
                    allocated_energy * segment.price_per_kwh
                    if account_schedule_fallback
                    else segment.unrounded_energy_charge * fraction
                )
                offset_fraction = (
                    Decimal(str((overlap_start - segment_start).total_seconds())) / segment_seconds
                )
                cumulative_start = (
                    segment.cumulative_start_kwh + segment.segment_energy_kwh * offset_fraction
                )
                cumulative_end = cumulative_start + allocated_energy
                label = (
                    f"{segment.tier_name} / {segment.tou_period}"
                    if segment.tou_period
                    else segment.tier_name
                )
                accumulator.add_cost(
                    context=context,
                    tou_period=label,
                    rate=segment.price_per_kwh,
                    energy_kwh=allocated_energy,
                    cost=allocated_cost,
                    tier_id=segment.tier_stable_id,
                    tier_name=segment.tier_name,
                    cumulative_start_kwh=cumulative_start,
                    cumulative_end_kwh=cumulative_end,
                    recalculation_version=segment.recalculation_version,
                    usage_authority_type=segment.usage_authority_type,
                )
            continue
        calculation = context.engine.calculate(
            start=left, end=right, energy_kwh=part_energy, cost_scope="energy_only"
        )
        energy_charge = calculation.energy_charge
        extra = calculation.total - energy_charge
        for item in calculation.slices:
            extra_share = extra * item.energy_kwh / part_energy if part_energy else ZERO
            cost = item.cost + extra_share
            rate = cost / item.energy_kwh if item.energy_kwh else item.price_per_kwh
            accumulator.add_cost(
                context=context,
                tou_period=item.bucket,
                rate=rate,
                energy_kwh=item.energy_kwh,
                cost=cost,
            )


def _add_reading(
    accumulator: DeviceBucketAccumulator,
    raw: RawReading,
    normalized: NormalizedInterval | None,
    left: datetime,
    right: datetime,
    contexts: list[RateContext],
    tier_segments: TierSegmentIndex | None,
    plan: HistoryExecutionPlan,
) -> None:
    raw_start = _aware_utc(raw.interval_start)
    raw_end = _aware_utc(raw.interval_end)
    duration = Decimal(str((raw_end - raw_start).total_seconds()))
    seconds = Decimal(str((right - left).total_seconds()))
    if duration <= 0 or seconds <= 0:
        return
    accumulator.coverage_ranges.append((left, right))
    accumulator.quality_flags.update(raw.quality_flags)
    if plan.needs_power and raw.power_avg is not None:
        accumulator.power_weighted += raw.power_avg * seconds
        accumulator.power_seconds += seconds
    if plan.needs_power and raw.power_max is not None:
        accumulator.peak_power_w = (
            raw.power_max
            if accumulator.peak_power_w is None
            else max(accumulator.peak_power_w, raw.power_max)
        )
    if plan.needs_energy:
        selected_energy_wh = (
            normalized.selected_energy_wh if normalized else raw.device_interval_energy_wh
        )
        if selected_energy_wh is not None:
            energy_kwh = selected_energy_wh / Decimal("1000") * seconds / duration
            accumulator.energy_kwh += energy_kwh
            accumulator.energy_available = True
            if plan.needs_cost:
                _apply_cost(
                    accumulator,
                    contexts=contexts,
                    start=left,
                    end=right,
                    energy_kwh=energy_kwh,
                    normalized_interval_id=normalized.id if normalized else None,
                    tier_segments=tier_segments,
                )
        else:
            accumulator.quality_flags.add("energy_unavailable")
    if plan.needs_voltage and raw.voltage_avg is not None:
        accumulator.voltage_weighted += raw.voltage_avg * seconds
        accumulator.voltage_seconds += seconds
        minimum = raw.voltage_min if raw.voltage_min is not None else raw.voltage_avg
        maximum = raw.voltage_max if raw.voltage_max is not None else raw.voltage_avg
        accumulator.voltage_min_v = (
            minimum
            if accumulator.voltage_min_v is None
            else min(accumulator.voltage_min_v, minimum)
        )
        accumulator.voltage_max_v = (
            maximum
            if accumulator.voltage_max_v is None
            else max(accumulator.voltage_max_v, maximum)
        )
    if plan.needs_current and raw.current_avg is not None:
        accumulator.current_weighted += raw.current_avg * seconds
        accumulator.current_seconds += seconds
    if plan.needs_power_factor and raw.power_factor is not None:
        weight = abs(raw.power_avg or ZERO) * seconds
        if weight:
            accumulator.factor_weighted += raw.power_factor * weight
            accumulator.factor_weight += weight
    if plan.needs_frequency and raw.frequency_hz is not None:
        accumulator.frequency_weighted += raw.frequency_hz * seconds
        accumulator.frequency_seconds += seconds


def _rate_contributions(
    accumulator: DeviceBucketAccumulator, scale: Decimal = Decimal("1")
) -> list[HistoryRateContribution]:
    return [
        HistoryRateContribution(
            utility_account_id=part.account_id,
            rate_plan_id=part.plan_id,
            rate_plan_name=part.plan_name,
            rate_version_id=part.version_id,
            rate_version=part.version_number,
            rate_effective_from=part.effective_from,
            tou_period=part.tou_period,
            tier_id=part.tier_id,
            tier_name=part.tier_name,
            cumulative_start_kwh=part.cumulative_start_kwh,
            cumulative_end_kwh=part.cumulative_end_kwh,
            recalculation_version=part.recalculation_version,
            usage_authority_type=part.usage_authority_type,
            energy_kwh=part.energy_kwh * scale,
            rate_per_kwh=(part.cost / part.energy_kwh if part.energy_kwh else ZERO),
            energy_cost=part.cost * scale,
        )
        for part in sorted(
            accumulator.cost_parts.values(),
            key=lambda item: (
                item.version_id,
                item.recalculation_version or 0,
                item.cumulative_start_kwh or ZERO,
                item.tou_period,
            ),
        )
    ]


def _labels_from_contributions(
    contributions: list[HistoryRateContribution],
) -> tuple[str | None, Decimal | None, Decimal | None, str | None, str | None, date | None, bool]:
    if not contributions:
        return None, None, None, None, None, None, False
    periods = sorted({item.tou_period for item in contributions})
    plans = sorted({item.rate_plan_name for item in contributions})
    versions = sorted({item.rate_version_id for item in contributions})
    total_energy = sum((item.energy_kwh for item in contributions), ZERO)
    total_cost = sum((item.energy_cost for item in contributions), ZERO)
    rate = total_cost / total_energy if total_energy else contributions[0].rate_per_kwh
    return (
        " + ".join(periods),
        rate,
        total_cost,
        plans[0] if len(plans) == 1 else "Mixed rates",
        versions[0] if len(versions) == 1 else None,
        contributions[0].rate_effective_from if len(versions) == 1 else None,
        len({(item.rate_plan_id, item.rate_version_id) for item in contributions}) > 1,
    )


def _individual_bucket(
    *,
    accumulator: DeviceBucketAccumulator,
    device: Device,
    left: datetime,
    right: datetime,
    zone: ZoneInfo,
) -> HistoryBucket:
    bucket_seconds = Decimal(str((right - left).total_seconds()))
    covered = min(bucket_seconds, _merge_duration(accumulator.coverage_ranges))
    coverage = covered / bucket_seconds * ONE_HUNDRED if bucket_seconds else ZERO
    contributions = _rate_contributions(accumulator)
    period, rate, cost, plan_name, version_id, effective_from, mixed = _labels_from_contributions(
        contributions
    )
    quality = set(accumulator.quality_flags)
    if coverage < ONE_HUNDRED:
        quality.add("partial_coverage")
    if accumulator.energy_available and accumulator.cost_missing:
        quality.add("rate_unavailable")
        cost = None
    local_start = left.astimezone(zone)
    local_end = right.astimezone(zone)
    return HistoryBucket(
        interval_start_utc=left,
        interval_end_utc=right,
        local_start=local_start.isoformat(),
        local_end=local_end.isoformat(),
        utc_offset=_offset_text(local_start),
        series_id=device.id,
        series_name=device.name,
        device_id=device.id,
        included_sensor_count=1,
        contributing_sensor_count=1 if covered else 0,
        energy_kwh=accumulator.energy_kwh if accumulator.energy_available else None,
        average_power_w=(
            accumulator.power_weighted / accumulator.power_seconds
            if accumulator.power_seconds
            else None
        ),
        peak_power_w=accumulator.peak_power_w,
        voltage_min_v=accumulator.voltage_min_v,
        voltage_avg_v=(
            accumulator.voltage_weighted / accumulator.voltage_seconds
            if accumulator.voltage_seconds
            else None
        ),
        voltage_max_v=accumulator.voltage_max_v,
        current_a=(
            accumulator.current_weighted / accumulator.current_seconds
            if accumulator.current_seconds
            else None
        ),
        power_factor=(
            accumulator.factor_weighted / accumulator.factor_weight
            if accumulator.factor_weight
            else None
        ),
        frequency_hz=(
            accumulator.frequency_weighted / accumulator.frequency_seconds
            if accumulator.frequency_seconds
            else None
        ),
        tou_period=period,
        rate_per_kwh=rate if not accumulator.cost_missing else None,
        energy_cost=cost,
        rate_plan_name=plan_name,
        rate_version_id=version_id,
        rate_effective_from=effective_from,
        mixed_rates=mixed,
        coverage_percent=coverage,
        missing_sensor_ids=[] if covered == bucket_seconds else [device.id],
        quality_flags=sorted(quality),
        rate_contributions=contributions,
    )


def _combined_bucket(
    *,
    accumulators: dict[str, DeviceBucketAccumulator],
    devices: list[Device],
    allocations: dict[str, Decimal],
    display_name: str,
    left: datetime,
    right: datetime,
    zone: ZoneInfo,
    strict: bool,
) -> HistoryBucket:
    bucket_seconds = Decimal(str((right - left).total_seconds()))
    expected = bucket_seconds * Decimal(len(devices))
    covered_total = ZERO
    missing: list[str] = []
    contributing = 0
    energy = ZERO
    energy_available = False
    power = ZERO
    power_available = False
    peak = ZERO
    peak_available = False
    voltage_weighted = ZERO
    voltage_weight = ZERO
    voltage_min: Decimal | None = None
    voltage_max: Decimal | None = None
    factor_weighted = ZERO
    factor_weight = ZERO
    frequency_weighted = ZERO
    frequency_weight = ZERO
    quality: set[str] = set()
    contributions: list[HistoryRateContribution] = []
    cost_missing = False
    for device in devices:
        accumulator = accumulators[device.id]
        scale = allocations.get(device.id, Decimal("1"))
        covered = min(bucket_seconds, _merge_duration(accumulator.coverage_ranges))
        covered_total += covered
        if covered:
            contributing += 1
        if covered < bucket_seconds:
            missing.append(device.id)
        if accumulator.energy_available:
            energy += accumulator.energy_kwh * scale
            energy_available = True
            if accumulator.cost_missing:
                cost_missing = True
        if accumulator.power_seconds:
            power += accumulator.power_weighted / accumulator.power_seconds * scale
            power_available = True
        if accumulator.peak_power_w is not None:
            peak += accumulator.peak_power_w * scale
            peak_available = True
        if accumulator.voltage_seconds:
            voltage_weighted += accumulator.voltage_weighted / accumulator.voltage_seconds * covered
            voltage_weight += covered
            voltage_min = (
                accumulator.voltage_min_v
                if voltage_min is None
                else min(voltage_min, accumulator.voltage_min_v or voltage_min)
            )
            voltage_max = (
                accumulator.voltage_max_v
                if voltage_max is None
                else max(voltage_max, accumulator.voltage_max_v or voltage_max)
            )
        if accumulator.factor_weight:
            factor_weighted += accumulator.factor_weighted
            factor_weight += accumulator.factor_weight
        if accumulator.frequency_seconds:
            frequency_weighted += (
                accumulator.frequency_weighted / accumulator.frequency_seconds * covered
            )
            frequency_weight += covered
        quality.update(accumulator.quality_flags)
        contributions.extend(_rate_contributions(accumulator, scale))
    coverage = covered_total / expected * ONE_HUNDRED if expected else ZERO
    if coverage < ONE_HUNDRED:
        quality.add("partial_coverage")
    if len(devices) > 1:
        quality.add("aggregate_current_unavailable")
    period, rate, cost, plan_name, version_id, effective_from, mixed = _labels_from_contributions(
        contributions
    )
    if energy_available and cost_missing:
        quality.add("rate_unavailable")
        rate = None
        cost = None
    withheld = strict and coverage < ONE_HUNDRED
    if withheld:
        quality.add("strict_coverage_withheld")
    local_start = left.astimezone(zone)
    local_end = right.astimezone(zone)
    return HistoryBucket(
        interval_start_utc=left,
        interval_end_utc=right,
        local_start=local_start.isoformat(),
        local_end=local_end.isoformat(),
        utc_offset=_offset_text(local_start),
        series_id="combined",
        series_name=display_name,
        included_sensor_count=len(devices),
        contributing_sensor_count=contributing,
        energy_kwh=None if withheld or not energy_available else energy,
        average_power_w=None if withheld or not power_available else power,
        peak_power_w=None if withheld or not peak_available else peak,
        voltage_min_v=None if withheld else voltage_min,
        voltage_avg_v=(
            None if withheld or not voltage_weight else voltage_weighted / voltage_weight
        ),
        voltage_max_v=None if withheld else voltage_max,
        current_a=None,
        power_factor=(None if withheld or not factor_weight else factor_weighted / factor_weight),
        frequency_hz=(
            None if withheld or not frequency_weight else frequency_weighted / frequency_weight
        ),
        tou_period=period,
        rate_per_kwh=None if withheld else rate,
        energy_cost=None if withheld else cost,
        rate_plan_name=plan_name,
        rate_version_id=version_id,
        rate_effective_from=effective_from,
        mixed_rates=mixed,
        coverage_percent=coverage,
        missing_sensor_ids=missing,
        quality_flags=sorted(quality),
        rate_contributions=contributions,
    )


def _summary(points: list[HistoryBucket], start: datetime, end: datetime) -> HistoryRangeSummary:
    selected = [
        point
        for point in points
        if point.interval_start_utc < end and point.interval_end_utc > start
    ]
    energy_values = [point.energy_kwh for point in selected if point.energy_kwh is not None]
    cost_values = [point.energy_cost for point in selected if point.energy_cost is not None]
    energy = sum(energy_values, ZERO) if energy_values else None
    cost_complete = all(
        point.energy_cost is not None
        for point in selected
        if point.energy_kwh is not None and point.energy_kwh != ZERO
    )
    cost = sum(cost_values, ZERO) if cost_values and cost_complete else None
    duration_seconds = sum(
        (
            Decimal(str((point.interval_end_utc - point.interval_start_utc).total_seconds()))
            for point in selected
            if point.average_power_w is not None
        ),
        ZERO,
    )
    average_power = (
        sum(
            (
                (point.average_power_w or ZERO)
                * Decimal(str((point.interval_end_utc - point.interval_start_utc).total_seconds()))
                for point in selected
                if point.average_power_w is not None
            ),
            ZERO,
        )
        / duration_seconds
        if duration_seconds
        else None
    )
    peak_points = [point for point in selected if point.peak_power_w is not None]
    highest_cost = max(
        (point for point in selected if point.energy_cost is not None),
        key=lambda point: point.energy_cost or ZERO,
        default=None,
    )
    highest_usage = max(
        (point for point in selected if point.energy_kwh is not None),
        key=lambda point: point.energy_kwh or ZERO,
        default=None,
    )
    tou: dict[str, dict[str, Decimal]] = {}
    for point in selected:
        for contribution in point.rate_contributions:
            item = tou.setdefault(
                contribution.tou_period, {"energy_kwh": ZERO, "energy_cost": ZERO}
            )
            item["energy_kwh"] += contribution.energy_kwh
            item["energy_cost"] += contribution.energy_cost
    weighted_coverage_seconds = sum(
        (
            point.coverage_percent
            * Decimal(str((point.interval_end_utc - point.interval_start_utc).total_seconds()))
            for point in selected
        ),
        ZERO,
    )
    all_seconds = sum(
        (
            Decimal(str((point.interval_end_utc - point.interval_start_utc).total_seconds()))
            for point in selected
        ),
        ZERO,
    )
    return HistoryRangeSummary(
        start_utc=start,
        end_utc=end,
        energy_kwh=energy,
        energy_cost=cost,
        blended_rate_per_kwh=cost / energy if cost is not None and energy else None,
        average_power_w=average_power,
        peak_power_w=max((point.peak_power_w or ZERO for point in peak_points), default=None),
        highest_cost_bucket_start=highest_cost.interval_start_utc if highest_cost else None,
        highest_cost_bucket_value=highest_cost.energy_cost if highest_cost else None,
        highest_usage_bucket_start=highest_usage.interval_start_utc if highest_usage else None,
        highest_usage_bucket_kwh=highest_usage.energy_kwh if highest_usage else None,
        coverage_percent=weighted_coverage_seconds / all_seconds if all_seconds else ZERO,
        contributing_sensor_count=max(
            (point.contributing_sensor_count for point in selected), default=0
        ),
        tou_breakdown=tou,
    )


def _withhold_overlapping_totals(summary: HistoryRangeSummary) -> None:
    """Keep coverage evidence but never expose a parent/child aggregate as a total."""
    summary.energy_kwh = None
    summary.energy_cost = None
    summary.blended_rate_per_kwh = None
    summary.average_power_w = None
    summary.peak_power_w = None
    summary.highest_cost_bucket_start = None
    summary.highest_cost_bucket_value = None
    summary.highest_usage_bucket_start = None
    summary.highest_usage_bucket_kwh = None
    summary.tou_breakdown = {}


def _raw_reading_columns(plan: HistoryExecutionPlan) -> list[Any]:
    columns: list[Any] = [
        RawReading.device_id,
        RawReading.interval_start,
        RawReading.interval_end,
        RawReading.quality_flags,
    ]
    if plan.needs_power:
        columns.extend((RawReading.power_avg, RawReading.power_max))
    elif plan.needs_power_factor:
        columns.append(RawReading.power_avg)
    if plan.needs_energy:
        columns.append(RawReading.device_interval_energy_wh)
    if plan.needs_voltage:
        columns.extend(
            (
                RawReading.voltage_avg,
                RawReading.voltage_min,
                RawReading.voltage_max,
            )
        )
    if plan.needs_current:
        columns.append(RawReading.current_avg)
    if plan.needs_power_factor:
        columns.append(RawReading.power_factor)
    if plan.needs_frequency:
        columns.append(RawReading.frequency_hz)
    return list(dict.fromkeys(columns))


async def _load_source_rows(
    session: AsyncSession,
    *,
    device_ids: list[str],
    start: datetime,
    end: datetime,
    plan: HistoryExecutionPlan,
    snapshot_at: datetime,
) -> list[tuple[RawReading, NormalizedInterval | None]]:
    if not device_ids:
        return []
    filters = (
        RawReading.device_id.in_(device_ids),
        RawReading.interval_end > start,
        RawReading.interval_start < end,
        RawReading.ingested_at <= snapshot_at,
    )
    ordering = (
        RawReading.interval_start,
        RawReading.device_id,
        RawReading.sequence,
    )
    raw_options = load_only(*_raw_reading_columns(plan))
    if plan.needs_energy:
        result = await session.execute(
            select(RawReading, NormalizedInterval)
            .join(
                NormalizedInterval,
                NormalizedInterval.raw_reading_id == RawReading.id,
                isouter=True,
            )
            .options(
                raw_options,
                load_only(NormalizedInterval.id, NormalizedInterval.selected_energy_wh),
            )
            .where(*filters)
            .order_by(*ordering)
            .limit(MAX_SOURCE_ROWS + 1)
        )
        return [(raw, normalized) for raw, normalized in result.all()]
    readings = list(
        await session.scalars(
            select(RawReading)
            .options(raw_options)
            .where(*filters)
            .order_by(*ordering)
            .limit(MAX_SOURCE_ROWS + 1)
        )
    )
    return [(reading, None) for reading in readings]


def _window_cte(windows: list[CoarseWindow], *, name: str, dialect: str) -> Any:
    if dialect == "sqlite":
        rows = [
            select(
                literal(item.bucket_index, type_=Integer).label("bucket_index"),
                literal(item.start, type_=DateTime(timezone=True)).label("window_start"),
                literal(item.end, type_=DateTime(timezone=True)).label("window_end"),
            )
            for item in windows
        ]
        return union_all(*rows).cte(name)
    return (
        values(
            column("bucket_index", Integer),
            column("window_start", DateTime(timezone=True)),
            column("window_end", DateTime(timezone=True)),
            name=name,
        )
        .data([(item.bucket_index, item.start, item.end) for item in windows])
        .alias(name)
    )


def _cost_window_cte(windows: list[CoarseCostWindow], *, name: str, dialect: str) -> Any:
    if dialect == "sqlite":
        rows = [
            select(
                literal(item.window_id, type_=Integer).label("window_id"),
                literal(item.bucket_index, type_=Integer).label("bucket_index"),
                literal(item.account_id, type_=String(36)).label("account_id"),
                literal(item.start, type_=DateTime(timezone=True)).label("window_start"),
                literal(item.end, type_=DateTime(timezone=True)).label("window_end"),
                literal(
                    item.context.version.id if item.context is not None else None,
                    type_=String(36),
                ).label("rate_version_id"),
            )
            for item in windows
        ]
        return union_all(*rows).cte(name)
    return (
        values(
            column("window_id", Integer),
            column("bucket_index", Integer),
            column("account_id", String(36)),
            column("window_start", DateTime(timezone=True)),
            column("window_end", DateTime(timezone=True)),
            column("rate_version_id", String(36)),
            name=name,
        )
        .data(
            [
                (
                    item.window_id,
                    item.bucket_index,
                    item.account_id,
                    item.start,
                    item.end,
                    item.context.version.id if item.context is not None else None,
                )
                for item in windows
            ]
        )
        .alias(name)
    )


def _overlap_bounds(
    row_start: Any, row_end: Any, window_start: Any, window_end: Any
) -> tuple[Any, Any]:
    overlap_start = case((row_start > window_start, row_start), else_=window_start)
    overlap_end = case((row_end < window_end, row_end), else_=window_end)
    return overlap_start, overlap_end


def _seconds_expression(session: AsyncSession, start: Any, end: Any) -> Any:
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    if dialect == "sqlite":
        # Contract fixtures use whole-second device intervals. Production
        # PostgreSQL retains sub-second precision through EXTRACT(EPOCH).
        return cast(func.strftime("%s", end), Integer) - cast(func.strftime("%s", start), Integer)
    return cast(func.extract("epoch", end - start), Numeric(24, 6))


def _exact_numeric_expression(session: AsyncSession, value: Any, scale: int) -> Any:
    """Keep SQLite fixture arithmetic stable without rounding PostgreSQL NUMERIC rows."""

    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    return func.round(value, scale) if dialect == "sqlite" else value


def _decimal_or_zero(value: object) -> Decimal:
    return ZERO if value is None else Decimal(str(value))


def _timedelta_from_decimal_seconds(value: Decimal) -> timedelta:
    microseconds = int((value * Decimal("1000000")).to_integral_value())
    return timedelta(microseconds=microseconds)


def _fixed_interval_membership(
    session: AsyncSession,
    boundaries: list[datetime],
    *,
    interval_start: Any,
    interval_end: Any,
    name: str,
) -> Any | None:
    """Map each source row to fixed-width buckets without rescanning it per window."""

    if session.bind is None or session.bind.dialect.name != "postgresql":
        return None
    durations = {right - left for left, right in pairwise(boundaries)}
    if len(durations) != 1:
        return None
    bucket_seconds = Decimal(str(durations.pop().total_seconds()))
    if bucket_seconds <= 0:
        return None
    range_start = literal(boundaries[0], type_=DateTime(timezone=True))
    range_end = literal(boundaries[-1], type_=DateTime(timezone=True))
    clamped_start = func.greatest(interval_start, range_start)
    clamped_end = func.least(interval_end, range_end)
    relative_start = _seconds_expression(session, range_start, clamped_start)
    relative_end = _seconds_expression(session, range_start, clamped_end)
    first_index = cast(func.floor(relative_start / bucket_seconds), Integer)
    # The end is exclusive. CEIL(seconds / width) - 1 therefore maps an exact
    # bucket boundary to the preceding bucket without subtracting a lossy
    # floating-point epsilon.
    last_index = cast(func.ceil(relative_end / bucket_seconds) - 1, Integer)
    return (
        func.generate_series(first_index, last_index)
        .table_valued("bucket_index")
        .render_derived(name)
        .lateral()
    )


def _fixed_window_membership(
    session: AsyncSession,
    boundaries: list[datetime],
) -> Any | None:
    return _fixed_interval_membership(
        session,
        boundaries,
        interval_start=RawReading.interval_start,
        interval_end=RawReading.interval_end,
        name="history_bucket_membership",
    )


async def _coarse_source_has_overlap(
    session: AsyncSession,
    *,
    device_ids: list[str],
    start: datetime,
    end: datetime,
    snapshot_at: datetime,
) -> bool:
    prior_end = func.lag(RawReading.interval_end).over(
        partition_by=RawReading.device_id,
        order_by=(RawReading.interval_start, RawReading.sequence),
    )
    ordered = (
        select(
            RawReading.interval_start.label("interval_start"),
            prior_end.label("prior_end"),
        )
        .where(
            RawReading.device_id.in_(device_ids),
            RawReading.interval_end > start,
            RawReading.interval_start < end,
            RawReading.ingested_at <= snapshot_at,
        )
        .subquery()
    )
    return bool(
        await session.scalar(
            select(literal(1))
            .where(
                ordered.c.prior_end.is_not(None),
                ordered.c.interval_start < ordered.c.prior_end,
            )
            .limit(1)
        )
    )


def _empty_coarse_accumulators(
    resolved: ResolvedHistoryScope,
    boundaries: list[datetime],
) -> list[dict[str, DeviceBucketAccumulator]]:
    return [
        {device.id: DeviceBucketAccumulator() for device in resolved.devices}
        for _ in range(len(boundaries) - 1)
    ]


async def _load_coarse_measurements(
    session: AsyncSession,
    *,
    resolved: ResolvedHistoryScope,
    boundaries: list[datetime],
    plan: HistoryExecutionPlan,
    snapshot_at: datetime,
    accumulators: list[dict[str, DeviceBucketAccumulator]] | None = None,
) -> CoarseLoadResult:
    device_ids = [device.id for device in resolved.devices]
    if accumulators is None:
        accumulators = _empty_coarse_accumulators(resolved, boundaries)
    if not device_ids:
        return CoarseLoadResult(accumulators, 0, 0, 0)
    if await _coarse_source_has_overlap(
        session,
        device_ids=device_ids,
        start=boundaries[0],
        end=boundaries[-1],
        snapshot_at=snapshot_at,
    ):
        raise ProblemError(
            409,
            "Coarse history needs reconciliation",
            "Overlapping immutable reading intervals must be reconciled before "
            "coarse history can be calculated exactly",
            "history_coarse_overlap",
        )

    windows = [
        CoarseWindow(index, left, right) for index, (left, right) in enumerate(pairwise(boundaries))
    ]
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    aggregate_rows = 0
    matched_readings = 0
    quality_row_count = 0
    fixed_membership = _fixed_window_membership(session, boundaries)
    for chunk_number, chunk_start in enumerate(range(0, len(windows), COARSE_WINDOW_CHUNK_SIZE)):
        chunk = windows[chunk_start : chunk_start + COARSE_WINDOW_CHUNK_SIZE]
        window_rows = _window_cte(
            chunk,
            name=f"history_windows_{chunk_number}",
            dialect=dialect,
        )
        overlap_start, overlap_end = _overlap_bounds(
            RawReading.interval_start,
            RawReading.interval_end,
            window_rows.c.window_start,
            window_rows.c.window_end,
        )
        overlap_seconds = _seconds_expression(session, overlap_start, overlap_end)
        raw_seconds = _seconds_expression(
            session, RawReading.interval_start, RawReading.interval_end
        )
        selected_energy = (
            case(
                (
                    NormalizedInterval.id.is_not(None),
                    NormalizedInterval.selected_energy_wh,
                ),
                else_=RawReading.device_interval_energy_wh,
            )
            if plan.needs_energy
            else literal(None, type_=Numeric(24, 6))
        )
        source_filters = and_(
            RawReading.device_id.in_(device_ids),
            RawReading.interval_end > chunk[0].start,
            RawReading.interval_start < chunk[-1].end,
            RawReading.ingested_at <= snapshot_at,
        )
        if fixed_membership is not None:
            source = RawReading.__table__.join(fixed_membership, true()).join(
                window_rows,
                window_rows.c.bucket_index == fixed_membership.c.bucket_index,
            )
        else:
            source = window_rows.join(
                RawReading,
                and_(
                    RawReading.interval_end > window_rows.c.window_start,
                    RawReading.interval_start < window_rows.c.window_end,
                ),
            )
        if plan.needs_energy:
            source = source.outerjoin(
                NormalizedInterval,
                NormalizedInterval.raw_reading_id == RawReading.id,
            )
        energy_kwh = case(
            (
                and_(selected_energy.is_not(None), raw_seconds > 0),
                _exact_numeric_expression(session, selected_energy, 6)
                * overlap_seconds
                / raw_seconds
                / Decimal("1000"),
            ),
            else_=None,
        )
        statement = (
            select(
                window_rows.c.bucket_index,
                RawReading.device_id,
                func.count(RawReading.id).label("source_count"),
                func.sum(func.json_array_length(RawReading.quality_flags)).label(
                    "quality_flag_count"
                ),
                func.sum(overlap_seconds).label("covered_seconds"),
                (
                    func.sum(
                        case(
                            (
                                RawReading.power_avg.is_not(None),
                                _exact_numeric_expression(session, RawReading.power_avg, 5)
                                * overlap_seconds,
                            ),
                            else_=None,
                        )
                    )
                    if plan.needs_power
                    else literal(None, type_=Numeric(24, 6))
                ).label("power_weighted"),
                (
                    func.sum(
                        case(
                            (RawReading.power_avg.is_not(None), overlap_seconds),
                            else_=None,
                        )
                    )
                    if plan.needs_power
                    else literal(None, type_=Numeric(24, 6))
                ).label("power_seconds"),
                (
                    func.max(_exact_numeric_expression(session, RawReading.power_max, 5))
                    if plan.needs_power
                    else literal(None, type_=Numeric(24, 6))
                ).label("peak_power_w"),
                func.sum(energy_kwh).label("energy_kwh"),
                (
                    func.sum(
                        case(
                            (
                                RawReading.voltage_avg.is_not(None),
                                _exact_numeric_expression(session, RawReading.voltage_avg, 4)
                                * overlap_seconds,
                            ),
                            else_=None,
                        )
                    )
                    if plan.needs_voltage
                    else literal(None, type_=Numeric(24, 6))
                ).label("voltage_weighted"),
                (
                    func.sum(
                        case(
                            (RawReading.voltage_avg.is_not(None), overlap_seconds),
                            else_=None,
                        )
                    )
                    if plan.needs_voltage
                    else literal(None, type_=Numeric(24, 6))
                ).label("voltage_seconds"),
                (
                    func.min(_exact_numeric_expression(session, RawReading.voltage_min, 4))
                    if plan.needs_voltage
                    else literal(None, type_=Numeric(24, 6))
                ).label("voltage_min_v"),
                (
                    func.max(_exact_numeric_expression(session, RawReading.voltage_max, 4))
                    if plan.needs_voltage
                    else literal(None, type_=Numeric(24, 6))
                ).label("voltage_max_v"),
                (
                    func.sum(
                        case(
                            (
                                RawReading.current_avg.is_not(None),
                                _exact_numeric_expression(session, RawReading.current_avg, 5)
                                * overlap_seconds,
                            ),
                            else_=None,
                        )
                    )
                    if plan.needs_current
                    else literal(None, type_=Numeric(24, 6))
                ).label("current_weighted"),
                (
                    func.sum(
                        case(
                            (RawReading.current_avg.is_not(None), overlap_seconds),
                            else_=None,
                        )
                    )
                    if plan.needs_current
                    else literal(None, type_=Numeric(24, 6))
                ).label("current_seconds"),
                (
                    func.sum(
                        case(
                            (
                                and_(
                                    RawReading.power_factor.is_not(None),
                                    RawReading.power_avg.is_not(None),
                                ),
                                _exact_numeric_expression(session, RawReading.power_factor, 5)
                                * func.abs(
                                    _exact_numeric_expression(session, RawReading.power_avg, 5)
                                )
                                * overlap_seconds,
                            ),
                            else_=None,
                        )
                    )
                    if plan.needs_power_factor
                    else literal(None, type_=Numeric(24, 6))
                ).label("factor_weighted"),
                (
                    func.sum(
                        case(
                            (
                                and_(
                                    RawReading.power_factor.is_not(None),
                                    RawReading.power_avg.is_not(None),
                                ),
                                func.abs(
                                    _exact_numeric_expression(session, RawReading.power_avg, 5)
                                )
                                * overlap_seconds,
                            ),
                            else_=None,
                        )
                    )
                    if plan.needs_power_factor
                    else literal(None, type_=Numeric(24, 6))
                ).label("factor_weight"),
                (
                    func.sum(
                        case(
                            (
                                RawReading.frequency_hz.is_not(None),
                                _exact_numeric_expression(session, RawReading.frequency_hz, 4)
                                * overlap_seconds,
                            ),
                            else_=None,
                        )
                    )
                    if plan.needs_frequency
                    else literal(None, type_=Numeric(24, 6))
                ).label("frequency_weighted"),
                (
                    func.sum(
                        case(
                            (RawReading.frequency_hz.is_not(None), overlap_seconds),
                            else_=None,
                        )
                    )
                    if plan.needs_frequency
                    else literal(None, type_=Numeric(24, 6))
                ).label("frequency_seconds"),
            )
            .select_from(source)
            .where(source_filters)
            .group_by(window_rows.c.bucket_index, RawReading.device_id)
            .order_by(window_rows.c.bucket_index, RawReading.device_id)
        )
        chunk_has_quality_flags = False
        for row in (await session.execute(statement)).mappings():
            aggregate_rows += 1
            matched_readings += int(row["source_count"] or 0)
            chunk_has_quality_flags = (
                chunk_has_quality_flags or int(row["quality_flag_count"] or 0) > 0
            )
            index = int(row["bucket_index"])
            accumulator = accumulators[index][str(row["device_id"])]
            covered = _decimal_or_zero(row["covered_seconds"])
            left, right = boundaries[index], boundaries[index + 1]
            covered = min(covered, Decimal(str((right - left).total_seconds())))
            if covered > 0:
                accumulator.coverage_ranges.append(
                    (left, left + _timedelta_from_decimal_seconds(covered))
                )
            for target, key in (
                ("power_weighted", "power_weighted"),
                ("power_seconds", "power_seconds"),
                ("voltage_weighted", "voltage_weighted"),
                ("voltage_seconds", "voltage_seconds"),
                ("current_weighted", "current_weighted"),
                ("current_seconds", "current_seconds"),
                ("factor_weighted", "factor_weighted"),
                ("factor_weight", "factor_weight"),
                ("frequency_weighted", "frequency_weighted"),
                ("frequency_seconds", "frequency_seconds"),
            ):
                setattr(accumulator, target, _decimal_or_zero(row[key]))
            accumulator.peak_power_w = (
                Decimal(str(row["peak_power_w"])) if row["peak_power_w"] is not None else None
            )
            accumulator.energy_available = row["energy_kwh"] is not None
            accumulator.energy_kwh = _decimal_or_zero(row["energy_kwh"])
            if plan.needs_energy and not accumulator.energy_available:
                accumulator.quality_flags.add("energy_unavailable")
            accumulator.voltage_min_v = (
                Decimal(str(row["voltage_min_v"])) if row["voltage_min_v"] is not None else None
            )
            accumulator.voltage_max_v = (
                Decimal(str(row["voltage_max_v"])) if row["voltage_max_v"] is not None else None
            )
        if chunk_has_quality_flags:
            # Expand and de-duplicate flags inside SQL. The result is bounded by
            # bucket x device x distinct flag, instead of hydrating every
            # flagged immutable reading into Python for the full range.
            quality_values = (
                func.json_each(RawReading.quality_flags)
                .table_valued("value")
                .alias(f"history_quality_{chunk_number}")
                if dialect == "sqlite"
                else func.json_array_elements_text(RawReading.quality_flags)
                .table_valued("value")
                .lateral()
                .alias(f"history_quality_{chunk_number}")
            )
            quality_statement = (
                select(
                    window_rows.c.bucket_index,
                    RawReading.device_id,
                    quality_values.c.value.label("quality_flag"),
                )
                .select_from(source.join(quality_values, true()))
                .where(
                    source_filters,
                    func.json_array_length(RawReading.quality_flags) > 0,
                )
                .distinct()
                .order_by(
                    window_rows.c.bucket_index,
                    RawReading.device_id,
                    quality_values.c.value,
                )
            )
            for quality_row in (await session.execute(quality_statement)).mappings():
                quality_row_count += 1
                accumulators[int(quality_row["bucket_index"])][
                    str(quality_row["device_id"])
                ].quality_flags.add(str(quality_row["quality_flag"]))
    return CoarseLoadResult(
        accumulators=accumulators,
        aggregate_row_count=aggregate_rows,
        scanned_reading_count=matched_readings,
        quality_row_count=quality_row_count,
    )


def _device_account_cte(device_accounts: dict[str, str | None], *, name: str) -> Any:
    rows = [
        select(
            literal(device_id).label("device_id"),
            literal(account_id).label("account_id"),
        )
        for device_id, account_id in device_accounts.items()
        if account_id is not None
    ]
    return union_all(*rows).cte(name)


def _coarse_cost_windows(
    boundaries: list[datetime], contexts: dict[str, list[RateContext]]
) -> list[CoarseCostWindow]:
    result: list[CoarseCostWindow] = []
    for bucket_index, (bucket_start, bucket_end) in enumerate(pairwise(boundaries)):
        for account_id, account_contexts in contexts.items():
            for left, right, context in _find_cost_contexts(
                account_contexts, bucket_start, bucket_end
            ):
                splits = (
                    context.engine.calculation_boundaries(left, right)
                    if context is not None
                    else (left, right)
                )
                for slice_start, slice_end in pairwise(splits):
                    result.append(
                        CoarseCostWindow(
                            window_id=len(result),
                            bucket_index=bucket_index,
                            account_id=account_id,
                            start=slice_start,
                            end=slice_end,
                            context=context,
                        )
                    )
    return result


async def _apply_coarse_costs(
    session: AsyncSession,
    *,
    accumulators: list[dict[str, DeviceBucketAccumulator]],
    boundaries: list[datetime],
    contexts: dict[str, list[RateContext]],
    device_accounts: dict[str, str | None],
    snapshot_at: datetime,
) -> int:
    mapped_accounts = {value for value in device_accounts.values() if value is not None}
    if not mapped_accounts:
        return 0
    windows = _coarse_cost_windows(boundaries, contexts)
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    window_by_id = {item.window_id: item for item in windows}
    device_map = _device_account_cte(device_accounts, name="history_device_accounts")
    contribution_rows = 0

    # Flat and TOU energy is aggregated inside exact immutable tariff slices.
    # A gap in the assignment timeline is queried too, so missing rates remain
    # explicit instead of silently pricing the whole chart bucket.
    linear_windows = [
        item
        for item in windows
        if item.context is None
        or item.context.engine.pricing_model not in {"tiered", "time_of_use_tiered"}
    ]
    for chunk_number, chunk_start in enumerate(
        range(0, len(linear_windows), COARSE_WINDOW_CHUNK_SIZE)
    ):
        chunk = linear_windows[chunk_start : chunk_start + COARSE_WINDOW_CHUNK_SIZE]
        window_rows = _cost_window_cte(
            chunk,
            name=f"history_cost_windows_{chunk_number}",
            dialect=dialect,
        )
        overlap_start, overlap_end = _overlap_bounds(
            RawReading.interval_start,
            RawReading.interval_end,
            window_rows.c.window_start,
            window_rows.c.window_end,
        )
        overlap_seconds = _seconds_expression(session, overlap_start, overlap_end)
        raw_seconds = _seconds_expression(
            session, RawReading.interval_start, RawReading.interval_end
        )
        selected_energy = case(
            (
                NormalizedInterval.id.is_not(None),
                NormalizedInterval.selected_energy_wh,
            ),
            else_=RawReading.device_interval_energy_wh,
        )
        energy_kwh = case(
            (
                and_(selected_energy.is_not(None), raw_seconds > 0),
                func.round(selected_energy, 6) * overlap_seconds / raw_seconds / Decimal("1000"),
            ),
            else_=None,
        )
        source = (
            window_rows.join(device_map, device_map.c.account_id == window_rows.c.account_id)
            .join(
                RawReading,
                and_(
                    RawReading.device_id == device_map.c.device_id,
                    RawReading.interval_end > window_rows.c.window_start,
                    RawReading.interval_start < window_rows.c.window_end,
                    RawReading.ingested_at <= snapshot_at,
                ),
            )
            .outerjoin(
                NormalizedInterval,
                NormalizedInterval.raw_reading_id == RawReading.id,
            )
        )
        statement = (
            select(
                window_rows.c.window_id,
                window_rows.c.bucket_index,
                RawReading.device_id,
                func.count(RawReading.id).label("source_count"),
                func.sum(energy_kwh).label("energy_kwh"),
            )
            .select_from(source)
            .group_by(
                window_rows.c.window_id,
                window_rows.c.bucket_index,
                RawReading.device_id,
            )
        )
        for row in (await session.execute(statement)).mappings():
            contribution_rows += 1
            window = window_by_id[int(row["window_id"])]
            accumulator = accumulators[int(row["bucket_index"])][str(row["device_id"])]
            if window.context is None:
                accumulator.cost_missing = True
                continue
            if row["energy_kwh"] is None:
                continue
            energy_kwh_value = Decimal(str(row["energy_kwh"]))
            calculation = window.context.engine.calculate(
                start=window.start,
                end=window.end,
                energy_kwh=energy_kwh_value,
                cost_scope="energy_only",
            )
            extra = calculation.total - calculation.energy_charge
            for item in calculation.slices:
                extra_share = (
                    extra * item.energy_kwh / energy_kwh_value if energy_kwh_value else ZERO
                )
                cost = item.cost + extra_share
                rate = cost / item.energy_kwh if item.energy_kwh else item.price_per_kwh
                accumulator.add_cost(
                    context=window.context,
                    tou_period=item.bucket,
                    rate=rate,
                    energy_kwh=item.energy_kwh,
                    cost=cost,
                )

    # Tiered/hybrid allocations are already chronological immutable facts. Read
    # only the latest billing-cycle recalculation and aggregate the exact segment
    # fractions into chart buckets.
    tier_windows = [
        item
        for item in windows
        if item.context is not None
        and item.context.engine.pricing_model in {"tiered", "time_of_use_tiered"}
    ]
    tier_account_devices: dict[str, list[str]] = defaultdict(list)
    for device_id, account_id in device_accounts.items():
        if account_id is not None:
            tier_account_devices[account_id].append(device_id)
    tier_device_filter = or_(
        *(
            and_(
                TierAllocationSegment.utility_account_id == account_id,
                NormalizedInterval.device_id.in_(device_ids),
            )
            for account_id, device_ids in tier_account_devices.items()
        )
    )
    tier_version_ids = {
        item.context.version.id for item in tier_windows if item.context is not None
    }
    matched_tier_windows: set[tuple[int, str]] = set()
    eligible_tier_membership = _fixed_interval_membership(
        session,
        boundaries,
        interval_start=TierAllocationSegment.interval_start,
        interval_end=TierAllocationSegment.interval_end,
        name="history_eligible_tier_membership",
    )
    eligible_tier_query = (
        select(
            TierAllocationSegment.utility_account_id.label("utility_account_id"),
            TierAllocationSegment.interval_start.label("interval_start"),
            TierAllocationSegment.interval_end.label("interval_end"),
            TierAllocationSegment.rate_version_id.label("rate_version_id"),
            TierAllocationSegment.tier_stable_id.label("tier_stable_id"),
            TierAllocationSegment.tier_name.label("tier_name"),
            TierAllocationSegment.tou_period.label("tou_period"),
            TierAllocationSegment.cumulative_start_kwh.label("cumulative_start_kwh"),
            TierAllocationSegment.cumulative_end_kwh.label("cumulative_end_kwh"),
            TierAllocationSegment.segment_energy_kwh.label("segment_energy_kwh"),
            TierAllocationSegment.price_per_kwh.label("price_per_kwh"),
            TierAllocationSegment.unrounded_energy_charge.label("unrounded_energy_charge"),
            TierAllocationSegment.usage_authority_type.label("usage_authority_type"),
            TierAllocationSegment.recalculation_version.label("recalculation_version"),
            NormalizedInterval.device_id.label("device_id"),
            *(
                [eligible_tier_membership.c.bucket_index.label("bucket_index")]
                if eligible_tier_membership is not None
                else []
            ),
        )
        .select_from(TierAllocationSegment)
        .join(
            BillingCycle,
            and_(
                BillingCycle.id == TierAllocationSegment.billing_cycle_id,
                BillingCycle.recalculation_version == TierAllocationSegment.recalculation_version,
            ),
        )
        .join(
            NormalizedInterval,
            NormalizedInterval.id == TierAllocationSegment.normalized_interval_id,
        )
        .where(
            TierAllocationSegment.utility_account_id.in_(mapped_accounts),
            TierAllocationSegment.rate_version_id.in_(tier_version_ids),
            TierAllocationSegment.interval_end > boundaries[0],
            TierAllocationSegment.interval_start < boundaries[-1],
            tier_device_filter,
        )
    )
    if eligible_tier_membership is not None:
        # Expand the rare boundary-spanning segment before materialization. The
        # resulting eligible relation carries a concrete bucket key, allowing
        # a small hash join instead of an account-wide segment x window join.
        eligible_tier_query = eligible_tier_query.join(eligible_tier_membership, true())
    eligible_tiers = eligible_tier_query.cte("history_eligible_tier_segments")
    if dialect == "postgresql":
        # Force a single bounded read of the current exact facts. Without this,
        # PostgreSQL can parameterize a broad interval-start scan once per
        # chart window and reject tens of thousands of unrelated rows each
        # time, even though every fact belongs to one fixed-width bucket.
        eligible_tiers = eligible_tiers.prefix_with("MATERIALIZED")
    for chunk_number, chunk_start in enumerate(
        range(0, len(tier_windows), COARSE_WINDOW_CHUNK_SIZE)
    ):
        chunk = tier_windows[chunk_start : chunk_start + COARSE_WINDOW_CHUNK_SIZE]
        window_rows = _cost_window_cte(
            chunk,
            name=f"history_tier_windows_{chunk_number}",
            dialect=dialect,
        )
        overlap_start, overlap_end = _overlap_bounds(
            eligible_tiers.c.interval_start,
            eligible_tiers.c.interval_end,
            window_rows.c.window_start,
            window_rows.c.window_end,
        )
        overlap_seconds = _seconds_expression(session, overlap_start, overlap_end)
        segment_seconds = _seconds_expression(
            session,
            eligible_tiers.c.interval_start,
            eligible_tiers.c.interval_end,
        )
        fully_contained = and_(
            eligible_tiers.c.interval_start >= window_rows.c.window_start,
            eligible_tiers.c.interval_end <= window_rows.c.window_end,
        )
        # The overwhelmingly common case is a short immutable meter interval
        # wholly inside its chart bucket. Preserve the stored exact NUMERIC
        # values directly in that path instead of evaluating several timestamp
        # EXTRACT/division expressions for every segment. Boundary-crossing
        # intervals retain the exact proportional calculation below.
        fraction = case(
            (fully_contained, literal(Decimal("1"), type_=Numeric(24, 12))),
            else_=overlap_seconds / segment_seconds,
        )
        cumulative_at_start = case(
            (fully_contained, eligible_tiers.c.cumulative_start_kwh),
            else_=eligible_tiers.c.cumulative_start_kwh
            + (
                eligible_tiers.c.segment_energy_kwh
                * _seconds_expression(
                    session,
                    eligible_tiers.c.interval_start,
                    overlap_start,
                )
                / segment_seconds
            ),
        )
        cumulative_at_end = case(
            (fully_contained, eligible_tiers.c.cumulative_end_kwh),
            else_=cumulative_at_start + eligible_tiers.c.segment_energy_kwh * fraction,
        )
        if eligible_tier_membership is not None:
            source = eligible_tiers.join(
                window_rows,
                and_(
                    window_rows.c.bucket_index == eligible_tiers.c.bucket_index,
                    window_rows.c.account_id == eligible_tiers.c.utility_account_id,
                    window_rows.c.rate_version_id == eligible_tiers.c.rate_version_id,
                ),
            )
        else:
            source = window_rows.join(
                eligible_tiers,
                and_(
                    eligible_tiers.c.utility_account_id == window_rows.c.account_id,
                    eligible_tiers.c.rate_version_id == window_rows.c.rate_version_id,
                    eligible_tiers.c.interval_end > window_rows.c.window_start,
                    eligible_tiers.c.interval_start < window_rows.c.window_end,
                ),
            )
        statement = (
            select(
                window_rows.c.window_id,
                window_rows.c.bucket_index,
                eligible_tiers.c.device_id,
                eligible_tiers.c.rate_version_id,
                eligible_tiers.c.tier_stable_id,
                eligible_tiers.c.tier_name,
                eligible_tiers.c.tou_period,
                eligible_tiers.c.price_per_kwh,
                eligible_tiers.c.recalculation_version,
                eligible_tiers.c.usage_authority_type,
                func.sum(eligible_tiers.c.segment_energy_kwh * fraction).label("energy_kwh"),
                func.sum(eligible_tiers.c.unrounded_energy_charge * fraction).label("energy_cost"),
                func.min(cumulative_at_start).label("cumulative_start_kwh"),
                func.max(cumulative_at_end).label("cumulative_end_kwh"),
            )
            .select_from(source)
            .where(
                eligible_tiers.c.interval_end > window_rows.c.window_start,
                eligible_tiers.c.interval_start < window_rows.c.window_end,
                segment_seconds > 0,
            )
            .group_by(
                window_rows.c.window_id,
                window_rows.c.bucket_index,
                eligible_tiers.c.device_id,
                eligible_tiers.c.rate_version_id,
                eligible_tiers.c.tier_stable_id,
                eligible_tiers.c.tier_name,
                eligible_tiers.c.tou_period,
                eligible_tiers.c.price_per_kwh,
                eligible_tiers.c.recalculation_version,
                eligible_tiers.c.usage_authority_type,
            )
        )
        for row in (await session.execute(statement)).mappings():
            contribution_rows += 1
            window = window_by_id[int(row["window_id"])]
            context = window.context
            if context is None or context.version.id != str(row["rate_version_id"]):
                continue
            device_id = str(row["device_id"])
            matched_tier_windows.add((window.window_id, device_id))
            accumulator = accumulators[int(row["bucket_index"])][device_id]
            energy_value = _decimal_or_zero(row["energy_kwh"])
            cost_value = _decimal_or_zero(row["energy_cost"])
            label = (
                f"{row['tier_name']} / {row['tou_period']}"
                if row["tou_period"]
                else str(row["tier_name"])
            )
            accumulator.add_cost(
                context=context,
                tou_period=label,
                rate=Decimal(str(row["price_per_kwh"])),
                energy_kwh=energy_value,
                cost=cost_value,
                tier_id=str(row["tier_stable_id"]),
                tier_name=str(row["tier_name"]),
                cumulative_start_kwh=Decimal(str(row["cumulative_start_kwh"])),
                cumulative_end_kwh=Decimal(str(row["cumulative_end_kwh"])),
                recalculation_version=int(row["recalculation_version"]),
                usage_authority_type=str(row["usage_authority_type"]),
            )

    # Missing chronological tier facts are materially different from a flat
    # rate gap. Preserve that distinction for every sensor/window so rolling
    # partial-day requests have the same quality semantics as the exact raw
    # path. The merge step applies these flags only when that sensor actually
    # contributed energy to the chart bucket.
    for window in tier_windows:
        for device_id in tier_account_devices.get(window.account_id, []):
            if (window.window_id, device_id) in matched_tier_windows:
                continue
            accumulator = accumulators[window.bucket_index][device_id]
            accumulator.cost_missing = True
            accumulator.quality_flags.add("tier_recalculation_required")

    return contribution_rows


def _merge_coarse_cost_accumulators(
    measurements: list[dict[str, DeviceBucketAccumulator]],
    costs: list[dict[str, DeviceBucketAccumulator]],
    device_accounts: dict[str, str | None],
) -> None:
    """Merge an independently queried pricing snapshot into measurement buckets."""

    for measurement_bucket, cost_bucket in zip(measurements, costs, strict=True):
        for device_id, accumulator in measurement_bucket.items():
            cost_accumulator = cost_bucket[device_id]
            accumulator.cost_parts = cost_accumulator.cost_parts
            accumulator.cost_missing = cost_accumulator.cost_missing
            if not accumulator.energy_available:
                continue
            accumulator.quality_flags.update(cost_accumulator.quality_flags)
            account_id = device_accounts.get(device_id)
            if account_id is None or not accumulator.cost_parts:
                accumulator.cost_missing = True


async def _load_parallel_coarse_history(
    session: AsyncSession,
    *,
    resolved: ResolvedHistoryScope,
    boundaries: list[datetime],
    plan: HistoryExecutionPlan,
    contexts: dict[str, list[RateContext]],
    device_accounts: dict[str, str | None],
    snapshot_at: datetime,
) -> tuple[CoarseLoadResult, int, float, float] | None:
    """Run independent exact aggregates against one exported PG snapshot.

    PostgreSQL's MVCC snapshot export keeps measurement and immutable pricing
    reads consistent without serially paying both range scans. Only one request
    per process may borrow an extra pool connection; all others retain the
    single-connection path so live device traffic keeps pool headroom.
    """

    bind = session.bind
    if (
        not isinstance(bind, AsyncEngine)
        or bind.dialect.name != "postgresql"
        or _parallel_coarse_history_slot.locked()
    ):
        return None

    isolation = str(await session.scalar(text("SHOW transaction_isolation")) or "")
    if isolation.lower() != "repeatable read":
        # A snapshot exported from READ COMMITTED would not govern the main
        # connection's following statement. Retain the serial path rather than
        # claim cross-query consistency that PostgreSQL does not provide.
        logger.info(
            "history.parallel_snapshot_isolation_unavailable",
            isolation=isolation,
        )
        return None

    await _parallel_coarse_history_slot.acquire()
    try:
        snapshot_id_value = await session.scalar(select(func.pg_export_snapshot()))
        snapshot_id = str(snapshot_id_value or "")
        if not _POSTGRES_SNAPSHOT_RE.fullmatch(snapshot_id):
            logger.warning("history.parallel_snapshot_invalid")
            return None

        try:
            async with bind.connect() as auxiliary_connection:
                auxiliary_connection = await auxiliary_connection.execution_options(
                    isolation_level="REPEATABLE READ"
                )
                transaction = await auxiliary_connection.begin()
                try:
                    # The snapshot identifier is generated by PostgreSQL and is
                    # restricted above to its documented hexadecimal/hyphen
                    # grammar before use in a statement that cannot bind it as
                    # a normal expression parameter.
                    await auxiliary_connection.execute(
                        text(f"SET TRANSACTION SNAPSHOT '{snapshot_id}'")
                    )
                    # PostgreSQL JIT compilation is counterproductive for this
                    # bounded interactive aggregate: compiling the generated
                    # exact bucket expressions can take longer than scanning
                    # the eligible facts. Scope the setting to this transaction
                    # only; analytical/background workloads retain their server
                    # policy.
                    await auxiliary_connection.execute(text("SET LOCAL jit = off"))
                    cost_accumulators = _empty_coarse_accumulators(resolved, boundaries)
                    async with AsyncSession(
                        bind=auxiliary_connection,
                        expire_on_commit=False,
                    ) as cost_session:

                        async def load_measurements() -> tuple[CoarseLoadResult, float]:
                            started = perf_counter()
                            result = await _load_coarse_measurements(
                                session,
                                resolved=resolved,
                                boundaries=boundaries,
                                plan=plan,
                                snapshot_at=snapshot_at,
                            )
                            return result, (perf_counter() - started) * 1000

                        async def load_costs() -> tuple[int, float]:
                            started = perf_counter()
                            count = await _apply_coarse_costs(
                                cost_session,
                                accumulators=cost_accumulators,
                                boundaries=boundaries,
                                contexts=contexts,
                                device_accounts=device_accounts,
                                snapshot_at=snapshot_at,
                            )
                            return count, (perf_counter() - started) * 1000

                        (
                            (measurement_result, source_ms),
                            (cost_rows, cost_ms),
                        ) = await asyncio.gather(load_measurements(), load_costs())
                    _merge_coarse_cost_accumulators(
                        measurement_result.accumulators,
                        cost_accumulators,
                        device_accounts,
                    )
                    return measurement_result, cost_rows, source_ms, cost_ms
                finally:
                    if transaction.is_active:
                        await transaction.rollback()
        except SQLAlchemyTimeoutError:
            # Pool pressure is an expected reason to retain the serial path;
            # correctness and device traffic take priority over this latency
            # optimization.
            logger.info("history.parallel_snapshot_pool_busy")
            return None
    finally:
        _parallel_coarse_history_slot.release()


async def query_history(
    session: AsyncSession,
    principal: SessionPrincipal,
    request: HistoryQueryRequest,
    *,
    source_strategy: Literal["auto", "raw", "coarse"] = "auto",
) -> HistoryQueryResponse:
    total_started = perf_counter()
    plan = HistoryExecutionPlan.from_request(request)
    bind = session.bind
    if isinstance(bind, AsyncEngine) and bind.dialect.name == "postgresql":
        if not session.in_transaction():
            # Establish one stable read snapshot before scope/rate resolution.
            # It may later be exported to the bounded pricing aggregate
            # connection.
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        await session.execute(text("SET LOCAL jit = off"))
    start = _aware_utc(request.start_utc)
    end = _aware_utc(request.end_utc)
    if end - start > MAX_HISTORY_RANGE:
        raise ProblemError(
            422,
            "History range is too large",
            "Select a range of 366 days or less",
            "history_range_limit",
        )
    scope_started = perf_counter()
    resolved = await resolve_history_scope(session, principal, request)
    scope_resolution_ms = (perf_counter() - scope_started) * 1000
    try:
        zone = ZoneInfo(request.timezone or resolved.site.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ProblemError(
            422, "Invalid timezone", "Use a valid IANA timezone", "history_timezone_invalid"
        ) from exc
    bucket = _automatic_bucket(start, end) if request.bucket == "auto" else request.bucket
    if bucket == "raw" and end - start > timedelta(days=2):
        raise ProblemError(
            422,
            "Raw history range is too large",
            "Raw history is limited to two days; select an aggregated bucket",
            "history_raw_range_limit",
        )
    boundaries = _bucket_boundaries(start, end, bucket, zone)
    total_buckets = len(boundaries) - 1
    if total_buckets > MAX_HISTORY_BUCKETS:
        raise ProblemError(
            422,
            "Too many history buckets",
            "Select a coarser bucket size",
            "history_bucket_limit",
        )
    page_start = (request.page - 1) * request.page_size
    page_end = min(page_start + request.page_size, total_buckets)
    if page_start >= total_buckets:
        raise ProblemError(
            422,
            "History page is outside the requested range",
            "Restart the History query from page 1",
            "history_page_invalid",
        )
    device_ids = [device.id for device in resolved.devices]
    site_data_state = await session.get(SiteDataState, resolved.site.id)
    history_revision = int(site_data_state.history_revision) if site_data_state is not None else 0
    fingerprint = _history_request_fingerprint(
        request,
        resolved,
        bucket,
        source_strategy,
        history_revision,
    )
    if request.page == 1 and request.continuation_token is not None:
        raise ProblemError(
            422,
            "History continuation is not valid for page 1",
            "Start a new History query without a continuation token",
            "history_continuation_unexpected",
        )
    if request.page > 1 and request.continuation_token is None:
        raise ProblemError(
            409,
            "History continuation is required",
            "Restart the History query from page 1",
            "history_continuation_required",
        )
    continuation = (
        _decode_history_continuation(
            token=request.continuation_token,
            principal=principal,
            fingerprint=fingerprint,
        )
        if request.continuation_token is not None
        else None
    )
    snapshot_at = continuation.snapshot_at if continuation is not None else datetime.now(UTC)
    # Page one performs one exact, full-range aggregate for summaries. Every
    # continuation page is restricted to its page windows and reuses the signed
    # summary/snapshot, so it never repeats full-range measurement or cost work.
    work_bucket_offset = page_start if continuation is not None else 0
    work_boundaries = (
        boundaries[page_start : page_end + 1] if continuation is not None else boundaries
    )
    work_start = work_boundaries[0]
    work_end = work_boundaries[-1]
    rate_context_started = perf_counter()
    contexts, device_accounts = (
        await _load_rate_contexts(session, resolved, start, end) if plan.needs_cost else ({}, {})
    )
    # A pricing digest exists only to bind paginated responses to one immutable
    # pricing snapshot. Single-page requests have no continuation to validate,
    # so avoid an otherwise redundant database round trip on the common 7-day
    # History view.
    requires_pricing_snapshot = continuation is not None or page_end < total_buckets
    pricing_input_fingerprint = "0" * 64
    if requires_pricing_snapshot:
        pricing_input_fingerprint = await _history_pricing_input_fingerprint(
            session,
            contexts=contexts,
            device_accounts=device_accounts,
            start=start,
            end=end,
        )
    if continuation is not None and not hmac.compare_digest(
        continuation.pricing_input_fingerprint,
        pricing_input_fingerprint,
    ):
        _raise_history_pricing_snapshot_changed()
    rate_context_ms = (perf_counter() - rate_context_started) * 1000
    if continuation is not None:
        use_coarse = continuation.use_coarse
    else:
        use_coarse = source_strategy == "coarse" or (
            source_strategy == "auto" and bucket in COARSE_HISTORY_BUCKETS
        )
        if (
            use_coarse
            and plan.needs_cost
            and await _coarse_requires_raw_tier_fallback(
                session,
                contexts=contexts,
                start=start,
                end=end,
            )
        ):
            use_coarse = False
            logger.info(
                "history.coarse_tier_fallback",
                site_id=resolved.site.id,
                start_utc=start,
                end_utc=end,
                reason="account_level_tier_segments",
            )
    rows: list[tuple[RawReading, NormalizedInterval | None]] = []
    coarse_result: CoarseLoadResult | None = None
    source_query_ms = 0.0
    tier_segment_query_ms = 0.0
    coarse_cost_row_count = 0
    parallel_result = None
    if use_coarse and plan.needs_cost:
        parallel_result = await _load_parallel_coarse_history(
            session,
            resolved=resolved,
            boundaries=work_boundaries,
            plan=plan,
            contexts=contexts,
            device_accounts=device_accounts,
            snapshot_at=snapshot_at,
        )
    if parallel_result is not None:
        (
            coarse_result,
            coarse_cost_row_count,
            source_query_ms,
            tier_segment_query_ms,
        ) = parallel_result
    else:
        source_started = perf_counter()
        if use_coarse:
            coarse_result = await _load_coarse_measurements(
                session,
                resolved=resolved,
                boundaries=work_boundaries,
                plan=plan,
                snapshot_at=snapshot_at,
            )
        else:
            rows = await _load_source_rows(
                session,
                device_ids=device_ids,
                start=work_start,
                end=work_end,
                plan=plan,
                snapshot_at=snapshot_at,
            )
        source_query_ms = (perf_counter() - source_started) * 1000
    if len(rows) > MAX_SOURCE_ROWS:
        raise ProblemError(
            422,
            "History query is too large",
            "Select a shorter range or coarser scope",
            "history_source_row_limit",
        )
    tier_segment_started = perf_counter()
    if coarse_result is not None and plan.needs_cost and parallel_result is None:
        coarse_cost_row_count = await _apply_coarse_costs(
            session,
            accumulators=coarse_result.accumulators,
            boundaries=work_boundaries,
            contexts=contexts,
            device_accounts=device_accounts,
            snapshot_at=snapshot_at,
        )
        _merge_coarse_cost_accumulators(
            coarse_result.accumulators,
            coarse_result.accumulators,
            device_accounts,
        )
    tier_segment_indexes: dict[str, TierSegmentIndex] = {}
    tier_segment_count = 0
    account_ids = {value for value in device_accounts.values() if value}
    if coarse_result is None and plan.needs_cost and account_ids:
        tier_segments = list(
            await session.scalars(
                select(TierAllocationSegment)
                .join(
                    BillingCycle,
                    BillingCycle.id == TierAllocationSegment.billing_cycle_id,
                )
                .where(
                    TierAllocationSegment.utility_account_id.in_(account_ids),
                    TierAllocationSegment.recalculation_version
                    == BillingCycle.recalculation_version,
                    TierAllocationSegment.interval_start < work_end,
                    TierAllocationSegment.interval_end > work_start,
                )
                .order_by(
                    TierAllocationSegment.interval_start,
                    TierAllocationSegment.segment_order,
                )
            )
        )
        tier_segment_count = len(tier_segments)
        tier_segments_by_account: dict[str, list[TierAllocationSegment]] = defaultdict(list)
        for segment in tier_segments:
            tier_segments_by_account[segment.utility_account_id].append(segment)
        tier_segment_indexes = {
            account_id: TierSegmentIndex.build(values)
            for account_id, values in tier_segments_by_account.items()
        }
    if parallel_result is None:
        tier_segment_query_ms = (perf_counter() - tier_segment_started) * 1000
    aggregation_started = perf_counter()
    if coarse_result is not None:
        accumulators = coarse_result.accumulators
    else:
        accumulators = [
            {device.id: DeviceBucketAccumulator() for device in resolved.devices}
            for _ in range(len(work_boundaries) - 1)
        ]
        for raw, normalized in rows:
            raw_start = max(work_start, _aware_utc(raw.interval_start))
            raw_end = min(work_end, _aware_utc(raw.interval_end))
            if raw_end <= raw_start:
                continue
            index = max(0, bisect_right(work_boundaries, raw_start) - 1)
            while index < len(accumulators) and work_boundaries[index] < raw_end:
                left = max(raw_start, work_boundaries[index])
                right = min(raw_end, work_boundaries[index + 1])
                if right > left:
                    account_id = device_accounts.get(raw.device_id)
                    _add_reading(
                        accumulators[index][raw.device_id],
                        raw,
                        normalized,
                        left,
                        right,
                        contexts.get(account_id or "", []),
                        tier_segment_indexes.get(account_id or ""),
                        plan,
                    )
                index += 1
    combined_work: list[HistoryBucket] = []
    for index in range(len(work_boundaries) - 1):
        left, right = work_boundaries[index], work_boundaries[index + 1]
        combined_work.append(
            _combined_bucket(
                accumulators=accumulators[index],
                devices=resolved.devices,
                allocations=resolved.allocations,
                display_name=resolved.display_name,
                left=left,
                right=right,
                zone=zone,
                strict=request.strict_coverage,
            )
        )
    if continuation is None:
        summary = _summary(combined_work, start, end)
        if resolved.overlap and request.display_mode == "individual":
            _withhold_overlapping_totals(summary)
        selected_summary = (
            _summary(
                combined_work,
                _aware_utc(request.selection_start_utc),
                _aware_utc(request.selection_end_utc),
            )
            if request.selection_start_utc and request.selection_end_utc
            else None
        )
        if resolved.overlap and request.display_mode == "individual" and selected_summary:
            _withhold_overlapping_totals(selected_summary)
        detail_start = page_start
        detail_end = page_end
    else:
        summary = continuation.summary
        selected_summary = continuation.selected_summary
        detail_start = 0
        detail_end = page_end - page_start
    combined = combined_work[detail_start:detail_end] if plan.return_combined else []
    individual = (
        [
            HistoryIndividualSeries(
                device_id=device.id,
                name=device.name,
                circuit_name=(
                    resolved.circuits[device.circuit_id].name
                    if device.circuit_id in resolved.circuits
                    else None
                ),
                status=device.status,
                points=[
                    _individual_bucket(
                        accumulator=accumulators[index][device.id],
                        device=device,
                        left=work_boundaries[index],
                        right=work_boundaries[index + 1],
                        zone=zone,
                    )
                    for index in range(detail_start, detail_end)
                ],
            )
            for device in resolved.devices
        ]
        if plan.build_individual
        else []
    )
    aggregation_ms = (perf_counter() - aggregation_started) * 1000
    if continuation is None:
        all_contributions = [
            contribution for point in combined_work for contribution in point.rate_contributions
        ]
        version_map: dict[str, dict[str, Any]] = {}
        for contribution in all_contributions:
            version_map[contribution.rate_version_id] = {
                "rate_plan_id": contribution.rate_plan_id,
                "rate_plan_name": contribution.rate_plan_name,
                "rate_version_id": contribution.rate_version_id,
                "rate_version": contribution.rate_version,
                "effective_from": contribution.rate_effective_from,
            }
        rate_versions_used = list(version_map.values())
        if any(
            point.energy_kwh is not None and "rate_unavailable" in point.quality_flags
            for point in combined_work
        ):
            tier_recalculation_required = any(
                "tier_recalculation_required" in point.quality_flags for point in combined_work
            )
            resolved.warnings.append(
                {
                    "code": "rate_unavailable",
                    "message": (
                        "Cost is unavailable until chronological billing-cycle tier "
                        "allocation is recalculated."
                        if tier_recalculation_required
                        else "Cost is unavailable where a selected sensor has no "
                        "historically effective rate assignment."
                    ),
                    "device_ids": [
                        device.id
                        for device in resolved.devices
                        if not contexts.get(device_accounts.get(device.id) or "")
                    ],
                }
            )
        warnings = resolved.warnings
        mixed_rates = len(
            {(item.rate_plan_id, item.rate_version_id) for item in all_contributions}
        ) > 1 or any(point.mixed_rates for point in combined_work)
    else:
        rate_versions_used = continuation.rate_versions_used
        warnings = continuation.warnings
        mixed_rates = continuation.mixed_rates
    next_page = request.page + 1 if page_end < total_buckets else None
    if continuation is not None or next_page is not None:
        # Read-committed transactions can observe a rate publication or tier
        # recalculation between statements. Verify the full input digest again
        # before returning data/token so a page can never mix two revisions.
        final_contexts, final_device_accounts = (
            await _load_rate_contexts(session, resolved, start, end)
            if plan.needs_cost
            else ({}, {})
        )
        final_pricing_input_fingerprint = await _history_pricing_input_fingerprint(
            session,
            contexts=final_contexts,
            device_accounts=final_device_accounts,
            start=start,
            end=end,
        )
        if not hmac.compare_digest(
            pricing_input_fingerprint,
            final_pricing_input_fingerprint,
        ):
            _raise_history_pricing_snapshot_changed()
    next_continuation_token = None
    if next_page is not None:
        next_continuation_token = (
            continuation.token
            if continuation is not None
            else _encode_history_continuation(
                principal=principal,
                fingerprint=fingerprint,
                snapshot_at=snapshot_at,
                pricing_input_fingerprint=pricing_input_fingerprint,
                summary=summary,
                selected_summary=selected_summary,
                rate_versions_used=rate_versions_used,
                warnings=warnings,
                mixed_rates=mixed_rates,
                use_coarse=use_coarse,
            )
        )
    serialization_started = perf_counter()
    response = HistoryQueryResponse(
        scope=HistoryResolvedScope(
            type=request.scope.type,
            display_name=resolved.display_name,
            site_id=resolved.site.id,
            site_name=resolved.site.name,
            timezone=zone.key,
            included_device_ids=[device.id for device in resolved.devices],
            included_device_names=[device.name for device in resolved.devices],
            excluded_device_ids=resolved.excluded_device_ids,
            mixed_rates=mixed_rates,
        ),
        display_mode=request.display_mode,
        metrics=list(request.metrics),
        bucket=bucket,
        summary=summary,
        selected_summary=selected_summary,
        combined=combined,
        individual=individual,
        rate_versions_used=rate_versions_used,
        warnings=warnings,
        total_buckets=total_buckets,
        page=request.page,
        page_size=request.page_size,
        next_page=next_page,
        next_continuation_token=next_continuation_token,
    )
    response_bytes = len(response.model_dump_json().encode("utf-8"))
    serialization_ms = (perf_counter() - serialization_started) * 1000
    total_ms = (perf_counter() - total_started) * 1000
    source_row_count = (
        coarse_result.scanned_reading_count if coarse_result is not None else len(rows)
    )
    logger.info(
        "history.query_completed" if source_row_count else "history.query_empty",
        site_id=resolved.site.id,
        scope_type=request.scope.type,
        device_ids=device_ids,
        source_reading_count=source_row_count,
        returned_bucket_count=len(combined) + sum(len(series.points) for series in individual),
        start_utc=start,
        end_utc=end,
        bucket=bucket,
        metrics=sorted(plan.metrics),
        source_kind=("database_aggregate" if coarse_result is not None else "raw_readings"),
        continuation_reused=continuation is not None,
        detail_bucket_start=work_bucket_offset,
        detail_bucket_count=page_end - page_start,
        summary_bucket_count=(len(combined_work) if continuation is None else 0),
        snapshot_at=snapshot_at,
        scope_resolution_ms=round(scope_resolution_ms, 3),
        source_query_ms=round(source_query_ms, 3),
        source_row_count=source_row_count,
        aggregate_row_count=(coarse_result.aggregate_row_count if coarse_result is not None else 0),
        quality_row_count=(coarse_result.quality_row_count if coarse_result is not None else 0),
        coarse_cost_row_count=coarse_cost_row_count,
        rate_context_ms=round(rate_context_ms, 3),
        tier_segment_query_ms=round(tier_segment_query_ms, 3),
        tier_segment_count=tier_segment_count,
        aggregation_ms=round(aggregation_ms, 3),
        serialization_ms=round(serialization_ms, 3),
        total_ms=round(total_ms, 3),
        response_bytes=response_bytes,
        cache_status=("signed_continuation" if continuation is not None else "summary_snapshot"),
    )
    return response


def history_csv(response: HistoryQueryResponse) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["power-monitor-history-export/1.0"])
    writer.writerow(["scope_type", _csv_safe(response.scope.type)])
    writer.writerow(["scope_name", _csv_safe(response.scope.display_name)])
    writer.writerow(["site_id", _csv_safe(response.scope.site_id)])
    writer.writerow(["timezone", _csv_safe(response.scope.timezone)])
    writer.writerow(["display_mode", _csv_safe(response.display_mode)])
    writer.writerow(["bucket", _csv_safe(response.bucket)])
    writer.writerow(["included_device_ids", "|".join(response.scope.included_device_ids)])
    writer.writerow(
        ["included_device_names", _csv_safe("|".join(response.scope.included_device_names))]
    )
    writer.writerow(["excluded_device_ids", "|".join(response.scope.excluded_device_ids)])
    writer.writerow([])
    writer.writerow(
        [
            "series_type",
            "series_id",
            "series_name",
            "device_id",
            "interval_start_utc",
            "interval_end_utc",
            "local_start",
            "local_end",
            "utc_offset",
            "energy_kwh",
            "average_power_w",
            "peak_power_w",
            "voltage_min_v",
            "voltage_avg_v",
            "voltage_max_v",
            "current_a",
            "power_factor",
            "frequency_hz",
            "tou_period",
            "rate_per_kwh",
            "interval_energy_cost",
            "rate_plan",
            "rate_version_id",
            "mixed_rates",
            "included_sensor_count",
            "contributing_sensor_count",
            "coverage_percent",
            "missing_sensor_ids",
            "quality_flags",
            "rate_contributions_json",
        ]
    )

    def write_point(series_type: str, point: HistoryBucket) -> None:
        import json

        writer.writerow(
            [
                series_type,
                _csv_safe(point.series_id),
                _csv_safe(point.series_name),
                _csv_safe(point.device_id),
                point.interval_start_utc.isoformat(),
                point.interval_end_utc.isoformat(),
                point.local_start,
                point.local_end,
                point.utc_offset,
                point.energy_kwh,
                point.average_power_w,
                point.peak_power_w,
                point.voltage_min_v,
                point.voltage_avg_v,
                point.voltage_max_v,
                point.current_a,
                point.power_factor,
                point.frequency_hz,
                _csv_safe(point.tou_period),
                point.rate_per_kwh,
                point.energy_cost,
                _csv_safe(point.rate_plan_name),
                point.rate_version_id,
                point.mixed_rates,
                point.included_sensor_count,
                point.contributing_sensor_count,
                point.coverage_percent,
                "|".join(point.missing_sensor_ids),
                "|".join(point.quality_flags),
                json.dumps(
                    [item.model_dump(mode="json") for item in point.rate_contributions],
                    separators=(",", ":"),
                ),
            ]
        )

    for point in response.combined:
        write_point("combined", point)
    for series in response.individual:
        for point in series.points:
            write_point("individual", point)
    return output.getvalue()
