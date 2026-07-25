import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, FileSearch, Plus, RefreshCw, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, json, request } from '../../api/client'
import { adaptRateEvidence, adaptRateSources, adaptRateVersions } from '../../api/adapters'
import { objectList, record, stringValue } from '../../api/validation'
import { EmptyState, ErrorState, InlineNotice, LoadingState } from '../../components/feedback/States'
import { TabList } from '../../components/layout/Layout'
import type { ElectricService, Home, RateEvidence, RatePlanVersion, RateSource } from '../../types/models'
import { statusLabel } from '../../utils/format'
import { StructuredRateEditor, type SavedDraft } from './StructuredRateEditor'
import { adaptRatePlanDraft } from './rateDocument'

interface PlanRow {
  id: string
  name: string
  code: string
  status: string
  revision: number
  pricingModel?: string
  versionId?: string
  version?: number
}

type RateView = 'plans' | 'sources' | 'versions' | 'evidence' | 'removed' | 'adjustments'

const rateViews: ReadonlyArray<readonly [RateView, string]> = [
  ['plans', 'Custom editor'],
  ['sources', 'Sources'],
  ['versions', 'Versions'],
  ['evidence', 'Evidence'],
  ['removed', 'Removed'],
  ['adjustments', 'Adjustments'],
]

function plansAdapter(value: unknown): PlanRow[] {
  const source = Array.isArray(value) ? objectList(value) : objectList(record(value).plans)
  return source.map((item) => {
    const latest = item.latest_version && typeof item.latest_version === 'object'
      ? record(item.latest_version)
      : {}
    return {
      id: stringValue(item.id),
      name: stringValue(item.name, stringValue(item.plan_name, 'Rate plan')),
      code: stringValue(item.code, stringValue(item.plan_code)),
      status: stringValue(item.status, 'draft'),
      revision: Number(item.lifecycle_revision ?? item.revision ?? 1),
      pricingModel: stringValue(latest.pricing_model, stringValue(item.pricing_model)) || undefined,
      versionId: stringValue(latest.id, stringValue(item.rate_version_id)) || undefined,
      version: Number(latest.version ?? item.version ?? 0) || undefined,
    }
  })
}

export function AdvancedRateSettings({
  home,
  services,
}: {
  home: Home
  services: ElectricService[]
}) {
  const [view, setView] = useState<RateView>('plans')
  const plans = useQuery({ queryKey: ['managed-rate-plans'], queryFn: () => request('/api/v1/rates/plans', {}, plansAdapter) })
  const removedPlans = useQuery({
    queryKey: ['removed-rate-plans'],
    queryFn: async () => {
      const [removed, retired] = await Promise.all([
        request('/api/v1/rates/plans?status=removed', {}, plansAdapter),
        request('/api/v1/rates/plans?status=retired', {}, plansAdapter),
      ])
      return [...removed, ...retired]
    },
    enabled: view === 'removed',
  })
  const sources = useQuery({ queryKey: ['rate-sources'], queryFn: () => request('/api/v1/admin/rate-sources', {}, adaptRateSources), enabled: view === 'sources' })
  return (
    <div className="advanced-rates">
      <TabList idBase="advanced-rates" label="Advanced rate settings" value={view} items={rateViews} onChange={setView} />
      <div
        id={`advanced-rates-panel-${view}`}
        className="rate-tab-panel"
        role="tabpanel"
        aria-labelledby={`advanced-rates-tab-${view}`}
        tabIndex={0}
      >
        {view === 'plans' && <PlanManager home={home} services={services} plans={plans.data ?? []} loading={plans.isLoading} error={plans.error} />}
        {view === 'sources' && <SourceManager sources={sources.data ?? []} loading={sources.isLoading} error={sources.error} />}
        {view === 'versions' && <VersionList plans={plans.data ?? []} />}
        {view === 'evidence' && <EvidenceList plans={plans.data ?? []} />}
        {view === 'removed' && <RemovedPlans plans={removedPlans.data ?? []} loading={removedPlans.isLoading} error={removedPlans.error} />}
        {view === 'adjustments' && <Adjustments services={services} />}
      </div>
    </div>
  )
}

