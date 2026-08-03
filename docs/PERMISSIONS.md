# Permission reference

Permissions are stable server identifiers. Display-text customization never
renames them. Dependencies are validated server-side, including `users.manage`
→ `users.view`; `users.disable`, `users.remove`, and `users.restore` →
`users.manage` and `users.view`;
`roles.manage` → `roles.view`; `rates.approve_candidates` →
`rates.review_candidates` and `rates.view`; `backups.restore` →
`backups.view`; `interface_text.manage` → `interface_text.view`; and
`status_indicators.manage` → `status_indicators.view`.

| Area | Permission codes |
|---|---|
| Dashboard/data | `overview.view`, `usage.view`, `history.view`, `history.export`, `costs.view`, `costs.export` |
| Sites/devices | `sites.view`, `sites.manage`, `utility_accounts.view`, `utility_accounts.manage`, `network.view`, `network.manage`, `topology.view`, `topology.manage`, `devices.view`, `devices.manage`, `devices.remove`, `enrollment.view`, `enrollment.manage`, `firmware.view`, `firmware.manage`, `firmware.deploy` |
| Rates | `rates.view`, `rates.manage_custom`, `rates.manage_sources`, `rates.check_sources`, `rates.review_candidates`, `rates.approve_candidates`, `rates.assign` |
| Alerts | `alerts.view`, `alerts.acknowledge`, `alerts.manage_rules`, `alerts.manage_delivery` |
| Backup/logs | `backups.view`, `backups.create`, `backups.restore`, `logs.export` |
| Administration | `users.view`, `users.manage`, `users.disable`, `users.remove`, `users.restore`, `users.manage_protected`, `roles.view`, `roles.manage`, `audit.view`, `settings.view`, `settings.manage`, `interface_text.view`, `interface_text.manage`, `status_indicators.view`, `status_indicators.manage` |

## Built-in matrix

| Built-in role | Effective template |
|---|---|
| Administrator (`admin`) | Every catalog permission, including protected-user management. |
| Regular User / Read-Only Viewer (`viewer`) | Read-only Home, History, and Billing data: `overview.view`, `usage.view`, `history.view`, `costs.view`, `sites.view`, `utility_accounts.view`, `topology.view`, `devices.view`, `rates.view`, `alerts.view`, and `status_indicators.view`. Export, private-bill, Settings, and mutation permissions are intentionally excluded. |
| Operator (`operator`) | Viewer plus topology and device management, enrollment, firmware view, alert acknowledgement, and alert-rule management. Device removal remains excluded and separately protected. |
| Rate Manager (`rate-manager`) | Viewer plus custom rates, rate sources/checks, candidate review/approval, and assignment. |

`firmware.view` exposes release and deployment status. `firmware.manage` uploads
and manages verified artifacts. `firmware.deploy` installs, cancels, retries,
promotes canaries, and authorizes an explicitly confirmed downgrade. Only the
built-in Administrator receives upload/deploy permissions by default; Operator
receives firmware view only.

Custom roles contain a validated subset of the same catalog. Direct per-user permission grants are intentionally not used; effective permissions are the union of assigned active roles, constrained by site scope.

The frontend treats the permission list in `/api/v1/auth/session` as authoritative;
role names are informational only. Home requires `overview.view`, History requires
`history.view`, and Billing requires `costs.view` or `rates.view`. Settings is shown
only when at least one Settings section policy is satisfied, and every action is
checked separately. A user/access revision change cancels and removes cached
permission-sensitive queries before the next account can render them.

The `20260731_0022` migration removes legacy Viewer export grants, records a role
revision and audit event, increments affected users' `access_revision`, and revokes
their active sessions so the stricter permission set takes effect immediately.

Only Administrator receives `status_indicators.manage` by default. Viewer,
Operator, and Rate Manager receive `status_indicators.view`; their effective
rendered indicators are still filtered by each indicator's underlying data
permission. A role layout cannot reveal a value that the role may not access.
