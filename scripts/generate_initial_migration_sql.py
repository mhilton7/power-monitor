from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_mock_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import models  # noqa: E402, F401
from app.db.base import Base  # noqa: E402


def render() -> str:
    statements: list[str] = []

    def collect(statement: Any, *_args: Any, **_kwargs: Any) -> None:
        rendered = str(statement.compile(dialect=engine.dialect)).strip()
        if rendered:
            statements.append(rendered)

    engine = create_mock_engine("postgresql://", collect)
    Base.metadata.create_all(engine, checkfirst=False)
    body = ";\n\n".join(statements) + ";\n"
    digest = hashlib.sha256(body.encode()).hexdigest()
    return f"-- Generated immutable initial schema. sha256(body)={digest}\n{body}"


if __name__ == "__main__":
    target = ROOT / "backend" / "alembic" / "versions" / "20260720_0001_schema.sql"
    target.write_text(render(), encoding="utf-8", newline="\n")
