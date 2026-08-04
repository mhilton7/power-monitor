# Hardware canary results

## Scope and safety boundary

The controlling validation window is limited to approximately one hour. The
canary is deployed to one verified sensor first. The second sensor may receive
only the exact same `firmware.bin` after the canary passes. No erase, factory
reset, re-enrollment, certificate replacement, microSD format, or deletion of
unacknowledged readings is permitted.

The exact 1.0.16 canary artifact is built and recorded below. The physical
stages remain `PENDING`: automated builds and simulations are not substitutes
for these hardware-sensitive gates, and the artifact must not be promoted to
the stable channel until all four stages pass.

## Automated pre-canary evidence

The source-level prerequisites completed before physical deployment:

- sensor Python tests: 102/102 passed;
- all four ESP32-S3 production/debug/simulated/recovery builds passed;
- PlatformIO native and Windows checked-runtime gates passed;
- isolated Linux GCC 12 ASan/UBSan/leak testing passed 128 sequences and
  14,080 events with no diagnostics;
- sensor Web UI tests passed 36/36 and browser tests passed 12/12; and
- server OTA lifecycle/fault-injection tests passed 30/30.

This evidence establishes automated readiness only. It does not populate any
candidate-identity field below and does not change the status of Stages 1-4.

## Pre-change physical reproduction

The canary sensor (LAN address, device UUID, boot UUID, and build identity
redacted from this committed report) was observed on firmware `1.0.15`. The
unredacted identifiers remain only in the access-controlled local probe
artifact used to bind every sample to the same physical device and boot.

The ten-minute, one-second baseline produced 596 successful local-health
responses, no reboot, and no accepted heartbeat or reading batch. Median free
internal heap was 61,524 bytes and median largest block was 32,756 bytes. The
TLS admission policy remained 65,536 bytes free and 32,768 bytes contiguous.
The durable backlog grew from 43 to 53 without losing its acknowledgement
cursor.

A later read-only check of the same boot reported:

- uptime 8,358 seconds;
- Wi-Fi connected and trusted time available;
- writable microSD, with an unhealthy storage index retained for diagnosis;
- PZEM healthy;
- 64,364 bytes free internal heap;
- 32,756-byte largest internal block;
- TLS not ready;
- 2,015 cumulative local TLS heap rejections;
- 179 accepted heartbeats and 49 accepted reading batches since boot, with no
  new successes after the fragmentation condition;
- server acknowledgement 5,168, newest durable sequence 5,251, and 83
  unacknowledged durable readings preserved.

The second sensor did not answer the same read-only health request at that
observation. It must be re-identified before deployment; its IP address alone
is not authoritative.

## Final candidate identity

| Evidence | Value |
| --- | --- |
| Sensor source commit | `68adaa423bd010b19d90a10f40b41b753453de4d` |
| Sensor release-record commit | `429b2f014f982fb847d26b7cceea162260a6caf1` (the later metadata-only record marks the same bytes as canary) |
| Server source commit | The authoritative generated value in `release/versions.json` |
| Firmware semantic version | `1.0.16` (canary; not promoted stable) |
| `firmware.bin` SHA-256 | `8986804382cffdd995ff0f3e11b020e85b52c93d991b911d6ea6f9a3a4b0b0c7` |
| `firmware.elf` SHA-256 / application build hash | `2982873bc8f22c089181c0edc378ca9ffd46489a825195e650c1b9d35a57d506` |
| Build-input fingerprint | `568431db9413da79e9d3c48fba8f5162a2f45c39750733033d2741382f979846` |
| Application offset | `0x20000`, revalidated from the final `flash-layout.json` and build provenance |

The final `firmware.bin` is 1,616,784 bytes. Regenerating the release metadata
from the verified build changed only its channel and release notes from
`stable` to `canary`; the firmware and ELF bytes and hashes above did not
change.

## Current external hardware blocker

On 2026-08-04, Windows enumerated no COM port and no ESP32 USB/JTAG or UART
device, so the required non-erasing bootstrap, partition backup, and serial
evidence collection could not begin. One bounded read-only LAN health request
to the identified Indoor-AC sensor succeeded and showed firmware 1.0.15,
`pm-protocol/1.0.0`, Wi-Fi/time/storage/PZEM health, 55,000 bytes free internal
heap, a 32,756-byte largest block, and 2,204 TLS heap-admission deferrals. Its
acknowledgement was 5,472, newest durable sequence 5,581, and backlog 109, so
the unacknowledged history remains preserved. This confirms that the sensor is
powered and that the 1.0.16 bootstrap is still needed; it is not evidence that
the 1.0.16 physical canary passed.

The next safe action is to connect exactly one intended canary by USB. After a
COM port appears, back up NVS (`0x9000`, `0x8000` bytes), OTA selector
(`0x11000`, `0x2000` bytes), and project configuration/certificate storage
(`0x14000`, `0xC000` bytes), then request fresh approval for the exact detected
port using: `I authorize the non-erasing application-only flash of firmware
1.0.16 to COMx.` Only `firmware.bin` at `0x20000` may then be written.

## Stage 1 - baseline and local Web UI load (10 minutes)

Status: **PENDING**

Required evidence: one-second samples; five minutes with the local UI closed;
five minutes with one Status tab, several refreshes, Diagnostics once, and
Setup once without saving. Record heartbeat gaps, heap medians/trends, largest
block, task high-water values, queues/drops, owners, backlog, reset evidence,
and local-request counts.

## Stage 2 - ordinary operation and bounded recovery (20 minutes)

Status: **PENDING**

Required evidence: ten-second samples and, only where safe, a short controlled
server/network interruption. Measurement and microSD writes must continue,
heartbeats must recover, the backlog must decrease without loss, and no reboot
may be needed.

## Stage 3 - managed OTA canary

Status: **PENDING**

Required evidence: authenticated manifest, exact target metadata, streamed
hash, partition/recovery persistence and readback, selected boot partition,
target boot/version/build, local validation, accepted heartbeat, accepted
durable reading, preserved configuration/history, and a terminal server
deployment.

If the installed image cannot allocate enough contiguous memory to download
its own repair, the only allowed bootstrap is the separately authorized,
non-erasing, application-only USB write after backing up NVS, OTA selector, and
project configuration/certificate partitions. That bootstrap is not itself
proof that managed OTA works.

## Stage 4 - exact artifact on the second sensor

Status: **PENDING**

The second sensor may be scheduled only after Stage 3 passes and its target
SHA-256 matches the canary artifact exactly. Monitor both sensors for the
remainder of the one-hour window.

## Acceptance summary

No hardware-sensitive firmware change may be promoted stable while any stage
above is pending. If the final hardware cannot be made available after every
automated and simulated gate passes, final status is
`BLOCKED_EXTERNAL_HARDWARE`, with the exact remaining commands and observation
steps recorded here.
