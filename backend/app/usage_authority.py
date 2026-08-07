from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import or_, select
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

ELIGIBILITY_MESSAGES: dict[str, str] = {
    "removed": "This sensor has been removed and cannot be used for billing.",
    "inactive": "This sensor is not active.",
    "revoked": "This sensor's credentials have been revoked.",
    "wrong_account": "This sensor belongs to a different electric service.",
    "wrong_site": "This sensor belongs to a different home.",
    "missing_circuit": "Assign this sensor to a reviewed physical circuit.",
    "wrong_device_role": "The sensor measurement role does not match this billing mode.",
    "wrong_circuit_role": "The circuit measurement role does not match this billing mode.",
    "missing_split_phase_group": (
        "A service-leg circuit must belong to a reviewed split-phase service group."
    ),
    "duplicate_sensor": "Choose distinct sensors.",
    "duplicate_circuit": "The selected sensors must measure distinct circuits.",
    "topology_overlap": "The selected service-leg circuits overlap.",
    "stale_reference": "The saved sensor no longer exists.",
}


@dataclass(frozen=True)
class AuthorityApplyRequest:
    mode: SensorAuthorityMode
    device_ids: tuple[str, ...]
    expected_revision: int | None
    actor_id: str | None
    reason: str
    idempotency_key: str
    source_ip: str | None = None


def _base_reasons(
    device: Device, account: UtilityAccount, circuit: Circuit | None
) -> tuple[str, ...]:
    reasons: list[str] = []
    if device.lifecycle_status == "decommissioned":
        reasons.append("removed")
    elif device.lifecycle_status != "active":
        reasons.append("inactive")
    if device.revoked_at is not None:
        reasons.append("revoked")
    if device.utility_account_id != account.id:
        reasons.append("wrong_account")
    if device.site_id != account.site_id:
        reasons.append("wrong_site")
    if circuit is None:
        reasons.append("missing_circuit")
    return tuple(reasons)


def _mode_reasons(
    device: Device,
    account: UtilityAccount,
    circuit: Circuit | None,
    mode: SensorAuthorityMode,
) -> tuple[str, ...]:
    reasons = list(_base_reasons(device, account, circuit))
    if circuit is None:
        return tuple(reasons)
    expected_role = "main" if mode == "whole_account_meter" else "service-leg"
    if device.measurement_role != expected_role:
        reasons.append("wrong_device_role")
    if circuit.measurement_role != expected_role:
        reasons.append("wrong_circuit_role")
    if mode == "service_leg_pair" and not circuit.split_phase_group:
        reasons.append("missing_split_phase_group")
    return tuple(reasons)


def _primary_reason(reasons: tuple[str, ...]) -> str:
    return reasons[0] if reasons else "eligible"


def _messages(reasons: tuple[str, ...]) -> list[str]:
    return [ELIGIBILITY_MESSAGES[reason] for reason in reasons]


