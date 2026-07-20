from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    AlertRule,
    BaselineRule,
    FixedChargeRule,
    RateDayType,
    RatePeriod,
    RatePlan,
    RateSeason,
    RateSource,
    RateSyncConfiguration,
    RateVersion,
    Role,
    Site,
    Utility,
)
from app.rates.engine import load_seed_plans
from app.rates.sources import APPROVED_SOURCE_URLS

SOURCE_PARSERS = {
    (
        "https://www.sce.com/save-money/rates-financing/residential-rate-plans/time-of-use-plans"
    ): "sce_public_tou_html_v1",
    "https://www.sce.com/save-money/rates-financing/sce-rate-advisory": "sce_rate_advisory_html_v1",
    (
        "https://www.sce.com/regulatory/regulatory-information/tariff-books/rates-pricing-choices"
    ): "sce_tariff_index_html_v1",
    "https://www.sce.com/regulatory/tariff-books/historical-rates": "sce_tariff_index_html_v1",
}
SOURCE_EFFECTIVE_HINTS = {
    "https://www.sce.com/save-money/rates-financing/residential-rate-plans/time-of-use-plans": date(
        2026, 6, 1
    )
}

DEFAULT_ALERTS: tuple[tuple[str, str, str, int], ...] = (
    ("Heartbeat stale", "heartbeat_stale", "critical", 30),
    ("Device API unreachable", "api_unreachable", "warning", 60),
    ("Authentication failure", "authentication_failure", "critical", 0),
    ("Protocol incompatibility", "protocol_incompatible", "critical", 0),
    ("PZEM unhealthy", "pzem_failure", "critical", 30),
    ("No valid reading", "reading_stale", "critical", 120),
    ("SD storage unhealthy", "sd_failure", "critical", 15),
    ("Synchronization backlog", "sync_backlog", "warning", 300),
    ("Missing sequence", "sequence_gap", "warning", 0),
    ("Time unsynchronized", "time_untrusted", "warning", 60),
    ("Low Wi-Fi signal", "low_rssi", "warning", 300),
    ("CT utilization 80%", "ct_limit_80", "warning", 60),
    ("CT utilization 90%", "ct_limit_90", "critical", 15),
    ("Voltage out of range", "voltage_range", "warning", 30),
    ("Frequency out of range", "frequency_range", "warning", 30),
    ("Unexpected reboot loop", "reboot_loop", "critical", 0),
    ("Firmware deployment failed", "firmware_failed", "critical", 0),
    ("Server worker unhealthy", "worker_failure", "critical", 60),
    ("Backup verification failed", "backup_failure", "critical", 0),
    ("SCE source check succeeded", "rate_check_succeeded", "info", 0),
    ("Official rate source changed", "rate_source_changed", "warning", 0),
    ("Rate candidate awaiting review", "rate_candidate_pending", "warning", 0),
    ("Rate candidate validation failed", "rate_candidate_validation_failed", "critical", 0),
    ("SCE rate source unavailable", "rate_source_unavailable", "warning", 0),
    ("SCE rate parser failed", "rate_parser_failed", "critical", 0),
    ("SCE source conflict detected", "rate_source_conflict", "critical", 0),
    ("Rate candidate approved", "rate_candidate_approved", "info", 0),
    ("Rate candidate rejected", "rate_candidate_rejected", "warning", 0),
    ("Rate version activated", "rate_version_activated", "info", 0),
    ("Rate version automatically activated", "rate_version_auto_activated", "warning", 0),
    ("Retroactive rate activated", "rate_retroactive_activated", "warning", 0),
    ("Rate estimates recalculated", "rate_estimates_recalculated", "info", 0),
    ("SCE source check is stale", "rate_source_stale", "warning", 0),
)


async def ensure_roles(session: AsyncSession) -> None:
    descriptions = {
        "admin": "Full system and security administration",
        "operator": "Devices, rates, alerts, firmware, and reports",
        "rate-manager": "Create, review, approve, and assign rate plans",
        "viewer": "Read-only dashboard and permitted exports",
    }
    for name, description in descriptions.items():
        if await session.get(Role, name) is None:
            session.add(Role(name=name, description=description))


