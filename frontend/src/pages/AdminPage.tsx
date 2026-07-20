import { useQuery } from '@tanstack/react-query'
import { Archive, BellRing, Database, HardDrive, Network, ServerCog, ShieldCheck, Users } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api'
import { UserManagement } from '../components/UserManagement'
import { NotificationSettings } from '../components/NotificationSettings'
import type { Site } from '../types'
import { EmptyState, ErrorState, formatTime, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'

interface Backup { id: string; started_at: string; completed_at?: string; status: string; manifest_hash?: string; verified_at?: string; verification_details: Record<string, unknown> }
interface Audit { id: string; occurred_at: string; actor_type: string; actor_id?: string; action: string; object_type?: string; object_id?: string; outcome: string; details: Record<string, unknown> }
interface SystemInfo { product: string; version: string; protocol: string; python_runtime: string; worker: { status: string; last_loop_at?: string; last_success_at?: string }; defaults: Record<string, unknown> }
interface Account { id: string; site_id: string; name: string; timezone: string; currency: string; billing_cycle_start_day: number; baseline_allocation_kwh?: string; generation_provider: string; active_rate_version_id?: string }

const tabs = [
  ['Users & roles', Users],
  ['Sites & accounts', Database],
  ['Notifications', BellRing],
  ['Backups', Archive],
  ['Server & network', Network],
  ['Security & audit', ShieldCheck],
  ['Diagnostics', ServerCog],
] as const

export function AdminPage({ currentUserId }: { currentUserId?: string }) {
  const [tab, setTab] = useState<(typeof tabs)[number][0]>('Users & roles')
  const sites = useQuery({ queryKey: ['sites'], queryFn: () => api<Site[]>('/api/v1/sites'), enabled: tab === 'Sites & accounts' || tab === 'Server & network' })
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => api<Account[]>('/api/v1/utility-accounts'), enabled: tab === 'Sites & accounts' })
  const backups = useQuery({ queryKey: ['backups'], queryFn: () => api<Backup[]>('/api/v1/backups'), enabled: tab === 'Backups' })
  const audits = useQuery({ queryKey: ['audit'], queryFn: () => api<Audit[]>('/api/v1/audit-events'), enabled: tab === 'Security & audit' })
  const system = useQuery({ queryKey: ['system'], queryFn: () => api<SystemInfo>('/api/v1/system/info'), enabled: tab === 'Diagnostics' })

  return (
    <>
      <PageTitle eyebrow="System administration" title="Administration" description="Manage local users, site boundaries, verified backups, security evidence, and server health." />
      <div className="admin-layout">
        <aside className="admin-tabs" role="tablist" aria-label="Administration sections">
          {tabs.map(([label, Icon]) => <button key={label} role="tab" aria-selected={tab === label} onClick={() => { setTab(label) }}><Icon size={18} />{label}</button>)}
        </aside>
        <div className="admin-content">
          {tab === 'Users & roles' && <UserManagement currentUserId={currentUserId} />}

          {tab === 'Sites & accounts' && (
            <>
              <Panel title="Physical sites" eyebrow="Timezone & poll boundary">
                {sites.isLoading ? <LoadingState /> : sites.error ? <ErrorState error={sites.error} /> : sites.data?.map((site) => (
                  <article className="admin-card" key={site.id}>
                    <span><Database /></span>
                    <div><strong>{site.name}</strong><small>{site.timezone} · {site.allowed_cidrs.length} permitted CIDRs</small></div>
                    <StatusPill status={site.allow_public_polling ? 'pending' : 'healthy'} label={site.allow_public_polling ? 'Public opt-in' : 'Private only'} />
                  </article>
                ))}
              </Panel>
              <Panel title="Utility accounts" eyebrow="Fixed charges apply once">
                {accounts.isLoading ? <LoadingState /> : accounts.error ? <ErrorState error={accounts.error} /> : accounts.data?.length ? accounts.data.map((account) => (
                  <article className="admin-card" key={account.id}>
                    <span><HardDrive /></span>
                    <div><strong>{account.name}</strong><small>Billing day {account.billing_cycle_start_day} · {account.generation_provider} generation · baseline {account.baseline_allocation_kwh ?? 'not set'}</small></div>
                    <StatusPill status={account.active_rate_version_id ? 'healthy' : 'pending'} label={account.active_rate_version_id ? 'Rate assigned' : 'Needs rate'} />
                  </article>
                )) : <EmptyState title="No utility account" message="Create one, select an effective rate version, then explicitly choose cost scope." />}
              </Panel>
            </>
          )}

          {tab === 'Notifications' && (
            <NotificationSettings />
          )}

          {tab === 'Backups' && (
            <Panel title="Verified backup history" eyebrow="Logical backup and restore evidence">
              {backups.isLoading ? <LoadingState /> : backups.error ? <ErrorState error={backups.error} /> : backups.data?.length ? (
                <div className="responsive-table"><table><thead><tr><th>Started</th><th>Status</th><th>Manifest</th><th>Restore verification</th></tr></thead><tbody>{backups.data.map((backup) => <tr key={backup.id}><td>{formatTime(backup.started_at)}</td><td><StatusPill status={backup.status} /></td><td><code>{backup.manifest_hash?.slice(0, 14) ?? '—'}{backup.manifest_hash ? '…' : ''}</code></td><td>{backup.verified_at ? `Verified ${formatTime(backup.verified_at)}` : 'Not verified'}</td></tr>)}</tbody></table></div>
              ) : <EmptyState title="No backup evidence" message="The scheduled production backup records its manifest and clean-database restore verification here." />}
            </Panel>
          )}

          {tab === 'Server & network' && (
            <Panel title="Polling security boundary" eyebrow="SSRF-resistant">
              <div className="network-policy"><Network /><div><strong>No public ESP32 port forwarding</strong><p>Use outbound push for NAT or remote devices, or a private VPN. Targets are resolved and revalidated against each site's allowed CIDRs.</p></div></div>
              {sites.isLoading ? <LoadingState /> : sites.error ? <ErrorState error={sites.error} /> : sites.data?.map((site) => (
                <dl className="policy-list" key={site.id}>
                  <div><dt>{site.name}</dt><dd>{site.allowed_cidrs.join(', ') || 'No pull CIDRs configured'}</dd></div>
                  <div><dt>Permitted domains</dt><dd>{site.allowed_domains.join(', ') || 'None'}</dd></div>
                  <div><dt>Public polling</dt><dd>{site.allow_public_polling ? 'Explicitly enabled' : 'Disabled'}</dd></div>
                </dl>
              ))}
            </Panel>
          )}

          {tab === 'Security & audit' && (
            <Panel title="Immutable audit trail" eyebrow="Latest 200 events">
              {audits.isLoading ? <LoadingState /> : audits.error ? <ErrorState error={audits.error} /> : audits.data?.length ? (
                <div className="audit-list">{audits.data.map((event) => <article key={event.id}><time>{formatTime(event.occurred_at)}</time><span className={`audit-outcome ${event.outcome}`}>{event.outcome}</span><p><strong>{event.action}</strong><small>{event.actor_type} {event.actor_id?.slice(0, 8) ?? 'anonymous'} · {event.object_type ?? 'system'} {event.object_id?.slice(0, 8) ?? ''}</small></p></article>)}</div>
              ) : <EmptyState title="No audit events" message="Authentication and administrative events appear here." />}
            </Panel>
          )}

          {tab === 'Diagnostics' && (
            <Panel title="Runtime & worker" eyebrow="Version evidence">
              {system.isLoading ? <LoadingState /> : system.error ? <ErrorState error={system.error} /> : system.data && (
                <><div className="diagnostic-hero"><span><ServerCog /></span><div><strong>{system.data.product} {system.data.version}</strong><small>{system.data.protocol} · {system.data.python_runtime}</small></div><StatusPill status={system.data.worker.status === 'healthy' ? 'healthy' : 'pending'} label={`Worker ${system.data.worker.status}`} /></div><dl className="detail-list"><div><dt>Worker last loop</dt><dd>{formatTime(system.data.worker.last_loop_at)}</dd></div><div><dt>Worker last success</dt><dd>{formatTime(system.data.worker.last_success_at)}</dd></div>{Object.entries(system.data.defaults).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{String(value)}</dd></div>)}</dl></>
              )}
            </Panel>
          )}
        </div>
      </div>
    </>
  )
}
