import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleOff,
  Database,
  MapPin,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Star,
  Users,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ActionScope, CanonicalAction } from '../actions'
import { sessionPermissions } from '../access'
import { api, ApiError } from '../api'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StatusPill,
  formatTime,
} from '../components/UI'
import type {
  AdminSite,
  ManagedUser,
  SensorNetworkPolicy,
  Session,
  SiteDependencySummary,
} from '../types'

type SiteFilter = 'current' | 'active' | 'disabled' | 'removed' | 'all'
type SiteSort = 'name' | 'created' | 'activity' | 'sensors' | 'status'
type DetailSection =
  | 'overview'
  | 'devices'
  | 'accounts'
  | 'access'
  | 'network'
  | 'coverage'
  | 'audit'

interface CreateDraft {
  name: string
  code: string
  description: string
  locationLabel: string
  organization: string
  timezone: string
  currency: string
  locale: string
  unitSystem: 'imperial' | 'metric'
  networkPolicyMode: 'inherit' | 'explicit' | 'existing'
  networkPolicyId: string
  userIds: string[]
  makeDefault: boolean
  createUtilityAfter: boolean
  confirmation: boolean
}

const initialDraft = (): CreateDraft => ({
  name: '',
  code: '',
  description: '',
  locationLabel: '',
  organization: '',
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/Los_Angeles',
  currency: 'USD',
  locale: navigator.language || 'en-US',
  unitSystem: 'imperial',
  networkPolicyMode: 'inherit',
  networkPolicyId: '',
  userIds: [],
  makeDefault: false,
  createUtilityAfter: false,
  confirmation: false,
})

const wizardSteps = [
  'Site identity',
  'Time and locale',
  'Network policy',
  'Initial access',
  'Optional utility setup',
  'Review and create',
]

function problemMessage(error: unknown): string {
  if (error instanceof ApiError) return error.problem.detail
  return error instanceof Error ? error.message : 'The site change could not be completed.'
}

function slug(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 80)
}

function configurationLabel(site: AdminSite): string {
  if (site.configuration_health === 'warning') return 'Configuration warning'
  return 'Configuration ready'
}

function auditRevision(details: Record<string, unknown>): string {
  const revision = details.revision
  return typeof revision === 'string' || typeof revision === 'number' ? String(revision) : '—'
}

