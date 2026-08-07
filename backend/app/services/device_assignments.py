from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AccountUsageAuthority,
    AggregateMember,
    AggregateSet,
    BillingCycle,
    Device,
    DeviceSiteAssignment,
)


@dataclass(frozen=True)
class ClearedDeviceAssignments:
    """Live topology and billing relationships removed from a sensor."""

    circuit_id: str | None
    utility_account_id: str | None
    cost_scope: str
    included_in_default_site_total: bool
    aggregate_member_ids: tuple[str, ...]
    usage_authority_ids: tuple[str, ...]
    affected_utility_account_ids: tuple[str, ...]
    invalidated_billing_cycle_ids: tuple[str, ...]
    closed_site_assignment_ids: tuple[str, ...]

    def audit_details(self) -> dict[str, object]:
        return {
            "circuit_id": self.circuit_id,
            "utility_account_id": self.utility_account_id,
            "cost_scope": self.cost_scope,
            "included_in_default_site_total": self.included_in_default_site_total,
            "removed_direct_aggregate_member_ids": list(self.aggregate_member_ids),
            "cleared_usage_authority_ids": list(self.usage_authority_ids),
            "affected_utility_account_ids": list(self.affected_utility_account_ids),
            "invalidated_billing_cycle_ids": list(self.invalidated_billing_cycle_ids),
            "closed_site_assignment_ids": list(self.closed_site_assignment_ids),
        }


def _assignment_end(effective_at: datetime, effective_from: datetime) -> datetime:
    """Return a constraint-safe end instant without changing the requested wall time."""

    candidate = effective_at
    if effective_from.tzinfo is None and candidate.tzinfo is not None:
        candidate = candidate.replace(tzinfo=None)
    elif effective_from.tzinfo is not None and candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=effective_from.tzinfo)
    if candidate <= effective_from:
        return effective_from + timedelta(microseconds=1)
    return candidate


async def clear_device_assignment_relationships(
    session: AsyncSession,
    *,
    device: Device,
    effective_at: datetime,
    close_site_assignment: bool,
    updated_by: str | None,
) -> ClearedDeviceAssignments:
    """Sever current topology/billing links while retaining readings and audit history."""

    aggregate_members = list(
        await session.scalars(select(AggregateMember).where(AggregateMember.device_id == device.id))
    )
    aggregate_member_ids = tuple(item.id for item in aggregate_members)
    aggregate_set_ids = {item.aggregate_set_id for item in aggregate_members}
    aggregate_sets = (
        list(
            await session.scalars(
                select(AggregateSet).where(AggregateSet.id.in_(aggregate_set_ids))
            )
        )
        if aggregate_set_ids
        else []
    )
    if aggregate_member_ids:
        await session.execute(
            delete(AggregateMember).where(AggregateMember.id.in_(aggregate_member_ids))
        )

    affected_utility_account_ids = {
        item.utility_account_id for item in aggregate_sets if item.utility_account_id is not None
    }
    if device.utility_account_id is not None:
        affected_utility_account_ids.add(device.utility_account_id)
    usage_authority_ids: list[str] = []
    for authority in await session.scalars(select(AccountUsageAuthority)):
        directly_referenced = device.id in authority.device_ids
        aggregate_referenced = authority.aggregate_set_id in aggregate_set_ids
        if not directly_referenced and not aggregate_referenced:
            continue
        if directly_referenced:
            authority.device_ids = [
                device_id for device_id in authority.device_ids if device_id != device.id
            ]
        authority.complete_account = False
        authority.confidence = "unverified"
        authority.revision += 1
        authority.updated_by = updated_by
        authority.updated_at = effective_at
        usage_authority_ids.append(authority.id)
        affected_utility_account_ids.add(authority.utility_account_id)

    invalidated_billing_cycle_ids: list[str] = []
    if affected_utility_account_ids:
        cycles = list(
            await session.scalars(
                select(BillingCycle).where(
                    BillingCycle.utility_account_id.in_(affected_utility_account_ids),
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
            cycle.updated_by = updated_by
            cycle.updated_at = effective_at
        invalidated_billing_cycle_ids = [item.id for item in cycles]

    closed_site_assignment_ids: tuple[str, ...] = ()
    if close_site_assignment:
        open_assignments = list(
            await session.scalars(
                select(DeviceSiteAssignment).where(
                    DeviceSiteAssignment.device_id == device.id,
                    DeviceSiteAssignment.effective_to.is_(None),
                )
            )
        )
        for assignment in open_assignments:
            assignment.effective_to = _assignment_end(effective_at, assignment.effective_from)
        closed_site_assignment_ids = tuple(item.id for item in open_assignments)

    cleared = ClearedDeviceAssignments(
        circuit_id=device.circuit_id,
        utility_account_id=device.utility_account_id,
        cost_scope=device.cost_scope,
        included_in_default_site_total=device.include_in_default_site_total,
        aggregate_member_ids=aggregate_member_ids,
        usage_authority_ids=tuple(usage_authority_ids),
        affected_utility_account_ids=tuple(sorted(affected_utility_account_ids)),
        invalidated_billing_cycle_ids=tuple(invalidated_billing_cycle_ids),
        closed_site_assignment_ids=closed_site_assignment_ids,
    )
    device.circuit_id = None
    device.utility_account_id = None
    device.cost_scope = "energy_only"
    device.include_in_default_site_total = False
    return cleared
