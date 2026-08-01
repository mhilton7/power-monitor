# Sensor storage controls

Sensor storage controls are part of the existing sensor and management APIs; no
new port, database, credential, or protocol version is introduced. The additive
fields remain compatible with `pm-protocol/1.0.0`.

## Access and configuration

`storage.view` permits assigned-site storage evidence. `storage.manage` permits
policy changes, acknowledgement-aware cleanup requests, and preparation for card
removal. The built-in viewer and rate-manager roles can view; operator and admin
can manage. Server-side permission and site-scope checks are authoritative.

The dashboard is under **Settings → Sensors → Storage** and shows:

- signed capacity, used/free space, card type, and pressure state;
- oldest/newest local sequence and the server reading acknowledgement;
- unsynchronized count, reclaimable bytes, and protected bytes;
- measurement/event segment and temporary-artifact counts;
- desired and effective policy versions and pending delivery state;
- last cleanup, cleanup recovery, growth estimate, and explicit dropped gaps.

The server queues configuration through the existing signed device configuration
channel. A legacy device configuration that lacks storage keys is merged with
the effective storage policy in the latest signed heartbeat, so deployment does
not silently replace existing behavior. Explicit desired/effective configuration
keys take precedence over heartbeat fallback evidence.

## Acknowledgements

Measurement cleanup continues to use the persisted highest contiguous accepted
reading sequence. Event evidence uses `device_event_sync_cursors` and the
optional signed `first_stored_event_sequence` boundary. The API returns
`highest_contiguous_event_sequence`; a sensor persists this value before event
segments become reclaimable. Arrival order alone never creates a deletion
boundary.

Raw server readings and historical costs remain immutable and independent of a
sensor card. Removing an acknowledged local segment does not remove a server
reading, History interval, bill, alert, audit record, or backup.

## Alerts

The worker evaluates distinct rules for notice, warning, critical, emergency,
cleanup blocked, write reserve unavailable, and durable interval dropped. Each
notification links to storage evidence and resolves from signed heartbeat state;
generic sensor-offline logic is not used as a substitute.

## Operations

Use the dashboard's **Prepare for removal** workflow and follow the firmware
guide `docs/STORAGE_RETENTION_AND_CARD_REPLACEMENT.md` in the sensor repository.
Do not format, erase, or fill a production card as a diagnostic. Do not reset or
reenroll a sensor to replace its card.
