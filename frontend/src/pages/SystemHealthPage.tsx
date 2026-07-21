import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowUpRight, Clock3, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { StatusIndicatorZone } from '../components/StatusIndicators'
import { ErrorState, formatTime, LoadingState, PageTitle, Panel } from '../components/UI'

interface SystemInfo {
  product: string
  version: string
  protocol: string
  python_runtime: string
  worker: { status: string; last_loop_at?: string; last_success_at?: string }
  defaults: Record<string, unknown>
}

export function SystemHealthPage() {
  const system = useQuery({
    queryKey: ['system'],
    queryFn: () => api<SystemInfo>('/api/v1/system/info'),
    refetchInterval: 15_000,
  })

  return (
    <>
      <PageTitle
        eyebrow="Administration · diagnostics"
        title="System Health"
        description="Review API, database, and background-worker readiness without crowding normal monitoring pages."
        actions={<Link className="button secondary" to="/alerts">Open alerts <ArrowUpRight size={16} /></Link>}
      />
      <StatusIndicatorZone zone="diagnostics_summary" />
      <Panel title="Runtime evidence" eyebrow="Authorized diagnostic details">
        {system.isLoading ? <LoadingState label="Checking server health…" /> : system.error ? (
          <ErrorState error={system.error} retry={() => { void system.refetch() }} />
        ) : system.data ? (
          <div className="system-health-details">
            <article><Activity aria-hidden="true" /><span><small>Release</small><strong>{system.data.product} {system.data.version}</strong><em>{system.data.python_runtime}</em></span></article>
            <article><ShieldCheck aria-hidden="true" /><span><small>Device protocol</small><strong>{system.data.protocol}</strong><em>Contract identifier</em></span></article>
            <article><Clock3 aria-hidden="true" /><span><small>Worker activity</small><strong>{formatTime(system.data.worker.last_loop_at)}</strong><em>Last success {formatTime(system.data.worker.last_success_at)}</em></span></article>
          </div>
        ) : null}
      </Panel>
      {system.data && <Panel title="Configured runtime defaults" eyebrow="Read-only evidence">
        <dl className="detail-list">
          {Object.entries(system.data.defaults).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{String(value)}</dd></div>)}
        </dl>
      </Panel>}
      <p className="diagnostic-note">Container readiness and TrueNAS health checks continue to run independently of this page. Operational failures remain alertable even when these cards are not shown in the shared dashboard shell.</p>
    </>
  )
}
