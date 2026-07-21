#!/usr/bin/env python3
"""Exercise cost-aware multi-sensor History through a deployed HTTPS gateway."""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.schemas import Reading  # noqa: E402
from app.security.protocol import PROTOCOL  # noqa: E402
from simulator.simulated_device.model import SimulatedDevice  # noqa: E402
from simulator.simulated_device.push import push_once  # noqa: E402


class WorkflowFailure(RuntimeError):
    """A deployed History acceptance check failed."""


def require(response: httpx.Response, operation: str) -> httpx.Response:
    if response.is_error:
        detail = response.text[:800].replace("\n", " ")
        raise WorkflowFailure(
            f"{operation} failed with HTTP {response.status_code}: {detail}"
        )
    return response


def flat_rate_document() -> dict[str, Any]:
    return {
        "schema_version": "power-monitor-rate-plan/1.0",
        "plan_name": "History integration one-dollar plan",
        "plan_code": "HISTORY-INTEGRATION-ONE-DOLLAR",
        "utility": "custom",
        "description": "Deterministic deployed History acceptance fixture",
        "currency": "USD",
        "timezone": "America/Los_Angeles",
        "ownership_scope": "global",
        "owner_id": None,
        "effective_from": "2026-01-01",
        "effective_through": None,
        "cost_scope_default": "energy_only",
        "source_label": "Deterministic release gate",
        "source_note": "Generated locally; never fetched from a utility website",
        "provider_mode": "custom_combined",
        "seasons": [
            {
                "name": "all-year",
                "start": "01-01",
                "end": "12-31",
                "priority": 0,
                "leap_day_behavior": "include",
                "schedules": [
                    {
                        "day_type": "all-days",
                        "dates": [],
                        "periods": [
                            {
                                "label": "integration-on-peak",
                                "start_minute": 0,
                                "end_minute": 1440,
                                "price_per_kwh": "1.00000000",
                                "delivery_per_kwh": "0",
                                "generation_per_kwh": "0",
                                "adjustment_per_kwh": "0",
                                "display_order": 0,
                            }
                        ],
                    }
                ],
            }
        ],
        "adjustments": [],
        "custom_notes": "Deployed History integration fixture",
        "cloned_from_rate_version_id": None,
    }


def deterministic_readings(device_index: int) -> list[Reading]:
    return [
        Reading(
            sequence=hour + 1,
            boot_id=str(uuid.UUID(int=device_index * 10 + hour + 1)),
            interval_start=datetime(2026, 7, 21, 3 + hour, tzinfo=UTC),
            interval_end=datetime(2026, 7, 21, 4 + hour, tzinfo=UTC),
            time_trusted=True,
            voltage_avg=Decimal("120"),
            voltage_min=Decimal("119"),
            voltage_max=Decimal("121"),
            current_avg=Decimal("8.333333"),
            power_avg=Decimal("1000"),
            power_max=Decimal("1200"),
            power_factor=Decimal("1"),
            frequency_hz=Decimal("60"),
            interval_energy_wh=Decimal("1000"),
            energy_method="interval",
            ct_rating_amps=Decimal("100"),
            firmware_version="1.0.0",
        )
        for hour in range(2)
    ]


