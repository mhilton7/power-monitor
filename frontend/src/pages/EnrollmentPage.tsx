import { useMutation, useQuery } from '@tanstack/react-query'
import { Clipboard, Clock3, KeyRound, Plus, RadioTower, ShieldCheck } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../api'
import type { Site } from '../types'
import { EmptyState, formatTime, PageTitle, Panel, StatusPill } from '../components/UI'

interface Token { id: string; token?: string; expires_at: string; created_at?: string; consumed_at?: string; revoked_at?: string; preassignment: Record<string, unknown> }

export function EnrollmentPage() {
  const [name, setName] = useState('')
  const [siteId, setSiteId] = useState('')
  const [role, setRole] = useState('submeter')
  const [mode, setMode] = useState('push')
  const [ct, setCt] = useState('100')
  const [created, setCreated] = useState<Token>()
  const [seconds, setSeconds] = useState(0)
  const sites = useQuery({ queryKey: ['sites'], queryFn: () => api<Site[]>('/api/v1/sites') })
  const tokens = useQuery({ queryKey: ['tokens'], queryFn: () => api<Token[]>('/api/v1/enrollment-tokens'), refetchInterval: 5000 })
  useEffect(() => { if (!siteId && sites.data?.[0]) setSiteId(sites.data[0].id) }, [siteId, sites.data])
  useEffect(() => { if (!created) return; const tick = () => { setSeconds(Math.max(0, Math.floor((new Date(created.expires_at).getTime() - Date.now()) / 1000))); }; tick(); const timer = window.setInterval(tick, 1000); return () => { window.clearInterval(timer); } }, [created])
  const create = useMutation({ mutationFn: () => api<Token>('/api/v1/enrollment-tokens', { method: 'POST', body: JSON.stringify({ site_id: siteId, name: name || null, measurement_role: role, ct_rating_amps: ct, connection_mode: mode, expires_in_seconds: 600 }) }), onSuccess: (token) => { setCreated(token); void tokens.refetch() } })
  const submit = (event: FormEvent) => { event.preventDefault(); create.mutate() }
  return (
    <>
      <PageTitle eyebrow="Secure onboarding" title="Device enrollment" description="Single-use, short-lived tokens create unique device credentials. There is no fleet-wide password." />
      <div className="enrollment-grid"><Panel title="Prepare an enrollment token" eyebrow="Administrator action"><form className="stack-form" onSubmit={submit}><label><span>Friendly name</span><input placeholder="Garage HVAC" value={name} onChange={(event) => { setName(event.target.value); }} /></label><label><span>Site</span><select value={siteId} onChange={(event) => { setSiteId(event.target.value); }}>{sites.data?.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}</select></label><div className="form-columns"><label><span>Measurement role</span><select value={role} onChange={(event) => { setRole(event.target.value); }}><option value="submeter">Submeter</option><option value="branch">Branch</option><option value="service-leg">Service leg</option><option value="main">Main</option><option value="informational">Informational</option></select></label><label><span>Connection mode</span><select value={mode} onChange={(event) => { setMode(event.target.value); }}><option value="push">Push / outbound</option><option value="hybrid">Hybrid</option><option value="pull">Pull</option></select></label></div><label><span>CT rating</span><div className="input-unit"><input type="number" min="1" max="5000" value={ct} onChange={(event) => { setCt(event.target.value); }} /><span>A</span></div></label><button className="button primary" disabled={create.isPending}><Plus size={17} /> Create 10-minute token</button></form></Panel><Panel title={created ? 'Token ready' : 'How enrollment works'} eyebrow={created ? 'Expires after one claim' : 'pm-protocol/1.0.0'}>{created ? <div className="token-ready"><div className="token-countdown"><Clock3 /><strong>{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}</strong><span>remaining</span></div><label><span>Enrollment token</span><div className="copy-field"><code>{created.token}</code><button className="icon-button" onClick={() => void navigator.clipboard.writeText(created.token ?? '')} aria-label="Copy token"><Clipboard /></button></div></label><ol><li><span>1</span>Connect the ESP32-S3 agent to trusted Wi-Fi.</li><li><span>2</span>Enter this server’s validated HTTPS URL and the token.</li><li><span>3</span>Confirm PZEM and mandatory microSD health.</li><li><span>4</span>The device claims once and stores its unique secret.</li></ol><div className="secret-warning"><KeyRound /><p><strong>The permanent secret is never shown here.</strong><small>After claim, only its fingerprint appears in administration.</small></p></div></div> : <div className="enrollment-steps"><div><span><ShieldCheck /></span><p><strong>Short-lived claim</strong><small>Ten minutes by default; one successful use.</small></p></div><div><span><KeyRound /></span><p><strong>Unique credential</strong><small>Directional HMAC keys derived with HKDF.</small></p></div><div><span><RadioTower /></span><p><strong>Signed first heartbeat</strong><small>Identity, address, live data, SD, meter, and time.</small></p></div></div>}</Panel></div>
      <Panel title="Recent enrollment tokens" eyebrow="Live claim state">{tokens.data?.length ? <div className="responsive-table"><table><thead><tr><th>Assignment</th><th>Created</th><th>Expires</th><th>Status</th></tr></thead><tbody>{tokens.data.map((token) => { const assignedName = typeof token.preassignment.name === 'string' ? token.preassignment.name : 'Unnamed sensor'; return <tr key={token.id}><td>{assignedName}</td><td>{formatTime(token.created_at)}</td><td>{formatTime(token.expires_at)}</td><td><StatusPill status={token.consumed_at ? 'healthy' : token.revoked_at || new Date(token.expires_at) < new Date() ? 'failed' : 'pending'} label={token.consumed_at ? 'Claimed' : token.revoked_at ? 'Revoked' : new Date(token.expires_at) < new Date() ? 'Expired' : 'Pending'} /></td></tr> })}</tbody></table></div> : <EmptyState title="No enrollment tokens" message="Create one when a physical or simulated sensor is ready to claim it." />}</Panel>
    </>
  )
}
