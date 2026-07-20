from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 on a host with Docker",
)
@pytest.mark.asyncio
async def test_postgres_17_migrates_from_empty_database() -> None:
    backend = Path(__file__).resolve().parents[1]
    with PostgresContainer("postgres:17.5-alpine") as postgres:
        url = postgres.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+asyncpg://"
        )
        environment = {**os.environ, "DATABASE_URL": url}
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "head",
            cwd=backend,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        assert process.returncode == 0, (stdout + stderr).decode()
        connection = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            revision = await connection.fetchval("SELECT version_num FROM alembic_version")
            table_count = await connection.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
            )
            assert revision == "20260720_0001"
            assert table_count == 53
        finally:
            await connection.close()
