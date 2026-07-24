import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Network, Plus, Search, ShieldAlert, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'
import type { SensorNetworkPolicy, Site } from '../types'
import { EmptyState, ErrorState, LoadingState, Panel, StatusPill, formatTime } from './UI'

interface ObservedDevice { device_id: string; device_name: string; site_id: string; address: string; last_seen_at: string; validation_error?: string }
interface TestResult { allowed: boolean; address: string; direction: string; mode: string; reason: string; matching_rule?: string }
interface NetworkRuntime { sensor_server_url: string; tls_verification_required: boolean; certificate_trust: string; default_device_api_port: number; communication_modes: string[]; mdns_authoritative: boolean; heartbeat_expectation_seconds: number; stale_device_seconds: number; server_time: string; server_timezone: string; trusted_forwarded_headers: boolean; address_source: string }

const policyLabels: Record<string, { label: string; help: string }> = {
  allow_listed_private: { label: 'Allow listed private networks only', help: 'Only enabled private CIDRs below are accepted. Recommended for sensor VLANs.' },
  allow_all_private: { label: 'Allow all private networks', help: 'Allows RFC1918 IPv4 and IPv6 ULA while rejecting loopback, link-local, multicast, metadata, and public ranges.' },
  deny_all: { label: 'Deny all device network access', help: 'Locks down this direction for maintenance. Signed authentication remains required when access is restored.' },
  legacy_authenticated_any: { label: 'Legacy signed ingress (review required)', help: 'Preserves the former network-unrestricted ingress behavior. Every request still requires a valid token or HMAC signature.' },
  legacy_public_and_listed: { label: 'Legacy public pull opt-in (review required)', help: 'Preserves the former public-polling opt-in plus listed networks. Select a safer reviewed mode.' },
}

const policyLabel = (value: string) => policyLabels[value] ?? { label: value, help: '' }

