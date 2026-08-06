from __future__ import annotations

import asyncio
import os
import socket
from datetime import UTC, datetime
from typing import Any

import structlog
from app.bills.service import due_retention_deletions
from app.config import get_settings
from app.db.models import WorkerState
from app.db.session import session_factory
from app.firmware_lifecycle import reconcile_stale_firmware_deployments
from app.logging import configure_logging
from sqlalchemy import text

from worker.app.data_reset import process_data_reset_operations
from worker.app.polling import poll_due_devices
from worker.app.rate_sync import (
    activate_due_versions,
    check_stale_sources,
    process_rate_sync_jobs,
)
from worker.app.tasks import (
    evaluate_alerts,
    process_cost_jobs,
    process_export_jobs,
    process_notification_jobs,
    process_report_jobs,
    process_tier_recalculations,
    recompute_recent_rollups,
    reconcile_missing_normalized_intervals,
)

LOCK_ID = 73473281


async def _try_lock(session: Any) -> bool:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return True
    return bool(await session.scalar(text(f"SELECT pg_try_advisory_lock({LOCK_ID})")))


async def _unlock(session: Any) -> None:
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        await session.execute(text(f"SELECT pg_advisory_unlock({LOCK_ID})"))


async def _process_work(session: Any, factory: Any, settings: Any) -> dict[str, Any]:
    firmware_reconciliation = await reconcile_stale_firmware_deployments(
        session, settings
    )
    # Reconciliation owns row locks on active deployments. Persist and release
    # them before unrelated normalization, notification, or network polling
    # work so a later worker failure cannot roll back an OTA terminal outcome
    # or block an authenticated sensor report for the duration of the loop.
    await session.commit()
    data_reset = await process_data_reset_operations(session, settings)
    history_normalization = await reconcile_missing_normalized_intervals(session)
    alerts = await evaluate_alerts(session, settings)
    notifications = await process_notification_jobs(session, settings)
    exports = await process_export_jobs(session, settings)
    reports = await process_report_jobs(session, settings)
    tier_recalculations = await process_tier_recalculations(session)
    costs = await process_cost_jobs(session)
    rollups = await recompute_recent_rollups(session)
    rate_sync = await process_rate_sync_jobs(session, settings)
    rates_activated = await activate_due_versions(session)
    stale_rate_sources = await check_stale_sources(session)
    # Release all earlier task locks before retention acquires deterministic
    # per-site reset barriers. Release those barriers again before network
    # polling so an active reset is never delayed by device I/O.
    await session.commit()
    utility_bill_retention_deletions = await due_retention_deletions(session)
    await session.commit()
    polling = await poll_due_devices(factory, settings)
    now = datetime.now(UTC)
    state = await session.get(WorkerState, "main")
    if state is None:
        state = WorkerState(
            worker_name="main",
            instance_id=f"{socket.gethostname()}:{os.getpid()}",
            last_loop_at=now,
            last_success_at=now,
            status="healthy",
            details={},
        )
        session.add(state)
    state.last_loop_at = now
    state.last_success_at = now
    state.status = "healthy"
    state.details = {
        "alerts": alerts,
        "notifications": notifications,
        "exports_completed": exports,
        "reports_completed": reports,
        "history_normalization": history_normalization,
        "data_reset": data_reset,
        "firmware_reconciliation": firmware_reconciliation,
        "cost_runs_completed": costs,
        "tier_recalculations_completed": tier_recalculations,
        "rollups": rollups,
        "rate_sync": rate_sync,
        "rates_activated": rates_activated,
        "stale_rate_sources": stale_rate_sources,
        "utility_bill_retention_deletions": utility_bill_retention_deletions,
        "polled": len(polling),
        "poll_failures": sum(
            item["status"] not in {"ok", "not_configured", "circuit_open"}
            for item in polling
        ),
    }
    await session.commit()
    return state.details


async def run_once() -> dict[str, Any]:
    settings = get_settings()
    factory = session_factory()
    # A PostgreSQL session-level advisory lock belongs to the physical
    # connection that acquired it. Work tasks commit independently, so keep a
    # dedicated lock session checked out until the complete loop is finished.
    async with factory() as lock_session:
        if not await _try_lock(lock_session):
            return {"status": "standby"}
        try:
            async with factory() as work_session:
                return await _process_work(work_session, factory, settings)
        finally:
            await _unlock(lock_session)


async def main() -> None:
    settings = get_settings()
    configure_logging(
        settings.log_level,
        json_logs=True,
        log_path=settings.log_path,
        service="worker",
        retention_days=settings.log_retention_days,
    )
    logger = structlog.get_logger()
    while True:
        try:
            result = await run_once()
            logger.info("worker_loop", **result)
        except Exception as exc:
            logger.exception("worker_loop_failed", error_type=type(exc).__name__)
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
