# Sensor Hardware and Billing Diagnosis

Captured read-only on 2026-08-06/07 before this repair. Secrets, signatures,
credentials, network names, and certificate material are intentionally omitted.

## Repository and release baseline

| Item | Captured value |
| --- | --- |
| Server commit | `dc25159925a5102fd7a0ec06c3fa0642cc81cfd0` |
| Server branch | `codex/data-only-factory-reset` |
| Deployed server/frontend | `1.0.47` / `1.0.47` |
| Database migration head | `20260806_0032_headless_agent_commands` |
| Headless firmware commit | `7a8ef4acca91c4be6d1deb6b26838f5d17616c87` |
| Headless firmware source version | `2.0.4` |
| Agent protocol | `pm-agent/2.0.0` |
| ESP-IDF | `5.5.5` |
| Partition-table SHA-256 | `8024b8521902352b87f3302f487638533801034c6b1eb5187cbd2ab885a37b77` |
| Modified/untracked files before repair | None in either writable repository |

## Active sensor evidence

The signed agent-status response, device-list response, and central UI were
captured while the devices were in their repeated SD-failure reboot loop.

| Field | Indoor-AC | Outdoor-AC |
| --- | --- | --- |
| Device UUID | `61c897c2-1f49-401d-8e72-d932997e7e7f` | `5830f827-fa9f-4726-aa6c-e7718a820000` |
| Site ID | `77ad4088-434f-4a1b-9813-78f79dd797ed` | `77ad4088-434f-4a1b-9813-78f79dd797ed` |
| Circuit ID | `a04979e3-4060-47c9-805a-0e267a397efd` | `28595f6c-bc96-4ef7-bca6-e58bf29db4ec` |
| Circuit / role | Indoor-AC / `branch` | Outdoor-AC / `branch` |
| Device role | `branch` | `branch` |
| Utility-account ID | `f12a435f-5a9c-402b-94cf-9742b39cce7b` | `f12a435f-5a9c-402b-94cf-9742b39cce7b` |
| Lifecycle | `active` | `active` |
| Included in default total | No | Yes |
| Protocol | `pm-agent/2.0.0` | `pm-agent/2.0.0` |
| Observed firmware | `2.0.3` after 2.0.4 rollback | `2.0.3` after 2.0.4 rollback |
| Build hash | `24903a6907c97d62c6bc8dc649297ed5c8766177340e9a7650044c157f53da19` | Same |
| Heartbeat freshness | Alternated online/offline at the 30-second boundary | Alternated online/offline at the 30-second boundary |
| Boot evidence | Boot ID repeatedly changed; uptime approximately 30 seconds; `reason_4` | Same |
| Last accepted reading/time/sequence | None | None |
| Live electrical fields | All absent | All absent |
| Database PZEM flag | `true` | `true` |
| Database SD flag | `false` | `false` |
| Raw `pzem` | `{"ok":true,"status":"healthy"}` | Same |
| Raw `sd` | `{"ok":false,"status":"unavailable","format_attempted":false}` | Same |
| Raw `latest` | `null` | `null` |
| Oldest/newest/syncable sequence | `0 / 0 / 0` | `0 / 0 / 0` |
| Server acknowledgement/backlog | `0 / 0` | `0 / 0` |
| Data generation | `0` | `0` |
| Card generation | `null` | `null` |
| Desired/effective configuration | `0 / 0` | `0 / 0` |

The PZEM hardware path was responding. The missing SD mount caused StorageTask
to enter exponential backoff. Its 16-second backoff exceeded the 15-second
panic watchdog, producing a roughly 30-second reboot cycle before a 60-second
measurement aggregate could be completed. The heartbeat also serialized
`latest` as `null`, so the server had no independent live-measurement path.

## Utility-account and billing evidence

| Field | Captured value |
| --- | --- |
| Account ID / site | `f12a435f-5a9c-402b-94cf-9742b39cce7b` / `77ad4088-434f-4a1b-9813-78f79dd797ed` |
| Current plan | SCE `DOMESTIC`, version 2 |
| Current cycle | 2026-08-01 through 2026-09-01, unfinalized |
| Stored authority | `service_leg_pair`, sensor measurements, complete-account claim |
| Assigned active sensors | Current Indoor-AC and Outdoor-AC UUIDs above |
| Eligible whole-account sensors | None: neither device/circuit is recorded as a whole-account meter |
| Eligible service-leg sensors | None: both devices and circuits are recorded as `branch` |
| Accepted readings / normalized intervals / tier allocations | `0 / 0 / 0` in the current UI state |
| Current usage/tier/remaining/cost | Unavailable |
| Physical topology | Unverified; no role mutation is authorized |

The editor rendered both current assigned sensors but neither checkbox was
selected and both were disabled. This is the exact state produced when the
stored authority contains two hidden IDs: raw `authority.device_ids` initializes
selection, the two hidden IDs satisfy the selection limit, and current visible
IDs cannot be added. The server then rejects the stale IDs with
`usage_authority_device_invalid`. The signed-in UI did not expose the raw IDs,
so this report records them as two stale hidden references rather than guessing
their UUID values.

Account assignment is internally consistent for the two current sensors. The
authority is not consistent with current device identity or topology. The two
branch roles must remain branches unless a separately reviewed physical check
proves that the CTs monitor distinct incoming service legs.

## Captured heartbeat contract matrix

| Meaning | Firmware field/type before repair | Server field/type before repair | Persistence/UI before repair | Mismatch/correction |
| --- | --- | --- | --- | --- |
| Live measurement | `latest: null`; serializer had no live structure | `dict | null`, read `latest.watts` | Only `current_watts`; other metrics absent | Emit and validate canonical typed `power_w` plus voltage/current/PF/frequency/energy/time |
| PZEM health | `{ok:boolean,status:string}` | Untyped `dict`; `get("ok") is True` | Boolean only | Typed status/details; reject malformed objects |
| SD health | `{ok:boolean,status:string,format_attempted:boolean}` | Untyped `dict`; `get("ok") is True` | Boolean and raw payload | Typed writable/mount/card/index/capacity evidence |
| Wi-Fi | `{connected:boolean,rssi_dbm:integer}` | Untyped `dict` | RSSI only | Typed connection state and bounded optional network evidence |
| Sequences | Integer object | Untyped `dict`; invalid values defaulted to zero | Cursor plus raw payload | Typed required non-negative evidence; no silent zero coercion |
| Capabilities | Mixed booleans/dictionaries | Untyped `dict` | SD was hard-coded optional | Signed storage requirement becomes authoritative |
| Resources | Free heap and stack margins split across objects | Untyped dictionaries | Raw payload only | Typed bounded resources retained and projected |

## Safety baseline

No storage format, flash erase, reading deletion, credential change, role
change, authority write, billing recalculation, deployment, publication, or
physical flash was performed while capturing this diagnosis.
