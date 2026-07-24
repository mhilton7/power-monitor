# Users & Access

Open the main-sidebar **Users & Access** page. It is the only human-user and
role-management interface and uses the existing local-account and session
system; it does not create a second identity store.

The redundant Administration-local **Users & roles** component was removed.
Its unique **Add user** capability now lives in **Users & Access** beside the
existing user, custom-role, effective-access, session, site-scope, and audit
tools. The legacy query routes `/admin?tab=users`, `users-roles`, or `roles`,
plus `/administration/users`, `/administration/users-roles`, and
`/administration/roles`, use replace-style redirects to
`/administration/access` (`/administration/users-access` redirects for compatibility). Safe search/filter query parameters are
preserved. The redirect never mounts a second list or duplicates API requests.

## Feature-parity inventory

| Capability | Canonical location |
|---|---|
| Add a local user with a built-in role | Users & Access → Add user |
| Search and lifecycle/role/site/MFA filters | Users & Access → Users |
| Effective permissions and inherited role sources | View access |
| Role and all-site/specific-site editing | Edit access |
| Session inventory and idempotent revocation | View access / Revoke sessions |
| Built-in role details and custom role create/clone/archive | Users & Access → Roles |
| Access and lifecycle audit history | View access |
| Disable, Enable, Remove, and controlled Restore | User row and access dialog |

No feature remains exclusive to the removed Administration component.

## Users

The Users tab shows status, built-in and custom roles, assigned sites,
effective-permission count, MFA state, last login, active sessions, creation
date, and protected-administrator status. Search by name/email and filter by
active, disabled, or removed status, role, site, or MFA. **View access** shows
the complete inherited permission matrix, role sources, current sessions, and
recent access-change audit events.

Select **Edit access** to change roles and all-site/specific-site scope. Review the before/after summary and session count before saving. Permission increases and Administrator changes require explicit confirmation plus a current-password reauthentication (and TOTP when enabled). A material change takes effect on the server immediately and revokes the target's active sessions.

The lifecycle states are **Active**, **Disabled**, and **Removed**:

- **Disable** is reversible and idempotent. It denies login and revokes every
  active session while retaining the display name, normalized email, roles,
  site assignments, history, and ownership references. **Enable** restores
  login only after the account still has an explicitly reviewed role.
- **Remove user** is an idempotent soft deprovisioning action, never a cascading
  database delete. It revokes sessions, denies login, records the actor,
  timestamp, optional reason, and stable user ID, snapshots the former role/site
  summary, and removes active role and site assignments. Readings, costs, rate
  decisions, alerts, reports, backups, audit events, revisions, and historical
  authorship remain intact.
- **Restore user** returns the retained identity to **Disabled** with no roles,
  sites, sessions, or refresh-token reuse. An administrator must explicitly
  review and assign new access before enabling it.

Removed accounts appear only with the **Removed** or **All** status filter.
Their retained identity, removal evidence, former access, and preservation
state remain visible, but they are excluded from active lists, counts, session
totals, and assignment selectors. A removed normalized email remains reserved;
creating a duplicate account returns an instruction to restore the retained
identity instead.

The Remove dialog shows the target identity, current access, sessions, MFA and
last-login context, and the historical records that remain. It requires a
reason, the exact email or immutable ID suffix, current administrator
reauthentication, an explicit high-risk confirmation, and the current access
revision. It prevents duplicate submission and uses a visible progress/result
state.

## Safeguards

- The server, not navigation visibility, enforces every permission and site boundary.
- An actor cannot grant a permission or site they do not possess.
- Protected administrators require `users.manage_protected` for ordinary
  access edits and cannot be disabled or removed through the dashboard.
- The final active recovery-capable Administrator cannot be demoted, disabled,
  or removed. Recovery capability requires effective `users.manage`,
  `roles.manage`, `users.disable`, `users.remove`, and `users.restore` with
  all-site scope.
- A signed-in administrator cannot remove their own account.
- Self-restriction requires reauthentication, explicit confirmation, and another active Administrator who retains recovery authority.
- Built-in roles are immutable; clone one to create an editable custom role.
- Optimistic revisions reject stale user and role forms.
- `users.view`, `users.manage`, `users.disable`, `users.remove`, and
  `users.restore` are checked by the API; button visibility is not an
  authorization boundary.

## Session behavior

Roles and site assignments are recalculated on every request. Access changes
also revoke sessions so browser state cannot retain earlier access. Disabled,
removed, and revoked sessions fail immediately. Re-enablement or restoration
never revives an old session or refresh token.

## API compatibility

Canonical lifecycle actions are:

- `POST /api/v1/admin/users/{user_id}/disable`
- `POST /api/v1/admin/users/{user_id}/enable`
- `POST /api/v1/admin/users/{user_id}/remove`
- `POST /api/v1/admin/users/{user_id}/restore`
- `GET /api/v1/admin/users?status=removed`

The older `DELETE /api/v1/users/{user_id}` endpoint remains only for API
compatibility and retains its historical reversible-disable semantics. It is
not the dashboard Remove action and does not hard-delete a user.

## Emergency administrator recovery

Recovery is deliberately unavailable over HTTP. It promotes an existing account; it never creates a hidden user or static password.

In the TrueNAS web interface, open **Apps > Installed > power-monitor > Workloads > api > Shell**. First verify the target without changing it:

```text
python /srv/tools/recover-admin.py --email owner@example.com --confirm owner@example.com --dry-run
```

Then recover the existing account:

```text
python /srv/tools/recover-admin.py --email owner@example.com --confirm owner@example.com
```

Add `--reset-password` only when necessary; the tool prompts privately twice, enforces the normal password policy, never accepts a password on the command line, and never prints it. Recovery enables the account, assigns only the built-in Administrator role, restores all-site scope, revokes all sessions, increments the access revision, and writes an immutable `user.emergency_admin_recovered` audit event. Sign in again and review that event. Do not use direct `docker` commands in the TrueNAS shell.
