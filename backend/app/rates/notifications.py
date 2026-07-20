from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlertInstance, AlertRule


async def emit_rate_alert(
    session: AsyncSession,
    rule_type: str,
    evidence: dict[str, Any],
    *,
    dedupe_key: str | None = None,
) -> AlertInstance | None:
    rule = await session.scalar(
        select(AlertRule).where(
            AlertRule.rule_type == rule_type,
            AlertRule.enabled.is_(True),
            AlertRule.device_id.is_(None),
            AlertRule.site_id.is_(None),
        )
    )
    if rule is None:
        return None
    if dedupe_key:
        existing = await session.scalar(
            select(AlertInstance).where(
                AlertInstance.rule_id == rule.id,
                AlertInstance.status.in_(["active", "acknowledged"]),
            )
        )
        if existing and existing.evidence.get("dedupe_key") == dedupe_key:
            existing.evidence = {**evidence, "dedupe_key": dedupe_key}
            return existing
    instance = AlertInstance(
        rule_id=rule.id,
        device_id=None,
        site_id=None,
        status="active",
        severity=rule.severity,
        opened_at=datetime.now(UTC),
        evidence={**evidence, **({"dedupe_key": dedupe_key} if dedupe_key else {})},
    )
    session.add(instance)
    return instance
