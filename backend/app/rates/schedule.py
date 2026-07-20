from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def schedule_parts(cron: str) -> tuple[int, int, int]:
    parts = cron.split()
    if len(parts) != 5 or parts[2:4] != ["*", "*"]:
        raise ValueError("Rate schedule must use minute hour * * weekday format")
    try:
        minute, hour, weekday = (int(parts[0]), int(parts[1]), int(parts[4]))
    except ValueError as exc:
        raise ValueError("Rate schedule values must be integers") from exc
    if not 0 <= minute <= 59 or not 0 <= hour <= 23 or not 0 <= weekday <= 6:
        raise ValueError("Rate schedule values are out of range")
    return minute, hour, weekday


def latest_scheduled_time(now: datetime, cron: str, timezone: str) -> datetime:
    minute, hour, cron_weekday = schedule_parts(cron)
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Rate schedule timezone must be a valid IANA timezone") from exc
    local_now = now.astimezone(zone)
    # Cron Sunday is 0; Python Monday is 0.
    python_weekday = (cron_weekday + 6) % 7
    days_back = (local_now.weekday() - python_weekday) % 7
    scheduled_date = local_now.date() - timedelta(days=days_back)
    local = datetime.combine(scheduled_date, time(hour, minute), tzinfo=zone)
    if local > local_now:
        local -= timedelta(days=7)
    return local.astimezone(UTC)


def next_scheduled_time(now: datetime, cron: str, timezone: str) -> datetime:
    latest = latest_scheduled_time(now, cron, timezone)
    if latest <= now.astimezone(UTC):
        return latest + timedelta(days=7)
    return latest
