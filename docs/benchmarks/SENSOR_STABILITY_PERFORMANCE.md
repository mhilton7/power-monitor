# Sensor stability and performance evidence

## Purpose

This record captures the physical baseline that reproduced heartbeat starvation
on firmware 1.0.15 and the acceptance plan for the internal-DRAM repair. It does
not promote a new firmware release and does not claim post-change physical
success before the exact binary is flashed and soaked.

## Environment

| Item | Value |
|---|---|
| Target | ESP32-S3 N16R8 production sensor |
| Firmware observed | 1.0.15 |
| Embedded build commit | `5c98b6939764` |
| Build timestamp | `2026-08-03T22:36:45Z` |
| Server URL | Private LAN deployment over verified TLS |
| Probe | `GET /api/local/health`, one request per second |
| TLS policy | 64 KiB total internal and 32 KiB largest contiguous block |
| Probe duration | 596 successful samples (approximately ten minutes) |

No device secret, enrollment token, signature, password, key, or sensitive
request body was captured.

## Reproduced baseline

The Indoor sensor completed 596 of 596 local-health probes without rebooting,
so Wi-Fi and the local HTTP plane remained alive. Nevertheless, central TLS was
starved:

| Metric | Baseline result |
|---|---:|
| Probe successes | 596 / 596 |
| Reboots | 0 |
| Samples classified idle low-total | 417 |
| Median probed free internal heap | 61,524 bytes |
| Median largest internal block | 32,756 bytes |
| Minimum largest internal block | 29,172 bytes |
| Unperturbed idle sample | 64,388 bytes free / 32,756-byte largest block |
| ServerSync unused stack watermark | 14,612 of 24,576 bytes |
| TLS heap rejections during probe | +193 |
| Successful heartbeats during probe | 0 |
| Durable backlog | 43 -> 53 |

The public probe itself temporarily consumed roughly 3 KiB, explaining why
its median total is below the separate idle sample. More importantly, the
unperturbed idle total was 1,148 bytes below the 65,536-byte TLS floor and the
largest block was only 12 bytes below the 32,768-byte contiguous floor. The
sensor therefore remained online locally while correctly rejecting every
central TLS attempt with `internal_heap_reserve_low`. This was not a DNS,
certificate, HMAC, server, SD, PZEM, or browser-cache failure.

A second (Outdoor) sensor using the same version could cross the admission
threshold between probes and continued to advance TLS admissions/successes.
That contrast further isolates the failure to per-device internal-heap margin,
not the shared server endpoint.

## Root cause and repair

Firmware 1.0.15 permanently reserved a 24 KiB `ServerSyncTask` stack after the
transport path had already been made bounded with PSRAM request/response
scratch. Physical runtime showed only 9,964 bytes used at the deepest observed
checkpoint, leaving 14,612 bytes unused. The repaired allocation is 20 KiB:

```text
measured unused at 24 KiB       14,612 bytes
returned internal task stack    4,096 bytes
projected unused at 20 KiB      10,516 bytes
projected margin                  51.3%
required margin                   25.0%
```

The change is deliberately smaller than moving TLS/DMA-sensitive objects or
weakening admission policy. It returns a fixed 4 KiB internal allocation while
retaining more than twice the required measured stack margin. Request (20 KiB),
response (24 KiB), canonical (2 KiB), and URL (1 KiB) scratch remain fixed in
PSRAM. TLS remains single-flight.

The mathematical post-change estimates are:

| Metric | Baseline | Static estimate after reclaim |
|---|---:|---:|
| Unperturbed idle free internal | 64,388 | 68,484 bytes |
| Median probed free internal | 61,524 | 65,620 bytes |
| ServerSync unused stack | 14,612 / 24,576 | 10,516 / 20,480 bytes |

These are estimates, not physical results. Freeing a task-stack allocation is
expected to return one contiguous block, but only the exact linked/flashed
binary can prove its placement and coalescence. The 64 KiB/32 KiB TLS gates are
unchanged.

## Diagnostics correction

The old local-health response assigned `tls_ready` from the memory-pressure
controller's cached/hysteretic state. A response could therefore say ready
while its own current heap values were below admission. The response now calls
the shared `memoryTlsReady(free_internal, largest_internal, integrity_ok)` on
the exact heap snapshot serialized in that response.

