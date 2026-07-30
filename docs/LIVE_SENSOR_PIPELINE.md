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

The online deadline is twice the configured heartbeat expectation. The live
measurement deadline is four times that expectation. The shared states are
`live`, `waiting`, `stale`, `offline`, `unavailable`, `invalid`, and
`needs_attention`.

`reporting_devices` counts fresh valid measurements. The Home aggregate uses
only sensors explicitly included by the topology. A sole active sensor is
an unambiguous Single Home aggregate and is included automatically. With
multiple sensors, parent/child or whole-home/submeter measurements are not
blindly summed.

## Sequence recovery

Raw readings remain immutable and deduplicated by `(device_id, sequence)`.
If an enrolled server has no readings but a sensor's retained microSD range
begins above sequence 1, the server may advance to `oldest_stored_sequence -
1` only when the signed heartbeat proves that the first batch starts at that
same retained sequence. The unavailable prefix is recorded as permanent
loss. An unverified gap is never skipped.

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
