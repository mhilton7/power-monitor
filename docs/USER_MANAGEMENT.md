# Users & Access

Open **Administration > Users & Access**. This page uses the existing local-account and session system; it does not create a second identity store.

## Users

The Users tab shows status, built-in and custom roles, assigned sites, effective-permission count, MFA state, last login, active sessions, creation date, and protected-administrator status. Search by name/email and filter by status, role, site, or MFA. **View access** shows the complete inherited permission matrix, role sources, current sessions, and recent access-change audit events.

Select **Edit access** to change roles and all-site/specific-site scope. Review the before/after summary and session count before saving. Permission increases and Administrator changes require explicit confirmation plus a current-password reauthentication (and TOTP when enabled). A material change takes effect on the server immediately and revokes the target's active sessions.

**Disable** retains the user and historical ownership while denying login and revoking sessions. **Enable** restores login. **Revoke sessions** is idempotent. Normal administration never hard-deletes users.

## Safeguards

- The server, not navigation visibility, enforces every permission and site boundary.
- An actor cannot grant a permission or site they do not possess.
- Protected administrators require `users.manage_protected`.
- The final active Administrator cannot be demoted or disabled.
- Self-restriction requires reauthentication, explicit confirmation, and another active Administrator who retains recovery authority.
- Built-in roles are immutable; clone one to create an editable custom role.
- Optimistic revisions reject stale user and role forms.

## Session behavior

Roles and site assignments are recalculated on every request. Access changes also revoke sessions so browser state cannot retain earlier access. Disabled and revoked sessions fail immediately and require a fresh login after re-enablement.

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
