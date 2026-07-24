import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowLeft,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  CheckCircle2,
  Copy,
  FileUp,
  Plus,
  Save,
  Trash2,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { CanonicalAction } from '../actions'
import { api } from '../api'
import { BillImportWorkspace } from './BillImportPage'
import { ErrorState, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'
import {
  formatCurrency,
  formatEnergy,
  formatEnergyRate,
  formatTierRange,
} from '../formatters'
import {
  emptyRateDocument,
  type DayScheduleDocument,
  type ManagedRateVersion,
  type RateAdjustmentDocument,
  type RatePeriodDocument,
  type RatePlanDocument,
  type RateSeasonDocument,
  type TierDefinitionDocument,
  type ValidationReport,
} from '../rates'

interface VersionResponse { version: ManagedRateVersion; document: RatePlanDocument }
interface Account { id: string; name: string }

const stepNames = [
  'Plan details',
  'Pricing & tiers',
  'TOU schedules',
  'Charges & adjustments',
  'Validate & preview',
]

interface TierPreview {
  display_total: string
  energy_charge: string
  blended_energy_rate: string | null
  display?: {
    energy_charge: string
    blended_energy_rate: string | null
    estimated_total: string
  }
  energy_by_tier_kwh: Record<string, string>
  charge_by_tier: Record<string, string>
  tier_thresholds: Array<{
    tier_id: string
    name: string
    lower_bound_kwh: string
    upper_bound_kwh: string | null
    derived_baseline_kwh: string | null
    display_range?: string
    display_usage?: string
    display_charge?: string
  }>
}

function clone(document: RatePlanDocument): RatePlanDocument {
  return JSON.parse(JSON.stringify(document)) as RatePlanDocument
}

function requiredAt<T>(items: T[], index: number): T {
  const item = items[index]
  if (!item) throw new Error('The selected editor row no longer exists')
  return item
}

function blankPeriod(start = 0): RatePeriodDocument {
  return {
    label: 'custom',
    start_minute: start,
    end_minute: 1440,
    price_per_kwh: '0.00000000',
    delivery_per_kwh: '0',
    generation_per_kwh: '0',
    adjustment_per_kwh: '0',
    display_order: 0,
  }
}

function blankTier(order: number, lower = '0'): TierDefinitionDocument {
  return {
    tier_id: `tier-${order + 1}`,
    name: `Tier ${order + 1}`,
    order,
    lower_bound_inclusive_kwh: lower,
    upper_bound_exclusive_kwh: null,
    lower_bound_multiplier: order === 0 ? '0' : null,
    upper_bound_multiplier: null,
    price_per_kwh: '0.00000000',
    tou_prices: {},
    season: null,
    source_citation: null,
  }
}

function scheduleIssues(schedule: DayScheduleDocument): string[] {
  const issues: string[] = []
  let cursor = 0
  for (const period of [...schedule.periods].sort((left, right) => left.start_minute - right.start_minute)) {
    if (period.start_minute < cursor) issues.push('One or more schedule periods overlap')
    if (period.start_minute > cursor) issues.push('This schedule does not cover the full day')
    cursor = Math.max(cursor, period.end_minute)
  }
  if (cursor !== 1440) issues.push('This schedule does not cover the full day')
  return [...new Set(issues)]
}

export function RateEditorPage({ canManage, canImportBills = false }: { canManage: boolean; canImportBills?: boolean }) {
  const { planId, versionId } = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [step, setStep] = useState(0)
  const [document, setDocument] = useState<RatePlanDocument>(emptyRateDocument)
  const [validation, setValidation] = useState<ValidationReport>()
  const [saved, setSaved] = useState<{ planId: string; versionId: string } | undefined>(
    planId && versionId ? { planId, versionId } : undefined,
  )
  const [sampleUsage, setSampleUsage] = useState('951')
  const [sampleCost, setSampleCost] = useState<TierPreview>()
  const [importOpen, setImportOpen] = useState(searchParams.get('bill_import') === 'open' || Boolean(searchParams.get('bill_id') || searchParams.get('account_id')))
  const [importNotice, setImportNotice] = useState('')
  const activationDialog = useRef<HTMLDialogElement>(null)
  const query = useQuery({
    queryKey: ['rate-version', versionId],
    queryFn: () => api<VersionResponse>(`/api/v1/rates/versions/${versionId}`),
    enabled: Boolean(versionId),
  })
  const accounts = useQuery({
    queryKey: ['utility-accounts'],
    queryFn: () => api<Account[]>('/api/v1/utility-accounts'),
    enabled: canManage && step === 4,
  })
  const editable = canManage && (!query.data || query.data.version.status === 'draft')

  function closeImporter() {
    setImportOpen(false)
    const next = new URLSearchParams(searchParams)
    next.delete('bill_import')
    next.delete('bill_id')
    next.delete('account_id')
    setSearchParams(next, { replace: true })
  }

  useEffect(() => {
    if (query.data) setDocument(query.data.document)
  }, [query.data])

  function update(mutator: (draft: RatePlanDocument) => void) {
    const next = clone(document)
    mutator(next)
    setDocument(next)
    setValidation(undefined)
  }

  function changeSeason(index: number, mutator: (season: RateSeasonDocument) => void) {
    update((draft) => { mutator(requiredAt(draft.seasons, index)); })
  }

  function changeSchedule(
    seasonIndex: number,
    scheduleIndex: number,
    mutator: (schedule: DayScheduleDocument) => void,
  ) {
    changeSeason(seasonIndex, (season) => { mutator(requiredAt(season.schedules, scheduleIndex)); })
  }

  function changePeriod(
    seasonIndex: number,
    scheduleIndex: number,
    periodIndex: number,
    mutator: (period: RatePeriodDocument) => void,
  ) {
    changeSchedule(seasonIndex, scheduleIndex, (schedule) =>
      { mutator(requiredAt(schedule.periods, periodIndex)); })
  }

  function changeAdjustment(index: number, mutator: (item: RateAdjustmentDocument) => void) {
    update((draft) => { mutator(requiredAt(draft.adjustments, index)); })
  }

  function normalizeTiers(tiers: TierDefinitionDocument[], basis: 'fixed_cycle_kwh' | 'daily_baseline_kwh') {
    tiers.forEach((tier, index) => {
      tier.order = index
      if (basis === 'fixed_cycle_kwh') {
        tier.lower_bound_inclusive_kwh = index === 0
          ? '0'
          : (requiredAt(tiers, index - 1).upper_bound_exclusive_kwh ?? tier.lower_bound_inclusive_kwh)
        tier.lower_bound_multiplier = null
        tier.upper_bound_multiplier = null
      } else {
        tier.lower_bound_multiplier = index === 0
          ? '0'
          : (requiredAt(tiers, index - 1).upper_bound_multiplier ?? tier.lower_bound_multiplier)
        tier.upper_bound_exclusive_kwh = null
      }
      if (index === tiers.length - 1) {
        tier.upper_bound_exclusive_kwh = null
        tier.upper_bound_multiplier = null
      }
    })
  }

  function changeTier(index: number, mutator: (item: TierDefinitionDocument) => void) {
    update((draft) => {
      mutator(requiredAt(draft.tiers, index))
      normalizeTiers(draft.tiers, draft.billing_cycle.threshold.basis)
    })
  }

  const touLabels = useMemo(
    () => [...new Set(document.seasons.flatMap((season) => season.schedules.flatMap((schedule) => schedule.periods.map((period) => period.label))))],
    [document.seasons],
  )

  const save = useMutation({
    mutationFn: async () => {
      if (saved) {
        await api(`/api/v1/rates/versions/${saved.versionId}`, {
          method: 'PATCH',
          body: JSON.stringify(document),
        })
        return saved
      }
      const result = await api<{
        plan: { id: string; versions: ManagedRateVersion[] }
      }>('/api/v1/rates/plans', { method: 'POST', body: JSON.stringify(document) })
      const version = requiredAt(result.plan.versions, 0)
      return { planId: result.plan.id, versionId: version.id }
    },
    onSuccess: (result) => {
      setSaved(result)
      void navigate(`/billing/rate-plans/${result.planId}/versions/${result.versionId}`, { replace: true })
    },
  })
  const validate = useMutation({
    mutationFn: () => api<ValidationReport>('/api/v1/rates/validate-document', {
      method: 'POST',
      body: JSON.stringify(document),
    }),
    onSuccess: setValidation,
  })
  const activate = useMutation({
    mutationFn: (id: string) => api(`/api/v1/rates/versions/${id}/activate`, { method: 'POST' }),
    onSuccess: () => { activationDialog.current?.close(); void query.refetch() },
  })
  const assign = useMutation({
    mutationFn: (accountId: string) => api('/api/v1/rates/assignments', {
      method: 'POST',
      body: JSON.stringify({
        utility_account_id: accountId,
        rate_version_id: saved?.versionId,
        provider_mode: document.provider_mode,
        cost_scope: document.cost_scope_default,
      }),
    }),
  })
  const preview = useMutation({
    mutationFn: () => api<TierPreview>('/api/v1/rates/preview-cost', {
      method: 'POST',
      body: JSON.stringify({
        document,
        interval_start: `${document.effective_from}T00:00:00-07:00`,
        interval_end: `${document.effective_from}T01:00:00-07:00`,
        energy_kwh: sampleUsage,
        cost_scope: document.cost_scope_default,
      }),
    }),
    onSuccess: setSampleCost,
  })
  const coverage = useMemo(
    () => document.seasons.flatMap((season) => season.schedules.map((schedule) => ({
      key: `${season.name}/${schedule.day_type}`,
      minutes: schedule.periods.reduce(
        (total, period) => total + Math.max(0, period.end_minute - period.start_minute),
        0,
      ),
      valid: scheduleIssues(schedule).length === 0,
    }))),
    [document.seasons],
  )

  if (query.isLoading) return <LoadingState label="Opening rate plan…" />
  if (query.error) return <ErrorState error={query.error} retry={() => void query.refetch()} />

  return (
    <>
      <PageTitle
        eyebrow={saved ? `Rate version ${query.data?.version.version ?? 1}` : 'New custom plan'}
        title={document.plan_name || 'Custom rate plan'}
        description="Build an exact, effective-dated schedule. Active versions remain immutable; edits are saved only to drafts."
        actions={<div className="inline-actions">{editable && canImportBills && <CanonicalAction id="rate_plan.import_from_bill" surface="resource_detail"><button className="button secondary" onClick={() => { setImportOpen(true); setImportNotice('') }}><FileUp size={16} /> Import rate plan from bill</button></CanonicalAction>}<button className="button secondary" onClick={() => navigate('/billing/rate-plans')}><ArrowLeft size={16} /> Rate plans</button></div>}
      />
      {importNotice && <p className="form-success" role="status">{importNotice}</p>}
      {importOpen && editable && canImportBills && <section className="bill-import-editor-workspace" aria-label="Utility bill import">
        <BillImportWorkspace
          currentDraft={document}
          onApplyDraft={(next) => {
            setDocument(next)
            setValidation(undefined)
            setImportNotice('Selected reviewed bill values were applied to this unsaved Custom Plan draft. Review every editor step, then save explicitly.')
            setStep(0)
          }}
          onClose={closeImporter}
        />
      </section>}
      <nav className="editor-steps" aria-label="Rate plan editor steps">
        {stepNames.map((name, index) => (
          <button
            key={name}
            className={index === step ? 'active' : ''}
            aria-current={index === step ? 'step' : undefined}
            onClick={() => { setStep(index); }}
          ><span>{index + 1}</span>{name}</button>
        ))}
      </nav>
      {!editable && <div className="source-note immutable-note"><CheckCircle2 size={18} /><p>This version is immutable. Clone it or create a new version to make changes.</p></div>}

      {step === 0 && (
        <Panel title="Plan details" eyebrow="Identity and applicability">
          <fieldset className="editor-fieldset" disabled={!editable}>
            <div className="form-columns">
              <label>Plan name<input value={document.plan_name} onChange={(event) => { update((draft) => { draft.plan_name = event.target.value }); }} /></label>
              <label>Plan code<input value={document.plan_code} onChange={(event) => { update((draft) => { draft.plan_code = event.target.value.toUpperCase().replace(/[^A-Z0-9._-]/g, '-') }); }} /></label>
            </div>
            <label>Utility<input value={document.utility} onChange={(event) => { update((draft) => { draft.utility = event.target.value }); }} /></label>
            <label>Description<textarea rows={3} value={document.description} onChange={(event) => { update((draft) => { draft.description = event.target.value }); }} /></label>
            <div className="form-columns">
              <label>Currency<input maxLength={3} value={document.currency} onChange={(event) => { update((draft) => { draft.currency = event.target.value.toUpperCase() }); }} /></label>
              <label>Timezone<input value={document.timezone} onChange={(event) => { update((draft) => { draft.timezone = event.target.value }); }} /></label>
            </div>
            <div className="form-columns">
              <label>Effective date<input type="date" value={document.effective_from} onChange={(event) => { update((draft) => { draft.effective_from = event.target.value }); }} /></label>
              <label>Optional end date<input type="date" value={document.effective_through ?? ''} onChange={(event) => { update((draft) => { draft.effective_through = event.target.value || null }); }} /></label>
            </div>
            <div className="form-columns">
              <label>Ownership scope<select value={document.ownership_scope} onChange={(event) => { update((draft) => { draft.ownership_scope = event.target.value as RatePlanDocument['ownership_scope']; if (draft.ownership_scope === 'global') draft.owner_id = null }); }}><option value="global">Global</option><option value="site">Site</option><option value="utility_account">Utility account</option></select></label>
              <label>Default cost scope<select value={document.cost_scope_default} onChange={(event) => { update((draft) => { draft.cost_scope_default = event.target.value as RatePlanDocument['cost_scope_default'] }); }}><option value="energy_only">Energy only</option><option value="allocated_account_estimate">Allocated account estimate</option><option value="full_account_estimate">Full account estimate</option></select></label>
            </div>
            <label>Provider mode<select value={document.provider_mode} onChange={(event) => { update((draft) => { draft.provider_mode = event.target.value as RatePlanDocument['provider_mode'] }); }}><option value="sce_delivery_generation">SCE delivery + SCE generation</option><option value="sce_delivery_cca">SCE delivery + CCA generation</option><option value="sce_delivery_direct_access">SCE delivery + Direct Access generation</option><option value="custom_combined">Custom combined</option></select></label>
            <div className="form-columns">
              <label>Source label<input value={document.source_label} onChange={(event) => { update((draft) => { draft.source_label = event.target.value }); }} /></label>
              <label>Source note<input value={document.source_note} onChange={(event) => { update((draft) => { draft.source_note = event.target.value }); }} /></label>
            </div>
          </fieldset>
        </Panel>
      )}

      {step === 1 && (
        <div className="editor-stack">
          <Panel title="Pricing model" eyebrow="Server-authoritative calculation strategy">
            <fieldset className="editor-fieldset" disabled={!editable}>
              <label>Pricing model<select value={document.pricing_model} onChange={(event) => { update((draft) => {
                const model = event.target.value as RatePlanDocument['pricing_model']
                draft.pricing_model = model
                draft.flat_rate_per_kwh = model === 'flat' ? (draft.flat_rate_per_kwh ?? '0.25000000') : null
                if (model === 'tiered' || model === 'time_of_use_tiered') {
                  if (draft.tiers.length < 2) {
                    const first = blankTier(0)
                    first.upper_bound_exclusive_kwh = '579'
                    const second = blankTier(1, '579')
                    draft.tiers = [first, second] as RatePlanDocument['tiers']
                  }
                  draft.hybrid_pricing = model === 'time_of_use_tiered'
                    ? (draft.hybrid_pricing ?? { method: 'tier_period_matrix' })
                    : null
                } else {
                  draft.tiers = [] as unknown as RatePlanDocument['tiers']
                  draft.hybrid_pricing = null
                }
              }); }}>
                <option value="flat">Flat</option>
                <option value="time_of_use">Time of use</option>
                <option value="tiered">Tiered by billing-cycle usage</option>
                <option value="time_of_use_tiered">Time of use + usage tiers</option>
              </select></label>
              {document.pricing_model === 'flat' && (
                <label>Flat energy price ($/kWh)<input inputMode="decimal" value={document.flat_rate_per_kwh ?? ''} onChange={(event) => { update((draft) => { draft.flat_rate_per_kwh = event.target.value }); }} /></label>
              )}
              {document.pricing_model === 'time_of_use' && (
                <p className="panel-copy">Time-of-use pricing uses the complete seasonal schedules in the next step. Existing TOU calculation behavior is preserved.</p>
              )}
              {(document.pricing_model === 'tiered' || document.pricing_model === 'time_of_use_tiered') && <>
                <div className="form-columns">
                  <label>Expected billing-cycle start day<input type="number" min="1" max="31" value={document.billing_cycle.expected_start_day} onChange={(event) => { update((draft) => { draft.billing_cycle.expected_start_day = Number(event.target.value) }); }} /></label>
                  <label>Threshold basis<select value={document.billing_cycle.threshold.basis} onChange={(event) => { update((draft) => {
                    const basis = event.target.value as RatePlanDocument['billing_cycle']['threshold']['basis']
                    draft.billing_cycle.threshold.basis = basis
                    draft.billing_cycle.threshold.daily_baseline_kwh = basis === 'daily_baseline_kwh' ? (draft.billing_cycle.threshold.daily_baseline_kwh ?? '19.3') : null
                    draft.billing_cycle.threshold.seasonal_baselines = basis === 'daily_baseline_kwh' ? draft.billing_cycle.threshold.seasonal_baselines : [] as unknown as RatePlanDocument['billing_cycle']['threshold']['seasonal_baselines']
                    normalizeTiers(draft.tiers, basis)
                    if (basis === 'daily_baseline_kwh') {
                      draft.tiers.forEach((tier, index) => {
                        tier.lower_bound_multiplier = index === 0 ? '0' : String(index)
                        tier.upper_bound_multiplier = index === draft.tiers.length - 1 ? null : String(index + 1)
                      })
                    }
                  }); }}><option value="fixed_cycle_kwh">Fixed billing-cycle kWh</option><option value="daily_baseline_kwh">Daily baseline × exact cycle days</option></select></label>
                </div>
                {document.billing_cycle.threshold.basis === 'daily_baseline_kwh' && <>
                  <div className="form-columns">
                    <label>Default daily baseline (kWh/day)<input inputMode="decimal" value={document.billing_cycle.threshold.daily_baseline_kwh ?? ''} onChange={(event) => { update((draft) => { draft.billing_cycle.threshold.daily_baseline_kwh = event.target.value }); }} /></label>
                    <label>Threshold rounding<select value={document.billing_cycle.threshold.rounding_policy} onChange={(event) => { update((draft) => { draft.billing_cycle.threshold.rounding_policy = event.target.value as RatePlanDocument['billing_cycle']['threshold']['rounding_policy'] }); }}><option value="none">No rounding</option><option value="nearest_kwh">Nearest kWh</option><option value="floor_kwh">Floor to kWh</option><option value="ceil_kwh">Ceil to kWh</option></select></label>
                  </div>
                  <div className="baseline-preview" aria-label="Exact cycle threshold previews">
                    {[28, 29, 30, 31].map((days) => <span key={days}><small>{days}-day cycle</small><strong>{(Number(document.billing_cycle.threshold.daily_baseline_kwh ?? 0) * days).toFixed(3)} kWh baseline</strong></span>)}
                  </div>
                  <div className="seasonal-baseline-list">
                    {document.billing_cycle.threshold.seasonal_baselines.map((baseline, index) => <article className="tier-card" key={`${baseline.name}-${index}`}>
                      <header><strong>{baseline.name || `Seasonal baseline ${index + 1}`}</strong><button className="icon-button danger-text" aria-label="Remove seasonal baseline" onClick={() => { update((draft) => { draft.billing_cycle.threshold.seasonal_baselines.splice(index, 1) }); }}><Trash2 size={15} /></button></header>
                      <div className="season-fields">
                        <label>Name<input value={baseline.name} onChange={(event) => { update((draft) => { requiredAt(draft.billing_cycle.threshold.seasonal_baselines, index).name = event.target.value }); }} /></label>
                        <label>Starts<input value={baseline.start} onChange={(event) => { update((draft) => { requiredAt(draft.billing_cycle.threshold.seasonal_baselines, index).start = event.target.value }); }} /></label>
                        <label>Ends<input value={baseline.end} onChange={(event) => { update((draft) => { requiredAt(draft.billing_cycle.threshold.seasonal_baselines, index).end = event.target.value }); }} /></label>
                        <label>kWh/day<input value={baseline.daily_kwh} onChange={(event) => { update((draft) => { requiredAt(draft.billing_cycle.threshold.seasonal_baselines, index).daily_kwh = event.target.value }); }} /></label>
                      </div>
                    </article>)}
                    <button className="button secondary" onClick={() => { update((draft) => { draft.billing_cycle.threshold.seasonal_baselines.push({ name: `baseline-${draft.billing_cycle.threshold.seasonal_baselines.length + 1}`, start: '06-01', end: '09-30', daily_kwh: draft.billing_cycle.threshold.daily_baseline_kwh ?? '19.3', source_citation: null }) }); }}><Plus size={15} /> Add seasonal baseline</button>
                  </div>
                </>}
                {document.pricing_model === 'time_of_use_tiered' && <label>Hybrid calculation method<select value={document.hybrid_pricing?.method ?? 'tier_period_matrix'} onChange={(event) => { update((draft) => { draft.hybrid_pricing = { method: event.target.value as NonNullable<RatePlanDocument['hybrid_pricing']>['method'] } }); }}><option value="tier_period_matrix">Separate price for every tier and TOU period</option><option value="tier_base_plus_tou_adder">Tier base price + TOU adder</option><option value="tou_base_plus_tier_adder">TOU base price + tier adder</option></select></label>}
              </>}
            </fieldset>
          </Panel>
          {(document.pricing_model === 'tiered' || document.pricing_model === 'time_of_use_tiered') && (
            <Panel title="Tier definitions" eyebrow={`${document.tiers.length} ordered tiers · final tier open-ended`}>
              <fieldset className="editor-fieldset" disabled={!editable}>
                <div className="tier-editor-list">
                  {document.tiers.map((tier, index) => {
                    const final = index === document.tiers.length - 1
                    return <article className="tier-card" key={tier.tier_id}>
                      <header><div><span className="plan-code">Tier {index + 1}</span><strong>{tier.name}</strong></div><div className="tier-order-actions">
                        <button className="icon-button" aria-label={`Move ${tier.name} up`} disabled={index === 0} onClick={() => { update((draft) => { const moved = draft.tiers.splice(index, 1)[0]; if (moved) draft.tiers.splice(index - 1, 0, moved); normalizeTiers(draft.tiers, draft.billing_cycle.threshold.basis) }); }}><ArrowUp size={15} /></button>
                        <button className="icon-button" aria-label={`Move ${tier.name} down`} disabled={final} onClick={() => { update((draft) => { const moved = draft.tiers.splice(index, 1)[0]; if (moved) draft.tiers.splice(index + 1, 0, moved); normalizeTiers(draft.tiers, draft.billing_cycle.threshold.basis) }); }}><ArrowDown size={15} /></button>
                        <button className="icon-button" aria-label={`Clone ${tier.name}`} onClick={() => { update((draft) => { const copy = structuredClone(requiredAt(draft.tiers, index)); copy.tier_id = `${copy.tier_id}-copy-${Date.now()}`; copy.name = `${copy.name} Copy`; draft.tiers.splice(index + 1, 0, copy); normalizeTiers(draft.tiers, draft.billing_cycle.threshold.basis) }); }}><Copy size={15} /></button>
                        <button className="icon-button danger-text" aria-label={`Remove ${tier.name}`} disabled={document.tiers.length <= 2} onClick={() => { update((draft) => { draft.tiers.splice(index, 1); normalizeTiers(draft.tiers, draft.billing_cycle.threshold.basis) }); }}><Trash2 size={15} /></button>
                      </div></header>
                      <div className="form-columns">
                        <label>Stable tier ID<input value={tier.tier_id} onChange={(event) => { changeTier(index, (item) => { item.tier_id = event.target.value.replace(/[^A-Za-z0-9._-]/g, '-') }); }} /></label>
                        <label>Display name<input value={tier.name} onChange={(event) => { changeTier(index, (item) => { item.name = event.target.value }); }} /></label>
                      </div>
                      <div className="tier-bounds-grid">
                        <label>Lower bound<input readOnly value={document.billing_cycle.threshold.basis === 'fixed_cycle_kwh' ? `${tier.lower_bound_inclusive_kwh} kWh` : `${tier.lower_bound_multiplier ?? '0'} × baseline`} /></label>
                        {!final && document.billing_cycle.threshold.basis === 'fixed_cycle_kwh' && <label>Exclusive upper bound (kWh)<input inputMode="decimal" value={tier.upper_bound_exclusive_kwh ?? ''} onChange={(event) => { changeTier(index, (item) => { item.upper_bound_exclusive_kwh = event.target.value }); }} /></label>}
                        {!final && document.billing_cycle.threshold.basis === 'daily_baseline_kwh' && <label>Exclusive upper baseline multiple<input inputMode="decimal" value={tier.upper_bound_multiplier ?? ''} onChange={(event) => { changeTier(index, (item) => { item.upper_bound_multiplier = event.target.value }); }} /></label>}
                        {final && <label>Upper bound<input readOnly value="Open-ended" /></label>}
                        <label>{document.pricing_model === 'time_of_use_tiered' && document.hybrid_pricing?.method !== 'tier_period_matrix' ? 'Tier rate / adder ($/kWh)' : 'Rate ($/kWh)'}<input inputMode="decimal" value={tier.price_per_kwh} onChange={(event) => { changeTier(index, (item) => { item.price_per_kwh = event.target.value }); }} /></label>
                      </div>
                      {document.pricing_model === 'time_of_use_tiered' && document.hybrid_pricing?.method === 'tier_period_matrix' && <div className="hybrid-price-grid">{touLabels.map((label) => <label key={label}>{label} ($/kWh)<input value={tier.tou_prices[label] ?? ''} onChange={(event) => { changeTier(index, (item) => { item.tou_prices[label] = event.target.value }); }} /></label>)}</div>}
                      <label>Source citation (optional)<input value={tier.source_citation ?? ''} onChange={(event) => { changeTier(index, (item) => { item.source_citation = event.target.value || null }); }} /></label>
                    </article>
                  })}
                </div>
                <button className="button secondary" onClick={() => { update((draft) => {
                  const previous = requiredAt(draft.tiers, draft.tiers.length - 1)
                  if (draft.billing_cycle.threshold.basis === 'fixed_cycle_kwh') previous.upper_bound_exclusive_kwh = String(Number(previous.lower_bound_inclusive_kwh) + 100)
                  else previous.upper_bound_multiplier = String(Number(previous.lower_bound_multiplier ?? 0) + 1)
                  draft.tiers.push(blankTier(draft.tiers.length, previous.upper_bound_exclusive_kwh ?? '0'))
                  normalizeTiers(draft.tiers, draft.billing_cycle.threshold.basis)
                }); }}><Plus size={15} /> Add tier</button>
              </fieldset>
            </Panel>
          )}
        </div>
      )}

      {step === 2 && (
        <div className="editor-stack">
          {(document.pricing_model === 'flat' || document.pricing_model === 'tiered') && <Panel title="TOU schedules not used" eyebrow="Pricing model"><p className="panel-copy">This pricing model does not depend on time of day. Continue to charges and adjustments.</p></Panel>}
          {(document.pricing_model === 'time_of_use' || document.pricing_model === 'time_of_use_tiered') && <>
          {document.seasons.map((season, seasonIndex) => (
            <Panel
              key={`${season.name}-${seasonIndex}`}
              title={season.name || `Season ${seasonIndex + 1}`}
              eyebrow={`${season.start} through ${season.end}`}
              actions={editable && <>
                <button className="button ghost" onClick={() => { update((draft) => {
                  const copy = structuredClone(requiredAt(draft.seasons, seasonIndex))
                  copy.name = `${copy.name}-copy`
                  draft.seasons.push(copy)
                }); }}><Copy size={14} /> Copy season</button>
                {document.seasons.length > 1 && <button className="icon-button danger-text" aria-label={`Delete ${season.name}`} onClick={() => { update((draft) => { draft.seasons.splice(seasonIndex, 1) }); }}><Trash2 size={16} /></button>}
              </>}
            >
              <fieldset className="editor-fieldset" disabled={!editable}>
                <div className="season-fields">
                  <label>Name<input value={season.name} onChange={(event) => { changeSeason(seasonIndex, (item) => { item.name = event.target.value }); }} /></label>
                  <label>Starts (MM-DD)<input value={season.start} onChange={(event) => { changeSeason(seasonIndex, (item) => { item.start = event.target.value }); }} /></label>
                  <label>Ends (MM-DD)<input value={season.end} onChange={(event) => { changeSeason(seasonIndex, (item) => { item.end = event.target.value }); }} /></label>
                  <label>Priority<input type="number" value={season.priority} onChange={(event) => { changeSeason(seasonIndex, (item) => { item.priority = Number(event.target.value) }); }} /></label>
                </div>
                {season.schedules.map((schedule, scheduleIndex) => (
                  <section className="schedule-editor" key={`${schedule.day_type}-${scheduleIndex}`}>
                    <header><div><strong>{schedule.day_type.replaceAll('-', ' ')}</strong><small>{schedule.periods.length} periods</small></div><div>
                      <button className="button ghost" onClick={() => { changeSeason(seasonIndex, (item) => {
                        const copy = structuredClone(requiredAt(item.schedules, scheduleIndex))
                        copy.day_type = copy.day_type === 'weekday' ? 'weekend' : 'weekday'
                        item.schedules.push(copy)
                      }); }}><Copy size={14} /> Copy schedule</button>
                      {season.schedules.length > 1 && <button className="icon-button danger-text" aria-label="Delete schedule" onClick={() => { changeSeason(seasonIndex, (item) => { item.schedules.splice(scheduleIndex, 1) }); }}><Trash2 size={15} /></button>}
                    </div></header>
                    <label>Day type<select value={schedule.day_type} onChange={(event) => { changeSchedule(seasonIndex, scheduleIndex, (item) => { item.day_type = event.target.value as DayScheduleDocument['day_type'] }); }}><option value="all-days">All days</option><option value="weekday">Weekday</option><option value="weekend">Weekend</option><option value="holiday">Holiday</option><option value="date-override">Date override</option></select></label>
                    <div className="rate-timeline" aria-label={`${season.name} ${schedule.day_type} schedule`}>
                      {schedule.periods.map((period, periodIndex) => <span key={`${period.label}-${periodIndex}`} style={{ left: `${period.start_minute / 14.4}%`, width: `${Math.max(0, period.end_minute - period.start_minute) / 14.4}%` }}>{period.label}</span>)}
                    </div>
                    {scheduleIssues(schedule).map((issue) => <p className="field-error schedule-error" role="alert" key={issue}>{issue}</p>)}
                    <div className="period-table">
                      <div className="period-row period-head"><span>Label</span><span>Start</span><span>End</span><span>Total $/kWh</span><span>Delivery</span><span>Generation</span><span /></div>
                      {schedule.periods.map((period, periodIndex) => (
                        <div className="period-row" key={periodIndex}>
                          <input aria-label="Period label" value={period.label} onChange={(event) => { changePeriod(seasonIndex, scheduleIndex, periodIndex, (item) => { item.label = event.target.value }); }} />
                          <input aria-label="Start minute" type="number" min="0" max="1439" value={period.start_minute} onChange={(event) => { changePeriod(seasonIndex, scheduleIndex, periodIndex, (item) => { item.start_minute = Number(event.target.value) }); }} />
                          <input aria-label="End minute" type="number" min="1" max="1440" value={period.end_minute} onChange={(event) => { changePeriod(seasonIndex, scheduleIndex, periodIndex, (item) => { item.end_minute = Number(event.target.value) }); }} />
                          <input aria-label="Total price per kWh" value={period.price_per_kwh} onChange={(event) => { changePeriod(seasonIndex, scheduleIndex, periodIndex, (item) => { item.price_per_kwh = event.target.value }); }} />
                          <input aria-label="Delivery component" value={period.delivery_per_kwh} onChange={(event) => { changePeriod(seasonIndex, scheduleIndex, periodIndex, (item) => { item.delivery_per_kwh = event.target.value }); }} />
                          <input aria-label="Generation component" value={period.generation_per_kwh} onChange={(event) => { changePeriod(seasonIndex, scheduleIndex, periodIndex, (item) => { item.generation_per_kwh = event.target.value }); }} />
                          <button className="icon-button danger-text" aria-label="Delete period" onClick={() => { changeSchedule(seasonIndex, scheduleIndex, (item) => { item.periods.splice(periodIndex, 1) }); }}><Trash2 size={14} /></button>
                        </div>
                      ))}
                    </div>
                    <button className="button secondary" onClick={() => { changeSchedule(seasonIndex, scheduleIndex, (item) => { const period = blankPeriod(item.periods.at(-1)?.end_minute ?? 0); period.display_order = item.periods.length; item.periods.push(period) }); }}><Plus size={14} /> Add period</button>
                  </section>
                ))}
                <button className="button secondary" onClick={() => { changeSeason(seasonIndex, (item) => { item.schedules.push({ day_type: 'weekday', dates: [], periods: [blankPeriod()] }) }); }}><Plus size={14} /> Add day schedule</button>
              </fieldset>
            </Panel>
          ))}
          {editable && <button className="button secondary add-season" onClick={() => { update((draft) => { draft.seasons.push({ name: `season-${draft.seasons.length + 1}`, start: '01-01', end: '12-31', priority: draft.seasons.length, leap_day_behavior: 'include', schedules: [{ day_type: 'all-days', dates: [], periods: [blankPeriod()] }] }) }); }}><Plus size={15} /> Add season</button>}
          </>}
        </div>
      )}

      {step === 3 && (
        <Panel title="Charges, credits, and adjustments" eyebrow="Applied in explicit calculation order">
          <fieldset className="editor-fieldset" disabled={!editable}>
            <p className="panel-copy">Whole-account items are ignored when the cost scope is energy-only.</p>
            <div className="adjustment-list">
              {document.adjustments.map((adjustment, index) => (
                <article key={index} className="adjustment-card">
                  <header><strong>{adjustment.name}</strong><button className="icon-button danger-text" aria-label="Delete adjustment" onClick={() => { update((draft) => { draft.adjustments.splice(index, 1) }); }}><Trash2 size={15} /></button></header>
                  <div className="form-columns">
                    <label>Name<input value={adjustment.name} onChange={(event) => { changeAdjustment(index, (item) => { item.name = event.target.value }); }} /></label>
                    <label>Type<select value={adjustment.component} onChange={(event) => { changeAdjustment(index, (item) => { item.component = event.target.value as RateAdjustmentDocument['component'] }); }}>{['daily_fixed_charge', 'monthly_fixed_charge', 'minimum_charge', 'baseline_credit', 'percentage_tax', 'fixed_tax', 'generation_provider', 'cca', 'direct_access', 'manual_credit', 'other'].map((value) => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}</select></label>
                  </div>
                  <div className="form-columns">
                    <label>Exact value<input value={adjustment.value} onChange={(event) => { changeAdjustment(index, (item) => { item.value = event.target.value }); }} /></label>
                    <label>Unit<input value={adjustment.unit} onChange={(event) => { changeAdjustment(index, (item) => { item.unit = event.target.value }); }} /></label>
                  </div>
                  <label>Scope<select value={adjustment.scope} onChange={(event) => { changeAdjustment(index, (item) => { item.scope = event.target.value }); }}><option value="all_energy">All energy</option><option value="allocated_account_estimate">Allocated account estimate</option><option value="full_account_estimate">Full account estimate</option></select></label>
                  <label>Description<input value={adjustment.description} onChange={(event) => { changeAdjustment(index, (item) => { item.description = event.target.value }); }} /></label>
                </article>
              ))}
            </div>
            <button className="button secondary" onClick={() => { update((draft) => { draft.adjustments.push({ name: 'New adjustment', component: 'other', operation: 'add', value: '0.00000000', unit: 'per_kwh', scope: 'full_account_estimate', eligibility: {}, effective_from: null, effective_to: null, calculation_order: draft.adjustments.length, description: '' }) }); }}><Plus size={15} /> Add adjustment</button>
          </fieldset>
        </Panel>
      )}

      {step === 4 && (
        <div className="validation-layout">
          <Panel title="Validation" eyebrow="Blocking checks and warnings" actions={canManage && <button className="button secondary" disabled={validate.isPending} onClick={() => { validate.mutate(); }}><CheckCircle2 size={15} /> Validate</button>}>
            {!validation ? <p className="panel-copy">Run validation to check pricing structure, exact decimals, billing-cycle thresholds, schedule coverage, provider assumptions, and account-charge scope.</p> : <>
              <div className="validation-summary"><StatusPill status={validation.valid ? 'validated' : 'failed'} label={validation.valid ? 'Ready to activate' : `${validation.errors.length} blocking errors`} /><code>{validation.integrity_sha256}</code></div>
              {[...validation.errors, ...validation.warnings].map((issue, index) => <div className={`validation-issue ${issue.level}`} key={`${issue.code}-${index}`}><AlertTriangle size={16} /><p><strong>{issue.message}</strong><small>{issue.path} · {issue.code}</small></p></div>)}
            </>}
          </Panel>
          <Panel title="Coverage and sample" eyebrow="Normalized preview">
            <div className="coverage-list">{coverage.map((item) => <div key={item.key}><span>{item.key}</span><strong className={item.valid ? 'good-text' : 'danger-text'}>{item.valid ? 'Complete' : `${item.minutes}/1,440 min · invalid`}</strong></div>)}</div>
            <div className="preview-controls">
              <label>Sample cycle usage (kWh)<input inputMode="decimal" value={sampleUsage} onChange={(event) => { setSampleUsage(event.target.value) }} /></label>
              <button className="button secondary" disabled={preview.isPending} onClick={() => { preview.mutate(); }}>{preview.isPending ? 'Calculating…' : 'Calculate preview'}</button>
            </div>
            {sampleCost && <div className="tier-preview-result">
              <p className="sample-cost"><span>Energy charge</span><strong>{sampleCost.display?.energy_charge ?? formatCurrency(sampleCost.energy_charge)}</strong></p>
              <p className="sample-cost"><span>Blended energy rate</span><strong>{sampleCost.display?.blended_energy_rate ?? formatEnergyRate(sampleCost.blended_energy_rate, { derived: true })}</strong></p>
              <p className="sample-cost"><span>Estimated sample total</span><strong>{sampleCost.display?.estimated_total ?? formatCurrency(sampleCost.display_total)}</strong></p>
              {sampleCost.tier_thresholds.length > 0 && <div className="table-wrap"><table><thead><tr><th>Tier</th><th>Range</th><th>Usage</th><th>Charge</th></tr></thead><tbody>
                {sampleCost.tier_thresholds.map((tier) => <tr key={tier.tier_id}>
                  <td>{tier.name}</td>
                  <td>{tier.display_range ?? formatTierRange(tier.lower_bound_kwh, tier.upper_bound_kwh)}</td>
                  <td>{tier.display_usage ?? formatEnergy(sampleCost.energy_by_tier_kwh[tier.tier_id] ?? '0')}</td>
                  <td>{tier.display_charge ?? formatCurrency(sampleCost.charge_by_tier[tier.tier_id] ?? '0')}</td>
                </tr>)}
              </tbody></table></div>}
            </div>}
            {preview.error && <p className="field-error">{preview.error.message}</p>}
            <details className="json-preview"><summary>Normalized JSON</summary><pre>{JSON.stringify(document, null, 2)}</pre></details>
          </Panel>
          {saved && <Panel title="Assign this version" eyebrow="Utility account"><label>Utility account<select defaultValue="" disabled={query.data?.version.status !== 'active' && query.data?.version.status !== 'approved'} onChange={(event) => { if (event.target.value) assign.mutate(event.target.value) }}><option value="">Select an account…</option>{accounts.data?.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</select></label>{assign.isSuccess && <p className="form-success">Rate version assigned.</p>}<small className="field-help">Activate or schedule the version before assigning it.</small></Panel>}
        </div>
      )}

      <footer className="editor-footer">
        <button className="button secondary" disabled={step === 0} onClick={() => { setStep((value) => value - 1); }}><ArrowLeft size={15} /> Previous</button>
        <div>
          {save.error && <span className="field-error">{save.error.message}</span>}
          {editable && <button className="button secondary" disabled={save.isPending} onClick={() => { save.mutate(); }}><Save size={15} /> Save draft</button>}
          {step < 4 ? <button className="button primary" onClick={() => { setStep((value) => value + 1); }}>Next <ArrowRight size={15} /></button> : editable && saved && <button className="button primary" disabled={!validation?.valid || activate.isPending} onClick={() => { activationDialog.current?.showModal() }}><CheckCircle2 size={15} /> Activate</button>}
        </div>
      </footer>
      <dialog ref={activationDialog} className="sensor-removal-dialog rate-activation-dialog" aria-labelledby="activate-rate-title">
        <form method="dialog" onSubmit={(event) => { event.preventDefault(); if (saved) activate.mutate(saved.versionId) }}>
          <header><div><span className="eyebrow">Immutable effective-dated change</span><h2 id="activate-rate-title">Activate rate version</h2></div><button type="button" className="icon-button" aria-label="Close activation dialog" onClick={() => activationDialog.current?.close()}><X /></button></header>
          <p>Confirm the exact version that future estimates will use. Historical calculations retain their original rate version.</p>
          <dl className="rate-meta"><div><dt>Plan</dt><dd>{document.plan_code}</dd></div><div><dt>Effective</dt><dd>{document.effective_from}</dd></div><div><dt>Cost scope</dt><dd>{document.cost_scope_default.replaceAll('_', ' ')}</dd></div><div><dt>Provider</dt><dd>{document.provider_mode.replaceAll('_', ' ')}</dd></div></dl>
          {validation?.warnings.length ? <p className="warning-text"><AlertTriangle size={16} /> {validation.warnings.length} warning{validation.warnings.length === 1 ? '' : 's'} were reviewed.</p> : <p className="good-text"><CheckCircle2 size={16} /> All blocking validation checks passed.</p>}
          {activate.error && <p className="field-error" role="alert">{activate.error.message}</p>}
          <footer><button type="button" className="button secondary" onClick={() => activationDialog.current?.close()}>Cancel</button><button className="button primary" disabled={activate.isPending}><CheckCircle2 size={15} /> {activate.isPending ? 'Activating…' : 'Activate version'}</button></footer>
        </form>
      </dialog>
    </>
  )
}