export function PhysicalSitesPage({ session }: { session: Session }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const permissions = sessionPermissions(session)
  const [filter, setFilter] = useState<SiteFilter>('current')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SiteSort>('name')
  const [quickFilter, setQuickFilter] = useState('all')
  const [createDraft, setCreateDraft] = useState<CreateDraft>()
  const [createStep, setCreateStep] = useState(0)
  const [selectedId, setSelectedId] = useState<string>()
  const [detailSection, setDetailSection] = useState<DetailSection>('overview')
  const [notice, setNotice] = useState('')
  const sites = useQuery({
    queryKey: ['admin-sites', filter, search],
    queryFn: () => api<AdminSite[]>(`/api/v1/admin/sites?status=${filter}&search=${encodeURIComponent(search)}`),
  })
  const activeSites = useQuery({
    queryKey: ['admin-sites', 'active', ''],
    queryFn: () => api<AdminSite[]>('/api/v1/admin/sites?status=active'),
  })
  const users = useQuery({
    queryKey: ['managed-users', 'active-for-sites'],
    queryFn: () => api<{ users: ManagedUser[] }>('/api/v1/admin/users?status=active'),
    enabled: Boolean(createDraft),
  })
  const policies = useQuery({
    queryKey: ['network-policies'],
    queryFn: () => api<SensorNetworkPolicy[]>('/api/v1/admin/network/policies'),
    enabled: Boolean(createDraft),
  })
  const selected = sites.data?.find((site) => site.id === selectedId)
    ?? activeSites.data?.find((site) => site.id === selectedId)
  const filteredSites = useMemo(() => {
    const values = [...(sites.data ?? [])].filter((site) => {
      if (quickFilter === 'default') return site.is_default
      if (quickFilter === 'sensors') return site.sensor_count > 0
      if (quickFilter === 'accounts') return site.utility_account_count > 0
      if (quickFilter === 'warning') return site.configuration_health === 'warning'
      return true
    })
    const statusOrder = { active: 0, disabled: 1, removed: 2 }
    return values.sort((left, right) => {
      if (sort === 'created') return Date.parse(right.created_at) - Date.parse(left.created_at)
      if (sort === 'activity') return Date.parse(right.latest_reading_at ?? '1970-01-01') - Date.parse(left.latest_reading_at ?? '1970-01-01')
      if (sort === 'sensors') return right.sensor_count - left.sensor_count
      if (sort === 'status') return statusOrder[left.lifecycle_state] - statusOrder[right.lifecycle_state]
      return left.name.localeCompare(right.name)
    })
  }, [quickFilter, sites.data, sort])

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin-sites'] })
    await queryClient.invalidateQueries({ queryKey: ['sites'] })
    window.dispatchEvent(new CustomEvent('pm-sites-changed'))
  }
  const create = useMutation({
    mutationFn: (draft: CreateDraft) => api<AdminSite & { next_step?: string }>('/api/v1/admin/sites', {
      method: 'POST',
      body: JSON.stringify({
        name: draft.name.trim(),
        code: draft.code,
        description: draft.description.trim() || null,
        location_label: draft.locationLabel.trim() || null,
        organization: draft.organization.trim() || null,
        timezone: draft.timezone,
        currency: draft.currency,
        locale: draft.locale,
        unit_system: draft.unitSystem,
        allowed_cidrs: [],
        allowed_domains: [],
        allow_public_polling: false,
        initial_user_ids: draft.userIds,
        make_default: draft.makeDefault,
        network_policy_mode: draft.networkPolicyMode,
        network_policy_id: draft.networkPolicyId || null,
        create_utility_account_after: draft.createUtilityAfter,
        confirmation: draft.confirmation,
      }),
    }),
    onSuccess: async (site) => {
      setCreateDraft(undefined)
      setCreateStep(0)
      setNotice('Site created.')
      await invalidate()
      setFilter('current')
      setSelectedId(site.id)
      if (site.next_step === 'create_utility_account') {
        await navigate(`/billing/accounts?site=${site.id}&create=account`)
      }
    },
  })

  function openCreate() {
    setCreateDraft(initialDraft())
    setCreateStep(0)
  }

  const canContinue = (draft: CreateDraft, step: number) => {
    if (step === 0) return Boolean(draft.name.trim() && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(draft.code))
    if (step === 1) return Boolean(draft.timezone && /^[A-Z]{3}$/.test(draft.currency) && draft.locale)
    if (step === 2) return draft.networkPolicyMode !== 'existing' || Boolean(draft.networkPolicyId)
    if (step === 5) return draft.confirmation
    return true
  }

  return (
    <section className="physical-sites">
      <header className="workspace-page-toolbar">
        <div>
          <span className="eyebrow">Sites & Network</span>
          <h2>Physical Sites</h2>
          <p>Manage site identity, timezone, network policy, assigned resources, and lifecycle state.</p>
        </div>
        <CanonicalAction id="site.create" surface="workspace_header" permitted={permissions.has('sites.create')}>
          <button className="button primary" onClick={openCreate}><Plus size={16} /> Add site</button>
        </CanonicalAction>
      </header>
      {notice && <p className="form-success" role="status">{notice}</p>}
      <div className="site-list-controls">
        <label className="search-field"><Search size={15} /><span className="sr-only">Search sites</span><input aria-label="Search sites" value={search} onChange={(event) => { setSearch(event.target.value) }} placeholder="Name, code, timezone, or location" /></label>
        <label><span>Status</span><select value={filter} onChange={(event) => { setFilter(event.target.value as SiteFilter) }}><option value="current">Current</option><option value="active">Active</option><option value="disabled">Disabled</option><option value="removed">Removed</option><option value="all">All</option></select></label>
        <label><span>Filter</span><select value={quickFilter} onChange={(event) => { setQuickFilter(event.target.value) }}><option value="all">All sites</option><option value="default">Default site</option><option value="sensors">Has sensors</option><option value="accounts">Has utility accounts</option><option value="warning">Configuration warning</option></select></label>
        <label><span>Sort</span><select value={sort} onChange={(event) => { setSort(event.target.value as SiteSort) }}><option value="name">Name</option><option value="created">Created date</option><option value="activity">Latest activity</option><option value="sensors">Sensor count</option><option value="status">Status</option></select></label>
      </div>
      {sites.isLoading ? <LoadingState label="Loading physical sites…" /> : sites.error ? <ErrorState error={sites.error} retry={() => { void sites.refetch() }} /> : filteredSites.length ? (
        <div className="site-list" role="list">
          {filteredSites.map((site) => (
            <article className="site-row" role="listitem" key={site.id}>
              <span className="site-row-icon"><Database /></span>
              <div className="site-row-identity">
                <div><h3>{site.name}</h3>{site.is_default && <span className="status status-good"><Star size={11} /> Default</span>}<StatusPill status={site.lifecycle_state === 'active' ? 'healthy' : site.lifecycle_state === 'disabled' ? 'pending' : 'revoked'} label={site.lifecycle_state} /></div>
                <small>{site.code} · {site.timezone}</small>
                <span>{site.network_policy_summary}</span>
              </div>
              <dl className="site-row-metrics">
                <div><dt>Sensors</dt><dd>{site.sensor_count}</dd></div>
                <div><dt>Accounts</dt><dd>{site.utility_account_count}</dd></div>
                <div><dt>Users</dt><dd>{site.assigned_user_count}</dd></div>
                <div><dt>Latest data</dt><dd>{site.latest_reading_at ? formatTime(site.latest_reading_at) : 'No readings'}</dd></div>
              </dl>
              <div className="site-row-health"><StatusPill status={site.configuration_health === 'ready' ? 'healthy' : 'pending'} label={configurationLabel(site)} /></div>
              <CanonicalAction id="site.view" surface="resource_row" resourceKey={site.id}>
                <button className="button secondary" onClick={() => { setSelectedId(site.id); setDetailSection('overview') }}>View details</button>
              </CanonicalAction>
            </article>
          ))}
        </div>
      ) : <EmptyState title="No matching sites" message={filter === 'removed' ? 'Removed sites will remain available here with their historical identity.' : 'Adjust the search or filter, or add an authorized physical site.'} />}
      {createDraft && (
        <SiteCreateWizard
          draft={createDraft}
          setDraft={setCreateDraft}
          step={createStep}
          setStep={setCreateStep}
          canContinue={canContinue}
          users={users.data?.users ?? []}
          policies={policies.data ?? []}
          error={create.error}
          pending={create.isPending}
          onClose={() => { setCreateDraft(undefined); create.reset() }}
          onCreate={() => { create.mutate(createDraft) }}
        />
      )}
      {selected && (
        <SiteDetails
          site={selected}
          session={session}
          section={detailSection}
          setSection={setDetailSection}
          activeSites={activeSites.data ?? []}
          onClose={() => { setSelectedId(undefined) }}
          onChanged={async (message, nextSite) => {
            setNotice(message)
            await invalidate()
            if (nextSite) setSelectedId(nextSite.id)
          }}
        />
      )}
    </section>
  )
}

