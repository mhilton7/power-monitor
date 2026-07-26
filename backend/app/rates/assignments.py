from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RateAssignment, RatePlan, RateVersion, UtilityAccount
from app.problem import ProblemError
from app.rates.documents import validate_document
from app.rates.service import version_document

ASSIGNABLE_VERSION_STATUSES = frozenset({"published", "active", "approved"})


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def overlaps(
    first_start: datetime,
    first_end: datetime | None,
    second_start: datetime,
    second_end: datetime | None,
) -> bool:
    maximum = datetime.max.replace(tzinfo=UTC)
    return aware(first_start) < (aware(second_end) if second_end else maximum) and aware(
        second_start
    ) < (aware(first_end) if first_end else maximum)


def assignment_state(assignment: RateAssignment, now: datetime | None = None) -> str:
    if assignment.cancelled_at is not None:
        return "cancelled"
    instant = aware(now or datetime.now(UTC))
    start = aware(assignment.effective_from)
    end = aware(assignment.effective_to) if assignment.effective_to else None
    if start > instant:
        return "scheduled"
    if end is None or end > instant:
        return "current"
    return "historical"


async def account_assignments(
    session: AsyncSession, account_id: str, *, include_cancelled: bool = True
) -> list[RateAssignment]:
    statement = (
        select(RateAssignment)
        .where(RateAssignment.utility_account_id == account_id)
        .order_by(RateAssignment.effective_from, RateAssignment.created_at)
    )
    if not include_cancelled:
        statement = statement.where(RateAssignment.cancelled_at.is_(None))
    return list(await session.scalars(statement))


def conflicting_pairs(
    assignments: list[RateAssignment],
) -> list[tuple[RateAssignment, RateAssignment]]:
    active = [item for item in assignments if item.cancelled_at is None]
    conflicts: list[tuple[RateAssignment, RateAssignment]] = []
    for index, item in enumerate(active):
        for other in active[index + 1 :]:
            if overlaps(
                item.effective_from,
                item.effective_to,
                other.effective_from,
                other.effective_to,
            ):
                conflicts.append((item, other))
    return conflicts


async def assignment_conflict_report(
    session: AsyncSession, account_id: str | None = None
) -> list[dict[str, Any]]:
    account_statement = select(UtilityAccount).where(UtilityAccount.status == "active")
    if account_id:
        account_statement = account_statement.where(UtilityAccount.id == account_id)
    accounts = list(await session.scalars(account_statement.order_by(UtilityAccount.created_at)))
    reports: list[dict[str, Any]] = []
    for account in accounts:
        assignments = await account_assignments(session, account.id)
        pairs = conflicting_pairs(assignments)
        if not pairs:
            continue
        conflict_ids = {item.id for pair in pairs for item in pair}
        records: list[dict[str, Any]] = []
        for assignment in assignments:
            if assignment.id not in conflict_ids:
                continue
            version = await session.get(RateVersion, assignment.rate_version_id)
            plan = await session.get(RatePlan, version.rate_plan_id) if version else None
            records.append(
                {
                    "assignment_id": assignment.id,
                    "assignment_revision": assignment.revision,
                    "rate_version_id": assignment.rate_version_id,
                    "plan_id": plan.id if plan else None,
                    "plan_code": plan.code if plan else None,
                    "plan_name": plan.name if plan else None,
                    "version": version.version if version else None,
                    "effective_from": assignment.effective_from,
                    "effective_to": assignment.effective_to,
                    "state": assignment_state(assignment),
                    "history_preserved": True,
                }
            )
        reports.append(
            {
                "utility_account_id": account.id,
                "electric_service": account.nickname or account.name,
                "assignments": records,
                "recommended_action": "Select the assignment that should remain current; "
                "other conflicting rows will be ended or cancelled without deleting history.",
            }
        )
    return reports


