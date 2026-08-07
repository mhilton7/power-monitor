from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AccountUsageAuthority,
    AuditEvent,
    BillingCycle,
    Circuit,
    Device,
    UtilityAccount,
)
from app.problem import ProblemError

SensorAuthorityMode = Literal["whole_account_meter", "service_leg_pair"]


@dataclass(frozen=True)
class AuthorityApplyRequest:
    mode: SensorAuthorityMode
    device_ids: tuple[str, ...]
    expected_revision: int | None
    actor_id: str | None
    reason: str
    idempotency_key: str
    source_ip: str | None = None


def _base_reason(device: Device, account: UtilityAccount, circuit: Circuit | None) -> str:
    if device.lifecycle_status == "decommissioned":
        return "removed"
    if device.lifecycle_status != "active" or device.revoked_at is not None:
        return "inactive"
    if device.utility_account_id != account.id:
        return "wrong_account"
    if device.site_id != account.site_id:
        return "wrong_site"
    if circuit is None:
        return "missing_circuit"
    return "eligible"


def _mode_reason(
    device: Device,
    account: UtilityAccount,
    circuit: Circuit | None,
    mode: SensorAuthorityMode,
) -> str:
    reason = _base_reason(device, account, circuit)
    if reason != "eligible":
        return reason
    if circuit is None:
        return "missing_circuit"
    expected_role = "main" if mode == "whole_account_meter" else "service-leg"
    if device.measurement_role != expected_role or circuit.measurement_role != expected_role:
        return "wrong_measurement_role"
    if mode == "service_leg_pair" and not circuit.split_phase_group:
        return "topology_overlap"
    return "eligible"


def _pair_topology_reason(circuits: list[Circuit]) -> str | None:
    if len(circuits) != 2 or circuits[0].id == circuits[1].id:
        return "duplicate"
    first, second = circuits
    if first.parent_id == second.id or second.parent_id == first.id:
        return "topology_overlap"
    if not first.split_phase_group or first.split_phase_group != second.split_phase_group:
        return "topology_overlap"
    return None