async def exercise(args: argparse.Namespace) -> None:
    setup_token = args.setup_token_file.read_text(encoding="utf-8").strip()
    if not setup_token:
        raise WorkflowFailure("administrator setup token file is empty")
    async with httpx.AsyncClient(
        base_url=args.base_url,
        verify=str(args.ca_certificate),
        timeout=30,
    ) as client:
        bootstrap = require(
            await client.post(
                "/api/v1/auth/bootstrap",
                json={
                    "bootstrap_secret": setup_token,
                    "email": "history-integration@example.com",
                    "display_name": "History Integration Administrator",
                    "password": "History-Integration-Password-47!",
                },
            ),
            "bootstrap administrator",
        )
        if not bootstrap.json().get("authenticated"):
            raise WorkflowFailure("bootstrap did not create an authenticated session")
        csrf = client.cookies.get("pm_csrf")
        if not csrf:
            raise WorkflowFailure("bootstrap did not establish a CSRF cookie")
        headers = {"X-CSRF-Token": csrf}
        sites = require(await client.get("/api/v1/sites"), "list sites").json()
        if len(sites) != 1:
            raise WorkflowFailure("clean deployment did not create one default site")
        site = sites[0]

        account = require(
            await client.post(
                "/api/v1/utility-accounts",
                headers=headers,
                json={
                    "site_id": site["id"],
                    "name": "History integration account",
                    "timezone": "America/Los_Angeles",
                    "currency": "USD",
                },
            ),
            "create utility account",
        ).json()
        circuits: list[dict[str, Any]] = []
        for leg in ("L1", "L2"):
            circuits.append(
                require(
                    await client.post(
                        "/api/v1/circuits",
                        headers=headers,
                        json={
                            "site_id": site["id"],
                            "parent_id": None,
                            "name": f"Integration service leg {leg}",
                            "measurement_role": "service-leg",
                            "split_phase_group": "integration-main-panel",
                        },
                    ),
                    f"create service leg {leg}",
                ).json()
            )

        device_ids: list[str] = []
        for index, circuit in enumerate(circuits):
            enrollment = require(
                await client.post(
                    "/api/v1/enrollment-tokens",
                    headers=headers,
                    json={
                        "site_id": site["id"],
                        "circuit_id": circuit["id"],
                        "name": f"History Sensor {index + 1}",
                        "measurement_role": "service-leg",
                        "connection_mode": "push",
                    },
                ),
                f"create enrollment {index + 1}",
            ).json()
            claim = require(
                await client.post(
                    "/api/v1/device-enrollment/claim",
                    json={
                        "token": enrollment["token"],
                        "protocol_version": PROTOCOL,
                        "hardware_id": f"esp32s3-history-integration-{index:02d}",
                        "capabilities": {
                            "hardware_target": "esp32-s3-pzem004t-v4",
                            "pzem_model": "PZEM-004T V4.0",
                            "sd_present": True,
                            "sd_required": True,
                            "supported_endpoints": ["health", "readings"],
                        },
                    },
                ),
                f"claim sensor {index + 1}",
            ).json()
            simulated = SimulatedDevice(
                index=index,
                device_id=claim["device_id"],
                secret=claim["enrollment_secret"],
                stored=deterministic_readings(index),
            )
            pushed = await push_once(client, simulated)
            if pushed != {"heartbeat": 1, "accepted": 2}:
                raise WorkflowFailure(
                    f"sensor {index + 1} ingestion was incomplete: {pushed}"
                )
            device_ids.append(claim["device_id"])

        created_plan = require(
            await client.post(
                "/api/v1/rates/plans", headers=headers, json=flat_rate_document()
            ),
            "create deterministic rate plan",
        ).json()
        version = created_plan["plan"]["versions"][0]
        require(
            await client.post(
                f"/api/v1/rates/versions/{version['id']}/activate", headers=headers
            ),
            "activate deterministic rate version",
        )
        require(
            await client.post(
                "/api/v1/rates/assignments",
                headers=headers,
                json={
                    "utility_account_id": account["id"],
                    "rate_version_id": version["id"],
                    "effective_from": "2026-01-01T08:00:00+00:00",
                    "provider_mode": "custom_combined",
                    "cost_scope": "energy_only",
                },
            ),
            "assign deterministic rate",
        )

        base_query = {
            "scope": {"type": "devices", "device_ids": device_ids},
            "display_mode": "combined_plus_individual",
            "metrics": ["power_w", "energy_kwh", "energy_cost", "usage_cost"],
            "start_utc": "2026-07-21T03:00:00Z",
            "end_utc": "2026-07-21T05:00:00Z",
            "selection_start_utc": "2026-07-21T03:00:00Z",
            "selection_end_utc": "2026-07-21T05:00:00Z",
            "bucket": "1h",
            "timezone": "America/Los_Angeles",
        }
        result = require(
            await client.post(
                "/api/v1/history/query", headers=headers, json=base_query
            ),
            "query combined History",
        ).json()
        summary = result["summary"]
        selected = result["selected_summary"]
        if Decimal(summary["energy_kwh"]) != Decimal("4") or Decimal(
            summary["energy_cost"]
        ) != Decimal("4"):
            raise WorkflowFailure(f"combined 8 PM–10 PM total is incorrect: {summary}")
        if Decimal(selected["energy_kwh"]) != Decimal("4") or Decimal(
            selected["energy_cost"]
        ) != Decimal("4"):
            raise WorkflowFailure(f"selected-range total is incorrect: {selected}")
        if len(result["combined"]) != 2 or len(result["individual"]) != 2:
            raise WorkflowFailure("combined + individual mode omitted expected series")
        for point in result["combined"]:
            if Decimal(point["average_power_w"]) != Decimal("2000"):
                raise WorkflowFailure("combined power is not the two-sensor sum")
            if Decimal(point["voltage_avg_v"]) != Decimal("120"):
                raise WorkflowFailure("combined voltage was not kept as a statistic")
            if point["current_a"] is not None:
                raise WorkflowFailure("incompatible service-leg current was aggregated")
            if point["rate_version_id"] != version["id"]:
                raise WorkflowFailure("historical rate-version provenance is missing")
            if Decimal(point["coverage_percent"]) != Decimal("100"):
                raise WorkflowFailure(
                    "complete deterministic data did not report 100% coverage"
                )

        for device_id in device_ids:
            single_query = {
                **base_query,
                "scope": {"type": "device", "device_id": device_id},
                "display_mode": "combined",
            }
            single = require(
                await client.post(
                    "/api/v1/history/query", headers=headers, json=single_query
                ),
                "query single-sensor compatibility",
            ).json()
            if Decimal(single["summary"]["energy_kwh"]) != Decimal("2"):
                raise WorkflowFailure("single-sensor History compatibility failed")

        individual = require(
            await client.post(
                "/api/v1/history/query",
                headers=headers,
                json={**base_query, "display_mode": "individual"},
            ),
            "query individual mode",
        ).json()
        if individual["combined"] or len(individual["individual"]) != 2:
            raise WorkflowFailure("individual mode returned an invalid series set")

        exported = require(
            await client.post(
                "/api/v1/history/export", headers=headers, json=base_query
            ),
            "export History CSV",
        ).text
        for expected in (
            "power-monitor-history-export/1.0",
            version["id"],
            "combined",
            "individual",
        ):
            if expected not in exported:
                raise WorkflowFailure(f"History CSV omitted {expected!r}")
        rows = list(csv.reader(io.StringIO(exported)))
        header_index = next(
            (
                index
                for index, row in enumerate(rows)
                if row and row[0] == "series_type"
            ),
            None,
        )
        if header_index is None:
            raise WorkflowFailure("History CSV has no interval header")
        header = rows[header_index]
        interval_rows = [
            dict(zip(header, row, strict=False))
            for row in rows[header_index + 1 :]
            if row
        ]
        combined_rows = [
            row for row in interval_rows if row["series_type"] == "combined"
        ]
        exported_energy = sum(
            (Decimal(row["energy_kwh"]) for row in combined_rows), Decimal("0")
        )
        exported_cost = sum(
            (Decimal(row["interval_energy_cost"]) for row in combined_rows),
            Decimal("0"),
        )
        if exported_energy != Decimal("4") or exported_cost != Decimal("4"):
            raise WorkflowFailure(
                "History CSV totals differ from server response: "
                f"{exported_energy}/{exported_cost}"
            )

        require(
            await client.put(
                f"/api/v1/circuits/{circuits[1]['id']}",
                headers=headers,
                json={
                    "site_id": site["id"],
                    "parent_id": circuits[0]["id"],
                    "name": circuits[1]["name"],
                    "measurement_role": "service-leg",
                    "split_phase_group": "integration-main-panel",
                },
            ),
            "create topology overlap",
        )
        overlap = await client.post(
            "/api/v1/history/query", headers=headers, json=base_query
        )
        if (
            overlap.status_code != 422
            or overlap.json().get("code") != "history_topology_overlap"
        ):
            raise WorkflowFailure(
                "parent/child overlap did not prevent double counting"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ca-certificate", required=True, type=Path)
    parser.add_argument("--setup-token-file", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(exercise(args))
    except (OSError, ValueError, httpx.HTTPError, WorkflowFailure) as exc:
        print(f"History workflow failed: {exc}", file=sys.stderr)
        return 1
    print(
        "History workflow passed: sensors=2 readings=4 combined=verified "
        "cost=$4.00 export=verified overlap=blocked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