function SiteCreateWizard({
  draft,
  setDraft,
  step,
  setStep,
  canContinue,
  users,
  policies,
  error,
  pending,
  onClose,
  onCreate,
}: {
  draft: CreateDraft
  setDraft: (value: CreateDraft | undefined) => void
  step: number
  setStep: (value: number) => void
  canContinue: (value: CreateDraft, step: number) => boolean
  users: ManagedUser[]
  policies: SensorNetworkPolicy[]
  error: unknown
  pending: boolean
  onClose: () => void
  onCreate: () => void
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="site-wizard" role="dialog" aria-modal="true" aria-label="Add physical site">
        <header><div><span className="eyebrow">Guided setup · Step {step + 1} of {wizardSteps.length}</span><h2>{wizardSteps[step]}</h2></div><button className="icon-button" onClick={onClose} aria-label="Close site wizard"><X /></button></header>
        <ol className="site-wizard-steps" aria-label="Site creation steps">{wizardSteps.map((label, index) => <li className={index === step ? 'active' : index < step ? 'complete' : ''} key={label}><span>{index < step ? <Check size={13} /> : index + 1}</span>{label}</li>)}</ol>
        <div className="site-wizard-body">
          {step === 0 && <div className="stack-form"><label><span>Site display name</span><input autoFocus value={draft.name} onChange={(event) => { const name = event.target.value; setDraft({ ...draft, name, code: draft.code && draft.code !== slug(draft.name) ? draft.code : slug(name) }) }} placeholder="Upland Site" /></label><label><span>Stable site code</span><input value={draft.code} onChange={(event) => { setDraft({ ...draft, code: slug(event.target.value) }) }} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" /><small className="field-help">Used as a stable audited identifier. It cannot be casually changed after creation.</small></label><label><span>Description <small>optional</small></span><textarea value={draft.description} onChange={(event) => { setDraft({ ...draft, description: event.target.value }) }} /></label><div className="form-columns"><label><span>Location label <small>optional, non-sensitive</small></span><input value={draft.locationLabel} onChange={(event) => { setDraft({ ...draft, locationLabel: event.target.value }) }} placeholder="Warehouse" /></label><label><span>Organization / group <small>optional</small></span><input value={draft.organization} onChange={(event) => { setDraft({ ...draft, organization: event.target.value }) }} /></label></div></div>}
          {step === 1 && <div className="stack-form"><label><span>IANA timezone</span><input value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }) }} list="site-timezones" /><datalist id="site-timezones"><option value="America/Los_Angeles" /><option value="America/Denver" /><option value="America/Chicago" /><option value="America/New_York" /><option value="UTC" /></datalist></label><div className="form-columns"><label><span>Currency</span><input value={draft.currency} maxLength={3} onChange={(event) => { setDraft({ ...draft, currency: event.target.value.toUpperCase() }) }} /></label><label><span>Locale</span><input value={draft.locale} onChange={(event) => { setDraft({ ...draft, locale: event.target.value }) }} /></label></div><label><span>Unit system</span><select value={draft.unitSystem} onChange={(event) => { setDraft({ ...draft, unitSystem: event.target.value as CreateDraft['unitSystem'] }) }}><option value="imperial">Imperial</option><option value="metric">Metric</option></select></label></div>}
          {step === 2 && <div className="stack-form"><fieldset><legend>Network-policy setup</legend><label className="choice-row"><input type="radio" name="network-mode" checked={draft.networkPolicyMode === 'inherit'} onChange={() => { setDraft({ ...draft, networkPolicyMode: 'inherit', networkPolicyId: '' }) }} /><span><strong>Inherit current server behavior</strong><small>Preserve the signed-ingress default for explicit review after creation.</small></span></label><label className="choice-row"><input type="radio" name="network-mode" checked={draft.networkPolicyMode === 'explicit'} onChange={() => { setDraft({ ...draft, networkPolicyMode: 'explicit', networkPolicyId: '' }) }} /><span><strong>Create explicit private-network policy</strong><small>Signed ingress from RFC1918/private networks; server pull remains denied until configured.</small></span></label><label className="choice-row"><input type="radio" name="network-mode" checked={draft.networkPolicyMode === 'existing'} onChange={() => { setDraft({ ...draft, networkPolicyMode: 'existing' }) }} /><span><strong>Copy an existing compatible policy</strong><small>Copies one selected direction without creating a second CIDR system.</small></span></label></fieldset>{draft.networkPolicyMode === 'existing' && <label><span>Existing policy</span><select value={draft.networkPolicyId} onChange={(event) => { setDraft({ ...draft, networkPolicyId: event.target.value }) }}><option value="">Choose a policy</option>{policies.map((policy) => <option value={policy.id} key={policy.id}>{policy.site_name} · {policy.direction.replaceAll('_', ' ')} · {policy.effective_summary}</option>)}</select></label>}<details><summary>Advanced settings</summary><p className="field-help">Permitted CIDRs, ingress modes, and server-to-device pull access remain editable after creation under Network Policy.</p></details></div>}
          {step === 3 && <div className="stack-form"><p>Assign existing users without changing their global roles. You cannot grant permissions beyond your own administrative scope.</p><fieldset className="selection-grid"><legend>Initial site access</legend>{users.length ? users.map((user) => <label key={user.id}><input type="checkbox" checked={draft.userIds.includes(user.id)} onChange={(event) => { setDraft({ ...draft, userIds: event.target.checked ? [...draft.userIds, user.id] : draft.userIds.filter((id) => id !== user.id) }) }} /><span><strong>{user.display_name}</strong><small>{user.email} · {user.roles.join(', ')}</small></span></label>) : <p className="field-help">No site-scoped active users are available. Organization-wide administrators retain access.</p>}</fieldset></div>}
          {step === 4 && <div className="stack-form"><label className="choice-row"><input type="radio" name="utility-next" checked={!draft.createUtilityAfter} onChange={() => { setDraft({ ...draft, createUtilityAfter: false }) }} /><span><strong>Configure later</strong><small>Create the site now without a utility account.</small></span></label><label className="choice-row"><input type="radio" name="utility-next" checked={draft.createUtilityAfter} onChange={() => { setDraft({ ...draft, createUtilityAfter: true }) }} /><span><strong>Create utility account after site creation</strong><small>Continue to the canonical Billing → Utility Accounts workflow after the site is saved.</small></span></label><label className="choice-row"><input type="checkbox" checked={draft.makeDefault} onChange={(event) => { setDraft({ ...draft, makeDefault: event.target.checked }) }} /><span><strong>Make this the default site</strong><small>The previous default is cleared transactionally.</small></span></label></div>}
          {step === 5 && <div className="stack-form"><dl className="review-grid"><div><dt>Name / code</dt><dd>{draft.name} · {draft.code}</dd></div><div><dt>Time and locale</dt><dd>{draft.timezone} · {draft.currency} · {draft.locale}</dd></div><div><dt>Network policy</dt><dd>{draft.networkPolicyMode.replaceAll('_', ' ')}</dd></div><div><dt>Initial access</dt><dd>{draft.userIds.length} user{draft.userIds.length === 1 ? '' : 's'}</dd></div><div><dt>Default</dt><dd>{draft.makeDefault ? 'Yes' : 'No'}</dd></div><div><dt>Next step</dt><dd>{draft.createUtilityAfter ? 'Create utility account' : 'Site details'}</dd></div></dl><label className="high-risk-confirm"><input type="checkbox" checked={draft.confirmation} onChange={(event) => { setDraft({ ...draft, confirmation: event.target.checked }) }} /><span>I reviewed the site identity, timezone, network boundary, and initial access assignments.</span></label></div>}
          {error ? <div className="form-error" role="alert"><strong>Site was not created</strong><span>{problemMessage(error)}</span></div> : null}
        </div>
        <footer><button className="button secondary" onClick={step ? () => { setStep(step - 1) } : onClose}>{step ? <><ChevronLeft size={15} /> Previous</> : 'Cancel'}</button>{step < wizardSteps.length - 1 ? <button className="button primary" disabled={!canContinue(draft, step)} onClick={() => { setStep(step + 1) }}>Continue <ChevronRight size={15} /></button> : <button className="button primary" disabled={!canContinue(draft, step) || pending} onClick={onCreate}>{pending ? 'Creating…' : 'Create site'}</button>}</footer>
      </section>
    </div>
  )
}

