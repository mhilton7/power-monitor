from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RateAssignment, RatePlan, RateVersion, UtilityAccount
from app.problem import ProblemError


def _scope_changed(
    *,
    plan_ids: Iterable[str] = (),
    locked_site_ids: Iterable[str] = (),
    current_site_ids: Iterable[str] = (),
    missing_plan_ids: Iterable[str] = (),
) -> ProblemError:
    return ProblemError(
        409,
        "Rate dependency scope changed",
        "Pricing dependencies changed while reset-safe locks were being acquired. "
        "Reload and retry the operation.",
        "rate_plan_dependency_scope_changed",
        extra={
            "retryable": True,
            "rate_plan_ids": sorted(set(plan_ids)),
            "locked_site_ids": sorted(set(locked_site_ids)),
            "current_site_ids": sorted(set(current_site_ids)),
            "missing_rate_plan_ids": sorted(set(missing_plan_ids)),
        },
    )


async def rate_plan_site_ids(session: AsyncSession, plan: RatePlan) -> list[str]:
    """Return the dependency-closed set of sites affected by a rate plan."""

    site_ids: set[str] = set()
    if plan.owner_site_id:
        site_ids.add(plan.owner_site_id)
    if plan.owner_utility_account_id:
        owner_site_id = await session.scalar(
            select(UtilityAccount.site_id).where(UtilityAccount.id == plan.owner_utility_account_id)
        )
        if owner_site_id:
            site_ids.add(owner_site_id)

    version_ids = select(RateVersion.id).where(RateVersion.rate_plan_id == plan.id)
    site_ids.update(
        await session.scalars(
            select(UtilityAccount.site_id).where(
                UtilityAccount.active_rate_version_id.in_(version_ids)
            )
        )
    )
    site_ids.update(
        await session.scalars(
            select(UtilityAccount.site_id)
            .join(
                RateAssignment,
                RateAssignment.utility_account_id == UtilityAccount.id,
            )
            .where(RateAssignment.rate_version_id.in_(version_ids))
        )
    )
    return sorted(site_ids)


async def rate_owner_site_ids(
    session: AsyncSession,
    *,
    ownership_scope: str,
    owner_id: str | None,
) -> list[str]:
    if ownership_scope == "site" and owner_id:
        return [owner_id]
    if ownership_scope == "utility_account" and owner_id:
        site_id = await session.scalar(
            select(UtilityAccount.site_id).where(UtilityAccount.id == owner_id)
        )
        return [site_id] if site_id else []
    return []


async def lock_rate_plans(
    session: AsyncSession,
    plan_ids: Iterable[str],
) -> dict[str, RatePlan]:
    """Lock existing plans in deterministic order after the caller holds Site locks."""

    ordered_ids = sorted(set(plan_ids))
    if not ordered_ids:
        return {}
    plans = list(
        await session.scalars(
            select(RatePlan)
            .where(RatePlan.id.in_(ordered_ids))
            .order_by(RatePlan.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    by_id = {plan.id: plan for plan in plans}
    missing = set(ordered_ids) - set(by_id)
    if missing:
        raise _scope_changed(plan_ids=ordered_ids, missing_plan_ids=missing)
    return by_id


async def ensure_rate_plans_reset_mutations_allowed(
    session: AsyncSession,
    plans: Iterable[RatePlan],
    *,
    extra_site_ids: Iterable[str] = (),
) -> dict[str, RatePlan]:
    """Lock dependency Sites, then plans, and prove the held scope stayed closed.

    A plan can gain an account dependency while the first closure is being read.
    We therefore recompute after the deterministic RatePlan locks are held.  If a
    committed dependency expanded beyond the already-held Site rows, callers must
    retry from a fresh snapshot; acquiring another Site at that point would invert
    the global Site -> RatePlan lock order.
    """

    plan_by_id = {plan.id: plan for plan in plans}
    held_site_ids = set(extra_site_ids)
    for plan in plan_by_id.values():
        held_site_ids.update(await rate_plan_site_ids(session, plan))

    # Local import avoids a module cycle: data_reset.service uses rates.service,
    # while rates.service also participates in this shared locking protocol.
    from app.data_reset.service import ensure_site_reset_mutations_allowed

    await ensure_site_reset_mutations_allowed(session, sorted(held_site_ids))
    locked = await lock_rate_plans(session, plan_by_id)

    current_site_ids: set[str] = set(extra_site_ids)
    for plan in locked.values():
        current_site_ids.update(await rate_plan_site_ids(session, plan))
    if not current_site_ids.issubset(held_site_ids):
        raise _scope_changed(
            plan_ids=locked,
            locked_site_ids=held_site_ids,
            current_site_ids=current_site_ids,
        )
    return locked


async def rate_plan_ids_for_versions(
    session: AsyncSession,
    version_ids: Iterable[str],
) -> set[str]:
    ordered_ids = sorted(set(version_ids))
    if not ordered_ids:
        return set()
    return set(
        await session.scalars(
            select(RateVersion.rate_plan_id).where(RateVersion.id.in_(ordered_ids))
        )
    )


async def account_rate_plan_ids(session: AsyncSession, account_id: str) -> set[str]:
    version_ids = set(
        await session.scalars(
            select(RateAssignment.rate_version_id).where(
                RateAssignment.utility_account_id == account_id
            )
        )
    )
    active_version_id = await session.scalar(
        select(UtilityAccount.active_rate_version_id).where(UtilityAccount.id == account_id)
    )
    if active_version_id:
        version_ids.add(active_version_id)
    return await rate_plan_ids_for_versions(session, version_ids)


async def ensure_account_rate_mutations_allowed(
    session: AsyncSession,
    account_id: str,
    *,
    extra_version_ids: Iterable[str] = (),
    extra_plan_ids: Iterable[str] = (),
) -> tuple[UtilityAccount, dict[str, RatePlan]]:
    """Acquire Site -> affected RatePlan -> UtilityAccount locks for assignments."""

    site_id = await session.scalar(
        select(UtilityAccount.site_id).where(UtilityAccount.id == account_id)
    )
    if site_id is None:
        raise ProblemError(
            404,
            "Electric service not found",
            "The electric service does not exist",
            "not_found",
        )

    from app.data_reset.service import ensure_site_reset_mutations_allowed

    await ensure_site_reset_mutations_allowed(session, [site_id])
    current_site_id = await session.scalar(
        select(UtilityAccount.site_id).where(UtilityAccount.id == account_id)
    )
    if current_site_id != site_id:
        raise _scope_changed(
            locked_site_ids=[site_id],
            current_site_ids=[current_site_id] if current_site_id else [],
        )

    plan_ids = await account_rate_plan_ids(session, account_id)
    plan_ids.update(await rate_plan_ids_for_versions(session, extra_version_ids))
    plan_ids.update(extra_plan_ids)
    plans = await lock_rate_plans(session, plan_ids)
    account = await session.scalar(
        select(UtilityAccount)
        .where(UtilityAccount.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None or account.site_id != site_id:
        raise _scope_changed(
            plan_ids=plan_ids,
            locked_site_ids=[site_id],
            current_site_ids=[account.site_id] if account is not None else [],
        )
    return account, plans