async def assign_version(
    session: AsyncSession,
    *,
    account_id: str,
    rate_version_id: str,
    effective_from: datetime,
    effective_to: datetime | None,
    replace_current: bool,
    reason: str | None,
    actor_id: str,
    idempotency_key: str | None = None,
    expected_account_revision: int | None = None,
    expected_current_assignment_revision: int | None = None,
) -> tuple[RateAssignment, list[str], bool]:
    if idempotency_key:
        existing = await session.scalar(
            select(RateAssignment).where(RateAssignment.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing, [], assignment_state(existing) == "current"
    account = await session.scalar(
        select(UtilityAccount).where(UtilityAccount.id == account_id).with_for_update()
    )
    if account is None:
        raise ProblemError(
            404, "Electric service not found", "The electric service does not exist", "not_found"
        )
    if account.status != "active":
        raise ProblemError(
            409,
            "Active electric service required",
            "Restore the electric service before changing its rate plan",
            "utility_account_archived",
        )
    if expected_account_revision is not None and account.revision != expected_account_revision:
        raise ProblemError(
            409,
            "Electric service changed",
            "Reload the electric service before changing its current plan",
            "stale_electric_service",
            extra={
                "blockers": [
                    {
                        "code": "service_revision_changed",
                        "message": "The electric service was updated by another request.",
                        "action": "reload",
                    }
                ],
                "current_service_revision": account.revision,
            },
        )
    version = await session.get(RateVersion, rate_version_id)
    if version is None or version.status not in ASSIGNABLE_VERSION_STATUSES:
        raise ProblemError(
            422,
            "Published rate version required",
            "Publish the selected version before assigning it",
            "rate_version_unavailable",
        )
    plan = await session.get(RatePlan, version.rate_plan_id)
    if (
        plan is None
        or plan.status in {"removed", "retired"}
        or version.status
        in {
            "removed",
            "retired",
        }
    ):
        raise ProblemError(
            409,
            "Rate plan unavailable",
            "Restore the plan and version before assigning it",
            "rate_plan_removed",
        )
    document = await version_document(session, version)
    report = validate_document(document)
    if not report.valid:
        raise ProblemError(
            422,
            "Rate version is incomplete",
            "Resolve every validation error and publish a complete rate version before assignment",
            "rate_version_invalid",
            extra={
                "blockers": [
                    {
                        "code": item.code,
                        "path": item.path,
                        "message": item.message,
                        "action": "review_and_publish",
                    }
                    for item in report.errors
                ]
            },
        )
    start = aware(effective_from)
    end = aware(effective_to) if effective_to else None
    if end is not None and end <= start:
        raise ProblemError(
            422,
            "Invalid assignment dates",
            "Assignment end must follow its start",
            "rate_assignment_invalid",
        )
    assignments = await account_assignments(session, account.id, include_cancelled=False)
    now = datetime.now(UTC)
    current_at_request = next(
        (item for item in assignments if assignment_state(item, now) == "current"),
        None,
    )
    if expected_current_assignment_revision is not None and (
        current_at_request is None
        or current_at_request.revision != expected_current_assignment_revision
    ):
        raise ProblemError(
            409,
            "Current assignment changed",
            "Reload the current plan before replacing it",
            "stale_rate_assignment",
            extra={
                "blockers": [
                    {
                        "code": "assignment_revision_changed",
                        "message": (
                            "The effective assignment changed before this request committed."
                        ),
                        "action": "reload",
                    }
                ],
                "current_assignment_id": current_at_request.id if current_at_request else None,
                "current_assignment_revision": (
                    current_at_request.revision if current_at_request else None
                ),
            },
        )
    existing_conflicts = conflicting_pairs(assignments)
    if existing_conflicts:
        conflict_ids = sorted({item.id for pair in existing_conflicts for item in pair})
        raise ProblemError(
            409,
            "Existing assignment conflict requires repair",
            "Choose the current plan in the assignment repair workflow before adding a schedule",
            "rate_assignment_repair_required",
            extra={
                "utility_account_id": account.id,
                "conflicting_assignment_ids": conflict_ids,
                "allowed_resolution_actions": ["review_conflicts", "select_current"],
            },
        )

    replaced: list[RateAssignment] = []
    next_future_start: datetime | None = None
    for item in assignments:
        if not overlaps(item.effective_from, item.effective_to, start, end):
            continue
        item_start = aware(item.effective_from)
        item_end = aware(item.effective_to) if item.effective_to else None
        covers_start = item_start <= start and (item_end is None or item_end > start)
        if replace_current and covers_start:
            replaced.append(item)
            continue
        if replace_current and end is None and item_start > start:
            next_future_start = (
                item_start if next_future_start is None else min(next_future_start, item_start)
            )
            continue
        raise ProblemError(
            409,
            "Rate assignment overlaps",
            "The selected effective window overlaps an existing current or scheduled plan",
            "rate_assignment_overlap",
            extra={
                "conflicting_assignment_id": item.id,
                "conflicting_rate_version_id": item.rate_version_id,
                "conflicting_effective_from": item.effective_from.isoformat(),
                "conflicting_effective_to": (
                    item.effective_to.isoformat() if item.effective_to else None
                ),
                "allowed_resolution_actions": [
                    "replace_current",
                    "choose_boundary",
                    "cancel_scheduled",
                ],
            },
        )
    if next_future_start is not None:
        end = next_future_start
    for item in replaced:
        if aware(item.effective_from) == start:
            item.cancelled_at = datetime.now(UTC)
            item.cancelled_by = actor_id
            item.cancellation_reason = reason or "Replaced at the same effective instant"
        else:
            item.effective_to = start
        item.revision += 1
    assignment = RateAssignment(
        utility_account_id=account.id,
        rate_version_id=version.id,
        effective_from=start,
        effective_to=end,
        assignment_reason=reason,
        assigned_by=actor_id,
        idempotency_key=idempotency_key,
        created_at=datetime.now(UTC),
    )
    session.add(assignment)
    effective_now = start <= now and (end is None or end > now)
    if effective_now:
        account.active_rate_version_id = version.id
    account.revision += 1
    await session.flush()
    return assignment, [item.id for item in replaced], effective_now


async def end_assignment(
    session: AsyncSession,
    *,
    account_id: str,
    effective_at: datetime,
    reason: str,
    actor_id: str,
) -> RateAssignment:
    account = await session.scalar(
        select(UtilityAccount).where(UtilityAccount.id == account_id).with_for_update()
    )
    if account is None:
        raise ProblemError(404, "Electric service not found", "Unknown service", "not_found")
    instant = aware(effective_at)
    assignments = await account_assignments(session, account.id, include_cancelled=False)
    current = next(
        (
            item
            for item in assignments
            if aware(item.effective_from) <= instant
            and (item.effective_to is None or aware(item.effective_to) > instant)
        ),
        None,
    )
    if current is None:
        raise ProblemError(
            409,
            "No current assignment",
            "There is no plan to end at the selected time",
            "rate_assignment_not_current",
        )
    if aware(current.effective_from) == instant:
        current.cancelled_at = datetime.now(UTC)
        current.cancelled_by = actor_id
        current.cancellation_reason = reason
    else:
        current.effective_to = instant
    current.assignment_reason = reason
    current.revision += 1
    if instant <= datetime.now(UTC):
        account.active_rate_version_id = None
    account.revision += 1
    await session.flush()
    return current


async def resolve_assignment_conflict(
    session: AsyncSession,
    *,
    account_id: str,
    keep_assignment_id: str,
    expected_assignment_ids: list[str],
    reason: str,
    actor_id: str,
) -> dict[str, Any]:
    account = await session.scalar(
        select(UtilityAccount).where(UtilityAccount.id == account_id).with_for_update()
    )
    if account is None:
        raise ProblemError(404, "Electric service not found", "Unknown service", "not_found")
    assignments = await account_assignments(session, account.id)
    conflicts = conflicting_pairs(assignments)
    actual_ids = sorted({item.id for pair in conflicts for item in pair})
    if not actual_ids:
        return {"resolved": True, "idempotent": True, "ended_assignment_ids": []}
    if sorted(expected_assignment_ids) != actual_ids:
        raise ProblemError(
            409,
            "Assignment conflicts changed",
            "Reload the repair report before choosing the current plan",
            "stale_assignment_conflicts",
            extra={"conflicting_assignment_ids": actual_ids},
        )
    keep = next((item for item in assignments if item.id == keep_assignment_id), None)
    if keep is None or keep.id not in actual_ids:
        raise ProblemError(
            422,
            "Invalid current-plan selection",
            "Select one of the conflicting assignments",
            "rate_assignment_resolution_invalid",
        )
    now = datetime.now(UTC)
    ended: list[str] = []
    for item in assignments:
        if item.id == keep.id or item.id not in actual_ids:
            continue
        # Preserve the conflicting row and audit trail, but remove it from rate
        # authority. Truncating a past-started row would retain a historical overlap.
        item.cancelled_at = now
        item.cancelled_by = actor_id
        item.cancellation_reason = reason
        item.revision += 1
        ended.append(item.id)
    remaining = [item for item in assignments if item.id not in ended and item.cancelled_at is None]
    if conflicting_pairs(remaining):
        raise ProblemError(
            409,
            "Assignment repair is incomplete",
            "The selected assignment does not resolve every overlap",
            "rate_assignment_resolution_incomplete",
        )
    if assignment_state(keep, now) == "current":
        account.active_rate_version_id = keep.rate_version_id
    else:
        account.active_rate_version_id = None
    account.revision += 1
    await session.flush()
    return {
        "resolved": True,
        "idempotent": False,
        "kept_assignment_id": keep.id,
        "ended_assignment_ids": ended,
        "history_preserved": True,
    }