def _pair_topology_reason(circuits: list[Circuit]) -> str | None:
    if len(circuits) != 2:
        return "topology_overlap"
    if circuits[0].id == circuits[1].id:
        return "duplicate_circuit"
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
    relevant_statement = select(Device).where(Device.site_id == account.site_id)
    if stored_ids:
        relevant_statement = select(Device).where(
            or_(Device.site_id == account.site_id, Device.id.in_(stored_ids))
        )
    relevant_devices = list(await session.scalars(relevant_statement))
    devices_by_id = {device.id: device for device in relevant_devices}
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
    for device in sorted(relevant_devices, key=lambda item: (item.name.lower(), item.id)):
        circuit = circuits.get(device.circuit_id or "")
        whole_reasons = _mode_reasons(device, account, circuit, "whole_account_meter")
        leg_reasons = _mode_reasons(device, account, circuit, "service_leg_pair")
        selected_mode = (
            cast(SensorAuthorityMode, authority.authority_type)
            if authority and authority.authority_type in {"whole_account_meter", "service_leg_pair"}
            else "whole_account_meter"
        )
        current_reasons = whole_reasons if selected_mode == "whole_account_meter" else leg_reasons
        currently_saved = device.id in stored_ids
        row = {
            "id": device.id,
            "device_id": device.id,
            "name": device.name,
            "lifecycle": device.lifecycle_status,
            "active": device.lifecycle_status == "active" and device.revoked_at is None,
            "revoked": device.revoked_at is not None,
            "site_id": device.site_id,
            "utility_account_id": device.utility_account_id,
            "measurement_role": device.measurement_role,
            "device_measurement_role": device.measurement_role,
            "circuit_id": device.circuit_id,
            "circuit_name": circuit.name if circuit else None,
            "circuit_role": circuit.measurement_role if circuit else None,
            "circuit_measurement_role": circuit.measurement_role if circuit else None,
            "split_phase_group": circuit.split_phase_group if circuit else None,
            "eligible_whole_home": not whole_reasons,
            "eligible_service_leg": not leg_reasons,
            "whole_home_eligibility_codes": list(whole_reasons),
            "whole_home_eligibility_messages": _messages(whole_reasons),
            "service_leg_eligibility_codes": list(leg_reasons),
            "service_leg_eligibility_messages": _messages(leg_reasons),
            "eligibility_codes": list(current_reasons),
            "eligibility_messages": _messages(current_reasons),
            "whole_account_reason": _primary_reason(whole_reasons),
            "service_leg_reason": _primary_reason(leg_reasons),
            "currently_saved_in_authority": currently_saved,
            "stale_authority_reference": currently_saved and bool(current_reasons),
        }
        sensor_rows.append(row)
        belongs_to_account = (
            device.site_id == account.site_id and device.utility_account_id == account.id
        )
        if belongs_to_account and not whole_reasons:
            eligible_whole.append(row)
        if belongs_to_account and not leg_reasons:
            eligible_legs.append(row)

    account_sensor_rows = [
        row
        for row in sensor_rows
        if row["site_id"] == account.site_id and row["utility_account_id"] == account.id
    ]

    mode: SensorAuthorityMode | None = (
        cast(SensorAuthorityMode, authority.authority_type)
        if authority and authority.authority_type in {"whole_account_meter", "service_leg_pair"}
        else None
    )
    valid_ids: list[str] = []
    invalid: list[dict[str, Any]] = []
    seen: set[str] = set()
    for device_id in raw_stored_ids:
        if device_id in seen:
            invalid.append(
                {
                    "device_id": device_id,
                    "reason": "duplicate_sensor",
                    "reasons": ["duplicate_sensor"],
                    "messages": [ELIGIBILITY_MESSAGES["duplicate_sensor"]],
                }
            )
            continue
        seen.add(device_id)
        stored_device = devices_by_id.get(device_id)
        if stored_device is None:
            invalid.append(
                {
                    "device_id": device_id,
                    "reason": "stale_reference",
                    "reasons": ["stale_reference"],
                    "messages": [ELIGIBILITY_MESSAGES["stale_reference"]],
                }
            )
            continue
        circuit = circuits.get(stored_device.circuit_id or "")
        reasons = (
            _mode_reasons(stored_device, account, circuit, mode) if mode else ("wrong_device_role",)
        )
        if not reasons:
            valid_ids.append(device_id)
        else:
            invalid.append(
                {
                    "device_id": device_id,
                    "name": stored_device.name,
                    "reason": _primary_reason(reasons),
                    "reasons": list(reasons),
                    "messages": _messages(reasons),
                }
            )

    if mode == "service_leg_pair" and not invalid and len(valid_ids) == 2:
        pair_circuits = [circuits[devices_by_id[item].circuit_id or ""] for item in valid_ids]
        pair_reason = _pair_topology_reason(pair_circuits)
        if pair_reason:
            invalid.extend(
                {
                    "device_id": item,
                    "reason": pair_reason,
                    "reasons": [pair_reason],
                    "messages": [ELIGIBILITY_MESSAGES[pair_reason]],
                }
                for item in valid_ids
            )
            valid_ids = []

    expected_count = 1 if mode == "whole_account_meter" else 2 if mode else 0
    healthy = bool(mode and len(valid_ids) == expected_count and not invalid)
    if invalid:
        recommendation = (
            "The saved usage source references an ineligible sensor. "
            "Choose the current eligible sensors and save to repair the configuration."
        )
    elif not account_sensor_rows:
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
        "sensors": sensor_rows,
        "account_assigned_sensors": account_sensor_rows,
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
    if len(selected_ids) != len(set(selected_ids)):
        raise ProblemError(
            422,
            "Sensor selection is invalid",
            "Choose distinct sensors; the same sensor cannot be selected more than once",
            "usage_authority_sensor_duplicate",
        )
    if len(selected_ids) != expected_count:
        selection = (
            "one complete-service sensor"
            if change.mode == "whole_account_meter"
            else "exactly two service-leg sensors"
        )
        raise ProblemError(
            422,
            "Sensor selection is incomplete",
            f"Select {selection}",
            "usage_authority_sensor_count",
        )
    selected = list(await session.scalars(select(Device).where(Device.id.in_(selected_ids))))
    if len(selected) != expected_count:
        raise ProblemError(
            422,
            "Saved sensor is no longer available",
            "The selection contains a stale sensor reference",
            "usage_authority_sensor_stale",
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
        reasons = _mode_reasons(
            device, account, circuits_by_id.get(device.circuit_id or ""), change.mode
        )
        if reasons:
            reason = _primary_reason(reasons)
            if reason in {"removed", "inactive", "revoked"}:
                code = "usage_authority_sensor_inactive"
                title = "Sensor is inactive"
            elif reason == "wrong_account":
                code = "usage_authority_sensor_wrong_account"
                title = "Sensor belongs to another electric service"
            elif reason == "wrong_site":
                code = "usage_authority_sensor_wrong_site"
                title = "Sensor belongs to another home"
            elif reason == "missing_circuit":
                code = "usage_authority_sensor_missing_circuit"
                title = "Sensor circuit assignment is missing"
            elif reason in {"wrong_device_role", "wrong_circuit_role"}:
                code = (
                    "usage_authority_sensor_not_whole_home"
                    if change.mode == "whole_account_meter"
                    else "usage_authority_sensor_wrong_role"
                )
                title = "Sensor measurement role is not eligible"
            else:
                code = "usage_authority_topology_overlap"
                title = "Sensor topology is not eligible"
            raise ProblemError(
                422,
                title,
                f"{device.name}: {' '.join(_messages(reasons))}",
                code,
                extra={
                    "device_id": device.id,
                    "eligibility_codes": list(reasons),
                    "eligibility_messages": _messages(reasons),
                },
            )
    if change.mode == "service_leg_pair":
        pair_reason = _pair_topology_reason(
            [circuits_by_id[item.circuit_id] for item in selected if item.circuit_id]
        )
        if pair_reason:
            code = (
                "usage_authority_sensor_duplicate_circuit"
                if pair_reason == "duplicate_circuit"
                else "usage_authority_topology_overlap"
            )
            raise ProblemError(
                422,
                "Service-leg topology is invalid",
                "Choose two distinct, non-overlapping circuits in the same reviewed "
                "split-phase group",
                code,
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