function SiteDetails({
  site,
  session,
  section,
  setSection,
  activeSites,
  onClose,
  onChanged,
}: {
  site: AdminSite
  session: Session
  section: DetailSection
  setSection: (value: DetailSection) => void
  activeSites: AdminSite[]
  onClose: () => void
  onChanged: (message: string, site?: AdminSite) => Promise<void>
}) {
  const permissions = sessionPermissions(session)
  const [editing, setEditing] = useState(false)
  const [edit, setEdit] = useState({
    name: site.name,
    description: site.description ?? '',
    locationLabel: site.location_label ?? '',
    organization: site.organization ?? '',
    timezone: site.timezone,
    currency: site.currency,
    locale: site.locale,
    unitSystem: site.unit_system,
    reason: '',
    timezoneConfirmed: false,
  })
  const [lifecycle, setLifecycle] = useState<'disable' | 'enable' | 'remove' | 'restore'>()
  const audit = useQuery({
    queryKey: ['site-audit', site.id],
    queryFn: () => api<Array<{ id: string; occurred_at: string; action: string; outcome: string; actor_id?: string; details: Record<string, unknown> }>>(`/api/v1/admin/sites/${site.id}/audit`),
    enabled: section === 'audit' && permissions.has('sites.view_audit'),
  })
  const editMutation = useMutation({
    mutationFn: () => api<AdminSite>(`/api/v1/admin/sites/${site.id}`, {
      method: 'PUT',
      body: JSON.stringify({
        revision: site.revision,
        name: edit.name,
        description: edit.description || null,
        location_label: edit.locationLabel || null,
        organization: edit.organization || null,
        timezone: edit.timezone,
        currency: edit.currency,
        locale: edit.locale,
        unit_system: edit.unitSystem,
        timezone_change_confirmed: edit.timezoneConfirmed,
        reason: edit.reason,
      }),
    }),
    onSuccess: async (value) => { setEditing(false); await onChanged('Site details updated.', value) },
  })
  const setDefault = useMutation({
    mutationFn: () => api<AdminSite>(`/api/v1/admin/sites/${site.id}/set-default`, {
      method: 'POST',
      body: JSON.stringify({ revision: site.revision, reason: 'Administrator selected the default site' }),
    }),
    onSuccess: async (value) => { await onChanged('Default site updated.', value) },
  })
  const sections: Array<[DetailSection, string]> = [
    ['overview', 'Overview'],
    ['devices', 'Assigned devices'],
    ['accounts', 'Utility accounts'],
    ['access', 'Users and access'],
    ['network', 'Network policy'],
    ['coverage', 'Data coverage'],
    ['audit', 'Audit history'],
  ]
  useEffect(() => {
    setEdit({
      name: site.name,
      description: site.description ?? '',
      locationLabel: site.location_label ?? '',
      organization: site.organization ?? '',
      timezone: site.timezone,
      currency: site.currency,
      locale: site.locale,
      unitSystem: site.unit_system,
      reason: '',
      timezoneConfirmed: false,
    })
  }, [site])
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="site-details-dialog" role="dialog" aria-modal="true" aria-label="Physical site details">
        <header><div><span className="eyebrow">{site.code}</span><h2>{site.name}</h2><div className="inline-actions">{site.is_default && <StatusPill status="healthy" label="Default site" />}<StatusPill status={site.lifecycle_state === 'active' ? 'healthy' : site.lifecycle_state === 'disabled' ? 'pending' : 'revoked'} label={site.lifecycle_state} /></div></div><button className="icon-button" onClick={onClose} aria-label="Close site details"><X /></button></header>
        <nav className="detail-tab-bar" aria-label="Site detail sections">{sections.map(([id, label]) => <button role="tab" aria-selected={section === id} key={id} onClick={() => { setSection(id) }}>{label}</button>)}</nav>
        <div className="site-detail-content">
          {section === 'overview' && !editing && <><dl className="site-overview-grid"><div><dt>Stable identity</dt><dd>{site.code}<small>{site.id}</small></dd></div><div><dt>Timezone</dt><dd>{site.timezone}</dd></div><div><dt>Locale</dt><dd>{site.locale} · {site.currency} · {site.unit_system}</dd></div><div><dt>Created</dt><dd>{formatTime(site.created_at)}</dd></div><div><dt>Network</dt><dd>{site.network_policy_summary}</dd></div><div><dt>Configuration</dt><dd>{configurationLabel(site)}</dd></div></dl>{site.description && <p>{site.description}</p>}<div className="site-detail-actions"><CanonicalAction id="site.edit" surface="resource_detail" resourceKey={site.id} permitted={permissions.has('sites.edit')}><button className="button secondary" disabled={site.lifecycle_state === 'removed'} onClick={() => { setEditing(true) }}><Pencil size={15} /> Edit</button></CanonicalAction>{!site.is_default && site.lifecycle_state === 'active' && <CanonicalAction id="site.set_default" surface="resource_detail" resourceKey={site.id} permitted={permissions.has('sites.set_default')}><button className="button secondary" disabled={setDefault.isPending} onClick={() => { setDefault.mutate() }}><Star size={15} /> Set as default</button></CanonicalAction>}{site.lifecycle_state === 'active' && <CanonicalAction id="site.disable" surface="resource_detail" resourceKey={site.id} permitted={permissions.has('sites.disable')}><button className="button secondary" onClick={() => { setLifecycle('disable') }}><CircleOff size={15} /> Disable</button></CanonicalAction>}{site.lifecycle_state === 'disabled' && <CanonicalAction id="site.enable" surface="resource_detail" resourceKey={site.id} permitted={permissions.has('sites.disable')}><button className="button secondary" onClick={() => { setLifecycle('enable') }}><RefreshCw size={15} /> Enable</button></CanonicalAction>}{site.lifecycle_state !== 'removed' && <CanonicalAction id="site.remove" surface="resource_detail" resourceKey={site.id} permitted={permissions.has('sites.remove')}><button className="button ghost danger-text" onClick={() => { setLifecycle('remove') }}><Archive size={15} /> Remove site</button></CanonicalAction>}{site.lifecycle_state === 'removed' && <CanonicalAction id="site.restore" surface="resource_detail" resourceKey={site.id} permitted={permissions.has('sites.restore')}><button className="button secondary" onClick={() => { setLifecycle('restore') }}><RefreshCw size={15} /> Restore site</button></CanonicalAction>}</div>{setDefault.error && <p className="form-error" role="alert">{problemMessage(setDefault.error)}</p>}</>}
          {section === 'overview' && editing && <form className="stack-form" onSubmit={(event: FormEvent) => { event.preventDefault(); editMutation.mutate() }}><div className="form-columns"><label><span>Display name</span><input value={edit.name} onChange={(event) => { setEdit({ ...edit, name: event.target.value }) }} /></label><label><span>Stable code</span><input value={site.code} disabled /><small className="field-help">The immutable internal ID and stable code remain unchanged.</small></label></div><label><span>Description</span><textarea value={edit.description} onChange={(event) => { setEdit({ ...edit, description: event.target.value }) }} /></label><div className="form-columns"><label><span>Location label</span><input value={edit.locationLabel} onChange={(event) => { setEdit({ ...edit, locationLabel: event.target.value }) }} /></label><label><span>Organization</span><input value={edit.organization} onChange={(event) => { setEdit({ ...edit, organization: event.target.value }) }} /></label></div><div className="form-columns"><label><span>Timezone</span><input value={edit.timezone} onChange={(event) => { setEdit({ ...edit, timezone: event.target.value, timezoneConfirmed: false }) }} /></label><label><span>Currency</span><input value={edit.currency} onChange={(event) => { setEdit({ ...edit, currency: event.target.value.toUpperCase() }) }} /></label><label><span>Locale</span><input value={edit.locale} onChange={(event) => { setEdit({ ...edit, locale: event.target.value }) }} /></label></div>{edit.timezone !== site.timezone && <div className="high-risk-warning"><strong>Timezone change impacts</strong><p>Local timestamps, TOU evaluation, billing-cycle boundaries, daily summaries, and scheduled jobs may change. Raw UTC readings and finalized reports remain immutable.</p><label><input type="checkbox" checked={edit.timezoneConfirmed} onChange={(event) => { setEdit({ ...edit, timezoneConfirmed: event.target.checked }) }} /> I reviewed these timezone impacts.</label></div>}<label><span>Change reason</span><input value={edit.reason} onChange={(event) => { setEdit({ ...edit, reason: event.target.value }) }} /></label>{editMutation.error && <p className="form-error" role="alert">{problemMessage(editMutation.error)}</p>}<footer className="dialog-actions"><button type="button" className="button secondary" onClick={() => { setEditing(false) }}>Cancel</button><button className="button primary" disabled={!edit.reason.trim() || (edit.timezone !== site.timezone && !edit.timezoneConfirmed) || editMutation.isPending}>Save site</button></footer></form>}
          {section === 'devices' && <><ResourceList icon={<MapPin />} title="Assigned sensors" values={site.dependencies.active.sensors.map((item) => ({ id: item.id, name: item.name, detail: `${item.status} · ${item.latest_reading_at ? formatTime(item.latest_reading_at) : 'No reading'}` }))} empty="No active sensors are assigned." />{site.lifecycle_state === 'active' && <CanonicalAction id="device.enroll" surface="contextual_link"><Link className="button secondary" to={`/monitoring/enrollment?site=${site.id}`}><Plus size={15} /> Enroll a sensor for this site</Link></CanonicalAction>}</>}
          {section === 'accounts' && <ResourceList icon={<Database />} title="Utility accounts" values={site.dependencies.active.utility_accounts.map((item) => ({ id: item.id, name: item.name, detail: item.status }))} empty="No active utility accounts are assigned." />}
          {section === 'access' && <ResourceList icon={<Users />} title="Site-scoped users" values={site.dependencies.active.users.map((item) => ({ id: item.id, name: item.display_name, detail: item.email }))} empty="No explicit site-scoped users are assigned. Organization-wide administrators may still inspect the site." />}
          {section === 'network' && <div className="resource-list"><header><ShieldCheck /><div><h3>Network policy</h3><p>Signed device authentication remains mandatory in every mode.</p></div></header>{site.network_policies.map((policy) => <article key={policy.id}><strong>{policy.direction.replaceAll('_', ' ')}</strong><span>{policy.summary}</span><small>{policy.cidrs.length} configured CIDR{policy.cidrs.length === 1 ? '' : 's'} · revision {policy.revision}</small></article>)}</div>}
          {section === 'coverage' && <dl className="site-overview-grid"><div><dt>Retained readings</dt><dd>{site.dependencies.retained.raw_readings.toLocaleString()}</dd></div><div><dt>Coverage begins</dt><dd>{site.dependencies.retained.history_start ? formatTime(site.dependencies.retained.history_start) : 'No readings'}</dd></div><div><dt>Latest reading</dt><dd>{site.dependencies.retained.history_end ? formatTime(site.dependencies.retained.history_end) : 'No readings'}</dd></div><div><dt>Billing cycles</dt><dd>{site.dependencies.retained.billing_cycles}</dd></div><div><dt>Circuits</dt><dd>{site.dependencies.retained.circuits}</dd></div><div><dt>Historical costs</dt><dd>{site.dependencies.retained.costs_and_rate_assignments ? 'Preserved' : 'None'}</dd></div></dl>}
          {section === 'audit' && (audit.isLoading ? <LoadingState /> : audit.error ? <ErrorState error={audit.error} /> : audit.data?.length ? <div className="audit-list">{audit.data.map((event) => <article key={event.id}><time>{formatTime(event.occurred_at)}</time><span className={`audit-outcome ${event.outcome}`}>{event.outcome}</span><p><strong>{event.action}</strong><small>Revision {auditRevision(event.details)} · actor {event.actor_id?.slice(0, 8) ?? 'system'}</small></p></article>)}</div> : <EmptyState title="No site audit events" message="Site creation and lifecycle changes appear here." />)}
        </div>
        {lifecycle && <SiteLifecycleDialog site={site} action={lifecycle} activeSites={activeSites} onClose={() => { setLifecycle(undefined) }} onChanged={onChanged} />}
      </section>
    </div>
  )
}