function VersionList({ plans }: { plans: PlanRow[] }) {
  const versions = useQuery({
    queryKey: ['all-rate-versions', plans.map((plan) => plan.id).join(':')],
    queryFn: async () => {
      const rows = await Promise.all(plans.map(async (plan) => {
        const items = await request(`/api/v1/rates/plans/${plan.id}/versions`, {}, adaptRateVersions)
        return items.map((version) => ({ ...version, planName: plan.name, planCode: plan.code }))
      }))
      return rows.flat()
    },
    enabled: plans.length > 0,
  })
  if (versions.isLoading) return <LoadingState label="Loading rate versions…" />
  if (versions.error) return <ErrorState error={versions.error} retry={() => { void versions.refetch() }} />
  return <PagedVersionList rows={versions.data ?? []} />
}

function PagedVersionList({ rows }: { rows: Array<RatePlanVersion & { planName: string; planCode: string }> }) {
  const [query, setQuery] = useState('')
  const matching = rows.filter((row) => `${row.planName} ${row.planCode} ${row.status} ${row.version}`.toLowerCase().includes(query.trim().toLowerCase()))
  return <section className="rate-advanced-panel"><div className="section-heading"><div><h3>Rate versions</h3><p>Every effective-dated version, including immutable history.</p></div><label className="compact-search"><span className="sr-only">Search rate versions</span><input type="search" placeholder="Search versions" value={query} onChange={(event) => { setQuery(event.target.value) }} /></label></div>{matching.length === 0 ? <EmptyState compact title="No rate versions" message="Create or import a rate plan to prepare its first draft." /> : <ul className="structured-list">{matching.map((row) => <li key={row.id}><div><strong>{row.planName} · v{row.version}</strong><span>{row.planCode} · {statusLabel(row.status)} · effective {row.effectiveFrom ?? 'not set'}{row.immutable ? ' · immutable' : ' · editable draft'}</span></div><span className={`pill ${row.status === 'active' ? 'success' : ''}`}>{statusLabel(row.pricingModel ?? 'unknown')}</span></li>)}</ul>}</section>
}

function EvidenceList({ plans }: { plans: PlanRow[] }) {
  const evidence = useQuery({
    queryKey: ['all-rate-evidence', plans.map((plan) => plan.versionId).join(':')],
    queryFn: async () => {
      const rows = await Promise.all(plans.map(async (plan) => {
        const versions = await request(`/api/v1/rates/plans/${plan.id}/versions`, {}, adaptRateVersions)
        const versionEvidence = await Promise.all(versions.map(async (version) => {
          const response = await request<unknown>(`/api/v1/rates/versions/${version.id}`)
          return adaptRateEvidence(response).map((item) => ({ ...item, versionId: version.id, planName: plan.name }))
        }))
        return versionEvidence.flat()
      }))
      return rows.flat()
    },
    enabled: plans.some((plan) => Boolean(plan.versionId)),
  })
  if (evidence.isLoading) return <LoadingState label="Loading source evidence…" />
  if (evidence.error) return <ErrorState error={evidence.error} retry={() => { void evidence.refetch() }} />
  const rows: Array<RateEvidence & { planName: string }> = evidence.data ?? []
  return <section className="rate-advanced-panel"><div className="section-heading"><div><h3>Source evidence</h3><p>Checksums and capture references retained with exact rate versions.</p></div></div>{rows.length === 0 ? <EmptyState compact title="No source evidence" message="Evidence appears after a managed source check or reviewed bill import." /> : <ul className="structured-list">{rows.map((row) => <li key={`${row.versionId}-${row.id}`}><div><strong>{row.planName}</strong><span>{row.displaySource} · {statusLabel(row.relationship)}{row.capturedAt ? ` · captured ${row.capturedAt}` : ''}</span><details className="technical-details"><summary>Checksum</summary><code>{row.checksum ?? 'Not provided'}</code></details></div><FileSearch /></li>)}</ul>}</section>
}