async def ensure_default_reference_data(
    session: AsyncSession, site_name: str, settings: Settings | None = None
) -> Site:
    await ensure_roles(session)
    utility = await session.scalar(
        select(Utility).where(Utility.name == "Southern California Edison")
    )
    if utility is None:
        utility = Utility(
            name="Southern California Edison",
            website="https://www.sce.com/",
        )
        session.add(utility)
        await session.flush()
    now = datetime.now(UTC)
    sync_config = await session.get(RateSyncConfiguration, "default")
    if sync_config is None:
        session.add(
            RateSyncConfiguration(
                id="default",
                enabled=settings.rate_sync_enabled if settings else True,
                schedule_cron=settings.rate_sync_cron if settings else "15 3 * * 0",
                timezone=(settings.rate_sync_timezone if settings else "America/Los_Angeles"),
                jitter_minutes=settings.rate_sync_jitter_minutes if settings else 20,
                approval_mode=settings.rate_sync_policy if settings else "manual_review",
                auto_activate_verified=(
                    settings.rate_sync_policy == "auto_activate_verified" if settings else False
                ),
                updated_at=now,
            )
        )
    elif settings and sync_config.updated_by is None and sync_config.last_attempted_run is None:
        sync_config.enabled = settings.rate_sync_enabled
        sync_config.schedule_cron = settings.rate_sync_cron
        sync_config.timezone = settings.rate_sync_timezone
        sync_config.jitter_minutes = settings.rate_sync_jitter_minutes
        sync_config.approval_mode = settings.rate_sync_policy
        sync_config.auto_activate_verified = settings.rate_sync_policy == "auto_activate_verified"
    for url in sorted(APPROVED_SOURCE_URLS):
        if await session.scalar(select(RateSource.id).where(RateSource.url == url)) is None:
            session.add(
                RateSource(
                    name=("SCE " + url.rsplit("/", 1)[-1].replace("-", " ").title()),
                    url=url,
                    parser_id=SOURCE_PARSERS[url],
                    effective_from_hint=SOURCE_EFFECTIVE_HINTS.get(url),
                    enabled=True,
                    consecutive_failures=0,
                    created_at=now,
                    updated_at=now,
                )
            )
    site = await session.scalar(select(Site).where(Site.name == site_name))
    if site is None:
        site = Site(name=site_name, timezone="America/Los_Angeles")
        session.add(site)
        await session.flush()
    existing_plan = await session.scalar(select(RatePlan.id).limit(1))
    if existing_plan is None:
        for plan_data in load_seed_plans().values():
            plan = RatePlan(
                utility_id=utility.id,
                code=plan_data["code"],
                name=plan_data["name"],
                description=plan_data.get("eligibility") or "SCE residential time-of-use preset",
                plan_kind="official_sce",
                ownership_scope="global",
                currency=plan_data["currency"],
                timezone=plan_data["timezone"],
                status="active",
            )
            session.add(plan)
            await session.flush()
            canonical = json.dumps(plan_data, sort_keys=True, separators=(",", ":")).encode()
            version = RateVersion(
                rate_plan_id=plan.id,
                version=int(plan_data["version"]),
                effective_from=datetime.fromisoformat(plan_data["effective_from"]).date(),
                effective_to=(
                    datetime.fromisoformat(plan_data["effective_to"]).date()
                    if plan_data.get("effective_to")
                    else None
                ),
                timezone=plan_data["timezone"],
                currency=plan_data["currency"],
                source_url="https://www.sce.com/save-money/rates-financing/residential-rate-plans/time-of-use-plans",
                source_checked_on=datetime(2026, 7, 20).date(),
                source_notes=(
                    "Editable seed; user verification required. Estimate, not utility bill."
                ),
                content_hash=hashlib.sha256(canonical).hexdigest(),
                is_active=True,
                status="active",
                source_kind="official_sce",
                source_checked_at=datetime(2026, 7, 20, tzinfo=UTC),
                source_label="SCE public residential TOU page",
                immutable_after_use=True,
                created_at=now,
            )
            session.add(version)
            await session.flush()
            for name, values in plan_data["seasons"].items():
                start_month, start_day = (int(part) for part in values["start"].split("-"))
                end_month, end_day = (int(part) for part in values["end"].split("-"))
                session.add(
                    RateSeason(
                        rate_version_id=version.id,
                        name=name,
                        start_month=start_month,
                        start_day=start_day,
                        end_month=end_month,
                        end_day=end_day,
                    )
                )
            for day_type, weekdays in (("weekday", [0, 1, 2, 3, 4]), ("weekend", [5, 6])):
                session.add(
                    RateDayType(
                        rate_version_id=version.id,
                        name=day_type,
                        weekdays=weekdays,
                        holiday_behavior=plan_data["holiday_behavior"],
                        holiday_source=plan_data["holiday_source"],
                    )
                )
            for season, day_types in plan_data["periods"].items():
                for day_type, periods in day_types.items():
                    for start, end, bucket, price in periods:
                        session.add(
                            RatePeriod(
                                rate_version_id=version.id,
                                season_name=season,
                                day_type=day_type,
                                start_minute=int(start),
                                end_minute=int(end),
                                bucket=bucket,
                                price_per_kwh=Decimal(price),
                            )
                        )
            session.add(
                FixedChargeRule(
                    rate_version_id=version.id,
                    name="Base service charge",
                    amount_per_day=Decimal(plan_data["base_service_charge_per_day"]),
                )
            )
            if plan_data.get("baseline_credit_per_kwh"):
                session.add(
                    BaselineRule(
                        rate_version_id=version.id,
                        credit_per_kwh=Decimal(plan_data["baseline_credit_per_kwh"]),
                    )
                )
    existing_alert = await session.scalar(select(AlertRule.id).limit(1))
    if existing_alert is None:
        for name, rule_type, severity, debounce in DEFAULT_ALERTS:
            session.add(
                AlertRule(
                    name=name,
                    rule_type=rule_type,
                    severity=severity,
                    debounce_seconds=debounce,
                    resolve_seconds=debounce,
                    configuration={},
                )
            )
    await session.flush()
    return site
