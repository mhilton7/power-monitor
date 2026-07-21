# Status indicator registry

The status-indicator registry is the single server-owned inventory for compact,
summary-style health and operational state. The browser receives definition
metadata and current values from the server; it does not recalculate device,
rate, alert, backup, or worker health. Every registered key is stable within
registry version `status-indicators/1.0`.

| Category | Registered keys |
|---|---|
| System | `system.api_health`, `system.database_health`, `system.worker_health` |
| Live data and site | `data.live_connection`, `data.current_power`, `site.current`, `data.energy_today`, `data.recent_peak`, `data.aggregate_coverage` |
| Alerts and fleet | `alerts.active_count`, `alerts.critical_count`, `alerts.warning_count`, `alerts.enabled_rule_count`, `alerts.disconnect_rule_state`, `device.online_count`, `device.offline_count`, `device.synchronized_count` |
| Rates and source automation | `rate.current_plan`, `rate.current_period`, `rate.current_price`, `rate.source_health`, `rate.update_pending`, `rate.last_successful_check`, `rate.next_scheduled_check`, `rate.review_policy` |
| Device health | `device.pzem_health`, `device.sd_health`, `device.sync_backlog`, `device.time_sync`, `device.wifi_signal`, `device.heartbeat_freshness` |
| Operations | `firmware.update_state`, `backup.last_result`, `backup.verification`, `notifications.delivery_health`, `enrollment.availability`, `topology.aggregate_overlap` |

Each definition declares its default label, description, data source, current
value schema, severity capability, default visibility/zone/order, allowed
zones and pages, minimum/preferred width, supported density modes, configurable
content, required permission, renderer, icon, and critical fallback. The API
returns only indicators whose underlying data permission the current user
holds.

The following surfaces are deliberately excluded:

- Alert, backup, enrollment, user, firmware-deployment, rate-version, and export
  job states remain attached to their records.
- Authentication, validation, access-denied, destructive-confirmation,
  missing-chart-data, and operation-error feedback is mandatory and cannot be
  hidden by presentation settings.
- The site selector remains a data-scope and authorization control. The
  configurable `site.current` indicator is only a read-only summary.
- Primary charts, sensor cards, tables, and other task content remain part of
  their owning page; they are not compact status indicators.

The site selector is passed to the batched value endpoint, and device-detail
routes additionally pass the selected device UUID. Device health indicators on
a detail page therefore represent that sensor, while fleet pages remain
aggregated over the authorized site scope.

Hiding an indicator is presentation-only. Collection, signed heartbeat
processing, status calculation, persistence, synchronization, alert rules,
notification delivery, backups, and audit evidence continue unchanged. Critical
definitions require an explicit confirmation and state where the same condition
remains visible before they can be hidden.

New definitions appear with safe server defaults and are reported to
administrators as unreviewed after the first published revision. Unknown keys
from a retired definition are ignored while reading an old published layout and
produce a warning; imports reject unknown keys so a typo cannot silently publish.