## Automated regression gates

The sensor repository now checks that:

1. `ServerSyncTask` is exactly 20 KiB and the physical-path projection retains
   at least 25% unused stack.
2. The total and contiguous TLS guards remain exactly 64 KiB and 32 KiB.
3. Local health derives `tls_ready` from its current heap snapshot, not the
   cached pressure state.
4. Task diagnostics report ServerSync on the same core on which it is created.
5. Native policy tests continue covering exact admission boundaries and
   fragmented/high-total cases.
6. Production, debug, simulation, administrator-recovery, native, and
   sanitizer builds remain compile/link/test gates as applicable.

### Completed automated matrix

The post-repair automated matrix completed on 2026-08-03:

| Gate | Measured result |
| --- | --- |
| Authoritative Python sensor suite | PASS, 102/102 tests in 43.670 seconds |
| PlatformIO `native-tests` | PASS |
| Windows `native-sanitized` fallback | PASS with checked iterators and stack protector; not counted as Linux sanitizer evidence |
| Production ESP32-S3 release | PASS, RAM 77,232/327,680 bytes (23.6%), application 1,616,329/6,291,456 bytes (25.7%) |
| ESP32-S3 debug | PASS, RAM 77,432 bytes, application 1,960,905 bytes |
| ESP32-S3 simulated | PASS, RAM 77,424 bytes, application 1,953,889 bytes |
| ESP32-S3 administrator recovery | PASS, RAM 51,000 bytes, application 624,561 bytes |
| Linux GCC 12 ASan/UBSan/leak | PASS, 128 sequences and 14,080 events; zero diagnostics |
| Sensor Web UI unit/component suite | PASS, 36/36 tests |
| Sensor Web UI production build | PASS |
| Sensor Web UI browser matrix | PASS, 12/12 across Chromium, Firefox, and WebKit |

An earlier frozen-source automated build produced a 1,616,768-byte
`firmware.bin` with SHA-256
`4a83c9fe5a7709897cc14bbe8004fcbf86e63a900b7dd2e330646d70d087ed99`
and an ELF with SHA-256
`c4dd4f3d6452682bd09fbf455c223823db3da924ce599a526b09ab2586d5fcd8`.
These hashes document that intermediate automated build only and are not the
final canary identity. The exact packaged 1.0.16 canary is 1,616,784 bytes with
firmware SHA-256
`8986804382cffdd995ff0f3e11b020e85b52c93d991b911d6ea6f9a3a4b0b0c7`;
its matching 36,096,012-byte ELF has SHA-256
`2982873bc8f22c089181c0edc378ca9ffd46489a825195e650c1b9d35a57d506`.
That exact identity is authoritative in
[`../ota/HARDWARE_CANARY_RESULTS.md`](../ota/HARDWARE_CANARY_RESULTS.md) and
remains canary-only until physical validation passes.

## Genuine Linux ASan and UBSan evidence

The Windows `native-sanitized` environment is a checked-iterator and stack
protector fallback because pinned MinGW 5.1 has no sanitizer runtimes. It is
not counted as sanitizer acceptance. On 2026-08-03 the audit therefore ran the
deterministic native source set in an isolated Linux container.

| Item | Evidence |
| --- | --- |
| Base | Debian Bookworm from the already-pinned `postgres:17.5-bookworm` image |
| Local compiler image | `power-monitor-sensor-sanitizer:gcc12-bookworm` |
| Image ID | `sha256:12384c9c05b84b9bd3b0ac3d7caea63b00e42500d21f3a287a6a54468d9fa2b6` |
| Compiler | `g++ (Debian 12.2.0-14+deb12u1) 12.2.0` |
| Runtime linkage | `libasan.so.8` and `libubsan.so.1` confirmed by `ldd` |
| Isolation | `--network none`, read-only container, read-only repository bind, tmpfs output |
| Sanitizers | AddressSanitizer, UndefinedBehaviorSanitizer, and ASan leak detection |
| Deterministic result | PASS: 128 sequences, 14,080 events |
| Native executable | PASS: `native C++ tests passed`, exit 0 |
| Diagnostics | No ASan, UBSan, or leak diagnostics |

The compiler-only image was prepared before any source was mounted. The
networked preparation step contained no repository data:

