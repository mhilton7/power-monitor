# Sensor Test Mode

Sensor Test Mode is an owner-only, disabled-by-default diagnostic at
**Settings > Advanced > Sensor Test Mode**. It lets an owner verify Home,
History, Billing preview, and Sensors behavior without enrolling or
impersonating an ESP32.

## Isolation model

The simulator is a single ephemeral server-process session guarded by an
async lock. Every object is classified as:

```text
source_type = simulated
environment = test_mode
```

Stable UUIDv5 sensor IDs are derived from the random session ID and sensor
index. The background loop and all interval history live only in memory. It
does not call enrollment, heartbeat, reading ingestion, device credential,
firmware, alert, report, backup, bill, or saved-cost services. It creates no
database row except required audit events. Normal History and CSV exports,
logical backups, retention, alerts, and System Health therefore have no
synthetic row to include.

The optional cost preview passes only the session's in-memory Decimal energy
through the current reviewed rate version with `energy_only` scope. It excludes
fixed charges and never creates a bill, billing-cycle record, adjustment,
calculation run, interval-cost row, or export.

## Controls and lifecycle

An owner can configure 0 through 32 active simulated sensors (default 1), a
1–60 second interval, steady/variable household/morning-and-evening/high/low or
custom load, base watts, variation, offline sensor indexes, pause/resume, and a
15 minute, 1 hour, 4 hour, or until-disabled duration. Count changes preserve
the identities of indexes that remain. Names begin at `Simulated Sensor 1`.

Enable creates one session and one update task. Repeated idempotency keys do not
start duplicate work. Update reconciles the existing list immediately. Reset
clears session history and accumulated energy. Exit cancels the update task and
discards every simulated sensor and interval. Auto-expiry performs the same
cleanup, records a system audit event on the next owner status poll, and returns
an expiry notice.

A persistent, accessible banner appears on Home, History, Billing, and Sensors
while active. Home, History, Billing, and Sensors render separate Test Mode
surfaces; they never merge the synthetic series with normal data. The Sensors
surface offers only simulated actions such as online/offline state. It never
offers enrollment, credential, firmware, or real-device maintenance actions.

## API

All reads require the built-in owner/administrator role. All writes additionally
require the normal CSRF proof and carry an idempotency key:

```text
GET  /api/v1/test-mode
POST /api/v1/test-mode/enable
PUT  /api/v1/test-mode
POST /api/v1/test-mode/disable
POST /api/v1/test-mode/reset
GET  /api/v1/test-mode/sensors
PUT  /api/v1/test-mode/sensors/{sensor_id}
GET  /api/v1/test-mode/history
```

Validation rejects non-integer, negative, or greater-than-32 counts, invalid
offline indexes, unsupported profiles, unsafe loads, and missing custom load.
Audit actions are `sensor_test_mode.enabled`, `.updated`, `.sensor_updated`,
`.reset`, `.disabled`, and `.expired`.

## Limitations

The session is intentionally process-local and disappears on API restart or
release upgrade. Production runs one API process per container; deployments
that independently change that topology must keep Test Mode disabled or add a
dedicated shared ephemeral coordinator. This mode validates presentation and
rate-preview behavior, not HMAC, Wi-Fi/VLAN, microSD backfill, PZEM acquisition,
firmware, or real hardware. Those paths continue to require the signed device
simulator or physical sensor workflow.
