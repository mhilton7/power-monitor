# Live sensor measurement pipeline

## Authoritative data flow

The production path for a physical PZEM-004T sensor is:

1. `power-monitor-sensor/src/meter/PzemMeter.cpp` reads voltage, current,
   active power, frequency, power factor, and the lifetime energy counter.
2. `power-monitor-sensor/src/app/Application.cpp` publishes the latest
   `MeasurementSnapshot` to diagnostics and sends interval samples to the
   aggregator.
3. The interval aggregator and
   `power-monitor-sensor/src/storage/SdStorage.cpp` persist immutable,
   sequence-numbered records on microSD.
4. `power-monitor-sensor/src/network/ServerSync.cpp` puts the newest
   measurement in the signed heartbeat and sends stored intervals through
   the signed reading-batch endpoint.
5. `backend/app/api/routes/device_protocol.py` verifies the HMAC identity,
   stores the heartbeat, and sends reading batches to the common ingestion
   service.
6. `backend/app/ingestion/service.py` deduplicates raw records by
   `(device_id, sequence)`, validates timestamps, updates the contiguous
   cursor, and creates normalized intervals.
7. `backend/app/live_measurements.py` loads the newest heartbeat and newest
   committed reading for all selected devices in two grouped queries. It
   validates and resolves one authoritative latest measurement per device.
8. `backend/app/api/routes/management.py` uses those measurements for both
   the devices response and the Home fleet summary. Status indicators in
   `backend/app/status_indicators.py` use the same resolver.
9. `backend/app/api/routes/system.py` publishes `heartbeat`, `reading`, and
   `device_status` SSE events.
10. `frontend-next/src/state/LiveHomeContext.tsx` invalidates the site-scoped
    sensors and Home-summary queries after those events, with 15-second
    polling as a fallback.
11. `frontend-next/src/api/adapters.ts` preserves null measurement values.
    `frontend-next/src/pages/home/HomePage.tsx` renders the header, main
    power value, status summary, and per-sensor electrical metrics.

Heartbeat measurements provide low-latency live display. History and energy
totals are always derived from committed `raw_readings` and
`normalized_intervals`; a heartbeat never fabricates history.

## Latest-measurement semantics

Each resolved measurement carries:

- device, site, circuit, and measurement-role identity;
- measurement and receipt timestamps in UTC;
- durable sequence when the source is a committed reading;
- power, voltage, current, frequency, and power factor;
- source (`heartbeat_live` or `committed_reading`);
- freshness state and invalid-metric evidence.

The newest valid measurement timestamp wins. An older heartbeat cannot
replace a newer committed reading. Future, untrusted durable, non-finite,
out-of-range, or missing-power candidates do not contribute to live power.
Missing is represented as null; a legitimate measured zero remains zero.

Connectivity is evaluated from the newest `DeviceHeartbeat.received_at`
timestamp actually written by this server. The denormalized `Device.status`
and `Device.last_seen_at` fields are supporting metadata, not freshness
authority. The explicit server-offline boundary is 30 seconds; it is exposed
as `offline_after_seconds` and guarded by configuration validation so a
deployment cannot lengthen it to hide synchronization failures. The live
measurement deadline remains four times the 15-second heartbeat expectation.
The shared measurement states are `live`, `waiting`, `stale`, `offline`,
`unavailable`, `invalid`, and `needs_attention`.

The devices response also includes the server-derived heartbeat receipt time,
age, and `never_received`/`online`/`offline` state. Fleet online counts use that
same result. The SSE device-status signature includes derived heartbeat and
measurement freshness, so crossing the offline boundary or accepting a new
heartbeat invalidates both active Home-summary and sensor queries. Polling
remains a fallback.

The server cannot observe a local pre-TLS deferral while the sensor is unable
to contact it. `previous_outage_reason` is therefore absent during an unseen
outage and is populated only after a later signed heartbeat reports a known,
allowlisted local-deferral reason. Unknown sensor text is never echoed.

`reporting_devices` counts fresh valid measurements. The Home aggregate uses
only sensors explicitly included by the topology. A sole active sensor is
an unambiguous Single Home aggregate and is included automatically. With
multiple sensors, parent/child or whole-home/submeter measurements are not
blindly summed.

## Sequence recovery

Raw readings remain immutable and deduplicated by `(device_id, sequence)`.
`oldest_stored_sequence` identifies all local SD evidence.
`oldest_syncable_sequence` separately identifies the first retained interval
with trustworthy UTC bounds. Startup intervals without trustworthy time stay
on the card but are never fabricated as server history.

If the first syncable sequence begins above sequence 1, the server may advance
to `oldest_syncable_sequence - 1` only when the signed heartbeat and first
batch agree. The same repair is allowed when a pre-upgrade server already
stored that exact first sequence but left its cursor at zero. The unavailable
prefix is recorded as permanent loss, existing raw rows remain unchanged, and
an unverified gap is never skipped.

Startup-time records can also be interspersed with trusted intervals when the
clock becomes trusted, restarts, and is restored again. A reading batch may
therefore include optional HMAC-signed `unavailable_sequence_ranges`. Each
range identifies immutable on-card evidence whose UTC interval was never
trustworthy. The server records those exact sequences as permanent loss and
advances the contiguous cursor only across the union of committed readings and
signed permanent-loss ranges. The field is bounded, ordered, non-overlapping,
and cannot overlap a reading in the same batch. A batch may contain only an
unavailable range when an entire bounded synchronization window has no
timestamp-safe records. No timestamp or historical reading is fabricated.

## Safe diagnostics

The pipeline emits structured, secret-free events including:

- `HEARTBEAT_ACCEPTED`
- `LIVE_MEASUREMENT_ACCEPTED` / `LIVE_MEASUREMENT_REJECTED`
- `READING_BATCH_ACCEPTED` / `READING_BATCH_DUPLICATE` /
  `READING_BATCH_REJECTED`
- `READING_CURSOR_BOOTSTRAPPED`
- `FLEET_SUMMARY_CALCULATED`
- `SSE_EVENT_PUBLISHED`

Set `VITE_LIVE_PIPELINE_DEBUG=true` only in a development frontend build to
log the selected Home ID, sensor/reporting counts, measurement timestamps,
freshness states, and query refresh time. No credentials or signatures are
logged.

## Production verification checklist

With the physical sensor connected:

1. Confirm serial logs show successful heartbeat and reading-batch HTTP 200
   responses.
2. Confirm accepted/duplicate/rejected counts, contiguous acknowledgment,
   and backlog.
3. Query the devices and fleet-summary endpoints for the same site.
4. Confirm the device ID, site ID, sequence, measurement timestamp, and
   electrical values agree.
5. Confirm History returns the committed interval independently of the live
   heartbeat.
6. Observe at least ten heartbeat intervals, restart frontend and API/worker
   containers, then disconnect and reconnect the sensor.
7. Confirm transitions through live, stale/offline, and automatic recovery
   without losing the sensor web interface.

Never use Sensor Test Mode as evidence for this checklist.