function RemovedPlans({ plans, loading, error }: { plans: PlanRow[]; loading: boolean; error: unknown }) {
  const client = useQueryClient()
  const restore = useMutation({
    mutationFn: (plan: PlanRow) => request(`/api/v1/admin/rate-plans/${plan.id}/restore`, json('POST', {
      expected_revision: plan.revision,
      reason: 'Restored from Single Home Billing',
      idempotency_key: crypto.randomUUID(),
    })),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['managed-rate-plans'] }),
        client.invalidateQueries({ queryKey: ['removed-rate-plans'] }),
      ])
    },
  })
  if (loading) return <LoadingState label="Loading removed plans…" />
  if (error) return <ErrorState error={error} />
  return (
    <section className="rate-advanced-panel">
      <h3>Removed and retired plans</h3>
      {plans.length === 0
        ? <EmptyState title="No removed plans" message="Retired plans remain here with their versions, assignments, costs, and evidence intact." />
        : <ul className="structured-list">{plans.map((plan) => <li key={plan.id}><div><strong>{plan.name}</strong><span>{statusLabel(plan.status)} · assignments are not restored automatically</span></div><button type="button" className="button secondary compact" disabled={restore.isPending} onClick={() => { restore.mutate(plan); }}>Restore</button></li>)}</ul>}
      {restore.error && <InlineNotice tone="danger">{errorMessage(restore.error)}</InlineNotice>}
    </section>
  )
}

