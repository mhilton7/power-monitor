import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clipboard, Clock3, KeyRound, Plus, RadioTower, ShieldCheck, Trash2 } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'
import type { Site } from '../types'
import { EmptyState, ErrorState, formatTime, PageTitle, Panel, StatusPill } from '../components/UI'

interface Token {
  id: string
  token?: string
  expires_at: string
  created_at?: string
  consumed_at?: string
  revoked_at?: string
  preassignment: Record<string, unknown>
}

const remaining = (expiresAt: string, now: number) => Math.max(0, Math.floor((new Date(expiresAt).getTime() - now) / 1000))

export function EnrollmentPage() {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [siteId, setSiteId] = useState('')
  const [role, setRole] = useState('submeter')
  const [mode, setMode] = useState('push')
  const [ct, setCt] = useState('100')
  const [createdTokens, setCreatedTokens] = useState<Token[]>([])
  const [now, setNow] = useState(Date.now())
  const sites = useQuery({ queryKey: ['sites'], queryFn: () => api<Site[]>('/api/v1/sites') })
  const tokens = useQuery({ queryKey: ['tokens'], queryFn: () => api<Token[]>('/api/v1/enrollment-tokens'), refetchInterval: 5000 })

  useEffect(() => { if (!siteId && sites.data?.[0]) setSiteId(sites.data[0].id) }, [siteId, sites.data])
  useEffect(() => {
    if (!createdTokens.length) return
    const timer = window.setInterval(() => { setNow(Date.now()) }, 1000)
    return () => { window.clearInterval(timer) }
  }, [createdTokens.length])

  const create = useMutation({
    mutationFn: () => api<Token>('/api/v1/enrollment-tokens', {
      method: 'POST',
      body: JSON.stringify({ site_id: siteId, name: name.trim() || null, measurement_role: role, ct_rating_amps: ct, connection_mode: mode, expires_in_seconds: 600 }),
    }),
    onSuccess: async (token) => {
      setCreatedTokens((current) => [token, ...current])
      setName('')
      setNow(Date.now())
      await queryClient.invalidateQueries({ queryKey: ['tokens'] })
    },
  })
  const revoke = useMutation({
    mutationFn: (tokenId: string) => api<void>(`/api/v1/enrollment-tokens/${tokenId}`, { method: 'DELETE' }),
    onSuccess: async (_, tokenId) => {
      setCreatedTokens((current) => current.filter((token) => token.id !== tokenId))
      await queryClient.invalidateQueries({ queryKey: ['tokens'] })
    },
  })
  const submit = (event: FormEvent) => { event.preventDefault(); create.mutate() }
  const createProblem = create.error instanceof ApiError ? create.error.problem : undefined
  const revokeProblem = revoke.error instanceof ApiError ? revoke.error.problem : undefined

  return (
    <>
      <PageTitle eyebrow="Secure onboarding" title="Multi-device enrollment" description="Prepare a separate short-lived, single-use token for every ESP32 sensor. Each claimed device receives its own permanent credential." />
      <aside className="fleet-scope enrollment-scope">
        <span><ShieldCheck size={18} /></span>
        <p><strong>Enroll several sensors in one session</strong><small>Generate the next token without losing the unclaimed tokens already on screen.</small></p>
        <StatusPill status="healthy" label="Unique credentials" />
      </aside>

      <div className="enrollment-grid">
        <Panel title="Prepare a sensor" eyebrow="Administrator action">
          <form className="stack-form" onSubmit={submit}>
            {createProblem && <div className="form-error" role="alert"><strong>{createProblem.title}</strong><span>{createProblem.detail}</span></div>}
            <label><span>Friendly name</span><input placeholder="Garage HVAC" value={name} onChange={(event) => { setName(event.target.value) }} /></label>
            <label><span>Site</span><select value={siteId} onChange={(event) => { setSiteId(event.target.value) }}>{sites.data?.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}</select></label>
            <div className="form-columns">
              <label><span>Measurement role</span><select value={role} onChange={(event) => { setRole(event.target.value) }}><option value="submeter">Submeter</option><option value="branch">Branch</option><option value="service-leg">Service leg</option><option value="main">Main</option><option value="informational">Informational</option></select></label>
              <label><span>Connection mode</span><select value={mode} onChange={(event) => { setMode(event.target.value) }}><option value="push">Push / outbound</option><option value="hybrid">Hybrid</option><option value="pull">Pull</option></select></label>
            </div>
            <label><span>CT rating</span><div className="input-unit"><input type="number" min="1" max="5000" value={ct} onChange={(event) => { setCt(event.target.value) }} /><span>A</span></div></label>
            <button className="button primary" disabled={create.isPending || !siteId}><Plus size={17} /> {create.isPending ? 'Generating…' : 'Add enrollment token'}</button>
          </form>
        </Panel>

        <Panel title={createdTokens.length ? `${createdTokens.length} ${createdTokens.length === 1 ? 'token' : 'tokens'} ready` : 'How enrollment works'} eyebrow={createdTokens.length ? 'Plaintext shown once' : 'pm-protocol/1.0.0'}>
          {createdTokens.length ? (
            <div className="generated-token-list">
              {createdTokens.map((token) => {
                const seconds = remaining(token.expires_at, now)
                const assignedName = typeof token.preassignment.name === 'string' ? token.preassignment.name : 'Unnamed sensor'
                return (
                  <article className="token-ready" key={token.id}>
                    <header><div><strong>{assignedName}</strong><small>Single-use enrollment token</small></div><div className="token-countdown"><Clock3 /><strong>{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}</strong><span>remaining</span></div></header>
                    <label><span>Enrollment token</span><div className="copy-field"><code>{token.token}</code><button className="icon-button" type="button" onClick={() => void navigator.clipboard.writeText(token.token ?? '')} aria-label={`Copy token for ${assignedName}`}><Clipboard /></button></div></label>
                    <button type="button" className="button ghost danger-text token-revoke" onClick={() => { revoke.mutate(token.id) }}><Trash2 size={15} /> Revoke token</button>
                  </article>
                )
              })}
              <div className="secret-warning"><KeyRound /><p><strong>Permanent device secrets are never shown here.</strong><small>Each token can be claimed only once and expires after ten minutes.</small></p></div>
            </div>
          ) : (
            <div className="enrollment-steps">
              <div><span><ShieldCheck /></span><p><strong>Short-lived claim</strong><small>Ten minutes by default; one successful use.</small></p></div>
              <div><span><KeyRound /></span><p><strong>Unique credential</strong><small>Directional HMAC keys derived with HKDF.</small></p></div>
              <div><span><RadioTower /></span><p><strong>Signed first heartbeat</strong><small>Identity, current address, meter, SD, and time health.</small></p></div>
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Enrollment queue" eyebrow="Live claim state">
        {revokeProblem && <div className="form-error" role="alert"><strong>{revokeProblem.title}</strong><span>{revokeProblem.detail}</span></div>}
        {tokens.error ? <ErrorState error={tokens.error} retry={() => { void tokens.refetch() }} /> : tokens.data?.length ? (
          <div className="responsive-table"><table><thead><tr><th>Assignment</th><th>Created</th><th>Expires</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{tokens.data.map((token) => {
            const assignedName = typeof token.preassignment.name === 'string' ? token.preassignment.name : 'Unnamed sensor'
            const pending = !token.consumed_at && !token.revoked_at && new Date(token.expires_at) >= new Date()
            return <tr key={token.id}><td>{assignedName}</td><td>{formatTime(token.created_at)}</td><td>{formatTime(token.expires_at)}</td><td><StatusPill status={token.consumed_at ? 'healthy' : token.revoked_at || !pending ? 'failed' : 'pending'} label={token.consumed_at ? 'Claimed' : token.revoked_at ? 'Revoked' : pending ? 'Pending' : 'Expired'} /></td><td className="table-actions">{pending && <button className="button ghost danger-text" onClick={() => { revoke.mutate(token.id) }} disabled={revoke.isPending}><Trash2 size={15} /> Revoke</button>}</td></tr>
          })}</tbody></table></div>
        ) : <EmptyState title="No enrollment tokens" message="Create one token for each physical or simulated sensor that is ready to claim it." />}
      </Panel>
    </>
  )
}
