# Modern interface information architecture

Power Monitor uses six permission-aware workspaces. The workspace ID and route are stable identifiers; editable interface text changes only the displayed labels.

| Workspace | Canonical children | Existing feature ownership |
| --- | --- | --- |
| Overview | Overview | Live power, energy, cost, and operational context |
| Monitoring | Devices, Topology, Enrollment | Sensor inventory, physical relationships, secure enrollment |
| Analytics | Usage, History, Costs | Interval analysis, aggregation, exports, billing-cycle projections |
| Billing | Utility Accounts, Rate Plans, Rate Sources | Account setup, custom/managed tariffs, evidence and review |
| Alerts | Alerts | Active alerts, acknowledgement, and alert rules |
| Administration | Access, Sites & Network, Notification Settings, Data Management, Interface, Security | Users/roles, physical sites, network policy, delivery, backups/logs, presentation, health/audit |

Bill Import is intentionally not a Billing tab. A bill used to draft a rate plan has one canonical entry point in the Custom Plan editor. A utility-account statement import is a separate account-scoped action and produces different data.

## Route compatibility

The router preserves bookmarks with replace-style redirects while retaining safe query parameters:

| Legacy route | Canonical route |
| --- | --- |
| `/devices`, `/topology`, `/enrollment` | `/monitoring/...` |
| `/usage`, `/history`, `/costs`, `/reports` | `/analytics/...` |
| `/rates`, `/rates/new`, `/rates/sources` | `/billing/...` |
| `/rates/import-bill` | `/billing/rate-plans/new?bill_import=open` |
| `/admin?tab=...` | Matching Administration or Billing destination |
| Old user, interface-text, status-layout, and system-health routes | Matching Administration destination |

Unknown legacy query parameters are removed. Back/Forward navigation, selected-site state, draft state, and filter state continue to use the canonical browser history and query string.

## Authorization and navigation

The server remains authoritative. The browser derives visible workspaces and tabs from the permission set in the authenticated session, and direct routes retain their permission guards. Empty workspaces are hidden; a workspace index redirects to its first authorized child. Regular viewer/operator roles do not gain Billing or Administration merely because a lower-level read permission exists.

Administration uses one horizontal workspace tab bar and, where needed, one contextual segmented control. It does not reproduce Administration children in the main sidebar. Sites & Network separates Physical Sites, Network Policy, Server Settings, and Observed Devices so the same controls are not rendered twice.

## Responsive and accessible behavior

- At 1440 px and wider, the persistent sidebar and compact top bar remain visible.
- At compact desktop/tablet sizes, the sidebar collapses without horizontal overflow.
- At mobile sizes, a labelled drawer contains the same permission-filtered destinations and current-site control.
- Workspace tabs, contextual tabs, dialogs, and the site wizard use native buttons, labels, focus order, and announced selected/current state.
- Page titles update on navigation. Reduced-motion preferences are respected.
- The global site selector stores a stable site UUID, clears inaccessible or removed selections, and falls back to the authorized default or first active site.

## Status-layout migration

Historical status-layout revisions are retained. Revision `20260724_0012` appends a system migration that maps legacy placements into six semantic zones:

`top_bar`, `overview_summary`, `workspace_header`, `page_summary`, `mobile_status_drawer`, and `administration_diagnostics`.

Runtime materialization also maps an older revision before rendering, so rollback and audit evidence stay useful without reviving obsolete sidebar/footer gaps.

## Visual references

- [Physical Sites before creating a site](screenshots/modern-ui/sites-desktop-wide-before.png)
- [Physical Sites detail after creation](screenshots/modern-ui/sites-desktop-wide-after-create.png)
- [Compact desktop](screenshots/modern-ui/sites-desktop-compact.png)
- [Tablet](screenshots/modern-ui/sites-tablet.png)
- [Mobile](screenshots/modern-ui/sites-mobile.png)
- [Mobile navigation drawer](screenshots/modern-ui/sites-mobile-drawer.png)
