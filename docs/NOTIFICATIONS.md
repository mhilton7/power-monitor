# Notifications

Power Monitor extends its existing `AlertRule`, `AlertInstance`,
`NotificationChannel`, and `NotificationAttempt` pipeline. It does not maintain a second
alert store. The API expands those records into one safe, typed notification contract using
the authoritative catalog in `backend/app/notifications.py`.

## Kinds and lifecycle

- `operational_alert` is an authoritative monitoring, meter, history, security, storage,
  backup, firmware, or server condition. It can be acknowledged or temporarily silenced,
  and resolves only when authoritative state becomes healthy.
- `setup_recommendation` is an optional, non-blocking suggestion shown only to a user who
  has permission to act. SMTP uses the stable key `recommendation.smtp_not_configured`.
- `delivery_issue` describes external alert-delivery failure separately from the underlying
  operational alert. Email success or failure never resolves that alert.

Operational states are `open`, `acknowledged`, `silenced`, and `resolved`.
Recommendation states may additionally be `dismissed` or `suppressed`. Acknowledgement
means "seen" and does not imply recovery. Silence always has an expiry and stops repeat
external delivery while leaving the condition visible. There is no manual technical-health
resolve action.

Any dismissible notification can also be removed from one user's notification
center. Removal appends a `dismissed` event and preserves the alert, monitoring,
delivery evidence, and audit history. It is not a technical-health resolve. If
the authoritative condition is observed again after removal, the updated
notification returns automatically.

## Detailed contract

`GET /api/v1/notifications` returns a bounded, paginated list. `GET
/api/v1/notifications/{id}` returns the same detailed representation. `/api/v1/alerts`
remains a compatible list route. Each notification includes:

- stable code, kind, category, severity, and state;
- affected resource, first/last observation, occurrence count, and duration;
- safe observed and expected values where the rule supplies them;
- safe evidence, impact, cause, remediation steps, automatic recovery, and a
  permission-filtered action;
- acknowledgement, active silence, and latest delivery evidence; and
- an explicit suppression policy.

The API batches rule, device, site, actor, channel, and delivery lookups. Evidence keys that
could contain passwords, secrets, signatures, credentials, private keys, cookies, or tokens
are never returned.

## Supported catalog

The catalog provides specific messages and remediation for heartbeat staleness, device API
reachability, signed authentication failures, protocol incompatibility, PZEM failures,
invalid readings, microSD failure, synchronization backlog, sequence gaps, untrusted time,
low RSSI, power surges, CT utilization, voltage/frequency range violations, reboot loops,
firmware deployment failure, server/worker health, backup verification, managed-rate-source
events, network-policy violations, and external delivery failures. Unknown legacy rule codes
receive a safe non-suppressible fallback.

## Optional email reminder suppression

SMTP delivery is optional. Missing SMTP does not change Configuration Status or make the
Home partially configured. Users with `alerts.manage_delivery` see an informational setup
recommendation; strict viewers do not.

`POST /api/v1/notifications/{id}/suppress` requires CSRF, site scope, effective permission,
an explicit confirmation boolean, scope (`user` or `home`), and an optional reason. Only
catalog entries explicitly marked optional and permanently suppressible are accepted.
Operational, security, storage, meter, data-integrity, backup, and firmware failures return
`notification_not_suppressible`.

Suppressions are durable and uniquely active by stable key and user/Home scope. They retain
creator, timestamp, reason, source notification, revision, and later restoration metadata.
They appear under Settings > Notifications > Ignored recommendations. Restoring with
`DELETE /api/v1/notification-suppressions/{id}?expected_revision=N` deactivates rather than
deletes the record; the recommendation returns only if SMTP remains unconfigured.

## Delivery evidence

SMTP credentials are encrypted at rest and never returned. Delivery attempts expose only
channel ID, attempt number, queued/started/completed times, lifecycle state, retry time, and
safe stage/code/summary. Expected states include queued, sending, delivered, retry scheduled,
failed, and channel disabled. Safe failures distinguish DNS, TCP, timeout, STARTTLS/TLS,
authentication, recipient rejection, and SMTP response failure.

## Permissions

- `alerts.view`: view operational alerts and history within effective site scope.
- `alerts.acknowledge`: acknowledge, silence, end silence, and remove an
  operational notification from the current user's notification center without
  resolving it.
- `alerts.manage_rules`: change alert rules.
- `alerts.manage_delivery`: configure/test delivery and suppress or restore optional email
  recommendations.

Checks use effective permission sets for built-in and custom roles, not role names.

## Immutable history and diagnostics

`GET /api/v1/notification-history` is paginated and filterable by event type, severity,
category, resource, and date range. Open, update, acknowledge, silence, expiry, resolve,
reopen, dismissal, suppression, restoration, and delivery transitions append `NotificationEvent`
records. They are not edited when current alert state changes.

Structured logs contain safe IDs, rule/category/resource/site, state, severity, actor or
channel IDs, and safe delivery error codes. They never contain SMTP passwords, message
bodies, session/CSRF values, device secrets, HMAC keys, or private keys.
