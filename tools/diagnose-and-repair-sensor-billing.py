#!/usr/bin/env python3
"""Diagnose sensor/billing authority and apply an explicitly approved repair.

Dry-run is the default. The tool uses the same topology and reconciliation
service as the HTTP API and never prints credentials or signed request data.
"""

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

from app.db.models import (  # noqa: E402
    AccountUsageAuthority,
    DeviceHeartbeat,
    RawReading,
    UtilityAccount,
)
from app.db.session import dispose_engine, session_factory  # noqa: E402
from app.usage_authority import (  # noqa: E402
    AuthorityApplyRequest,
    apply_sensor_usage_authority,
    authority_reconciliation_plan,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose and reconcile sensor billing authority"
    )
    parser.add_argument("--account-id", help="limit diagnosis to one utility account")
    parser.add_argument("--apply", action="store_true", help="apply the reviewed plan")
    parser.add_argument(
        "--device-id", action="append", default=[], help="exact selected UUID"
    )
    parser.add_argument(
        "--topology-mode", choices=["whole_account_meter", "service_leg_pair"]
    )
    parser.add_argument(
        "--expected-revision",
        type=int,
        help="current authority revision; use 0 when no authority is configured",
    )
    parser.add_argument("--reason")
    parser.add_argument("--confirmation")
    parser.add_argument("--idempotency-key")
    parser.add_argument(
        "--actor-id", help="existing administrator UUID for the audit record"
    )
    return parser.parse_args()


async def sensor_evidence(session: Any, device_id: str) -> dict[str, Any]:
    heartbeat = await session.scalar(
        select(DeviceHeartbeat)
        .where(DeviceHeartbeat.device_id == device_id)
        .order_by(DeviceHeartbeat.received_at.desc())
        .limit(1)
    )
    reading = await session.scalar(
        select(RawReading)
        .where(RawReading.device_id == device_id)
        .order_by(RawReading.interval_end.desc())
        .limit(1)
    )
    return {
        "heartbeat_received_at": heartbeat.received_at.isoformat()
        if heartbeat
        else None,
        "pzem_ok": heartbeat.pzem_ok if heartbeat else None,
        "pzem_status": heartbeat.pzem_status if heartbeat else None,
        "sd_ok": heartbeat.sd_ok if heartbeat else None,
        "sd_status": heartbeat.sd_status if heartbeat else None,
        "latest_measurement_at": heartbeat.device_time.isoformat()
        if heartbeat and heartbeat.device_time
        else None,
        "current_watts": str(heartbeat.current_watts)
        if heartbeat and heartbeat.current_watts is not None
        else None,
        "latest_accepted_reading_at": reading.interval_end.isoformat()
        if reading
        else None,
        "latest_accepted_sequence": reading.sequence if reading else None,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.apply:
        required = {
            "account-id": args.account_id,
            "device-id": args.device_id,
            "topology-mode": args.topology_mode,
            "reason": args.reason,
            "idempotency-key": args.idempotency_key,
        }
        missing = [name for name, value in required.items() if not value]
        if args.expected_revision is None:
            missing.append("expected-revision")
        if missing:
            raise SystemExit(
                "--apply requires " + ", ".join(f"--{name}" for name in missing)
            )
        phrase = (
            "CONFIRM SERVICE LEG PAIR"
            if args.topology_mode == "service_leg_pair"
            else "CONFIRM WHOLE ACCOUNT METER"
        )
        if args.confirmation != phrase:
            raise SystemExit(f"--confirmation must be exactly: {phrase}")

    reports: list[dict[str, Any]] = []
    try:
        async with session_factory()() as session:
            statement = select(UtilityAccount).order_by(UtilityAccount.id)
            if args.account_id:
                statement = statement.where(UtilityAccount.id == args.account_id)
            accounts = list(await session.scalars(statement))
            if args.account_id and not accounts:
                raise SystemExit("utility account was not found")
            for account in accounts:
                authority = await session.scalar(
                    select(AccountUsageAuthority).where(
                        AccountUsageAuthority.utility_account_id == account.id
                    )
                )
                before = await authority_reconciliation_plan(
                    session, account, authority
                )
                for sensor in before["account_assigned_sensors"]:
                    sensor["runtime_evidence"] = await sensor_evidence(
                        session, sensor["id"]
                    )
                    sensor["authority_member"] = sensor["id"] in before["device_ids"]
                    sensor["authority_valid"] = (
                        sensor["id"] in before["valid_device_ids"]
                    )
                report: dict[str, Any] = {"account_id": account.id, "before": before}
                if args.apply:
                    updated, after = await apply_sensor_usage_authority(
                        session,
                        account,
                        AuthorityApplyRequest(
                            mode=args.topology_mode,
                            device_ids=tuple(args.device_id),
                            expected_revision=(
                                None
                                if args.expected_revision == 0
                                else args.expected_revision
                            ),
                            actor_id=args.actor_id,
                            reason=args.reason,
                            idempotency_key=args.idempotency_key,
                        ),
                    )
                    report.update(
                        {"applied": True, "revision": updated.revision, "after": after}
                    )
                reports.append(report)
            if args.apply:
                await session.commit()
            else:
                await session.rollback()
    finally:
        await dispose_engine()
    return {"mode": "apply" if args.apply else "dry-run", "accounts": reports}


def main() -> None:
    print(json.dumps(asyncio.run(run(arguments())), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
