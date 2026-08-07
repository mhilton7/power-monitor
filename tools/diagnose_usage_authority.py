#!/usr/bin/env python3
"""Print the authoritative sensor billing eligibility plan without mutating data."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.models import AccountUsageAuthority, UtilityAccount
from app.db.session import dispose_engine, session_factory
from app.usage_authority import authority_reconciliation_plan


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only account usage-authority diagnosis"
    )
    parser.add_argument("--account", required=True, help="utility-account UUID")
    return parser.parse_args()


async def diagnose(account_id: str) -> dict[str, Any]:
    try:
        async with session_factory()() as session:
            account = await session.get(UtilityAccount, account_id)
            if account is None:
                raise SystemExit("utility account was not found")
            authority = await session.scalar(
                select(AccountUsageAuthority).where(
                    AccountUsageAuthority.utility_account_id == account.id
                )
            )
            plan = await authority_reconciliation_plan(session, account, authority)
            await session.rollback()
            return {
                "mode": "dry-run",
                "account": {
                    "id": account.id,
                    "site_id": account.site_id,
                    "status": account.status,
                },
                "stored_authority_ids": plan["device_ids"],
                "valid_authority_ids": plan["valid_device_ids"],
                "stale_or_invalid_authority": plan["invalid_devices"],
                "eligible_whole_home_sensors": plan["eligible_whole_account_sensors"],
                "eligible_service_leg_sensors": plan["eligible_service_leg_sensors"],
                "sensors": plan["sensors"],
                "stored_authority_healthy": plan["stored_authority_healthy"],
                "recommended_correction": plan["recommended_repair"],
            }
    finally:
        await dispose_engine()


def main() -> None:
    args = arguments()
    print(json.dumps(asyncio.run(diagnose(args.account)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
