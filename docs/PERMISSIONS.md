# Permission reference

Permissions are stable server identifiers. Display-text customization never renames them. Dependencies are validated server-side, including `users.manage` → `users.view`, `roles.manage` → `roles.view`, `rates.approve_candidates` → `rates.review_candidates` and `rates.view`, `backups.restore` → `backups.view`, `interface_text.manage` → `interface_text.view`, and `status_indicators.manage` → `status_indicators.view`.

| Area | Permission codes |
|---|---|
| Dashboard/data | `overview.view`, `usage.view`, `history.view`, `history.export`, `costs.view`, `costs.export` |
| Sites/devices | `sites.view`, `sites.manage`, `utility_accounts.view`, `utility_accounts.manage`, `network.view`, `network.manage`, `topology.view`, `topology.manage`, `devices.view`, `devices.manage`, `devices.remove`, `enrollment.view`, `enrollment.manage`, `firmware.view`, `firmware.manage` |
| Rates | `rates.view`, `rates.manage_custom`, `rates.manage_sources`, `rates.check_sources`, `rates.review_candidates`, `rates.approve_candidates`, `rates.assign` |
| Alerts | `alerts.view`, `alerts.acknowledge`, `alerts.manage_rules`, `alerts.manage_delivery` |
| Backup/logs | `backups.view`, `backups.create`, `backups.restore`, `logs.export` |
| Administration | `users.view`, `users.manage`, `users.manage_protected`, `roles.view`, `roles.manage`, `audit.view`, `settings.view`, `settings.manage`, `interface_text.view`, `interface_text.manage`, `status_indicators.view`, `status_indicators.manage` |

## Built-in matrix

| Built-in role | Effective template |
|---|---|
| Administrator (`admin`) | Every catalog permission, including protected-user management. |
| Regular User / Read-Only Viewer (`viewer`) | Overview, Usage, History/export, Costs/export, assigned Sites, Topology, Devices, Rates, Alerts, and published status-layout view. |
| Operator (`operator`) | Viewer plus topology and device management, enrollment, firmware view, alert acknowledgement, and alert-rule management. Device removal remains excluded and separately protected. |
| Rate Manager (`rate-manager`) | Viewer plus custom rates, rate sources/checks, candidate review/approval, and assignment. |

Custom roles contain a validated subset of the same catalog. Direct per-user permission grants are intentionally not used; effective permissions are the union of assigned active roles, constrained by site scope.

Only Administrator receives `status_indicators.manage` by default. Viewer,
Operator, and Rate Manager receive `status_indicators.view`; their effective
rendered indicators are still filtered by each indicator's underlying data
permission. A role layout cannot reveal a value that the role may not access.
