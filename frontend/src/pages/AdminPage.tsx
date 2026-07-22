import { useQuery } from '@tanstack/react-query'
import { Archive, BellRing, Database, Network, ShieldCheck, Users } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { ApplicationLogs } from '../components/ApplicationLogs'
import { NetworkPolicyPanel } from '../components/NetworkPolicyPanel'
import { NotificationSettings } from '../components/NotificationSettings'
import { UserManagement } from '../components/UserManagement'
import { UtilityAccountsPanel } from '../components/UtilityAccountsPanel'
import { EmptyState, ErrorState, formatTime, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'
import type { SensorNetworkPolicy, Site } from '../types'

interface Backup { id: string; started_at: string; completed_at?: string; status: string; manifest_hash?: string; verified_at?: string; verification_details: Record<string, unknown> }
interface Audit { id: string; occurred_at: string; actor_type: string; actor_id?: string; action: string; object_type?: string; object_id?: string; outcome: string; details: Record<string, unknown> }

const tabs = [
  ['Users & roles', Users],
  ['Sites & accounts', Database],
  ['Notifications', BellRing],
  ['Backups', Archive],
  ['Server & network', Network],
  ['Security & audit', ShieldCheck],
] as const

export function AdminPage({ currentUserId }: { currentUserId?: string }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab')
  const initialTab = tabParam === 'sites-accounts' ? 'Sites & accounts' : tabParam === 'server-network' ? 'Server & network' : 'Users & roles'
  const [tab, setTab] = useState<(typeof tabs)[number][0]>(initialTab)
  const sites = useQuery({ queryKey: ['sites'], queryFn: () => api<Site[]>('/api/v1/sites'), enabled: tab === 'Sites & accounts' || tab === 'Server & network' })
  const policies = useQuery({ queryKey: ['network-policies'], queryFn: () => api<SensorNetworkPolicy[]>('/api/v1/admin/network/policies'), enabled: tab === 'Sites & accounts' })
  const backups = useQuery({ queryKey: ['backups'], queryFn: () => api<Backup[]>('/api/v1/backups'), enabled: tab === 'Backups' })
  const audits = useQuery({ queryKey: ['audit'], queryFn: () => api<Audit[]>('/api/v1/audit-events'), enabled: tab === 'Security & audit' })

  function selectTab(label: (typeof tabs)[number][0]) {
    setTab(label)
    const value = label === 'Sites & accounts' ? 'sites-accounts' : label === 'Server & network' ? 'server-network' : ''
    setSearchParams(value ? { tab: value } : {})
  }

  return (
    <>
      <PageTitle eyebrow="System administration" title="Administration" description="Manage local users, site boundaries, utility accounts, sensor networks, verified backups, and security evidence." />
      <div className="admin-layout">
        <aside className="admin-tabs" role="tablist" aria-label="Administration sections">
          {tabs.map(([label, Icon]) => <button key={label} role="tab" aria-selected={tab === label} onClick={() => { selectTab(label); }}><Icon size={18} />{label}</button>)}
        </aside>
        <div className="admin-content">
          {tab === 'Users & roles' && <UserManagement currentUserId={currentUserId} />}

          {tab === 'Sites & accounts' && (
            <>
              <Panel title="Physical sites" eyebrow="Timezone and explicit network boundary">
                {sites.isLoading ? <LoadingState /> : sites.error ? <ErrorState error={sites.error} /> : sites.data?.map((site) => {
                  const ingress = policies.data?.find((item) => item.site_id === site.id && item.direction === 'device_ingress')
                  const pull = policies.data?.find((item) => item.site_id === site.id && item.direction === 'server_pull')
                  return <article className="admin-card" key={site.id}>
                    <span><Database /></span>
                    <div><strong>{site.name}</strong><small>{site.timezone}</small><small>Ingress: {ingress?.effective_summary ?? 'Loading explicit policy…'} · Pull: {pull?.effective_summary ?? 'Loading explicit policy…'}</small></div>
                    <div className="admin-card-actions">
                      <StatusPill status={!ingress || !pull || ingress.mode.startsWith('legacy') || pull.mode.startsWith('legacy') ? 'pending' : ingress.mode === 'deny_all' && pull.mode === 'deny_all' ? 'failed' : 'healthy'} label={!ingress || !pull ? 'Loading' : ingress.mode.startsWith('legacy') || pull.mode.startsWith('legacy') ? 'Review required' : 'Explicit policy'} />
                      <button className="button ghost" onClick={() => { setTab('Server & network'); setSearchParams({ tab: 'server-network', site: site.id }); }}>Manage network policy</button>
                    </div>
                  </article>
                })}
              </Panel>
              {sites.data && <UtilityAccountsPanel sites={sites.data} initialRateVersionId={searchParams.get('rate_version_id') ?? undefined} />}
            </>
          )}

          {tab === 'Notifications' && <NotificationSettings />}

          {tab === 'Backups' && (
            <><ApplicationLogs /><Panel title="Verified backup history" eyebrow="Logical backup and restore evidence">
              {backups.isLoading ? <LoadingState /> : backups.error ? <ErrorState error={backups.error} /> : backups.data?.length ? (
                <div className="responsive-table"><table><thead><tr><th>Started</th><th>Status</th><th>Manifest</th><th>Restore verification</th></tr></thead><tbody>{backups.data.map((backup) => <tr key={backup.id}><td>{formatTime(backup.started_at)}</td><td><StatusPill status={backup.status} /></td><td><code>{backup.manifest_hash?.slice(0, 14) ?? '—'}{backup.manifest_hash ? '…' : ''}</code></td><td>{backup.verified_at ? `Verified ${formatTime(backup.verified_at)}` : 'Not verified'}</td></tr>)}</tbody></table></div>
              ) : <EmptyState title="No backup evidence" message="The scheduled production backup records its manifest and clean-database restore verification here." />}
            </Panel></>
          )}

          {tab === 'Server & network' && (
            sites.isLoading ? <LoadingState /> : sites.error ? <ErrorState error={sites.error} /> : sites.data && <NetworkPolicyPanel sites={sites.data} initialSiteId={searchParams.get('site') ?? undefined} />
          )}

          {tab === 'Security & audit' && (
            <Panel title="Immutable audit trail" eyebrow="Latest 200 events">
              {audits.isLoading ? <LoadingState /> : audits.error ? <ErrorState error={audits.error} /> : audits.data?.length ? (
                <div className="audit-list">{audits.data.map((event) => <article key={event.id}><time>{formatTime(event.occurred_at)}</time><span className={`audit-outcome ${event.outcome}`}>{event.outcome}</span><p><strong>{event.action}</strong><small>{event.actor_type} {event.actor_id?.slice(0, 8) ?? 'anonymous'} · {event.object_type ?? 'system'} {event.object_id?.slice(0, 8) ?? ''}</small></p></article>)}</div>
              ) : <EmptyState title="No audit events" message="Authentication and administrative events appear here." />}
            </Panel>
          )}
        </div>
      </div>
    </>
  )
}