async def authority_reconciliation_plan(
    session: AsyncSession,
    account: UtilityAccount,
    authority: AccountUsageAuthority | None = None,
) -> dict[str, Any]:
    if authority is None:
        authority = await session.scalar(
            select(AccountUsageAuthority).where(
                AccountUsageAuthority.utility_account_id == account.id
            )
        )
    raw_stored_ids = list(authority.device_ids if authority else [])
    stored_ids = list(dict.fromkeys(raw_stored_ids))
    account_devices = list(
        await session.scalars(
            select(Device).where(
                Device.site_id == account.site_id,
                Device.utility_account_id == account.id,
            )
        )
    )
    referenced_devices = (
        list(await session.scalars(select(Device).where(Device.id.in_(stored_ids))))
        if stored_ids
        else []
    )
    devices_by_id = {device.id: device for device in [*account_devices, *referenced_devices]}
    circuit_ids = {device.circuit_id for device in devices_by_id.values() if device.circuit_id}
    circuits = (
        {
            circuit.id: circuit
            for circuit in await session.scalars(select(Circuit).where(Circuit.id.in_(circuit_ids)))
        }
        if circuit_ids
        else {}
    )

    sensor_rows: list[dict[str, Any]] = []
    eligible_whole: list[dict[str, Any]] = []
    eligible_legs: list[dict[str, Any]] = []
    for device in sorted(account_devices, key=lambda item: (item.name.lower(), item.id)):
        circuit = circuits.get(device.circuit_id or "")
        whole_reason = _mode_reason(device, account, circuit, "whole_account_meter")
        leg_reason = _mode_reason(device, account, circuit, "service_leg_pair")
        row = {
            "id": device.id,
            "name": device.name,
            "lifecycle": device.lifecycle_status,
            "site_id": device.site_id,
            "utility_account_id": device.utility_account_id,
            "measurement_role": device.measurement_role,
            "circuit_id": device.circuit_id,
            "circuit_name": circuit.name if circuit else None,
            "circuit_role": circuit.measurement_role if circuit else None,
            "split_phase_group": circuit.split_phase_group if circuit else None,
            "whole_account_reason": whole_reason,
            "service_leg_reason": leg_reason,
        }
        sensor_rows.append(row)
        if whole_reason == "eligible":
            eligible_whole.append(row)
        if leg_reason == "eligible":
            eligible_legs.append(row)

    mode: SensorAuthorityMode | None = (
        cast(SensorAuthorityMode, authority.authority_type)
        if authority and authority.authority_type in {"whole_account_meter", "service_leg_pair"}
        else None
    )
    valid_ids: list[str] = []
    invalid: list[dict[str, str]] = []
    seen: set[str] = set()
    for device_id in raw_stored_ids:
        if device_id in seen:
            invalid.append({"device_id": device_id, "reason": "duplicate"})
            continue
        seen.add(device_id)
        stored_device = devices_by_id.get(device_id)
        if stored_device is None:
            invalid.append({"device_id": device_id, "reason": "stale_reference"})
            continue
        circuit = circuits.get(stored_device.circuit_id or "")
        reason = (
            _mode_reason(stored_device, account, circuit, mode)
            if mode
            else "wrong_measurement_role"
        )
        if reason == "eligible":
            valid_ids.append(device_id)
        else:
            invalid.append({"device_id": device_id, "name": stored_device.name, "reason": reason})

    if mode == "service_leg_pair" and not invalid and len(valid_ids) == 2:
        pair_circuits = [circuits[devices_by_id[item].circuit_id or ""] for item in valid_ids]
        pair_reason = _pair_topology_reason(pair_circuits)
        if pair_reason:
            invalid.extend({"device_id": item, "reason": pair_reason} for item in valid_ids)
            valid_ids = []

    expected_count = 1 if mode == "whole_account_meter" else 2 if mode else 0
    healthy = bool(mode and len(valid_ids) == expected_count and not invalid)
    if invalid:
        recommendation = (
            "The saved usage source references an ineligible sensor. "
            "Choose the current eligible sensors and save to repair the configuration."
        )
    elif not account_devices:
        recommendation = "Assign an active sensor to this utility account."
    elif not healthy:
        recommendation = (
            "Select one verified whole-account meter or two verified non-overlapping "
            "service-leg sensors."
        )
    else:
        recommendation = "No authority repair is required."

    return {
        "configured": authority is not None,
        "authority_type": authority.authority_type if authority else None,
        "calculation_role": authority.calculation_role if authority else "unavailable",
        "complete_account": bool(authority.complete_account) if authority else False,
        "confidence": authority.confidence if authority else "unknown",
        "source_reference": authority.source_reference if authority else None,
        "aggregate_set_id": authority.aggregate_set_id if authority else None,
        "device_ids": raw_stored_ids,
        "valid_device_ids": valid_ids,
        "invalid_devices": invalid,
        "invalid_device_ids": [item["device_id"] for item in invalid],
        "revision": authority.revision if authority else 0,
        "updated_at": authority.updated_at.isoformat() if authority else None,
        "stored_authority_healthy": healthy,
        "account_assigned_sensors": sensor_rows,
        "eligible_whole_account_sensors": eligible_whole,
        "eligible_service_leg_sensors": eligible_legs,
        "recommended_repair": recommendation,
    }


