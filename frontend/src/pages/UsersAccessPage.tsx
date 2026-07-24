import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Copy, KeyRound, Pencil, Plus, Power, RotateCcw, Search, ShieldCheck, Trash2, UserPlus, X } from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { CanonicalAction } from '../actions'
import { api, ApiError } from '../api'
import { sessionPermissions } from '../access'
import type { AccessRole, ManagedUser, PermissionDefinition, Session, Site } from '../types'
import { EmptyState, ErrorState, formatTime, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'

interface PermissionCatalog {
  permissions: PermissionDefinition[]
  dependencies: Record<string, string[]>
}

interface AccessHistory {
  events: Array<{ id: string; occurred_at: string; actor_id?: string; action: string; outcome: string; details: Record<string, unknown> }>
}

type UserAction = 'enable' | 'disable' | 'remove' | 'restore' | 'revoke'

function problem(error: unknown): string | undefined {
  return error instanceof ApiError ? error.problem.detail : error instanceof Error ? error.message : undefined
}

function toggle(current: string[], value: string): string[] {
  return current.includes(value) ? current.filter((item) => item !== value) : [...current, value]
}

function labelForRole(roleId: string, roles: AccessRole[]): string {
  return roles.find((role) => role.id === roleId)?.display_name ?? roleId
}

export function UsersAccessPage({ session }: { session: Session }) {
  const queryClient = useQueryClient()
  const actorPermissions = sessionPermissions(session)
  const canManageUsers = actorPermissions.has('users.manage')
  const canDisableUsers = actorPermissions.has('users.disable')
  const canRemoveUsers = actorPermissions.has('users.remove')
  const canRestoreUsers = actorPermissions.has('users.restore')
  const canManageRoles = actorPermissions.has('roles.manage')
  const [tab, setTab] = useState<'users' | 'roles'>('users')
  const [notice, setNotice] = useState<string>()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('current')
  const [roleFilter, setRoleFilter] = useState('all')
  const [siteFilter, setSiteFilter] = useState('all')
  const [mfaFilter, setMfaFilter] = useState('all')
  const [protectedFilter, setProtectedFilter] = useState('all')
  const [roleKindFilter, setRoleKindFilter] = useState('all')
  const [selectedUserId, setSelectedUserId] = useState<string>()
  const [editingUser, setEditingUser] = useState(false)
  const [userAction, setUserAction] = useState<UserAction>()
  const [showCreate, setShowCreate] = useState(false)

  const users = useQuery({ queryKey: ['managed-users'], queryFn: () => api<{ users: ManagedUser[] }>('/api/v1/admin/users?include_removed=true') })
  const roles = useQuery({ queryKey: ['managed-roles'], queryFn: () => api<{ roles: AccessRole[] }>('/api/v1/admin/roles') })
  const permissionCatalog = useQuery({ queryKey: ['permission-catalog'], queryFn: () => api<PermissionCatalog>('/api/v1/admin/permissions') })
  const sites = useQuery({ queryKey: ['sites'], queryFn: () => api<Site[]>('/api/v1/sites') })
  const userDetail = useQuery({
    queryKey: ['managed-user', selectedUserId],
    queryFn: () => api<ManagedUser>(`/api/v1/admin/users/${selectedUserId}`),
    enabled: Boolean(selectedUserId),
  })
  const history = useQuery({
    queryKey: ['managed-user-history', selectedUserId],
    queryFn: () => api<AccessHistory>(`/api/v1/admin/users/${selectedUserId}/access-history`),
    enabled: Boolean(selectedUserId),
  })

  const filteredUsers = useMemo(() => (users.data?.users ?? []).filter((user) => {
    const term = search.trim().toLowerCase()
    if (term && !`${user.display_name} ${user.email}`.toLowerCase().includes(term)) return false
    if (status === 'current' && user.status === 'removed') return false
    if (status !== 'current' && status !== 'all' && user.status !== status) return false
    const filterRoles = user.status === 'removed' ? user.former_access?.roles ?? [] : user.roles
    const filterSites = user.status === 'removed' ? user.former_access?.site_ids ?? [] : user.site_ids
    const filterAllSites = user.status === 'removed' ? user.former_access?.all_sites : user.all_sites
    if (roleFilter !== 'all' && !filterRoles.includes(roleFilter)) return false
    if (siteFilter !== 'all' && !filterAllSites && !filterSites.includes(siteFilter)) return false
    if (mfaFilter !== 'all' && user.mfa_enabled !== (mfaFilter === 'enabled')) return false
    if (protectedFilter !== 'all' && user.protected_administrator !== (protectedFilter === 'protected')) return false
    const hasCustomRole = user.roles.some((roleId) => roles.data?.roles.some((role) => role.id === roleId && !role.built_in))
    if (roleKindFilter === 'custom' && !hasCustomRole) return false
    if (roleKindFilter === 'built-in' && hasCustomRole) return false
    return true
  }), [mfaFilter, protectedFilter, roleFilter, roleKindFilter, roles.data, search, siteFilter, status, users.data])

  const refreshUsers = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['managed-users'] }),
      queryClient.invalidateQueries({ queryKey: ['managed-user', selectedUserId] }),
      queryClient.invalidateQueries({ queryKey: ['managed-user-history', selectedUserId] }),
    ])
  }

  const actionMutation = useMutation({
    mutationFn: async ({ action, reason, confirmation, password, totp }: { action: UserAction; reason: string; confirmation: string; password: string; totp: string }) => {
      if (!selectedUserId) throw new Error('Select a user first.')
      const selected = userDetail.data
      if (!selected) throw new Error('The selected user is not loaded.')
      if (password) await api('/api/v1/auth/reauthenticate', { method: 'POST', body: JSON.stringify({ password, totp_code: totp || undefined }) })
      if (action === 'revoke') return api<{ sessions_revoked: number }>(`/api/v1/admin/users/${selectedUserId}/revoke-sessions`, { method: 'POST' })
      return api<{ sessions_revoked: number; changed: boolean }>(`/api/v1/admin/users/${selectedUserId}/${action}`, {
        method: 'POST',
        body: JSON.stringify({
          reason: reason || undefined,
          expected_revision: selected.access_revision,
          ...(action === 'remove' ? { confirmation } : {}),
          confirm_high_risk: action === 'disable' || action === 'remove' || action === 'restore',
        }),
      })
    },
    onSuccess: async (result, variables) => {
      const notices: Record<UserAction, string> = {
        revoke: `Sessions revoked. ${result.sessions_revoked} session(s) ended.`,
        disable: 'User disabled. Sign-in is suspended until the account is enabled.',
        enable: 'User enabled.',
        remove: 'User removed. Sessions and current access assignments were ended; history was preserved.',
        restore: 'User restored in a disabled, unassigned state. Review access before enabling sign-in.',
      }
      setNotice(notices[variables.action])
      setUserAction(undefined)
      await refreshUsers()
    },
  })

  return (
    <>
      <PageTitle eyebrow="Administration · Identity and scope" title="Users & Access" description="Manage user roles, permissions, site access, account status, and active sessions." />
      {notice && <div className="success-banner" role="status"><ShieldCheck size={18} />{notice}<button className="icon-button" aria-label="Dismiss notification" onClick={() => { setNotice(undefined) }}><X size={16} /></button></div>}
      <div className="workspace-tabs" role="tablist" aria-label="Users and roles">
        <button role="tab" aria-selected={tab === 'users'} onClick={() => { setTab('users') }}>Users</button>
        <button role="tab" aria-selected={tab === 'roles'} onClick={() => { setTab('roles') }}>Roles</button>
      </div>
      {tab === 'users' ? (
        <>
          <Panel
            title="User accounts"
            eyebrow="Server-enforced access"
            actions={canManageUsers ? <CanonicalAction id="user.add" surface="panel_header"><button className="button primary" onClick={() => { setShowCreate(true) }}><UserPlus size={16} /> Add user</button></CanonicalAction> : undefined}
          >
            <div className="filter-grid access-filters">
              <label className="search-control"><span>Search users</span><div><Search size={16} /><input value={search} onChange={(event) => { setSearch(event.target.value) }} placeholder="Name or email" /></div></label>
              <label><span>Status</span><select value={status} onChange={(event) => { setStatus(event.target.value) }}><option value="current">Active and disabled</option><option value="active">Active</option><option value="disabled">Disabled</option><option value="removed">Removed</option><option value="all">All</option></select></label>
              <label><span>Role</span><select value={roleFilter} onChange={(event) => { setRoleFilter(event.target.value) }}><option value="all">All roles</option>{roles.data?.roles.map((role) => <option key={role.id} value={role.id}>{role.display_name}</option>)}</select></label>
              <label><span>Site</span><select value={siteFilter} onChange={(event) => { setSiteFilter(event.target.value) }}><option value="all">All sites</option>{sites.data?.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}</select></label>
              <label><span>MFA</span><select value={mfaFilter} onChange={(event) => { setMfaFilter(event.target.value) }}><option value="all">Any MFA status</option><option value="enabled">Enabled</option><option value="disabled">Disabled</option></select></label>
              <label><span>Administrator protection</span><select value={protectedFilter} onChange={(event) => { setProtectedFilter(event.target.value) }}><option value="all">Any protection</option><option value="protected">Protected administrator</option><option value="unprotected">Not protected</option></select></label>
              <label><span>Role type</span><select value={roleKindFilter} onChange={(event) => { setRoleKindFilter(event.target.value) }}><option value="all">Built-in or custom</option><option value="custom">Has custom role</option><option value="built-in">Built-in roles only</option></select></label>
            </div>
            {users.isLoading ? <LoadingState label="Loading users…" /> : users.error ? <ErrorState error={users.error} retry={() => { void users.refetch() }} /> : filteredUsers.length ? (
              <div className="responsive-table"><table className="access-table"><thead><tr><th>User</th><th>Status</th><th>Roles</th><th>Sites</th><th>Permissions</th><th>MFA</th><th>Last login</th><th>Sessions</th><th>Created</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>
                {filteredUsers.map((user) => <tr key={user.id}>
                  <td><div className="user-cell"><span>{user.display_name.slice(0, 1).toUpperCase()}</span><p><strong>{user.display_name}{user.protected_administrator && <ShieldCheck size={14} aria-label="Protected administrator" />}</strong><small>{user.email}</small></p></div></td>
                  <td><StatusPill status={user.status === 'active' ? 'healthy' : user.status === 'removed' ? 'failed' : 'pending'} label={user.status === 'active' ? 'Active' : user.status === 'removed' ? 'Removed' : 'Disabled'} /></td>
                  <td>{(user.status === 'removed' ? user.former_access?.roles ?? [] : user.roles).map((item) => <span className="role-badge" key={item}>{labelForRole(item, roles.data?.roles ?? [])}</span>)}</td>
                  <td>{user.status === 'removed' ? 'Access ended' : user.all_sites ? 'All sites' : user.sites.map((site) => site.name).join(', ') || 'No sites'}</td>
                  <td>{user.permission_count} effective</td><td>{user.mfa_enabled ? 'Enabled' : 'Not enabled'}</td><td>{formatTime(user.last_login_at)}</td><td>{user.active_session_count}</td><td>{formatTime(user.created_at)}</td>
                  <td><button className="button ghost" onClick={() => { setSelectedUserId(user.id); setEditingUser(false) }}>View access</button></td>
                </tr>)}
              </tbody></table></div>
            ) : <EmptyState title="No matching users" message="Adjust the filters or search terms." />}
          </Panel>
          {showCreate && <CreateUserDialog
            roles={roles.data?.roles ?? []}
            onClose={() => { setShowCreate(false) }}
            onCreated={async () => {
              setShowCreate(false)
              setNotice('User created. Access is enforced by the selected built-in roles.')
              await refreshUsers()
            }}
          />}
          {selectedUserId && <UserAccessDialog
            user={userDetail.data}
            loading={userDetail.isLoading}
            error={userDetail.error}
            roles={roles.data?.roles ?? []}
            sites={sites.data ?? []}
            permissions={permissionCatalog.data?.permissions ?? []}
            canManage={canManageUsers}
            canDisable={canDisableUsers}
            canRemove={canRemoveUsers}
            canRestore={canRestoreUsers}
            currentUserId={session.user?.id}
            editing={editingUser}
            setEditing={setEditingUser}
            history={history.data}
            onClose={() => { setSelectedUserId(undefined); setEditingUser(false) }}
            onSaved={async (message) => { setNotice(message); setEditingUser(false); await refreshUsers() }}
            onAction={(action) => { actionMutation.reset(); setUserAction(action) }}
          />}
          {userAction && userDetail.data && <UserActionDialog
            action={userAction}
            user={userDetail.data}
            pending={actionMutation.isPending}
            error={problem(actionMutation.error)}
            onCancel={() => { setUserAction(undefined); actionMutation.reset() }}
            onConfirm={(reason, confirmation, password, totp) => { actionMutation.mutate({ action: userAction, reason, confirmation, password, totp }) }}
          />}
        </>
      ) : <RolesWorkspace
        roles={roles.data?.roles ?? []}
        loading={roles.isLoading || permissionCatalog.isLoading}
        error={roles.error ?? permissionCatalog.error}
        catalog={permissionCatalog.data}
        canManage={canManageRoles}
        actorPermissions={actorPermissions}
        onNotice={setNotice}
      />}
    </>
  )
}

