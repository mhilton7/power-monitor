import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, UserPlus, X } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'
import type { Role, User } from '../types'
import { ErrorState, LoadingState, Panel, StatusPill } from './UI'

type ManagedUser = User & { is_active: boolean }

const roleOptions: Array<{ value: Role; label: string; description: string }> = [
  { value: 'admin', label: 'Administrator', description: 'Full user and system control' },
  { value: 'operator', label: 'Operator', description: 'Manage devices and operations' },
  { value: 'rate-manager', label: 'Rate manager', description: 'Manage rate plans and SCE reviews' },
  { value: 'viewer', label: 'Viewer', description: 'Read-only dashboard access' },
]

export function UserManagement({ currentUserId }: { currentUserId?: string }) {
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [actorPassword, setActorPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [confirmAdmin, setConfirmAdmin] = useState(false)
  const [roles, setRoles] = useState<Role[]>(['viewer'])
  const [confirmRemoval, setConfirmRemoval] = useState<string>()
  const users = useQuery({ queryKey: ['users'], queryFn: () => api<ManagedUser[]>('/api/v1/users') })

  const resetForm = () => {
    setDisplayName('')
    setEmail('')
    setPassword('')
    setActorPassword('')
    setTotp('')
    setConfirmAdmin(false)
    setRoles(['viewer'])
  }
  const createUser = useMutation({
    mutationFn: async () => {
      if (roles.includes('admin')) {
        await api('/api/v1/auth/reauthenticate', {
          method: 'POST',
          body: JSON.stringify({ password: actorPassword, totp_code: totp || undefined }),
        })
      }
      return api<{ id: string }>('/api/v1/users', {
        method: 'POST',
        body: JSON.stringify({ display_name: displayName.trim(), email: email.trim(), password, roles, confirm_high_risk: confirmAdmin }),
      })
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['users'] })
      resetForm()
      setShowCreate(false)
    },
  })
  const removeUser = useMutation({
    mutationFn: (userId: string) => api<void>(`/api/v1/users/${userId}`, { method: 'DELETE' }),
    onSuccess: async () => {
      setConfirmRemoval(undefined)
      await queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })
  const toggleRole = (role: Role) => {
    setRoles((current) => current.includes(role) ? current.filter((item) => item !== role) : [...current, role])
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (roles.length) createUser.mutate()
  }
  const createProblem = createUser.error instanceof ApiError ? createUser.error.problem : undefined
  const removeProblem = removeUser.error instanceof ApiError ? removeUser.error.problem : undefined

  return (
    <Panel
      title="Users & role assignments"
      eyebrow="Backend-enforced authorization"
      actions={<button className="button primary" onClick={() => { setShowCreate((value) => !value); createUser.reset() }}><UserPlus size={16} /> Add user</button>}
    >
      {showCreate && (
        <form className="user-create-form" onSubmit={submit}>
          <header>
            <div><span className="eyebrow">New local account</span><h3>Create a user</h3></div>
            <button type="button" className="icon-button" aria-label="Close user form" onClick={() => { setShowCreate(false); createUser.reset() }}><X /></button>
          </header>
          {createProblem && <div className="form-error" role="alert"><strong>{createProblem.title}</strong><span>{createProblem.detail}</span></div>}
          <div className="form-columns">
            <label><span>Display name</span><input value={displayName} onChange={(event) => { setDisplayName(event.target.value) }} autoComplete="name" required /></label>
            <label><span>Email address</span><input type="email" value={email} onChange={(event) => { setEmail(event.target.value) }} autoComplete="email" required /></label>
          </div>
          <label><span>Temporary password</span><input type="password" value={password} onChange={(event) => { setPassword(event.target.value) }} autoComplete="new-password" minLength={14} required /><small>At least 14 characters using three character classes.</small></label>
          <fieldset className="role-options">
            <legend>Roles</legend>
            {roleOptions.map((option) => (
              <label key={option.value}>
                <input type="checkbox" checked={roles.includes(option.value)} onChange={() => { toggleRole(option.value) }} />
                <span><strong>{option.label}</strong><small>{option.description}</small></span>
              </label>
            ))}
          </fieldset>
          {roles.includes('admin') && <section className="high-risk-warning"><strong>Protected administrator creation</strong><p>Confirm this privilege grant and reauthenticate before creating the account.</p><label className="checkbox-line"><input type="checkbox" checked={confirmAdmin} onChange={(event) => { setConfirmAdmin(event.target.checked) }} />I reviewed the administrator access being granted.</label><div className="form-columns"><label><span>Current password</span><input type="password" autoComplete="current-password" value={actorPassword} onChange={(event) => { setActorPassword(event.target.value) }} required /></label><label><span>MFA code <small>if enabled</small></span><input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" value={totp} onChange={(event) => { setTotp(event.target.value) }} /></label></div></section>}
          {!roles.length && <p className="field-error">Select at least one role.</p>}
          <footer><button type="button" className="button secondary" onClick={() => { setShowCreate(false) }}>Cancel</button><button className="button primary" disabled={createUser.isPending || !roles.length || (roles.includes('admin') && (!confirmAdmin || !actorPassword))}><Plus size={16} /> {createUser.isPending ? 'Creating…' : 'Create user'}</button></footer>
        </form>
      )}

      {removeProblem && <div className="form-error" role="alert"><strong>{removeProblem.title}</strong><span>{removeProblem.detail}</span></div>}
      {users.isLoading ? <LoadingState /> : users.error ? <ErrorState error={users.error} retry={() => { void users.refetch() }} /> : (
        <div className="responsive-table">
          <table>
            <thead><tr><th>User</th><th>Roles</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {users.data?.map((user) => {
                const isCurrent = user.id === currentUserId
                return (
                  <tr key={user.id}>
                    <td><div className="user-cell"><span>{user.display_name.slice(0, 1).toUpperCase()}</span><p><strong>{user.display_name}{isCurrent && <small className="current-user">You</small>}</strong><small>{user.email}</small></p></div></td>
                    <td>{user.roles.map((role) => <span className="role-badge" key={role}>{role}</span>)}</td>
                    <td><StatusPill status={user.is_active ? 'healthy' : 'failed'} label={user.is_active ? 'Active' : 'Removed'} /></td>
                    <td className="table-actions">
                      {confirmRemoval === user.id ? (
                        <div className="confirm-action"><span>Remove access?</span><button className="button ghost" onClick={() => { setConfirmRemoval(undefined) }}>Cancel</button><button className="button danger" onClick={() => { removeUser.mutate(user.id) }} disabled={removeUser.isPending}>Remove</button></div>
                      ) : (
                        <button className="button ghost danger-text" disabled={!user.is_active || isCurrent || user.roles.includes('admin')} title={isCurrent ? 'Another administrator must remove your account' : user.roles.includes('admin') ? 'Use Users & Access for protected administrator changes' : undefined} onClick={() => { setConfirmRemoval(user.id); removeUser.reset() }}><Trash2 size={15} /> {isCurrent ? 'Current account' : 'Remove'}</button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
