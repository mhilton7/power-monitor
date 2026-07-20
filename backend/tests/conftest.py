from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app


@pytest.fixture
def test_settings() -> Settings:
    runtime = Path(__file__).resolve().parents[2] / ".test-runtime"
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        app_master_key=Fernet.generate_key().decode(),
        session_pepper="test-session-pepper-with-at-least-32-bytes",
        bootstrap_secret="test-bootstrap-secret-with-at-least-16",
        public_origin="http://test",
        cookie_secure=False,
        firmware_path=runtime / "firmware",
        report_path=runtime / "reports",
        backup_path=runtime / "backups",
    )


@pytest.fixture
async def session_factory_fixture() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def session(
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory_fixture() as value:
        yield value


@pytest.fixture
async def api_client(
    session_factory_fixture: async_sessionmaker[AsyncSession], test_settings: Settings
) -> AsyncIterator[object]:
    import httpx

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory_fixture() as value:
            yield value

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()