```text
docker run --name pm-sensor-sanitizer-debian-prep --entrypoint sh \
  postgres:17.5-bookworm -lc \
  "apt-get update; apt-get install -y --no-install-recommends g++; \
   g++ --version; ldconfig -p | grep -E 'libasan|libubsan'"
docker commit pm-sensor-sanitizer-debian-prep \
  power-monitor-sensor-sanitizer:gcc12-bookworm
docker rm pm-sensor-sanitizer-debian-prep
```

The test step was fully offline. The compile source list exactly mirrors the
repository's PlatformIO native `build_src_filter`:

```text
docker run --rm --network none --read-only --entrypoint sh \
  --mount type=bind,source=E:\Documents\Codex\power-monitor-sensor,target=/src,readonly \
  --tmpfs /work:exec,size=536870912 --tmpfs /tmp:exec,size=67108864 \
  -w /work power-monitor-sensor-sanitizer:gcc12-bookworm -euc '
g++ -std=c++17 -O1 -g3 -Wall -Wextra -Werror \
  -Wno-maybe-uninitialized -DPM_NATIVE_TEST=1 \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  -fno-sanitize-recover=all \
  -I/src/include -I/src/src -I/src/test/test_native_cpp \
  -I/src/.pio/libdeps/native-tests/ArduinoJson/src \
  /src/test/test_native_cpp/main.cpp \
  /src/src/config/AtomicConfigStore.cpp \
  /src/src/config/ConfigValidationHelpers.cpp \
  /src/src/config/ProvisioningTransaction.cpp \
  /src/src/meter/PzemProtocol.cpp /src/src/core/Algorithms.cpp \
  /src/src/api/CompactUiStatus.cpp \
  /src/src/diagnostics/DiagnosticCore.cpp \
  /src/src/network/ReadingWireFormat.cpp \
  /src/src/network/ServerSyncPolicy.cpp \
  /src/src/ota/OtaManifestV2.cpp /src/src/ota/OtaUpdatePolicy.cpp \
  /src/src/storage/RecordFormat.cpp /src/src/storage/StoragePolicy.cpp \
  -fsanitize=address,undefined -o native-asan-ubsan
ldd native-asan-ubsan | grep -E "libasan|libubsan"
ASAN_OPTIONS=detect_leaks=1:abort_on_error=1:check_initialization_order=1:strict_string_checks=1 \
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 ./native-asan-ubsan'
```

`-Wmaybe-uninitialized` is suppressed only for GCC's known warning in
ArduinoJson 7.4.3's empty `CollectionIterator`; every other warning remains an
error. This run covers the portable deterministic native modules and their
randomized state machines. It does not instrument the Xtensa binary,
FreeRTOS/Arduino framework, Wi-Fi/TLS driver, physical PZEM UART, or microSD
driver. Those remain covered by firmware builds and the exact-binary physical
canary rather than by host sanitizers.

## Required exact-binary canary

Before release promotion, flash one sensor without erasing NVS, configuration,
or microSD data, then run at least one hour with the exact candidate hash:

- poll local health at one-second cadence;
- exercise normal and maximum bounded heartbeat/reading/event responses;
- keep the sensor Web UI open and repeat bounded Status/health requests;
- include server unavailable, DNS failure, Wi-Fi reconnect, SD activity, and
  backlog recovery;
- require zero reboot, zero unexplained queue drop, no permanent
  `internal_heap_reserve_low`, and heartbeat/batch progress;
- require every critical task to retain at least 25% measured stack margin;
- verify internal free and largest-block trends do not decline;
- confirm `tls_ready` agrees with the serialized current heap values at every
  sample; and
- confirm configuration, enrollment, history, and microSD contents remain
  intact.

If the exact candidate fails the contiguous-block gate despite the 4 KiB
reclaim, do not lower either TLS threshold. The next safe optimization is to
move only bounded, non-DMA response-body storage to one-time PSRAM-backed
slots, followed by the same exact-binary canary.

## Status

The pre-change failure is reproduced and its primary cause is confirmed. The
code-level repair and automated checks can be completed without touching
physical state. Final hardware-sensitive acceptance remains
`BLOCKED_EXTERNAL_HARDWARE` until the exact new ELF completes the canary above.
