from __future__ import annotations

import asyncio
import os
import socket
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text

from app.config import get_settings
from app.db.models import WorkerState
from app.db.session import session_factory
from app.logging import configure_logging
from worker.app.polling import poll_due_devices
from worker.app.tasks import (
    evaluate_alerts,
    process_cost_jobs,
    process_export_jobs,
    process_notification_jobs,
    process_report_jobs,
    recompute_recent_rollups,
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


async def run_once() -> dict[str, Any]:
    settings = get_settings()
    factory = session_factory()
    async with factory() as session:
        if not await _try_lock(session):
            return {"status": "standby"}
        try:
            alerts = await evaluate_alerts(session, settings)
            notifications = await process_notification_jobs(session, settings)
            exports = await process_export_jobs(session, settings)
            reports = await process_report_jobs(session, settings)
            costs = await process_cost_jobs(session)
            rollups = await recompute_recent_rollups(session)
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
                "cost_runs_completed": costs,
                "rollups": rollups,
                "polled": len(polling),
                "poll_failures": sum(
                    item["status"] not in {"ok", "not_configured", "circuit_open"}
                    for item in polling
                ),
            }
            await session.commit()
            return state.details
        finally:
            await _unlock(session)


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