function ResourceList({ icon, title, values, empty }: { icon: ReactNode; title: string; values: Array<{ id: string; name: string; detail: string }>; empty: string }) {
  return <div className="resource-list"><header>{icon}<div><h3>{title}</h3><p>{values.length} active assignment{values.length === 1 ? '' : 's'}</p></div></header>{values.length ? values.map((item) => <article key={item.id}><strong>{item.name}</strong><span>{item.detail}</span><small>{item.id}</small></article>) : <EmptyState title={`No ${title.toLowerCase()}`} message={empty} />}</div>
}

function SiteLifecycleDialog({
  site,
  action,
  activeSites,
  onClose,
  onChanged,
}: {
  site: AdminSite
  action: 'disable' | 'enable' | 'remove' | 'restore'
  activeSites: AdminSite[]
  onClose: () => void
  onChanged: (message: string, site?: AdminSite) => Promise<void>
}) {
  const [reason, setReason] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [reviewed, setReviewed] = useState(false)
  const [dependencies, setDependencies] = useState<SiteDependencySummary>(site.dependencies)
  const [sensorActions, setSensorActions] = useState<Record<string, { action: 'archive' | 'transfer'; target?: string }>>({})
  const [accountActions, setAccountActions] = useState<Record<string, { action: 'archive' | 'transfer'; target?: string }>>({})
  const [endedUsers, setEndedUsers] = useState<string[]>([])
  const refreshDependencies = useQuery({
    queryKey: ['site-dependencies', site.id, site.revision],
    queryFn: () => api<SiteDependencySummary>(`/api/v1/admin/sites/${site.id}/dependencies`),
    enabled: action === 'remove',
  })
  useEffect(() => {
    if (refreshDependencies.data) setDependencies(refreshDependencies.data)
  }, [refreshDependencies.data])
  const resolve = useMutation({
    mutationFn: () => api<{ site: AdminSite; resolution: unknown }>(`/api/v1/admin/sites/${site.id}/transfer-resources`, {
      method: 'POST',
      body: JSON.stringify({
        revision: dependencies.revision,
        reason,
        sensors: dependencies.active.sensors.map((sensor) => ({
          device_id: sensor.id,
          action: sensorActions[sensor.id]?.action,
          target_site_id: sensorActions[sensor.id]?.target || null,
        })),
        utility_accounts: dependencies.active.utility_accounts.map((account) => ({
          utility_account_id: account.id,
          action: accountActions[account.id]?.action,
          target_site_id: accountActions[account.id]?.target || null,
        })),
        end_user_access_ids: endedUsers,
      }),
    }),
    onSuccess: async (value) => {
      setDependencies(value.site.dependencies)
      await onChanged('Site dependencies resolved.', value.site)
    },
  })
  const lifecycle = useMutation({
    mutationFn: () => api<AdminSite>(`/api/v1/admin/sites/${site.id}/${action}`, {
      method: 'POST',
      body: JSON.stringify(action === 'remove'
        ? { revision: dependencies.revision, reason, confirmation, dependency_reviewed: reviewed }
        : action === 'restore'
          ? { revision: site.revision, reason, confirm_high_risk: reviewed }
          : { revision: site.revision, reason }),
    }),
    onSuccess: async (value) => {
      await onChanged(
        action === 'disable' ? 'Site disabled. Signed ingestion continues; ordinary access and new assignments are restricted.'
          : action === 'enable' ? 'Site enabled.'
            : action === 'remove' ? 'Site removed from active navigation. Historical data was preserved.'
              : 'Site restored in a disabled state. Users, policies, sensors, accounts, and rates require review.',
        value,
      )
      onClose()
    },
  })
  const resolveReady = dependencies.active.sensors.every((item) => {
    const resolution = sensorActions[item.id]
    return resolution && (resolution.action === 'archive' || Boolean(resolution.target))
  }) && dependencies.active.utility_accounts.every((item) => {
    const resolution = accountActions[item.id]
    return resolution && (resolution.action === 'archive' || Boolean(resolution.target))
  }) && dependencies.active.users.every((item) => endedUsers.includes(item.id))
  const removalReady = !dependencies.blockers.length
    && !dependencies.required_actions.length
    && reviewed
    && reason.trim().length >= 3
    && [site.name, site.code].includes(confirmation)
  return (
    <ActionScope scopeKey={`site-lifecycle:${site.id}:${action}`}>
    <div className="nested-modal-backdrop" role="presentation">
      <section className="site-lifecycle-dialog" role="dialog" aria-modal="true" aria-label={`${action} site`}>
        <header><div><span className="eyebrow">Site lifecycle · Revision {action === 'remove' ? dependencies.revision : site.revision}</span><h2>{action.charAt(0).toUpperCase()}{action.slice(1)} {site.name}?</h2></div><button className="icon-button" onClick={onClose} aria-label={`Close ${action} site confirmation`}><X /></button></header>
        {action === 'remove' ? <><div className="removal-impact"><strong>Historical records are retained</strong><p>The site will be removed from active navigation and new assignments. Historical measurements, costs, bills, alerts, and audit records remain linked to {site.code}.</p></div><dl className="site-dependency-summary"><div><dt>Status</dt><dd>{site.lifecycle_state}</dd></div><div><dt>Default site</dt><dd>{site.is_default ? 'Yes — blocked' : 'No'}</dd></div><div><dt>Sensors</dt><dd>{dependencies.active.sensors.length}</dd></div><div><dt>Accounts</dt><dd>{dependencies.active.utility_accounts.length}</dd></div><div><dt>Users</dt><dd>{dependencies.active.users.length}</dd></div><div><dt>Active alerts</dt><dd>{dependencies.active.alerts}</dd></div><div><dt>Retained readings</dt><dd>{dependencies.retained.raw_readings.toLocaleString()}</dd></div><div><dt>History range</dt><dd>{dependencies.retained.history_start ? `${formatTime(dependencies.retained.history_start)} — ${formatTime(dependencies.retained.history_end)}` : 'No readings'}</dd></div></dl>{dependencies.blockers.map((blocker) => <p className="form-error" key={blocker.code}><strong>{blocker.code.replaceAll('_', ' ')}</strong><span>{blocker.message}</span></p>)}{dependencies.active.sensors.length > 0 && <DependencyResolution title="Sensors" values={dependencies.active.sensors} activeSites={activeSites.filter((item) => item.id !== site.id)} actions={sensorActions} setActions={setSensorActions} />}{dependencies.active.utility_accounts.length > 0 && <DependencyResolution title="Utility accounts" values={dependencies.active.utility_accounts} activeSites={activeSites.filter((item) => item.id !== site.id)} actions={accountActions} setActions={setAccountActions} />}{dependencies.active.users.length > 0 && <fieldset className="dependency-users"><legend>End active site access</legend>{dependencies.active.users.map((user) => <label key={user.id}><input type="checkbox" checked={endedUsers.includes(user.id)} onChange={(event) => { setEndedUsers(event.target.checked ? [...endedUsers, user.id] : endedUsers.filter((id) => id !== user.id)) }} /><span>{user.display_name}<small>{user.email} · user account is preserved</small></span></label>)}</fieldset>}{dependencies.required_actions.length > 0 && <CanonicalAction id="site.transfer_resources" surface="dialog" resourceKey={site.id}><button className="button secondary" disabled={!resolveReady || reason.trim().length < 3 || resolve.isPending} onClick={() => { resolve.mutate() }}><ArrowRight size={15} /> Resolve selected dependencies</button></CanonicalAction>}<label><span>Removal reason</span><textarea value={reason} onChange={(event) => { setReason(event.target.value) }} /></label><label><span>Type exact site name or code</span><input value={confirmation} onChange={(event) => { setConfirmation(event.target.value) }} aria-describedby="site-removal-confirm-help" /><small id="site-removal-confirm-help">Enter {site.name} or {site.code}</small></label><label className="high-risk-confirm"><input type="checkbox" checked={reviewed} onChange={(event) => { setReviewed(event.target.checked) }} /><span>I reviewed retained history, active resources, transfers, archives, access changes, and blockers.</span></label></> : <><p>{action === 'disable' ? 'Ordinary selection and new assignments will stop. Existing signed sensor ingestion continues, and all history remains available to administrators.' : action === 'enable' ? 'The site will return to ordinary selection and may accept new assignments.' : 'The original immutable site ID will be restored in a disabled state. Sensors, accounts, users, network policy, and rates will not reactivate automatically.'}</p><label><span>Reason</span><textarea value={reason} onChange={(event) => { setReason(event.target.value) }} /></label>{action === 'restore' && <label className="high-risk-confirm"><input type="checkbox" checked={reviewed} onChange={(event) => { setReviewed(event.target.checked) }} /><span>I will review users, network policy, sensors, utility accounts, and rates before enabling this site.</span></label>}</>}
        {(resolve.error || lifecycle.error) && <div className="form-error" role="alert"><strong>Site change was not completed</strong><span>{problemMessage(resolve.error ?? lifecycle.error)}</span></div>}
        <footer><button className="button secondary" onClick={onClose}>Cancel</button><CanonicalAction id={action === 'remove' ? 'site.remove' : action === 'restore' ? 'site.restore' : action === 'disable' ? 'site.disable' : 'site.enable'} surface="dialog" resourceKey={site.id}><button className={`button ${action === 'remove' ? 'danger' : 'primary'}`} disabled={lifecycle.isPending || (action === 'remove' ? !removalReady : reason.trim().length < 3 || (action === 'restore' && !reviewed))} onClick={() => { lifecycle.mutate() }}>{lifecycle.isPending ? 'Saving…' : action === 'remove' ? 'Remove site' : action === 'restore' ? 'Restore site' : action === 'disable' ? 'Disable site' : 'Enable site'}</button></CanonicalAction></footer>
      </section>
    </div>
    </ActionScope>
  )
}

function DependencyResolution({
  title,
  values,
  activeSites,
  actions,
  setActions,
}: {
  title: string
  values: Array<{ id: string; name: string }>
  activeSites: AdminSite[]
  actions: Record<string, { action: 'archive' | 'transfer'; target?: string }>
  setActions: (value: Record<string, { action: 'archive' | 'transfer'; target?: string }>) => void
}) {
  return <fieldset className="dependency-resolution"><legend>{title}</legend>{values.map((item) => { const value = actions[item.id]; return <div key={item.id}><strong>{item.name}</strong><select aria-label={`${title} action for ${item.name}`} value={value?.action ?? ''} onChange={(event) => { const action = event.target.value as 'archive' | 'transfer'; setActions({ ...actions, [item.id]: { action } }) }}><option value="">Choose action</option><option value="archive">Archive with site</option><option value="transfer">Transfer</option></select>{value?.action === 'transfer' && <select aria-label={`Destination for ${item.name}`} value={value.target ?? ''} onChange={(event) => { setActions({ ...actions, [item.id]: { ...value, target: event.target.value } }) }}><option value="">Choose active site</option>{activeSites.map((target) => <option value={target.id} key={target.id}>{target.name}</option>)}</select>}</div>})}</fieldset>
}