async def apply_sensor_usage_authority(
    session: AsyncSession,
    account: UtilityAccount,
    change: AuthorityApplyRequest,
) -> tuple[AccountUsageAuthority, dict[str, Any]]:
    existing_audit = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "utility_account.usage_authority_reconciled",
            AuditEvent.object_id == account.id,
            AuditEvent.correlation_id == change.idempotency_key,
        )
    )
    authority = await session.scalar(
        select(AccountUsageAuthority)
        .where(AccountUsageAuthority.utility_account_id == account.id)
        .with_for_update()
    )
    if existing_audit is not None and authority is not None:
        return authority, await authority_reconciliation_plan(session, account, authority)

    current_revision = authority.revision if authority else None
    if change.expected_revision != current_revision:
        raise ProblemError(
            409,
            "Usage authority changed",
            "Reload the account usage authority before saving",
            "stale_revision",
        )
    selected_ids = list(change.device_ids)
    expected_count = 1 if change.mode == "whole_account_meter" else 2
    if len(selected_ids) != expected_count or len(set(selected_ids)) != expected_count:
        raise ProblemError(
            422,
            "Sensor selection is invalid",
            f"{change.mode} requires exactly {expected_count} distinct eligible sensor(s)",
            "usage_authority_device_invalid",
        )
    selected = list(await session.scalars(select(Device).where(Device.id.in_(selected_ids))))
    if len(selected) != expected_count:
        raise ProblemError(
            422,
            "Sensor selection is invalid",
            "The selection contains a stale sensor reference",
            "usage_authority_stale_reference",
        )
    circuits_by_id = {
        circuit.id: circuit
        for circuit in await session.scalars(
            select(Circuit).where(
                Circuit.id.in_({item.circuit_id for item in selected if item.circuit_id})
            )
        )
    }
    for device in selected:
        reason = _mode_reason(
            device, account, circuits_by_id.get(device.circuit_id or ""), change.mode
        )
        if reason != "eligible":
            raise ProblemError(
                422,
                "Sensor topology is not eligible",
                f"{device.name} is not eligible: {reason}",
                f"usage_authority_{reason}",
            )
    if change.mode == "service_leg_pair":
        pair_reason = _pair_topology_reason(
            [circuits_by_id[item.circuit_id] for item in selected if item.circuit_id]
        )
        if pair_reason:
            raise ProblemError(
                422,
                "Service-leg topology is invalid",
                "Choose two distinct, non-overlapping circuits in the same reviewed "
                "split-phase group",
                "usage_authority_topology_overlap",
            )

    before = await authority_reconciliation_plan(session, account, authority)
    now = datetime.now(UTC)
    if authority is None:
        authority = AccountUsageAuthority(
            utility_account_id=account.id,
            authority_type=change.mode,
            calculation_role="sensor_measurements",
            device_ids=[],
            confidence="high",
            complete_account=True,
            revision=1,
            updated_at=now,
        )
        session.add(authority)
    else:
        authority.revision += 1
    authority.authority_type = change.mode
    authority.calculation_role = "sensor_measurements"
    authority.aggregate_set_id = None
    authority.device_ids = sorted(selected_ids)
    authority.source_reference = None
    authority.confidence = "high"
    authority.complete_account = True
    authority.updated_by = change.actor_id
    authority.updated_at = now
    await session.flush()
    after = await authority_reconciliation_plan(session, account, authority)
    if not after["stored_authority_healthy"]:
        raise ProblemError(
            422,
            "Usage authority is invalid",
            "The selected sensors did not produce a healthy authority plan",
            "usage_authority_reconciliation_failed",
        )
    cycles = list(
        await session.scalars(
            select(BillingCycle).where(
                BillingCycle.utility_account_id == account.id,
                BillingCycle.finalized_at.is_(None),
                BillingCycle.status != "finalized",
            )
        )
    )
    for cycle in cycles:
        cycle.recalculation_required = True
        cycle.usage_source_type = "unavailable"
        cycle.tier_progress_source_type = "unavailable"
        cycle.projection_source_type = "unavailable"
        cycle.updated_by = change.actor_id
        cycle.updated_at = now
    session.add(
        AuditEvent(
            occurred_at=now,
            actor_type="user",
            actor_id=change.actor_id,
            action="utility_account.usage_authority_reconciled",
            object_type="account_usage_authority",
            object_id=account.id,
            source_ip=change.source_ip,
            outcome="success",
            correlation_id=change.idempotency_key,
            details={
                "reason": change.reason,
                "before": before,
                "after": after,
                "unfinalized_cycles_marked": len(cycles),
            },
        )
    )
    return authority, after
