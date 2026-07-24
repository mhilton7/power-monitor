import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowLeft, Clock3, Cpu, Database, GitCompare, Globe2, History, Network, RadioTower, Settings2, ShieldCheck, Wrench, Zap } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { EmptyState, ErrorState, formatNumber, formatTime, LoadingState, Metric, PageTitle, Panel, StatusPill } from '../components/UI'

interface Detail {
  device: Record<string, string | number | undefined>
  latest_heartbeat?: Record<string, unknown>
  sync: { highest_contiguous_sequence: number; maximum_seen_sequence: number; gaps: Array<{ start: number; end: number }> }
  addresses: Array<{ host: string; port: number; source: string; last_seen_at: string; validation_error?: string }>
  credential_fingerprints: Array<{ fingerprint: string; valid_from: string; valid_until?: string; revoked_at?: string }>
  events: Array<{ event_id: string; occurred_at: string; category: string; severity: string; evidence: Record<string, unknown> }>
}

const tabs = [
  ['Live', Activity], ['History', History], ['Cost attribution', GitCompare], ['Health', Cpu], ['Storage & sync', Database],
  ['Network', Network], ['Configuration', Settings2], ['Events', Clock3], ['Firmware', ShieldCheck], ['Maintenance', Wrench],
] as const

export function DeviceDetailPage() {
  const { deviceId = '' } = useParams()
  const [tab, setTab] = useState<(typeof tabs)[number][0]>('Live')
  const query = useQuery({ queryKey: ['device', deviceId], queryFn: () => api<Detail>(`/api/v1/devices/${deviceId}`) })
  if (query.isLoading) return <LoadingState label="Loading device evidence…" />
  if (query.error || !query.data) return <ErrorState error={query.error} retry={() => void query.refetch()} />
  const detail = query.data
  const device = detail.device
  const heartbeat = detail.latest_heartbeat as { latest?: Record<string, string>; pzem?: Record<string, unknown>; sd?: Record<string, unknown>; time?: Record<string, unknown>; resources?: Record<string, unknown> } | undefined
  return (
    <>
      <Link className="back-link" to="/monitoring/devices"><ArrowLeft size={16} /> Device fleet</Link>
      <PageTitle eyebrow={`${String(device.measurement_role)} monitor`} title={String(device.name)} description={`Identity ${String(device.id)} · ${String(device.connection_mode)} mode`} actions={<StatusPill status={String(device.status)} />} />
      <section className="metric-grid metric-grid-4">
        <Metric label="Live power" value={formatNumber(heartbeat?.latest?.power_w)} unit="W" detail="Latest signed measurement" />
        <Metric label="Voltage" value={formatNumber(heartbeat?.latest?.voltage_v)} unit="V" detail="PZEM average" />
        <Metric label="Sequence cursor" value={detail.sync.highest_contiguous_sequence} detail={`${detail.sync.maximum_seen_sequence} maximum seen`} />
        <Metric label="Firmware" value={String(device.firmware_version ?? 'Unknown')} detail={`Config ${String(device.effective_config_version ?? 0)} / ${String(device.desired_config_version ?? 0)}`} />
      </section>
      <div className="tabs" role="tablist" aria-label="Device detail views">
        {tabs.map(([label, Icon]) => <button key={label} role="tab" aria-selected={tab === label} onClick={() => { setTab(label); }}><Icon size={16} />{label}</button>)}
      </div>
      {tab === 'Live' && <div className="detail-grid"><Panel title="Latest measurement" eyebrow="Signed heartbeat"><dl className="detail-list"><div><dt>Power</dt><dd>{formatNumber(heartbeat?.latest?.power_w)} W</dd></div><div><dt>Current</dt><dd>{formatNumber(heartbeat?.latest?.current_a, 3)} A</dd></div><div><dt>Power factor</dt><dd>{formatNumber(heartbeat?.latest?.power_factor, 3)}</dd></div><div><dt>Frequency</dt><dd>{formatNumber(heartbeat?.latest?.frequency_hz, 2)} Hz</dd></div><div><dt>Measurement time</dt><dd>{formatTime(heartbeat?.latest?.measured_at)}</dd></div></dl></Panel><Panel title="Application health" eyebrow="Authoritative evidence"><div className="health-stack"><div><RadioTower /><p><strong>Heartbeat</strong><small>{formatTime(String(device.last_seen_at ?? ''))}</small></p><StatusPill status="healthy" /></div><div><Zap /><p><strong>PZEM meter</strong><small>{JSON.stringify(heartbeat?.pzem ?? {})}</small></p><StatusPill status={(heartbeat?.pzem?.ok as boolean) ? 'healthy' : 'failed'} /></div><div><Database /><p><strong>microSD</strong><small>{JSON.stringify(heartbeat?.sd ?? {})}</small></p><StatusPill status={(heartbeat?.sd?.ok as boolean) ? 'healthy' : 'failed'} /></div></div></Panel></div>}
      {tab === 'Storage & sync' && <Panel title="Sequence synchronization" eyebrow="microSD recovery"><div className="sync-track"><span style={{ width: `${detail.sync.maximum_seen_sequence ? detail.sync.highest_contiguous_sequence / detail.sync.maximum_seen_sequence * 100 : 100}%` }} /></div><p>{detail.sync.highest_contiguous_sequence} contiguous records committed of {detail.sync.maximum_seen_sequence} seen.</p>{detail.sync.gaps.length ? <div className="gap-list">{detail.sync.gaps.map((gap) => <span key={`${gap.start}-${gap.end}`}>Missing {gap.start}–{gap.end}</span>)}</div> : <EmptyState title="No open sequence gaps" message="The server cursor is contiguous through the acknowledged sequence." />}</Panel>}
      {tab === 'Network' && <Panel title="Address history" eyebrow="Identity is permanent; addresses are not"><div className="responsive-table"><table><thead><tr><th>Address</th><th>Source</th><th>Last seen</th><th>Poll validation</th></tr></thead><tbody>{detail.addresses.map((address) => <tr key={`${address.host}-${address.source}`}><td><Globe2 size={15} /> {address.host}:{address.port}</td><td>{address.source}</td><td>{formatTime(address.last_seen_at)}</td><td>{address.validation_error ?? 'Eligible after site-policy validation'}</td></tr>)}</tbody></table></div></Panel>}
      {tab === 'Events' && <Panel title="Device event timeline" eyebrow="Last 100"><div className="timeline">{detail.events.length ? detail.events.map((event) => <div key={event.event_id}><span /><time>{formatTime(event.occurred_at)}</time><p><strong>{event.category}</strong><small>{event.severity} · {JSON.stringify(event.evidence)}</small></p></div>) : <EmptyState title="No device events" message="Boot, meter, storage, network, configuration, OTA, and security events appear here." />}</div></Panel>}
      {tab === 'Health' && <Panel title="Subsystem evidence" eyebrow="Latest report"><pre className="json-view">{JSON.stringify({ pzem: heartbeat?.pzem, sd: heartbeat?.sd, time: heartbeat?.time, resources: heartbeat?.resources }, null, 2)}</pre></Panel>}
      {tab === 'Configuration' && <Panel title="Desired and effective configuration" eyebrow="Immutable versions"><p>Desired version <strong>{String(device.desired_config_version)}</strong>; effective version <strong>{String(device.effective_config_version)}</strong>. CT changes require an explicit warning; hardware pins stay local-only.</p></Panel>}
      {tab === 'Cost attribution' && <Panel title="Cost scope" eyebrow="No automatic account charges"><StatusPill status="healthy" label={String(device.cost_scope)} /><p>This one-CT device receives monitored-load energy charges only unless an administrator assigns a full-account aggregate.</p></Panel>}
      {tab === 'History' && <Panel title="Measurement history" eyebrow="UTC source · local display"><Link className="button secondary" to={`/analytics/history?device_id=${deviceId}`}>Open interactive history</Link></Panel>}
      {tab === 'Firmware' && <Panel title="Firmware state" eyebrow="Signed packages only"><p>Installed version <strong>{String(device.firmware_version ?? 'Unknown')}</strong>. Compatible signed deployments are managed from Firmware.</p><Link className="button secondary" to="/firmware">Open firmware management</Link></Panel>}
      {tab === 'Maintenance' && <Panel title="Maintenance actions" eyebrow="Audited and authorized"><p>Reboot, sync retry, credential rotation, replacement, and revocation require operator or administrator privileges and are recorded in the audit log.</p></Panel>}
    </>
  )
}