export function NetworkPolicyPanel({
  sites,
  initialSiteId,
  section = 'all',
}: {
  sites: Site[]
  initialSiteId?: string
  section?: 'all' | 'policy' | 'observed' | 'server'
}) {
  const queryClient = useQueryClient()
  const [siteId, setSiteId] = useState(initialSiteId ?? sites[0]?.id ?? '')
  const [direction, setDirection] = useState<'device_ingress' | 'server_pull'>('device_ingress')
  const [mode, setMode] = useState('')
  const [reason, setReason] = useState('Administrator reviewed explicit sensor-network behavior')
  const [cidr, setCidr] = useState('')
  const [label, setLabel] = useState('Sensor VLAN')
  const [testAddress, setTestAddress] = useState('')
  const [testResult, setTestResult] = useState<TestResult>()
  const [editingCidr, setEditingCidr] = useState<string>()
  const [editNetwork, setEditNetwork] = useState('')
  const [editLabel, setEditLabel] = useState('')
  const [success, setSuccess] = useState('')
  const showPolicy = section === 'all' || section === 'policy'
  const showObserved = section === 'all' || section === 'observed'
  const showServer = section === 'all' || section === 'server'
  const policies = useQuery({ queryKey: ['network-policies'], queryFn: () => api<SensorNetworkPolicy[]>('/api/v1/admin/network/policies'), enabled: showPolicy })
  const runtime = useQuery({ queryKey: ['network-runtime'], queryFn: () => api<NetworkRuntime>('/api/v1/admin/network/runtime'), enabled: showServer })
  const observed = useQuery({ queryKey: ['observed-devices', siteId], queryFn: () => api<ObservedDevice[]>(`/api/v1/admin/network/observed-devices?site_id=${siteId}`), enabled: Boolean(siteId) && showObserved })
  const policy = useMemo(() => policies.data?.find((item) => item.site_id === siteId && item.direction === direction), [direction, policies.data, siteId])
  useEffect(() => { if (policy) setMode(policy.mode) }, [policy])

  const savePolicy = useMutation({
    mutationFn: () => api<SensorNetworkPolicy>(`/api/v1/admin/network/policies/${policy?.id}`, { method: 'PUT', body: JSON.stringify({ revision: policy?.revision, mode, reason }) }),
    onSuccess: async () => { setSuccess('Network policy updated.'); await queryClient.invalidateQueries({ queryKey: ['network-policies'] }) },
  })
  const addCidr = useMutation({
    mutationFn: () => api<{ warnings: string[] }>('/api/v1/admin/network/cidrs', { method: 'POST', body: JSON.stringify({ policy_id: policy?.id, network: cidr, label, enabled: true }) }),
    onSuccess: async (result) => { setSuccess(result.warnings.length ? `CIDR added. ${result.warnings.join(' ')}` : 'CIDR added.'); setCidr(''); await queryClient.invalidateQueries({ queryKey: ['network-policies'] }) },
  })
  const removeCidr = useMutation({
    mutationFn: (id: string) => api<void>(`/api/v1/admin/network/cidrs/${id}`, { method: 'DELETE' }),
    onSuccess: async () => { setSuccess('CIDR removed.'); await queryClient.invalidateQueries({ queryKey: ['network-policies'] }) },
  })
  const updateCidr = useMutation({
    mutationFn: ({ id, network, label: nextLabel, enabled, revision }: { id: string; network: string; label: string; enabled: boolean; revision: number }) => api<{ warnings: string[] }>(`/api/v1/admin/network/cidrs/${id}`, { method: 'PUT', body: JSON.stringify({ policy_id: policy?.id, network, label: nextLabel, enabled, revision }) }),
    onSuccess: async (result) => { setEditingCidr(undefined); setSuccess(result.warnings.length ? `CIDR updated. ${result.warnings.join(' ')}` : 'CIDR updated.'); await queryClient.invalidateQueries({ queryKey: ['network-policies'] }) },
  })
  const test = useMutation({
    mutationFn: () => api<TestResult>('/api/v1/admin/network/test-address', { method: 'POST', body: JSON.stringify({ policy_id: policy?.id, address: testAddress }) }),
    onSuccess: setTestResult,
  })
  const suggest = useMutation({
    mutationFn: () => api<{ available: boolean; proposed_cidr?: string; reason?: string }>('/api/v1/admin/network/suggest-current'),
    onSuccess: (result) => { if (result.available && result.proposed_cidr) { setCidr(result.proposed_cidr); setLabel('Current private network'); setSuccess('Private network suggested. Review it before adding.'); } else setSuccess(result.reason ?? 'No safe private network suggestion is available.') },
  })

  function add(event: FormEvent) { event.preventDefault(); addCidr.mutate() }
  const mutationError = savePolicy.error ?? addCidr.error ?? updateCidr.error ?? removeCidr.error ?? test.error
  const problem = mutationError instanceof ApiError ? mutationError.problem : undefined

  return <>
    {showPolicy && <Panel title="Sensor network policy" eyebrow="Explicit per-site controls">
      <div className="network-policy"><Network /><div><strong>Network policy supplements signed device authentication</strong><p>Enrollment tokens, TLS, unique device credentials, HMAC signatures, replay protection, and server-side authorization remain mandatory. CIDR membership alone never authenticates a sensor.</p></div></div>
      <div className="network-policy-toolbar"><label><span>Physical site</span><select value={siteId} onChange={(event) => { setSiteId(event.target.value); }}>{sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}</select></label><div className="segmented" role="tablist" aria-label="Sensor network direction"><button role="tab" aria-selected={direction === 'device_ingress'} onClick={() => { setDirection('device_ingress'); }}>Sensor ingress</button><button role="tab" aria-selected={direction === 'server_pull'} onClick={() => { setDirection('server_pull'); }}>Server pull access</button></div></div>
      {policies.isLoading ? <LoadingState /> : policies.error ? <ErrorState error={policies.error} /> : !policy ? <EmptyState title="Policy unavailable" message="Reload after the site migration completes." /> : <>
        {policy.migration_notice_pending && <aside className="migration-notice" role="status"><ShieldAlert /><div><strong>Legacy behavior was preserved exactly</strong><p>{direction === 'device_ingress' ? 'Before this update, signed device ingress had no CIDR restriction. That behavior remains until you select and save a reviewed mode.' : policy.mode === 'deny_all' ? 'The former empty CIDR list rejected every server-pull target. It is now explicit deny-all.' : 'The former configured pull boundary was migrated into explicit rules.'}</p></div></aside>}
        <div className="policy-heading"><div><span>Effective behavior</span><strong>{policy.effective_summary}</strong></div><StatusPill status={policy.mode.startsWith('legacy') ? 'pending' : policy.mode === 'deny_all' ? 'failed' : 'healthy'} label={policy.mode.startsWith('legacy') ? 'Review required' : 'Explicit policy'} /></div>
        <fieldset className="policy-mode-options"><legend>{direction === 'device_ingress' ? 'Device-to-server ingress' : 'Server-to-device pull access'}</legend>{['allow_listed_private', 'allow_all_private', 'deny_all'].map((value) => <label className={mode === value ? 'selected' : ''} key={value}><input type="radio" name="network-mode" value={value} checked={mode === value} onChange={() => { setMode(value); }} /><span><strong>{policyLabel(value).label}</strong><small>{policyLabel(value).help}</small></span></label>)}</fieldset>
        <label className="change-reason"><span>Change reason</span><input value={reason} onChange={(event) => { setReason(event.target.value); }} /></label>
        <div className="inline-actions"><button className="button primary" disabled={savePolicy.isPending || mode === policy.mode} onClick={() => { savePolicy.mutate(); }}>{savePolicy.isPending ? 'Saving…' : 'Save policy'}</button></div>
        {success && <p className="form-success" role="status">{success}</p>}{problem && <div className="form-error" role="alert"><strong>{problem.title}</strong><span>{problem.detail}</span></div>}
      </>}
    </Panel>}

    {showPolicy && policy && <div className="network-settings-grid"><Panel title="Private CIDR rules" eyebrow="Canonical server validation" actions={<button className="button ghost" onClick={() => { suggest.mutate(); }}>Add current private network</button>}>
      <form className="cidr-form" onSubmit={add}><label><span>Network label</span><input value={label} onChange={(event) => { setLabel(event.target.value); }} /></label><label><span>IPv4 or IPv6 CIDR</span><input value={cidr} onChange={(event) => { setCidr(event.target.value); }} placeholder="192.168.50.0/24" /></label><button className="button secondary" disabled={addCidr.isPending || !cidr || !label}><Plus size={15} /> Add CIDR</button></form>
      {policy.cidrs.length ? <div className="cidr-list">{policy.cidrs.map((item) => <article key={item.id}>{editingCidr === item.id ? <><label><span>Label</span><input value={editLabel} onChange={(event) => { setEditLabel(event.target.value); }} /></label><label><span>CIDR</span><input value={editNetwork} onChange={(event) => { setEditNetwork(event.target.value); }} /></label><div className="inline-actions"><button className="button secondary" disabled={updateCidr.isPending || !editLabel || !editNetwork} onClick={() => { updateCidr.mutate({ id: item.id, network: editNetwork, label: editLabel, enabled: item.enabled, revision: item.revision }); }}>Save</button><button className="button ghost" onClick={() => { setEditingCidr(undefined); }}>Cancel</button></div></> : <><div><strong>{item.label}</strong><code>{item.network}</code></div><StatusPill status={item.enabled ? 'healthy' : 'pending'} label={item.enabled ? 'Enabled' : 'Disabled'} /><div className="inline-actions"><button className="button ghost" onClick={() => { setEditingCidr(item.id); setEditNetwork(item.network); setEditLabel(item.label); }}>Edit</button><button className="button ghost" disabled={updateCidr.isPending} onClick={() => { updateCidr.mutate({ id: item.id, network: item.network, label: item.label, enabled: !item.enabled, revision: item.revision }); }}>{item.enabled ? 'Disable' : 'Enable'}</button><button className="icon-button danger-text" aria-label={`Remove ${item.label}`} disabled={removeCidr.isPending} onClick={() => { removeCidr.mutate(item.id); }}><Trash2 /></button></div></>}</article>)}</div> : <EmptyState title="No CIDRs configured" message={policy.mode === 'deny_all' ? 'This explicitly means server/device access for this direction is denied.' : 'Add a private sensor network before enabling listed-networks-only mode.'} />}
    </Panel>
    <Panel title="Test sensor IP" eyebrow="No outbound scan"><form className="test-address-form" onSubmit={(event) => { event.preventDefault(); test.mutate() }}><label><span>Address</span><input value={testAddress} onChange={(event) => { setTestAddress(event.target.value); }} placeholder="192.168.50.42" /></label><button className="button secondary" disabled={test.isPending || !testAddress}><Search size={15} /> Test address</button></form>{testResult && <article className={`test-result ${testResult.allowed ? 'allowed' : 'blocked'}`}><div>{testResult.allowed ? <CheckCircle2 /> : <ShieldAlert />}<strong>{testResult.allowed ? 'Allowed' : 'Blocked'}</strong></div><dl><div><dt>Direction</dt><dd>{testResult.direction.replaceAll('_', ' ')}</dd></div><div><dt>Effective policy</dt><dd>{policyLabels[testResult.mode]?.label ?? testResult.mode}</dd></div><div><dt>Matching rule</dt><dd>{testResult.matching_rule ?? 'None'}</dd></div><div><dt>Reason</dt><dd>{testResult.reason}</dd></div></dl></article>}<p className="field-help">This evaluates policy only. It does not connect to or scan the address.</p></Panel></div>}

    {showObserved && <Panel title="Observed signed device addresses" eyebrow="Heartbeat evidence">
      {observed.isLoading ? <LoadingState /> : observed.error ? <ErrorState error={observed.error} /> : observed.data?.length ? <div className="responsive-table"><table><thead><tr><th>Sensor</th><th>Observed IP</th><th>Last signed heartbeat</th><th>Pull-policy state</th></tr></thead><tbody>{observed.data.map((device) => <tr key={`${device.device_id}-${device.address}`}><td>{device.device_name}</td><td><code>{device.address}</code></td><td>{formatTime(device.last_seen_at)}</td><td><StatusPill status={device.validation_error ? 'failed' : 'healthy'} label={device.validation_error ?? 'Accepted'} /></td></tr>)}</tbody></table></div> : <EmptyState title="No signed device addresses observed" message="Current sensor addresses appear after a valid signed heartbeat. mDNS and ICMP are not authoritative." />}
    </Panel>}

    {showServer && <Panel title="Device communication settings" eyebrow="Read-only security posture">{runtime.isLoading ? <LoadingState /> : runtime.error ? <ErrorState error={runtime.error} /> : runtime.data && <dl className="policy-list"><div><dt>Sensor server URL</dt><dd>{runtime.data.sensor_server_url}</dd></div><div><dt>TLS / certificate trust</dt><dd>{runtime.data.tls_verification_required ? `Required · ${runtime.data.certificate_trust}` : 'Invalid deployment state'}</dd></div><div><dt>Default device API port</dt><dd>{runtime.data.default_device_api_port}</dd></div><div><dt>Communication modes</dt><dd>{runtime.data.communication_modes.join(', ')}</dd></div><div><dt>mDNS discovery</dt><dd>{runtime.data.mdns_authoritative ? 'Enabled as supporting evidence' : 'Not authoritative; core operation uses signed heartbeats'}</dd></div><div><dt>Heartbeat / stale threshold</dt><dd>{runtime.data.heartbeat_expectation_seconds}s expected · {runtime.data.stale_device_seconds}s stale</dd></div><div><dt>Server time</dt><dd>{formatTime(runtime.data.server_time)} · {runtime.data.server_timezone}</dd></div><div><dt>Address source</dt><dd>{runtime.data.address_source} (authoritative)</dd></div><div><dt>Forwarded headers</dt><dd>{runtime.data.trusted_forwarded_headers ? 'Accepted only from configured trusted proxy CIDRs' : 'Ignored'}</dd></div><div><dt>Browser/admin CIDRs</dt><dd>Not controlled by sensor policy</dd></div></dl>}</Panel>}
  </>
}
