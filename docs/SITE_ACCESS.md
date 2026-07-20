# Site access

Every human user has either all-site scope or an explicit set of site UUIDs. No-site scope is valid and exposes no site-bound resources until an administrator assigns one.

Server filters apply to sites, accounts, circuits, aggregates, devices, readings/history, fleet summaries, alerts, reports/exports, and live events. Mutations re-check the target resource's site. An unassigned site/device is hidden with a not-found response where appropriate, preventing names and metadata from leaking through IDOR probes, dropdowns, search, or exports.

Permissions and scope combine: `devices.manage` permits an operation only for an assigned site. An administrator with limited scope cannot grant all-site access or a site outside their own scope. Device identity remains its UUID; changing a human user's scope does not touch ESP32 HMAC credentials, enrollment secrets, or signed heartbeat behavior.

Existing users are migrated to all-site scope to preserve prior behavior. Administrators can then narrow accounts deliberately through **Users & Access**; the save summary lists sites added/removed and revokes affected sessions.