function UserAccessDialog({ user, loading, error, roles, sites, permissions, canManage, canDisable, canRemove, canRestore, currentUserId, editing, setEditing, history, onClose, onSaved, onAction }: {
  user?: ManagedUser
  loading: boolean
  error: unknown
  roles: AccessRole[]
  sites: Site[]
  permissions: PermissionDefinition[]
  canManage: boolean
  canDisable: boolean
  canRemove: boolean
  canRestore: boolean
  currentUserId?: string
  editing: boolean
  setEditing: (value: boolean) => void
  history?: AccessHistory
  onClose: () => void
  onSaved: (message: string) => Promise<void>
  onAction: (action: UserAction) => void
}) {
  const [roleIds, setRoleIds] = useState<string[]>([])
  const [allSites, setAllSites] = useState(false)
  const [siteIds, setSiteIds] = useState<string[]>([])
  const [reason, setReason] = useState('')
  const [confirmHighRisk, setConfirmHighRisk] = useState(false)
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  useEffect(() => {
    if (!user) return
    setRoleIds(user.roles)
    setAllSites(user.all_sites)
    setSiteIds(user.site_ids)
    setReason('')
    setConfirmHighRisk(false)
    setPassword('')
    setTotp('')
  }, [user, editing])
  const effective = useMemo(() => new Set(roles.filter((role) => roleIds.includes(role.id)).flatMap((role) => role.permissions)), [roleIds, roles])
  const priorPermissions = new Set(user?.permissions ?? [])
  const addedPermissions = [...effective].filter((item) => !priorPermissions.has(item)).sort()
  const removedPermissions = [...priorPermissions].filter((item) => !effective.has(item)).sort()
  const addedSites = siteIds.filter((item) => !user?.site_ids.includes(item))
  const removedSites = (user?.site_ids ?? []).filter((item) => !siteIds.includes(item))
  const roleChanged = JSON.stringify([...roleIds].sort()) !== JSON.stringify([...(user?.roles ?? [])].sort())
  const highRisk = addedPermissions.length > 0 || roleIds.includes('admin') !== Boolean(user?.roles.includes('admin')) || user?.id === undefined
  const save = useMutation({
    mutationFn: async () => {
      if (!user) throw new Error('User access is not loaded.')
      if (highRisk && password) await api('/api/v1/auth/reauthenticate', { method: 'POST', body: JSON.stringify({ password, totp_code: totp || undefined }) })
      return api<ManagedUser & { sessions_revoked: number }>(`/api/v1/admin/users/${user.id}/access`, {
        method: 'PUT',
        body: JSON.stringify({ role_ids: roleIds, all_sites: allSites, site_ids: allSites ? [] : siteIds, expected_revision: user.access_revision, reason: reason || undefined, confirm_high_risk: confirmHighRisk }),
      })
    },
    onSuccess: async (result) => { await onSaved(`User access updated. ${result.sessions_revoked} session(s) revoked.`) },
  })

  return <div className="modal-backdrop" role="presentation"><section className="access-dialog" role="dialog" aria-modal="true" aria-label="User access details">
    <header><div><span className="eyebrow">Identity and authorization</span><h2>{user?.display_name ?? 'User access'}</h2>{user && <p>{user.email}</p>}</div><button className="icon-button" onClick={onClose} aria-label="Close access details"><X /></button></header>
    {loading ? <LoadingState /> : error ? <ErrorState error={error} /> : user && <>
      <div className="access-dialog-actions">
        {canManage && user.status !== 'removed' && <button className="button primary" onClick={() => { setEditing(!editing) }}><Pencil size={16} />{editing ? 'Cancel editing' : 'Edit access'}</button>}
        {canManage && user.status !== 'removed' && <button className="button secondary" onClick={() => { onAction('revoke') }}><KeyRound size={16} />Revoke sessions</button>}
        {canDisable && user.status !== 'removed' && (user.is_active ? <CanonicalAction id="user.disable" surface="resource_detail" resourceKey={user.id}><button
          className="button danger"
          disabled={user.protected_account || user.id === currentUserId}
          title={user.protected_account ? 'Protected bootstrap accounts cannot be disabled' : user.id === currentUserId ? 'Use another administrator to disable your account' : undefined}
          onClick={() => { onAction('disable') }}
        ><Power size={16} />Disable</button></CanonicalAction> : <button className="button secondary" onClick={() => { onAction('enable') }}><Power size={16} />Enable</button>)}
        {canRemove && user.status !== 'removed' && <CanonicalAction id="user.remove" surface="resource_detail" resourceKey={user.id}><button
          className="button danger"
          disabled={user.protected_account || user.id === currentUserId}
          title={user.protected_account ? 'Protected bootstrap accounts cannot be removed' : user.id === currentUserId ? 'Use another administrator to remove your account' : undefined}
          onClick={() => { onAction('remove') }}
        ><Trash2 size={16} />Remove user</button></CanonicalAction>}
        {canRestore && user.status === 'removed' && <button className="button secondary" onClick={() => { onAction('restore') }}><RotateCcw size={16} />Restore</button>}
      </div>
      {editing && user.status !== 'removed' ? <form className="access-edit-form" onSubmit={(event) => { event.preventDefault(); save.mutate() }}>
        <fieldset><legend>Role assignments</legend><div className="selection-grid">{roles.filter((role) => !role.archived).map((role) => <label key={role.id}><input type="checkbox" checked={roleIds.includes(role.id)} onChange={() => { setRoleIds(toggle(roleIds, role.id)) }} /><span><strong>{role.display_name}</strong><small>{role.built_in ? 'Built-in template' : 'Custom role'} · {role.permissions.length} permissions</small></span></label>)}</div></fieldset>
        <fieldset><legend>Site access</legend><label className="toggle-row"><span><strong>All sites</strong><small>Includes current and future sites.</small></span><input type="checkbox" checked={allSites} onChange={(event) => { setAllSites(event.target.checked) }} /></label>{!allSites && <div className="selection-grid">{sites.map((site) => <label key={site.id}><input type="checkbox" checked={siteIds.includes(site.id)} onChange={() => { setSiteIds(toggle(siteIds, site.id)) }} /><span><strong>{site.name}</strong><small>{site.timezone}</small></span></label>)}</div>}</fieldset>
        <label><span>Change reason <small>optional</small></span><textarea value={reason} maxLength={500} onChange={(event) => { setReason(event.target.value) }} /></label>
        <section className="difference-summary" aria-label="Access change summary"><h3>Review changes before saving</h3><dl><div><dt>Role changes</dt><dd>{roleChanged ? `${user.roles.map((id) => labelForRole(id, roles)).join(', ')} → ${roleIds.map((id) => labelForRole(id, roles)).join(', ') || 'None'}` : 'None'}</dd></div><div><dt>Permissions added</dt><dd>{addedPermissions.join(', ') || 'None'}</dd></div><div><dt>Permissions removed</dt><dd>{removedPermissions.join(', ') || 'None'}</dd></div><div><dt>Sites added</dt><dd>{allSites && !user.all_sites ? 'All sites' : addedSites.map((id) => sites.find((site) => site.id === id)?.name ?? id).join(', ') || 'None'}</dd></div><div><dt>Sites removed</dt><dd>{!allSites && user.all_sites ? 'All-sites access' : removedSites.map((id) => sites.find((site) => site.id === id)?.name ?? id).join(', ') || 'None'}</dd></div><div><dt>Sessions</dt><dd>{user.active_session_count} active session(s) will be revoked</dd></div></dl></section>
        <details className="permission-preview" open><summary>Effective permission preview ({effective.size})</summary><div className="permission-pills">{permissions.filter((item) => effective.has(item.code)).map((item) => <span key={item.code} title={item.description}>{item.label}</span>)}</div></details>
        {highRisk && <section className="high-risk-warning"><strong>High-risk authorization change</strong><p>This change adds permissions or changes administrator access. Confirm explicitly and reauthenticate before saving.</p><label className="checkbox-line"><input type="checkbox" checked={confirmHighRisk} onChange={(event) => { setConfirmHighRisk(event.target.checked) }} />I reviewed the privilege increase and administrator-lockout risk.</label><div className="form-columns"><label><span>Current password</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => { setPassword(event.target.value) }} required /></label><label><span>MFA code <small>if enabled</small></span><input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" value={totp} onChange={(event) => { setTotp(event.target.value) }} /></label></div></section>}
        {save.error && <div className="form-error" role="alert"><strong>Access was not changed</strong><span>{problem(save.error)}</span></div>}
        <footer><button type="button" className="button secondary" onClick={() => { setEditing(false) }}>Cancel</button><button className="button primary" disabled={save.isPending || !roleIds.length || (highRisk && (!confirmHighRisk || !password))}>{save.isPending ? 'Saving…' : 'Save access'}</button></footer>
      </form> : <div className="access-detail-grid">
        <Panel title={user.status === 'removed' ? 'Lifecycle record' : 'Current access'} eyebrow={user.status === 'removed' ? 'Identity retained' : 'Effective now'}><dl className="detail-list"><div><dt>Status</dt><dd>{user.status}</dd></div><div><dt>Roles</dt><dd>{user.status === 'removed' ? 'No active roles' : user.roles.map((id) => labelForRole(id, roles)).join(', ')}</dd></div><div><dt>Sites</dt><dd>{user.status === 'removed' ? 'No active site access' : user.all_sites ? 'All sites' : user.sites.map((site) => site.name).join(', ') || 'No sites assigned'}</dd></div>{user.status === 'removed' && <><div><dt>Removed</dt><dd>{formatTime(user.removed_at)}</dd></div><div><dt>Removed by</dt><dd>{user.removed_by ?? 'Unknown administrator'}</dd></div><div><dt>Reason</dt><dd>{user.removal_reason ?? 'No reason recorded'}</dd></div><div><dt>Former roles</dt><dd>{user.former_access?.roles.map((id) => labelForRole(id, roles)).join(', ') || 'None'}</dd></div><div><dt>Former site scope</dt><dd>{user.former_access?.all_sites ? 'All sites' : `${user.former_access?.site_ids.length ?? 0} explicit site(s)`}</dd></div><div><dt>Historical records</dt><dd>Preserved</dd></div></>}<div><dt>MFA</dt><dd>{user.mfa_enabled ? 'Enabled' : 'Not enabled'}</dd></div><div><dt>Last login</dt><dd>{formatTime(user.last_login_at)}</dd></div><div><dt>Active sessions</dt><dd>{user.active_session_count}</dd></div></dl></Panel>
        <Panel title={`Effective permissions (${user.permission_count})`} eyebrow="Read only"><div className="permission-pills">{user.permissions.map((code) => <span key={code} title={permissions.find((item) => item.code === code)?.description}>{permissions.find((item) => item.code === code)?.label ?? code}</span>)}</div>{user.permission_sources && Object.entries(user.permission_sources).map(([role, codes]) => <p className="permission-source" key={role}><strong>{labelForRole(role, roles)}:</strong> {codes.join(', ')}</p>)}</Panel>
        <Panel title="Active sessions" eyebrow="Server-side sessions">{user.sessions?.length ? <div className="session-list">{user.sessions.map((item) => <article key={item.id}><strong>{item.source_ip ?? 'Unknown address'}</strong><span>{item.user_agent ?? 'Unknown browser'}</span><small>Last used {formatTime(item.last_seen_at)} · expires {formatTime(item.expires_at)}</small></article>)}</div> : <EmptyState title="No active sessions" message="This account is signed out everywhere." />}</Panel>
        <Panel title="Access change history" eyebrow="Audited">{history?.events.length ? <div className="audit-list">{history.events.map((event) => <article key={event.id}><time>{formatTime(event.occurred_at)}</time><span className={`audit-outcome ${event.outcome}`}>{event.outcome}</span><p><strong>{event.action.replaceAll('_', ' ')}</strong><small>{typeof event.details.reason === 'string' ? event.details.reason : 'No reason provided'}</small></p></article>)}</div> : <EmptyState title="No access changes" message="Role, site, status, and session events appear here." />}</Panel>
      </div>}
    </>}
  </section></div>
}