/*
function RateList({
  title,
  empty,
  plans,
  loading,
  error,
  detail,
}: {
  title: string
  empty: string
  plans: PlanRow[]
  loading: boolean
  error: unknown
  detail: (plan: PlanRow) => string
}) {
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const pageSize = 10
  const matching = plans.filter((plan) => `${plan.name} ${plan.code} ${plan.status}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  const pageCount = Math.max(1, Math.ceil(matching.length / pageSize))
  const safePage = Math.min(page, pageCount - 1)
  const visible = matching.slice(safePage * pageSize, (safePage + 1) * pageSize)
  if (loading) return <LoadingState label={`Loading ${title.toLocaleLowerCase()}…`} />
  if (error) return <ErrorState error={error} />
  return (
    <section className="rate-advanced-panel">
      <div className="section-heading">
        <div><h3>{title}</h3><p>{matching.length} matching record{matching.length === 1 ? '' : 's'}</p></div>
        <label className="compact-search"><span className="sr-only">Search {title.toLocaleLowerCase()}</span><input type="search" placeholder={`Search ${title.toLocaleLowerCase()}`} value={query} onChange={(event) => { setQuery(event.target.value); setPage(0); }} /></label>
      </div>
      {plans.length === 0 ? <EmptyState compact title={title} message={empty} /> : matching.length === 0 ? <EmptyState compact title="No matching records" message="Clear the search or try a different name, code, or status." /> : <ul className="structured-list">{visible.map((plan) => <li key={plan.id}><div><strong>{plan.name}</strong><span>{detail(plan)}</span></div><FileSearch aria-hidden="true" /></li>)}</ul>}
      {pageCount > 1 && <nav className="pagination" aria-label={`${title} pages`}><button type="button" className="button secondary compact" disabled={safePage === 0} onClick={() => { setPage((current) => Math.max(0, current - 1)); }}>Previous</button><span>Page {safePage + 1} of {pageCount}</span><button type="button" className="button secondary compact" disabled={safePage + 1 >= pageCount} onClick={() => { setPage((current) => Math.min(pageCount - 1, current + 1)); }}>Next</button></nav>}
    </section>
  )
}

function ReducedPlanManager({ home, plans, loading, error }: { home: Home; plans: PlanRow[]; loading: boolean; error: unknown }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('My electric plan')
  const [code, setCode] = useState('CUSTOM-HOME')
  const [model, setModel] = useState<'flat' | 'time_of_use' | 'tiered' | 'time_of_use_tiered'>('flat')
  const [flatRate, setFlatRate] = useState('0.25000')
  const [offPeak, setOffPeak] = useState('0.25000')
  const [onPeak, setOnPeak] = useState('0.45000')
  const [tiers, setTiers] = useState([
    { name: 'Tier 1', upper: '500', rate: '0.25000' },
    { name: 'Tier 2', upper: '', rate: '0.35000' },
  ])
  const document = useMemo(() => {
    const tiered = model === 'tiered' || model === 'time_of_use_tiered'
    const tou = model === 'time_of_use' || model === 'time_of_use_tiered'
    return {
      schema_version: 'power-monitor-rate-plan/1.0',
      plan_name: name,
      plan_code: code.toUpperCase().replace(/[^A-Z0-9._-]+/g, '-'),
      utility: 'Southern California Edison',
      description: 'Administrator-defined Single Home rate plan',
      currency: home.currency,
      timezone: home.timezone,
      pricing_model: model,
      flat_rate_per_kwh: model === 'flat' ? flatRate : null,
      billing_cycle: {
        expected_start_day: 1,
        threshold: { basis: 'fixed_cycle_kwh', daily_baseline_kwh: null, baseline_region: null, baseline_category: null, rounding_policy: 'none', seasonal_baselines: [], source_citation: null },
      },
      tiers: tiered ? tiers.map((tier, index) => ({
        tier_id: `tier-${index + 1}`,
        name: tier.name,
        order: index,
        lower_bound_inclusive_kwh: index === 0 ? '0' : tiers[index - 1]?.upper || '0',
        upper_bound_exclusive_kwh: tier.upper || null,
        lower_bound_multiplier: null,
        upper_bound_multiplier: null,
        price_per_kwh: tier.rate,
        tou_prices: model === 'time_of_use_tiered' ? { 'Off-Peak': tier.rate, 'On-Peak': String(Number(tier.rate) + Number(onPeak) - Number(offPeak)) } : {},
        season: null,
        source_citation: null,
      })) : [],
      hybrid_pricing: model === 'time_of_use_tiered' ? { method: 'tier_period_matrix' } : null,
      ownership_scope: 'site',
      owner_id: home.id,
      effective_from: new Date().toISOString().slice(0, 10),
      effective_through: null,
      cost_scope_default: 'energy_only',
      source_label: 'Administrator-defined rate plan',
      source_note: 'Created in Single Home advanced rate settings',
      provider_mode: 'custom_combined',
      seasons: tou ? [{
        name: 'Year round',
        start: '01-01',
        end: '12-31',
        priority: 0,
        leap_day_behavior: 'include',
        schedules: [{
          day_type: 'all-days',
          dates: [],
          periods: [
            { label: 'Off-Peak', start_minute: 0, end_minute: 960, price_per_kwh: offPeak, delivery_per_kwh: '0', generation_per_kwh: '0', adjustment_per_kwh: '0', display_order: 0 },
            { label: 'On-Peak', start_minute: 960, end_minute: 1260, price_per_kwh: onPeak, delivery_per_kwh: '0', generation_per_kwh: '0', adjustment_per_kwh: '0', display_order: 1 },
            { label: 'Off-Peak', start_minute: 1260, end_minute: 1440, price_per_kwh: offPeak, delivery_per_kwh: '0', generation_per_kwh: '0', adjustment_per_kwh: '0', display_order: 2 },
          ],
        }],
      }] : [],
      adjustments: [],
      custom_notes: '',
      cloned_from_rate_version_id: null,
    }
  }, [code, flatRate, home.currency, home.id, home.timezone, model, name, offPeak, onPeak, tiers])
  const create = useMutation({
    mutationFn: () => request('/api/v1/rates/plans', json('POST', document)),
    onSuccess: async () => {
      setOpen(false)
      await client.invalidateQueries({ queryKey: ['managed-rate-plans'] })
    },
  })
  const clone = useMutation({
    mutationFn: (plan: PlanRow) => request(`/api/v1/rates/plans/${plan.id}/clone`, json('POST')),
    onSuccess: async () => client.invalidateQueries({ queryKey: ['managed-rate-plans'] }),
  })

  if (loading) return <LoadingState label="Loading rate plans…" />
  if (error) return <ErrorState error={error} />
  return (
    <section className="rate-advanced-panel">
      <div className="section-heading"><div><h3>Custom plan editor</h3><p>Create exact flat, time-of-use, tiered, or hybrid pricing.</p></div><button type="button" className="button secondary" onClick={() => { setOpen(!open); }}><Plus size={16} /> New plan</button></div>
      {open && (
        <form className="structured-editor" onSubmit={(event) => { event.preventDefault(); create.mutate() }}>
          <div className="form-grid">
            <label><span>Plan name</span><input value={name} onChange={(event) => { setName(event.target.value); }} required /></label>
            <label><span>Plan code</span><input value={code} onChange={(event) => { setCode(event.target.value); }} required /></label>
            <label><span>Pricing model</span><select value={model} onChange={(event) => { setModel(event.target.value as typeof model); }}><option value="flat">Flat</option><option value="time_of_use">Time of use</option><option value="tiered">Billing-cycle tiered</option><option value="time_of_use_tiered">TOU + tiered</option></select></label>
            {model === 'flat' && <label><span>Energy rate</span><input type="number" step="0.00001" min="0" value={flatRate} onChange={(event) => { setFlatRate(event.target.value); }} /></label>}
            {(model === 'time_of_use' || model === 'time_of_use_tiered') && <><label><span>Off-peak rate</span><input type="number" step="0.00001" min="0" value={offPeak} onChange={(event) => { setOffPeak(event.target.value); }} /></label><label><span>On-peak rate</span><input type="number" step="0.00001" min="0" value={onPeak} onChange={(event) => { setOnPeak(event.target.value); }} /></label></>}
          </div>
          {(model === 'tiered' || model === 'time_of_use_tiered') && (
            <fieldset className="tier-editor">
              <legend>Billing-cycle tiers</legend>
              {tiers.map((tier, index) => (
                <div key={index}>
                  <input aria-label={`Tier ${index + 1} name`} value={tier.name} onChange={(event) => { setTiers((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, name: event.target.value } : item)); }} />
                  <input aria-label={`Tier ${index + 1} upper limit`} placeholder="No upper limit" type="number" min="0" step="0.001" value={tier.upper} onChange={(event) => { setTiers((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, upper: event.target.value } : item)); }} />
                  <input aria-label={`Tier ${index + 1} rate`} type="number" min="0" step="0.00001" value={tier.rate} onChange={(event) => { setTiers((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, rate: event.target.value } : item)); }} />
                  {tiers.length > 2 && <button type="button" className="icon-button" aria-label={`Remove tier ${index + 1}`} onClick={() => { setTiers((current) => current.filter((_, itemIndex) => itemIndex !== index)); }}><Trash2 /></button>}
                </div>
              ))}
              <button type="button" className="button secondary compact" onClick={() => { setTiers((current) => [...current, { name: `Tier ${current.length + 1}`, upper: '', rate: current.at(-1)?.rate ?? '0' }]); }}><Plus size={15} /> Add tier</button>
            </fieldset>
          )}
          {create.error && <p className="form-error" role="alert">{errorMessage(create.error)}</p>}
          <div className="form-actions"><button type="button" className="button secondary" onClick={() => { setOpen(false); }}>Cancel</button><button type="submit" className="button primary" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create draft plan'}</button></div>
        </form>
      )}
      {plans.length === 0 ? <EmptyState compact title="No rate plans" message="Create a custom plan or upload an electric bill." /> : <ul className="structured-list">{plans.map((plan) => <li key={plan.id}><div><strong>{plan.name}</strong><span>{plan.code} · {statusLabel(plan.pricingModel ?? 'unknown')} · {statusLabel(plan.status)}</span></div><button type="button" className="button ghost compact" disabled={clone.isPending} onClick={() => { clone.mutate(plan); }}>Clone</button></li>)}</ul>}
    </section>
  )
}

*/

