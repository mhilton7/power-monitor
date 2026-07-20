from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import asyncpg

from app.config import get_settings


async def check() -> None:
    database_url = get_settings().database_url.replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    connection = await asyncpg.connect(database_url, timeout=3)
    try:
        last_success = await connection.fetchval(
            "SELECT last_success_at FROM worker_state "
            "WHERE worker_name = 'main' AND status = 'healthy'"
        )
        if last_success is None or last_success < datetime.now(UTC) - timedelta(
            seconds=45
        ):
            raise SystemExit("worker loop is stale")
    finally:
        await connection.close()


asyncio.run(check())
