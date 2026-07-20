from __future__ import annotations

import re
from pathlib import Path

from app.db import models  # noqa: F401
from app.db.base import Base


def test_initial_migration_is_frozen_and_covers_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = (root / "alembic" / "versions" / "20260720_0001_initial.py").read_text()
    schema = (root / "alembic" / "versions" / "20260720_0001_schema.sql").read_text()
    assert "Base.metadata" not in revision
    migrated_tables = set(re.findall(r"CREATE TABLE ([a-z_]+)", schema))
    assert migrated_tables == set(Base.metadata.tables)
    assert "CREATE UNIQUE INDEX" in schema
    assert "ix_raw_site_time" in schema
    assert "TIMESTAMP WITH TIME ZONE" in schema