function PlanManager({ home, services, plans, loading, error }: { home: Home; services: ElectricService[]; plans: PlanRow[]; loading: boolean; error: unknown }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState<PlanRow>()
  const [lifecycleTarget, setLifecycleTarget] = useState<PlanRow>()
  const [lifecycleReason, setLifecycleReason] = useState('Administrator reviewed rate-plan lifecycle')
  const [lifecycleConfirmation, setLifecycleConfirmation] = useState('')
  const editorDraft = useQuery({
    queryKey: ['rate-editor-version', target?.versionId],
    queryFn: () => request(`/api/v1/rates/versions/${target?.versionId ?? ''}`, {}, adaptRatePlanDraft),
    enabled: open && Boolean(target?.versionId),
  })
  const dependencies = useQuery({
    queryKey: ['rate-plan-dependencies', lifecycleTarget?.id],
    queryFn: () => request<Record<string, unknown>>(`/api/v1/admin/rate-plans/${lifecycleTarget?.id ?? ''}/dependencies`),
    enabled: Boolean(lifecycleTarget),
  })
  const clone = useMutation({
    mutationFn: (plan: PlanRow) => request(`/api/v1/rates/plans/${plan.id}/clone`, json('POST')),
    onSuccess: async () => client.invalidateQueries({ queryKey: ['managed-rate-plans'] }),
  })
  const newVersion = useMutation({
    mutationFn: async (plan: PlanRow) => {
      const response = record(await request(`/api/v1/rates/plans/${plan.id}/versions`, json('POST')), 'new rate version')
      const versionId = stringValue(response.id)
      if (!versionId) throw new Error('The server did not return the new draft version.')
      return { ...plan, versionId, status: 'draft', version: Number(response.version ?? (plan.version ?? 0) + 1) }
    },
    onSuccess: (plan) => {
      setTarget(plan)
      setOpen(true)
      void client.invalidateQueries({ queryKey: ['managed-rate-plans'] })
    },
  })
  const retire = useMutation({
    mutationFn: (plan: PlanRow) => {
      if (!plan.versionId) throw new Error('This plan does not have a version to retire.')
      return request(`/api/v1/rates/versions/${plan.versionId}/retire`, json('POST'))
    },
    onSuccess: async () => {
      setLifecycleTarget(undefined)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['managed-rate-plans'] }),
        client.invalidateQueries({ queryKey: ['removed-rate-plans'] }),
      ])
    },
  })
  const remove = useMutation({
    mutationFn: (plan: PlanRow) => {
      const review = dependencies.data ?? {}
      const deleteDraft = review.permanent_draft_deletion_eligible === true
      return request(
        deleteDraft ? `/api/v1/admin/rate-plan-drafts/${plan.id}` : `/api/v1/admin/rate-plans/${plan.id}/remove`,
        json(deleteDraft ? 'DELETE' : 'POST', {
          expected_revision: plan.revision,
          confirmation: lifecycleConfirmation,
          reason: lifecycleReason,
          idempotency_key: `remove-${plan.id}-${crypto.randomUUID()}`,
        }),
      )
    },
    onSuccess: async () => {
      setLifecycleTarget(undefined)
      setLifecycleConfirmation('')
      await Promise.all([
        client.invalidateQueries({ queryKey: ['managed-rate-plans'] }),
        client.invalidateQueries({ queryKey: ['removed-rate-plans'] }),
      ])
    },
  })
  if (loading) return <LoadingState label="Loading rate plans…" />
  if (error) return <ErrorState error={error} />
  const removalBlocked = dependencies.data?.removal_blocked === true
  const removalReady = Boolean(
    lifecycleTarget
    && lifecycleConfirmation.trim().toLocaleLowerCase() === lifecycleTarget.code.trim().toLocaleLowerCase()
    && lifecycleReason.trim().length >= 8
    && !removalBlocked,
  )
  return (
    <section className="rate-advanced-panel">
      <div className="section-heading">
        <div><h3>Custom plan editor</h3><p>Build, validate, preview, publish, and assign complete flat, TOU, tiered, or hybrid plans.</p></div>
        <button type="button" className="button secondary" onClick={() => { setTarget(undefined); setOpen(!open) }}><Plus size={16} /> {open && !target ? 'Hide editor' : 'New plan'}</button>
      </div>
      {open && (!target ? <StructuredRateEditor home={home} services={services} onClose={() => { setOpen(false) }} /> : editorDraft.isLoading ? <LoadingState label="Opening editable rate version…" /> : editorDraft.error ? <ErrorState error={editorDraft.error} retry={() => { void editorDraft.refetch() }} /> : editorDraft.data ? <StructuredRateEditor key={target.versionId} home={home} services={services} initialDraft={editorDraft.data} initialSaved={{ planId: target.id, versionId: target.versionId ?? '', status: target.status } satisfies SavedDraft} onClose={() => { setOpen(false); setTarget(undefined) }} /> : null)}
      {lifecycleTarget && (
        <section className="plan-lifecycle-panel" aria-label={`Lifecycle controls for ${lifecycleTarget.name}`}>
          <div className="section-heading"><div><h4>Retire or remove {lifecycleTarget.name}</h4><p>The server reviews assignments, history, evidence, and plan kind before allowing removal.</p></div><button type="button" className="button ghost compact" onClick={() => { setLifecycleTarget(undefined); setLifecycleConfirmation('') }}>Cancel</button></div>
          {dependencies.isLoading ? <LoadingState label="Reviewing plan dependencies…" /> : dependencies.error ? <ErrorState error={dependencies.error} retry={() => { void dependencies.refetch() }} /> : (
            <>
              <div className="dependency-summary">
                <span><small>Permanent draft deletion</small><strong>{dependencies.data?.permanent_draft_deletion_eligible === true ? 'Eligible' : 'Not eligible'}</strong></span>
                <span><small>Removal</small><strong>{removalBlocked ? 'Blocked by active assignments' : 'Available after confirmation'}</strong></span>
                <span><small>History</small><strong>Versions and evidence preserved</strong></span>
              </div>
              <div className="form-grid">
                <label><span>Reason</span><input value={lifecycleReason} onChange={(event) => { setLifecycleReason(event.target.value) }} /></label>
                <label><span>Type {lifecycleTarget.code} to confirm removal</span><input value={lifecycleConfirmation} onChange={(event) => { setLifecycleConfirmation(event.target.value) }} /></label>
              </div>
              {removalBlocked && <InlineNotice tone="warning">Replace or end active and future assignments before removing this plan.</InlineNotice>}
              <div className="inline-actions">
                {lifecycleTarget.versionId && !['draft', 'retired'].includes(lifecycleTarget.status) && <button type="button" className="button secondary compact" disabled={retire.isPending} onClick={() => { retire.mutate(lifecycleTarget) }}><Archive size={15} /> {retire.isPending ? 'Retiring…' : 'Retire version'}</button>}
                <button type="button" className="button danger compact" disabled={!removalReady || remove.isPending} onClick={() => { remove.mutate(lifecycleTarget) }}>{remove.isPending ? 'Removing…' : dependencies.data?.permanent_draft_deletion_eligible === true ? 'Delete unused draft' : 'Remove plan'}</button>
              </div>
            </>
          )}
        </section>
      )}
      {plans.length === 0 ? <EmptyState compact title="No rate plans" message="Create a custom plan or upload an electric bill." /> : <ul className="structured-list rate-plan-library">{plans.map((plan) => <li key={plan.id}><div><strong>{plan.name}</strong><span>{plan.code} · {statusLabel(plan.pricingModel ?? 'unknown')} · {statusLabel(plan.status)}</span></div><div className="inline-actions">{plan.status === 'draft' && plan.versionId ? <button type="button" className="button ghost compact" onClick={() => { setTarget(plan); setOpen(true) }}>Edit draft</button> : plan.versionId && <button type="button" className="button ghost compact" disabled={newVersion.isPending} onClick={() => { newVersion.mutate(plan) }}>New version</button>}<button type="button" className="button ghost compact" disabled={clone.isPending} onClick={() => { clone.mutate(plan) }}>Clone</button><button type="button" className="button ghost compact" onClick={() => { setLifecycleTarget(plan); setLifecycleConfirmation('') }}>Lifecycle</button></div></li>)}</ul>}
      {(clone.error || newVersion.error || retire.error || remove.error) && <InlineNotice tone="danger">{errorMessage(clone.error ?? newVersion.error ?? retire.error ?? remove.error)}</InlineNotice>}
    </section>
  )
}

