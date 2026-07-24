import { useQuery } from '@tanstack/react-query'
import { Activity, Archive, Database, FileText, Network, RadioTower, ShieldCheck } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { sessionPermissions } from '../access'
import { api } from '../api'
import { ApplicationLogs } from '../components/ApplicationLogs'
import { NetworkPolicyPanel } from '../components/NetworkPolicyPanel'
import { NotificationSettings } from '../components/NotificationSettings'
import { UtilityAccountsPanel } from '../components/UtilityAccountsPanel'
import { EmptyState, ErrorState, formatTime, LoadingState, Panel, StatusPill } from '../components/UI'
import type { Session, Site } from '../types'
import { InterfaceTextPage } from './InterfaceTextPage'
import { PhysicalSitesPage } from './PhysicalSitesPage'
import { StatusIndicatorsPage } from './StatusIndicatorsPage'
import { SystemHealthPage } from './SystemHealthPage'

interface Backup {
  id: string
  started_at: string
  completed_at?: string
  status: string
  manifest_hash?: string
  verified_at?: string
}

interface Audit {
  id: string
  occurred_at: string
  actor_type: string
  actor_id?: string
  action: string
  object_type?: string
  object_id?: string
  outcome: string
}

type SitesNetworkView = 'sites' | 'network' | 'connectivity' | 'observed'

function ContextTabs({
  value,
  onChange,
  options,
  label,
}: {
  value: string
  onChange: (value: string) => void
  options: Array<{ id: string; label: string; icon: typeof Database }>
  label: string
}) {
  return (
    <nav className="context-tab-bar" aria-label={label}>
      {options.map(({ id, label: optionLabel, icon: Icon }) => (
        <button
          type="button"
          key={id}
          className={value === id ? 'active' : undefined}
          aria-current={value === id ? 'page' : undefined}
          onClick={() => { onChange(id) }}
        >
          <Icon size={16} aria-hidden="true" />
          {optionLabel}
        </button>
      ))}
    </nav>
  )
}

export function BillingAccountsPage({ canViewBills }: { canViewBills: boolean }) {
  const [params] = useSearchParams()
  const sites = useQuery({
    queryKey: ['sites'],
    queryFn: () => api<Site[]>('/api/v1/sites'),
  })
  if (sites.isLoading) return <LoadingState label="Loading utility accounts…" />
  if (sites.error) return <ErrorState error={sites.error} />
  if (!sites.data?.length) {
    return (
      <EmptyState
        title="Create a physical site first"
        message="Utility accounts must belong to an active physical site."
      />
    )
  }
  return (
    <UtilityAccountsPanel
      sites={sites.data}
      initialRateVersionId={params.get('rate_version_id') ?? undefined}
      initialSiteId={params.get('site') ?? undefined}
      openCreate={params.get('create') === 'account'}
      canViewBills={canViewBills}
    />
  )
}

export function SitesNetworkPage({ session }: { session: Session }) {
  const [params, setParams] = useSearchParams()
  const permissions = sessionPermissions(session)
  const options = [
    ...(permissions.has('sites.view') ? [{ id: 'sites', label: 'Physical Sites', icon: Database }] : []),
    ...(permissions.has('network.view') ? [
      { id: 'network', label: 'Network Policy', icon: Network },
      { id: 'connectivity', label: 'Server Settings', icon: Activity },
      { id: 'observed', label: 'Observed Devices', icon: RadioTower },
    ] : []),
  ]
  const requested = params.get('view')
  const requestedIsAllowed = options.some((option) => option.id === requested)
  const view = (requestedIsAllowed ? requested : options[0]?.id ?? 'sites') as SitesNetworkView
  const sites = useQuery({
    queryKey: ['sites'],
    queryFn: () => api<Site[]>('/api/v1/sites'),
    enabled: view !== 'sites',
  })
  const setView = (next: string) => {
    const updated = new URLSearchParams(params)
    updated.set('view', next)
    setParams(updated, { replace: true })
  }
  return (
    <>
      <ContextTabs
        value={view}
        onChange={setView}
        label="Sites and network sections"
        options={options}
      />
      {view === 'sites' && <PhysicalSitesPage session={session} />}
      {view === 'network' && (
        sites.isLoading ? <LoadingState label="Loading site policies…" />
          : sites.error ? <ErrorState error={sites.error} />
            : sites.data?.length
              ? <NetworkPolicyPanel sites={sites.data} initialSiteId={params.get('site') ?? undefined} section="policy" />
              : <EmptyState title="No active sites" message="Restore or create a site before configuring network policy." />
      )}
      {view === 'connectivity' && sites.data?.length && <NetworkPolicyPanel sites={sites.data} initialSiteId={params.get('site') ?? undefined} section="server" />}
      {view === 'observed' && sites.data?.length && <NetworkPolicyPanel sites={sites.data} initialSiteId={params.get('site') ?? undefined} section="observed" />}
    </>
  )
}

