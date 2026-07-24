# Physical site management

Physical sites are managed at **Administration → Sites & Network → Physical Sites**. A site is a stable UUID-backed boundary for devices, utility accounts, rates, users, network policy, readings, costs, bills, alerts, and audit evidence.

## Lifecycle

| State | Selector behavior | New assignments | Historical data |
| --- | --- | --- | --- |
| `active` | Visible when authorized | Allowed | Available |
| `disabled` | Hidden from ordinary selection | Blocked | Preserved and administratively available |
| `removed` | Hidden from active navigation | Blocked | Preserved under the original site UUID |

Create uses a six-step wizard for identity, timezone/locale, network policy, initial access, optional utility setup, and confirmation. The stable site code is unique and is not the display name.

Edit increments the optimistic-lock revision and requires an audit reason for material changes. Changing timezone warns that tariff boundaries and historical cost projections may need recalculation; it does not rewrite UTC readings.

Set Default transactionally clears the prior default and assigns the selected active site. The top-bar selector refreshes after every lifecycle mutation and falls back to the authorized default or first active site if its stored UUID becomes invalid.

Disable is reversible. It stops ordinary selection and new assignments while signed ingestion and historical retention remain intact. Enable returns the site to active selection after administrator review.

## Removal and dependencies

Remove is soft removal. Before it can succeed, the server returns a dependency inventory containing:

- active sensors and their latest reading state;
- active utility accounts;
- active user access assignments;
- network-policy and rate references;
- open alerts and background jobs;
- retained reading, cost, bill, and audit counts/date ranges.

Every active sensor must be transferred to another authorized active site or archived. Every active utility account must likewise be transferred or archived. User site-access assignments end without deleting the user. Transfers are effective-dated in `device_site_assignments` and `utility_account_site_assignments`, preserving the former site and time boundary.

The administrator must provide a reason, resolve all required actions, review retained history, and type the exact site name or code. Concurrent changes are rejected with the current revision so the impact can be reviewed again.

Removal is blocked when the site is:

- the last active site;
- the current default site (select a replacement first);
- still referenced by unresolved active resources;
- outside the administrator’s site scope.

Restore returns the original UUID to `disabled`. It does not silently reactivate sensors, accounts, users, rates, or network access. Those assignments require explicit review before Enable.

No production hard-delete endpoint is exposed. Database downgrade mechanics are not a user-facing lifecycle operation.

## Permissions

The server checks the specific permission on every mutation:

`sites.view`, `sites.create`, `sites.edit`, `sites.set_default`, `sites.disable`, `sites.remove`, `sites.restore`, `sites.transfer_resources`, and `sites.view_audit`.

Site scope is enforced independently. Unauthorized IDs return a not-found response to avoid leaking site names or metadata. CSRF protection applies to all mutations.

## API

The canonical endpoints are under `/api/v1/admin/sites`:

- `GET /` and `GET /{site_id}`;
- `POST /`;
- `PUT /{site_id}`;
- `POST /{site_id}/set-default`;
- `POST /{site_id}/disable` and `/enable`;
- `GET /{site_id}/dependencies`;
- `POST /{site_id}/transfer-resources`;
- `POST /{site_id}/remove` and `/restore`;
- `GET /{site_id}/audit`.

Legacy site listing remains compatible. The old general-management delete endpoint no longer hard-deletes a site and directs callers to the reviewed lifecycle API.

## Audit and retention

Lifecycle events record actor, request ID, object UUID, revision, reason, source/destination site, effective timestamp, and whether history was preserved. Device lifecycle events additionally record transfers/archives. Secrets, device credentials, signatures, and sensitive request bodies are never included.

Migration `20260724_0012` is additive: it adds lifecycle metadata, permission rows, and effective-dated assignment tables; backfills stable codes/default selection/current assignments; and does not rewrite raw readings.

