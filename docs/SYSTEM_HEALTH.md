# System Health

API, PostgreSQL, and asynchronous-worker health is available to authorized
administrators at **Administration > Security > System Health**. These detailed diagnostic
cards are intentionally absent from Overview, Devices, Topology, History,
Rates, Alerts, Enrollment, Backups, and normal administration pages.

Moving the cards does not disable monitoring. Container readiness probes,
TrueNAS health checks, worker freshness, signed-heartbeat processing, alert
rules, notification delivery, and audit evidence continue independently of the
dashboard layout. The System Health page refreshes its server-owned values and
also shows release, protocol, runtime, worker timestamps, and configured runtime
defaults. Operational alert records remain under **Alerts & Notifications**.

The three system indicators are marked `diagnostics_only` in the server
registry. Their only desktop placement is `diagnostics_summary`; layout imports
or historical revisions cannot move them back into a normal-page global row.
The resolved-layout API also suppresses an invalid legacy placement as a
fail-safe.

If the page itself cannot load, use the TrueNAS Apps workload and health views.
Do not disable TLS verification or publish the API/database ports while
diagnosing a failure.