export function NotificationsWorkspacePage() {
  return <NotificationSettings />
}

export function DataManagementPage({ session }: { session: Session }) {
  const permissions = sessionPermissions(session)
  const canViewBackups = permissions.has('backups.view')
  const canExportLogs = permissions.has('logs.export')
  const backups = useQuery({
    queryKey: ['backups'],
    queryFn: () => api<Backup[]>('/api/v1/backups'),
    enabled: canViewBackups,
  })
  return (
    <>
      {canExportLogs && <ApplicationLogs />}
      {canViewBackups && <Panel title="Verified backup history" eyebrow="Logical backup and restore evidence">
        {backups.isLoading ? <LoadingState />
          : backups.error ? <ErrorState error={backups.error} />
            : backups.data?.length ? (
              <div className="responsive-table">
                <table>
                  <thead><tr><th>Started</th><th>Status</th><th>Manifest</th><th>Restore verification</th></tr></thead>
                  <tbody>
                    {backups.data.map((backup) => (
                      <tr key={backup.id}>
                        <td>{formatTime(backup.started_at)}</td>
                        <td><StatusPill status={backup.status} /></td>
                        <td><code>{backup.manifest_hash?.slice(0, 14) ?? '—'}{backup.manifest_hash ? '…' : ''}</code></td>
                        <td>{backup.verified_at ? `Verified ${formatTime(backup.verified_at)}` : 'Not verified'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <EmptyState title="No backup evidence" message="Scheduled backups record checksums and clean-database restore verification here." />}
      </Panel>}
    </>
  )
}

export function InterfaceWorkspacePage({ session }: { session: Session }) {
  const permissions = sessionPermissions(session)
  const options = [
    ...(permissions.has('interface_text.view') ? [{ id: 'text', label: 'Dashboard & Login Text', icon: FileText }] : []),
    ...(permissions.has('status_indicators.view') ? [{ id: 'layout', label: 'Status Indicators & Layout', icon: Database }] : []),
  ]
  const [params, setParams] = useSearchParams()
  const requested = params.get('view')
  const view = options.some((option) => option.id === requested) ? requested ?? 'text' : options[0]?.id ?? 'text'
  return (
    <>
      <ContextTabs
        value={view}
        onChange={(next) => { setParams({ view: next }, { replace: true }) }}
        label="Interface settings"
        options={options}
      />
      {view === 'text'
        ? <InterfaceTextPage canManage={permissions.has('interface_text.manage')} />
        : <StatusIndicatorsPage canManage={permissions.has('status_indicators.manage')} />}
    </>
  )
}

export function SecurityWorkspacePage({ session }: { session: Session }) {
  const permissions = sessionPermissions(session)
  const options = [
    ...(permissions.has('settings.view') ? [{ id: 'health', label: 'System Health', icon: ShieldCheck }] : []),
    ...(permissions.has('audit.view') ? [{ id: 'audit', label: 'Security & Audit', icon: Archive }] : []),
  ]
  const [params, setParams] = useSearchParams()
  const requested = params.get('view')
  const view = options.some((option) => option.id === requested) ? requested ?? 'health' : options[0]?.id ?? 'health'
  const audits = useQuery({
    queryKey: ['audit'],
    queryFn: () => api<Audit[]>('/api/v1/audit-events'),
    enabled: view === 'audit',
  })
  return (
    <>
      <ContextTabs
        value={view}
        onChange={(next) => { setParams({ view: next }, { replace: true }) }}
        label="Security sections"
        options={options}
      />
      {view === 'health' ? <SystemHealthPage /> : (
        <Panel title="Immutable audit trail" eyebrow="Latest 200 events">
          {audits.isLoading ? <LoadingState />
            : audits.error ? <ErrorState error={audits.error} />
              : audits.data?.length ? (
                <div className="audit-list">
                  {audits.data.map((event) => (
                    <article key={event.id}>
                      <time>{formatTime(event.occurred_at)}</time>
                      <span className={`audit-outcome ${event.outcome}`}>{event.outcome}</span>
                      <p>
                        <strong>{event.action}</strong>
                        <small>{event.actor_type} {event.actor_id?.slice(0, 8) ?? 'anonymous'} · {event.object_type ?? 'system'} {event.object_id?.slice(0, 8) ?? ''}</small>
                      </p>
                    </article>
                  ))}
                </div>
              ) : <EmptyState title="No audit events" message="Authentication and administrative events appear here." />}
        </Panel>
      )}
    </>
  )
}
