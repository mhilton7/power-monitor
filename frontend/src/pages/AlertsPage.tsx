import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, BellOff, CheckCheck, Clock3, Filter, Mail, Webhook } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api'
import { EmptyState, ErrorState, formatTime, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'

interface Alert { id: string; name: string; status: string; severity: string; device_id?: string; site_id?: string; opened_at: string; acknowledged_at?: string; resolved_at?: string; evidence: Record<string, unknown> }
interface Rule { id: string; name: string; rule_type: string; severity: string; enabled: boolean; debounce_seconds: number; resolve_seconds: number }

export function AlertsPage() {
  const [filter, setFilter] = useState('active')
  const client = useQueryClient()
  const alerts = useQuery({ queryKey: ['alerts', filter], queryFn: () => api<Alert[]>(`/api/v1/alerts${filter === 'all' ? '' : `?status=${filter}`}`) })
  const rules = useQuery({ queryKey: ['alert-rules'], queryFn: () => api<Rule[]>('/api/v1/alert-rules') })
  const acknowledge = useMutation({ mutationFn: (id: string) => api(`/api/v1/alerts/${id}/acknowledge`, { method: 'POST', body: JSON.stringify({ note: 'Acknowledged from fleet dashboard' }) }), onSuccess: async () => client.invalidateQueries({ queryKey: ['alerts'] }) })
  return (
    <>
      <PageTitle eyebrow="Operational response" title="Alerts & notifications" description="Evidence, debounce, resolution, acknowledgement, silence, maintenance, and delivery state stay together." />
      <div className="alert-summary"><div><span className="severity-icon critical"><Bell /></span><p><strong>{alerts.data?.filter((item) => item.severity === 'critical').length ?? 0}</strong><small>Critical</small></p></div><div><span className="severity-icon warning"><Clock3 /></span><p><strong>{alerts.data?.filter((item) => item.severity === 'warning').length ?? 0}</strong><small>Warning</small></p></div><div><span className="severity-icon good"><CheckCheck /></span><p><strong>{rules.data?.filter((rule) => rule.enabled).length ?? 0}</strong><small>Rules enabled</small></p></div><div><span className="severity-icon neutral"><Webhook /></span><p><strong>Local</strong><small>Delivery capable</small></p></div></div>
      <Panel className="table-panel" title="Alert timeline" eyebrow="Current evidence" actions={<label className="filter-field"><Filter size={15} /><select value={filter} onChange={(event) => { setFilter(event.target.value); }}><option value="active">Active</option><option value="acknowledged">Acknowledged</option><option value="resolved">Resolved</option><option value="all">All</option></select></label>}>
        {alerts.isLoading ? <LoadingState /> : alerts.error ? <ErrorState error={alerts.error} /> : alerts.data?.length ? <div className="alert-list">{alerts.data.map((alert) => <article key={alert.id} className={`alert-row severity-${alert.severity}`}><span className="alert-marker" /><div className="alert-body"><header><strong>{alert.name}</strong><StatusPill status={alert.status} /></header><p>{Object.entries(alert.evidence).map(([key, value]) => `${key}: ${String(value)}`).join(' · ') || 'No additional evidence'}</p><small>Opened {formatTime(alert.opened_at)} · {alert.device_id ? `Device ${alert.device_id.slice(0, 8)}` : 'Server'}</small></div>{alert.status === 'active' && <button className="button secondary" disabled={acknowledge.isPending} onClick={() => { acknowledge.mutate(alert.id); }}>Acknowledge</button>}</article>)}</div> : <EmptyState title="No alerts in this view" message="Healthy is quiet. Default rules continue evaluating meter, storage, time, sync, network, firmware, worker, and backup evidence." />}
      </Panel>
      <Panel title="Delivery channels" eyebrow="No mandatory third party"><div className="channel-grid"><article><span><Mail /></span><p><strong>SMTP email</strong><small>Local or hosted SMTP with retry history</small></p><StatusPill status="pending" label="Not configured" /></article><article><span><Webhook /></span><p><strong>HTTPS webhook</strong><small>Signed generic delivery with redaction</small></p><StatusPill status="pending" label="Not configured" /></article><article><span><BellOff /></span><p><strong>Maintenance windows</strong><small>Scoped silences retain underlying evidence</small></p><StatusPill status="healthy" label="Available" /></article></div></Panel>
    </>
  )
}