function SourceManager({ sources, loading, error }: { sources: RateSource[]; loading: boolean; error: unknown }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [parser, setParser] = useState('sce_public_tou_html_v1')
  const [effective, setEffective] = useState(new Date().toISOString().slice(0, 10))
  const create = useMutation({
    mutationFn: () => request('/api/v1/admin/rate-sources', json('POST', { name, url, parser_id: parser, effective_from: parser === 'sce_public_tou_html_v1' ? effective : undefined })),
    onSuccess: async () => {
      setName('')
      setUrl('')
      setOpen(false)
      await client.invalidateQueries({ queryKey: ['rate-sources'] })
    },
  })
  const check = useMutation({
    mutationFn: () => request('/api/v1/admin/rate-sources/check-now', json('POST')),
    onSuccess: async () => client.invalidateQueries({ queryKey: ['rate-sources'] }),
  })
  if (loading) return <LoadingState label="Loading approved sources…" />
  if (error) return <ErrorState error={error} />
  return (
    <section className="rate-advanced-panel">
      <div className="section-heading"><div><h3>Managed rate sources</h3><p>Approved HTTPS sources are archived and reviewed before activation.</p></div><div className="inline-actions"><button type="button" className="button secondary" onClick={() => { check.mutate(); }} disabled={check.isPending}><RefreshCw size={16} /> Check now</button><button type="button" className="button secondary" onClick={() => { setOpen(!open); }}><Plus size={16} /> Add source</button></div></div>
      {open && <form className="structured-editor" onSubmit={(event) => { event.preventDefault(); create.mutate() }}><div className="form-grid"><label><span>Name</span><input value={name} onChange={(event) => { setName(event.target.value); }} required minLength={3} /></label><label><span>Approved HTTPS URL</span><input type="url" value={url} onChange={(event) => { setUrl(event.target.value); }} required /></label><label><span>Source type</span><select value={parser} onChange={(event) => { setParser(event.target.value); }}><option value="sce_public_tou_html_v1">SCE public TOU page</option><option value="sce_tariff_pdf_v1">SCE tariff PDF</option></select></label>{parser === 'sce_public_tou_html_v1' && <label><span>Effective date</span><input type="date" value={effective} onChange={(event) => { setEffective(event.target.value); }} /></label>}</div>{create.error && <p className="form-error" role="alert">{errorMessage(create.error)}</p>}<div className="form-actions"><button className="button primary" disabled={create.isPending}>Add approved source</button></div></form>}
      {sources.length === 0 ? <EmptyState compact title="No approved sources" message="Add an official SCE page or tariff PDF to start managed checks." /> : <ul className="structured-list">{sources.map((source) => <li key={source.id}><div><strong>{source.name}</strong><span>{source.displayOrigin} · {source.sourceType}{source.lastSuccessAt ? ' · Checked successfully' : ''}</span><details className="technical-details"><summary>Technical details</summary><code>{source.technicalUrl}</code><code>{source.parserId}</code></details></div><span className={`pill ${source.enabled ? 'success' : ''}`}>{source.enabled ? 'Enabled' : 'Disabled'}</span></li>)}</ul>}
    </section>
  )
}

function Adjustments({ services }: { services: ElectricService[] }) {
  return (
    <section className="rate-advanced-panel">
      <h3>Manual adjustments</h3>
      {services.length === 0 ? <EmptyState title="No electric service" message="Create an electric service before adding account adjustments." /> : (
        <>
          <InlineNotice><ShieldCheck size={16} /> Fixed charges and credits remain scoped to the electric service and are applied only once.</InlineNotice>
          <ul className="structured-list">{services.map((service) => <li key={service.id}><div><strong>{service.name}</strong><span>{statusLabel(service.costScope)} · {service.provider}</span></div><Archive aria-hidden="true" /></li>)}</ul>
        </>
      )}
    </section>
  )
}