function CreateUserDialog({ roles: availableRoles, onClose, onCreated }: { roles: AccessRole[]; onClose: () => void; onCreated: () => Promise<void> }) {
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [roleIds, setRoleIds] = useState<string[]>(['viewer'])
  const [confirmAdmin, setConfirmAdmin] = useState(false)
  const [actorPassword, setActorPassword] = useState('')
  const [totp, setTotp] = useState('')
  const builtInRoles = availableRoles.filter((role) => role.built_in && ['admin', 'operator', 'rate-manager', 'viewer'].includes(role.id))
  const create = useMutation({
    mutationFn: async () => {
      if (roleIds.includes('admin')) {
        await api('/api/v1/auth/reauthenticate', {
          method: 'POST',
          body: JSON.stringify({ password: actorPassword, totp_code: totp || undefined }),
        })
      }
      return api('/api/v1/users', {
        method: 'POST',
        body: JSON.stringify({
          display_name: displayName.trim(),
          email: email.trim(),
          password,
          roles: roleIds,
          confirm_high_risk: confirmAdmin,
        }),
      })
    },
    onSuccess: onCreated,
  })
  return <div className="modal-backdrop" role="presentation"><form className="role-editor-dialog" role="dialog" aria-modal="true" aria-label="Create user" onSubmit={(event) => { event.preventDefault(); create.mutate() }}>
    <header><div><span className="eyebrow">New local account</span><h2>Create a user</h2></div><button type="button" className="icon-button" aria-label="Close user form" onClick={onClose}><X /></button></header>
    <div className="form-columns">
      <label><span>Display name</span><input value={displayName} onChange={(event) => { setDisplayName(event.target.value) }} autoComplete="name" required /></label>
      <label><span>Email address</span><input type="email" value={email} onChange={(event) => { setEmail(event.target.value) }} autoComplete="email" required /></label>
    </div>
    <label><span>Temporary password</span><input type="password" value={password} onChange={(event) => { setPassword(event.target.value) }} autoComplete="new-password" minLength={14} required /><small>At least 14 characters using three character classes.</small></label>
    <fieldset><legend>Initial built-in roles</legend><div className="selection-grid">{builtInRoles.map((role) => <label key={role.id}><input type="checkbox" checked={roleIds.includes(role.id)} onChange={() => { setRoleIds(toggle(roleIds, role.id)) }} /><span><strong>{role.display_name}</strong><small>{role.description}</small></span></label>)}</div><small>Custom roles can be assigned after creation from the user access editor.</small></fieldset>
    {roleIds.includes('admin') && <section className="high-risk-warning"><strong>Protected administrator creation</strong><p>Confirm this privilege grant and reauthenticate before creating the account.</p><label className="checkbox-line"><input type="checkbox" checked={confirmAdmin} onChange={(event) => { setConfirmAdmin(event.target.checked) }} />I reviewed the administrator access being granted.</label><div className="form-columns"><label><span>Current password</span><input type="password" autoComplete="current-password" value={actorPassword} onChange={(event) => { setActorPassword(event.target.value) }} required /></label><label><span>MFA code <small>if enabled</small></span><input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" value={totp} onChange={(event) => { setTotp(event.target.value) }} /></label></div></section>}
    {create.error && <div className="form-error" role="alert"><strong>User was not created</strong><span>{problem(create.error)}</span></div>}
    <footer><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button className="button primary" disabled={create.isPending || !roleIds.length || (roleIds.includes('admin') && (!confirmAdmin || !actorPassword))}><Plus size={16} />{create.isPending ? 'Creating…' : 'Create user'}</button></footer>
  </form></div>
}

function UserActionDialog({ action, user, pending, error, onCancel, onConfirm }: { action: UserAction; user: ManagedUser; pending: boolean; error?: string; onCancel: () => void; onConfirm: (reason: string, confirmation: string, password: string, totp: string) => void }) {
  const [reason, setReason] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const protectedAction = action === 'disable' && user.protected_administrator
  const removal = action === 'remove'
  const restore = action === 'restore'
  const requiresReason = removal || restore
  const requiresReauthentication = removal || protectedAction
  const removalConfirmationMatches = confirmation.trim().toLowerCase() === user.email.toLowerCase()
    || confirmation.trim() === user.id.slice(-8)
  const titles: Record<UserAction, string> = { revoke: 'Revoke active sessions', disable: 'Disable user', enable: 'Enable user', remove: 'Remove user', restore: 'Restore user' }
  return <div className="modal-backdrop modal-top" role="presentation"><form className="confirm-dialog" role="dialog" aria-modal="true" aria-label={`${action} user`} onSubmit={(event) => { event.preventDefault(); onConfirm(reason, confirmation, password, totp) }}>
    <header><div><span className="eyebrow">{removal ? 'Deprovision identity' : restore ? 'Recover retained identity' : 'Protected account action'}</span><h2>{titles[action]}</h2></div><button type="button" className="icon-button" onClick={onCancel} aria-label="Close confirmation"><X /></button></header>
    <p>{action === 'revoke' ? `${user.active_session_count} active session(s) will be ended.` : action === 'disable' ? `${user.display_name} will lose sign-in access until re-enabled.` : action === 'enable' ? `${user.display_name} will be allowed to sign in again.` : removal ? 'This user will no longer be able to sign in. Active sessions will be revoked. Current roles and site assignments will be removed. Historical audit and ownership records will be retained.' : 'The identity will return as disabled with no active roles or site assignments. Review access before enabling it.'}</p>
    {removal && <dl className="detail-list removal-summary"><div><dt>User</dt><dd>{user.display_name} · {user.email}</dd></div><div><dt>Current roles</dt><dd>{user.roles.join(', ') || 'None'}</dd></div><div><dt>Assigned sites</dt><dd>{user.all_sites ? 'All sites' : user.sites.map((site) => site.name).join(', ') || 'None'}</dd></div><div><dt>Active sessions</dt><dd>{user.active_session_count}</dd></div><div><dt>MFA</dt><dd>{user.mfa_enabled ? 'Enabled' : 'Not enabled'}</dd></div><div><dt>Last login</dt><dd>{formatTime(user.last_login_at)}</dd></div><div><dt>Historical references</dt><dd>Audit, authorship, ownership, reports, and revisions retained</dd></div></dl>}
    <label><span>Reason <small>{requiresReason ? 'required' : 'optional'}</small></span><textarea value={reason} minLength={requiresReason ? 3 : undefined} maxLength={500} required={requiresReason} onChange={(event) => { setReason(event.target.value) }} /></label>
    {removal && <label><span>Type the exact email address or final eight ID characters</span><input value={confirmation} autoComplete="off" onChange={(event) => { setConfirmation(event.target.value) }} required /><small>{user.email} or {user.id.slice(-8)}</small></label>}
    {requiresReauthentication && <div className="high-risk-warning"><strong>{removal ? 'Removal confirmation and reauthentication are required.' : 'Administrator protection applies.'}</strong><p>The server also rejects self-removal, protected bootstrap changes, and removal or disablement of the last effective administrator.</p><label><span>Current password</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => { setPassword(event.target.value) }} required /></label><label><span>MFA code <small>if enabled</small></span><input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" value={totp} onChange={(event) => { setTotp(event.target.value) }} /></label></div>}
    {error && <div className="form-error" role="alert"><strong>Action failed</strong><span>{error}</span></div>}
    <footer><button type="button" className="button secondary" onClick={onCancel}>Cancel</button><button className={`button ${action === 'disable' || removal ? 'danger' : 'primary'}`} disabled={pending || (requiresReason && reason.trim().length < 3) || (removal && !removalConfirmationMatches) || (requiresReauthentication && !password)}>{pending ? 'Applying…' : removal ? 'Remove user' : restore ? 'Restore user' : 'Confirm'}</button></footer>
  </form></div>
}

function RolesWorkspace({ roles, loading, error, catalog, canManage, actorPermissions, onNotice }: { roles: AccessRole[]; loading: boolean; error: unknown; catalog?: PermissionCatalog; canManage: boolean; actorPermissions: Set<string>; onNotice: (message: string) => void }) {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<AccessRole>()
  const [creating, setCreating] = useState(false)
  const [cloneFrom, setCloneFrom] = useState<AccessRole>()
  const [archiveRole, setArchiveRole] = useState<AccessRole>()
  const archive = useMutation({
    mutationFn: (role: AccessRole) => api(`/api/v1/admin/roles/${role.id}/archive`, { method: 'POST', body: JSON.stringify({ reason: 'Archived from Users & Access' }) }),
    onSuccess: async () => { setArchiveRole(undefined); onNotice('Role archived.'); await queryClient.invalidateQueries({ queryKey: ['managed-roles'] }) },
  })
  return <Panel title="Roles and permission templates" eyebrow="Built-in roles are immutable" actions={canManage ? <button className="button primary" onClick={() => { setCreating(true); setCloneFrom(undefined) }}><Plus size={16} />Create role</button> : undefined}>
    {loading ? <LoadingState /> : error ? <ErrorState error={error} /> : <div className="role-card-grid">{roles.map((role) => <article className={`role-card ${role.archived ? 'archived' : ''}`} key={role.id}><header><div><span className="eyebrow">{role.built_in ? 'Built-in template' : role.archived ? 'Archived custom role' : 'Custom role'}</span><h3>{role.display_name}</h3></div><StatusPill status={role.archived ? 'revoked' : 'healthy'} label={role.archived ? 'Archived' : `Revision ${role.revision}`} /></header><p>{role.description}</p><dl><div><dt>Permissions</dt><dd>{role.permissions.length}</dd></div><div><dt>Assigned users</dt><dd>{role.assigned_user_count}</dd></div></dl><footer><button className="button ghost" onClick={() => { setSelected(role) }}>View</button>{canManage && role.built_in && <button className="button secondary" onClick={() => { setCloneFrom(role); setCreating(true) }}><Copy size={15} />Clone</button>}{canManage && !role.built_in && !role.archived && <button className="button secondary" onClick={() => { setSelected(role) }}><Pencil size={15} />Edit</button>}{canManage && !role.built_in && !role.archived && <button className="button ghost danger-text" disabled={role.assigned_user_count > 0} title={role.assigned_user_count ? 'Reassign users before archiving' : undefined} onClick={() => { setArchiveRole(role) }}><Archive size={15} />Archive</button>}</footer></article>)}</div>}
    {(creating || selected) && <RoleEditor role={selected} cloneFrom={cloneFrom} catalog={catalog} canManage={canManage} actorPermissions={actorPermissions} onClose={() => { setSelected(undefined); setCreating(false); setCloneFrom(undefined) }} onSaved={async () => { setSelected(undefined); setCreating(false); setCloneFrom(undefined); onNotice('Role saved.'); await queryClient.invalidateQueries({ queryKey: ['managed-roles'] }) }} />}
    {archiveRole && <div className="modal-backdrop" role="presentation"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-label="Archive role"><h2>Archive {archiveRole.display_name}?</h2><p>Archived roles remain in revision and audit history and cannot be assigned.</p>{archive.error && <div className="form-error"><strong>Role was not archived</strong><span>{problem(archive.error)}</span></div>}<footer><button className="button secondary" onClick={() => { setArchiveRole(undefined) }}>Cancel</button><button className="button danger" disabled={archive.isPending} onClick={() => { archive.mutate(archiveRole) }}>Archive role</button></footer></section></div>}
  </Panel>
}

function RoleEditor({ role, cloneFrom, catalog, canManage, actorPermissions, onClose, onSaved }: { role?: AccessRole; cloneFrom?: AccessRole; catalog?: PermissionCatalog; canManage: boolean; actorPermissions: Set<string>; onClose: () => void; onSaved: () => Promise<void> }) {
  const source = role ?? cloneFrom
  const editable = canManage && !role?.built_in && !role?.archived
  const [name, setName] = useState(cloneFrom ? `${cloneFrom.display_name} copy` : role?.display_name ?? '')
  const [description, setDescription] = useState(role?.description ?? cloneFrom?.description ?? '')
  const [selected, setSelected] = useState<string[]>(source?.permissions ?? [])
  const [reason, setReason] = useState('')
  const [confirm, setConfirm] = useState(false)
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const highRisk = (catalog?.permissions ?? []).some((permission) => permission.high_risk && selected.includes(permission.code))
  const permissionGroups = useMemo(() => (catalog?.permissions ?? []).reduce<Record<string, PermissionDefinition[]>>((groups, permission) => {
    ;(groups[permission.group] ??= []).push(permission)
    return groups
  }, {}), [catalog])
  const changePermission = (code: string, checked: boolean) => {
    const next = new Set(selected)
    if (checked) {
      next.add(code)
      for (const dependency of catalog?.dependencies[code] ?? []) next.add(dependency)
    } else {
      next.delete(code)
      for (const [permission, dependencies] of Object.entries(catalog?.dependencies ?? {})) if (dependencies.includes(code)) next.delete(permission)
    }
    setSelected([...next])
  }
  const save = useMutation({
    mutationFn: async () => {
      if (highRisk && password) await api('/api/v1/auth/reauthenticate', { method: 'POST', body: JSON.stringify({ password, totp_code: totp || undefined }) })
      const body = JSON.stringify({ display_name: name, description, permissions: selected, expected_revision: role?.revision, reason: reason || undefined, confirm_high_risk: confirm })
      const path = cloneFrom ? `/api/v1/admin/roles/${cloneFrom.id}/clone` : role ? `/api/v1/admin/roles/${role.id}` : '/api/v1/admin/roles'
      return api<AccessRole>(path, { method: role && !cloneFrom ? 'PUT' : 'POST', body })
    },
    onSuccess: onSaved,
  })
  return <div className="modal-backdrop" role="presentation"><form className="role-editor-dialog" role="dialog" aria-modal="true" aria-label="Role editor" onSubmit={(event: FormEvent) => { event.preventDefault(); save.mutate() }}><header><div><span className="eyebrow">{role?.built_in ? 'Built-in role details' : cloneFrom ? `Clone ${cloneFrom.display_name}` : role ? `Custom role revision ${role.revision}` : 'New custom role'}</span><h2>{role?.built_in ? role.display_name : 'Role editor'}</h2></div><button type="button" className="icon-button" onClick={onClose} aria-label="Close role editor"><X /></button></header>
    <div className="form-columns"><label><span>Role name</span><input value={name} minLength={3} maxLength={120} disabled={!editable} onChange={(event) => { setName(event.target.value) }} required /></label><label><span>Change reason <small>optional</small></span><input value={reason} maxLength={500} disabled={!editable} onChange={(event) => { setReason(event.target.value) }} /></label></div><label><span>Description</span><textarea value={description} minLength={3} maxLength={255} disabled={!editable} onChange={(event) => { setDescription(event.target.value) }} required /></label>
    {role?.assigned_user_count ? <div className="high-risk-warning"><strong>{role.assigned_user_count} user(s) are assigned.</strong><p>Saving a material role change revokes their active sessions.</p></div> : null}
    <div className="permission-groups">{Object.entries(permissionGroups).map(([group, items]) => <fieldset key={group}><legend>{group}</legend>{items.map((permission) => <label key={permission.code} title={permission.description}><input type="checkbox" checked={selected.includes(permission.code)} disabled={!editable || !actorPermissions.has(permission.code)} onChange={(event) => { changePermission(permission.code, event.target.checked) }} /><span><strong>{permission.label}{permission.high_risk && ' · Protected'}</strong><small>{permission.code}<br />{permission.description}</small></span></label>)}</fieldset>)}</div>
    {highRisk && editable && <section className="high-risk-warning"><strong>Protected permissions selected</strong><p>Confirm the scope and reauthenticate before saving this role.</p><label className="checkbox-line"><input type="checkbox" checked={confirm} onChange={(event) => { setConfirm(event.target.checked) }} />I confirm this high-risk role definition.</label><div className="form-columns"><label><span>Current password</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => { setPassword(event.target.value) }} required /></label><label><span>MFA code <small>if enabled</small></span><input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" value={totp} onChange={(event) => { setTotp(event.target.value) }} /></label></div></section>}
    {save.error && <div className="form-error" role="alert"><strong>Role was not saved</strong><span>{problem(save.error)}</span></div>}
    <footer><button type="button" className="button secondary" onClick={onClose}>{editable ? 'Cancel' : 'Close'}</button>{editable && <button className="button primary" disabled={save.isPending || !name.trim() || !description.trim() || !selected.length || (highRisk && (!confirm || !password))}>{save.isPending ? 'Saving…' : 'Save role'}</button>}</footer>
  </form></div>
}
