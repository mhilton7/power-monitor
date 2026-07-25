import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, Copy, Plus, Save, ShieldCheck, Trash2 } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'
import { errorMessage, json, request } from '../../api/client'
import { objectList, record, stringValue } from '../../api/validation'
import { InlineNotice } from '../../components/feedback/States'
import { TabList } from '../../components/layout/Layout'
import type { ElectricService, Home } from '../../types/models'
import { money, rate, statusLabel } from '../../utils/format'
import {
  adjustment,
  newRateDraft,
  period,
  tier,
  type PricingModel,
  type RateAdjustmentDraft,
  type RatePlanDraft,
  type RateValidationResult,
} from './rateDocument'

type EditorSection = 'details' | 'model' | 'dates' | 'tiers' | 'schedules' | 'adjustments' | 'cycle' | 'assignment' | 'preview' | 'lifecycle'

const sections: ReadonlyArray<readonly [EditorSection, string]> = [
  ['details', '1 Details'],
  ['model', '2 Model'],
  ['dates', '3 Dates & seasons'],
  ['tiers', '4 Tiers'],
  ['schedules', '5 TOU schedules'],
  ['adjustments', '6 Charges'],
  ['cycle', '7 Billing cycle'],
  ['assignment', '8 Assignment'],
  ['preview', '9 Preview'],
  ['lifecycle', '10 Publish'],
]

export interface SavedDraft {
  planId: string
  versionId: string
  status: string
}

interface PreviewResult {
  display_total?: string
  energy_charge?: string
  blended_energy_rate?: string | null
  display?: { energy_charge?: string; blended_energy_rate?: string; estimated_total?: string }
  integrity_sha256?: string
}

function validationAdapter(value: unknown): RateValidationResult {
  const source = record(value, 'rate validation')
  const issues = (key: 'errors' | 'warnings') => objectList(source[key]).map((item) => ({
    level: stringValue(item.level, key === 'errors' ? 'error' : 'warning'),
    code: stringValue(item.code),
    path: stringValue(item.path),
    message: stringValue(item.message),
  }))
  return {
    valid: source.valid === true,
    errors: issues('errors'),
    warnings: issues('warnings'),
    integrity_sha256: stringValue(source.integrity_sha256),
  }
}

function savedDraftAdapter(value: unknown): SavedDraft {
  const root = record(value, 'saved rate plan')
  const plan = record(root.plan, 'saved rate plan')
  const versions = objectList(plan.versions)
  const latest = versions[0] ?? record(root.version)
  const planId = stringValue(plan.id, stringValue(root.plan_id))
  const versionId = stringValue(latest.id, stringValue(root.version_id))
  if (!planId || !versionId) throw new Error('The server saved the draft but did not return its identifiers.')
  return { planId, versionId, status: stringValue(latest.status, 'draft') }
}

function cloneDraft(value: RatePlanDraft): RatePlanDraft {
  return structuredClone(value)
}

export function StructuredRateEditor({
  home,
  services,
  onClose,
  initialDraft,
  initialSaved,
}: {
  home: Home
  services: ElectricService[]
  onClose: () => void
  initialDraft?: RatePlanDraft
  initialSaved?: SavedDraft
}) {
  const client = useQueryClient()
  const [section, setSection] = useState<EditorSection>('details')
  const [draft, setDraft] = useState<RatePlanDraft>(() => initialDraft ? cloneDraft(initialDraft) : newRateDraft(home))
  const [saved, setSaved] = useState<SavedDraft | undefined>(initialSaved)
  const [validation, setValidation] = useState<RateValidationResult>()
  const [preview, setPreview] = useState<PreviewResult>()
  const [sampleKwh, setSampleKwh] = useState('500')
  const [serviceId, setServiceId] = useState(services[0]?.id ?? '')
  const [assigned, setAssigned] = useState(false)
  const [activated, setActivated] = useState(false)

  const isTou = draft.pricing_model === 'time_of_use' || draft.pricing_model === 'time_of_use_tiered'
  const isTiered = draft.pricing_model === 'tiered' || draft.pricing_model === 'time_of_use_tiered'
  const periodLabels = useMemo(() => [...new Set(draft.seasons.flatMap((season) => season.schedules.flatMap((schedule) => schedule.periods.map((item) => item.label))))], [draft.seasons])

  const update = (change: (next: RatePlanDraft) => void) => {
    setDraft((current) => {
      const next = cloneDraft(current)
      change(next)
      return next
    })
    setValidation(undefined)
  }

  const save = useMutation({
    mutationFn: async () => {
      if (saved) {
        await request(`/api/v1/rates/versions/${saved.versionId}`, json('PATCH', draft))
        return saved
      }
      return request('/api/v1/rates/plans', json('POST', draft), savedDraftAdapter)
    },
    onSuccess: async (result) => {
      setSaved(result)
      await client.invalidateQueries({ queryKey: ['managed-rate-plans'] })
    },
  })
  const validate = useMutation({
    mutationFn: () => request('/api/v1/rates/validate-document', json('POST', draft), validationAdapter),
    onSuccess: setValidation,
  })
  const calculate = useMutation({
    mutationFn: () => request<PreviewResult>('/api/v1/rates/preview-cost', json('POST', {
      document: draft,
      interval_start: `${draft.effective_from}T00:00:00-07:00`,
      interval_end: `${draft.effective_from}T01:00:00-07:00`,
      energy_kwh: sampleKwh,
      cost_scope: draft.cost_scope_default,
    })),
    onSuccess: setPreview,
  })
  const activate = useMutation({
    mutationFn: async () => {
      if (!saved) throw new Error('Save this draft before publishing it.')
      return request(`/api/v1/rates/versions/${saved.versionId}/activate`, json('POST'))
    },
    onSuccess: () => {
      setActivated(true)
      setSaved((current) => current ? { ...current, status: 'active' } : current)
    },
  })
  const assign = useMutation({
    mutationFn: async () => {
      if (!saved || !serviceId) throw new Error('Choose an electric service and publish this version first.')
      return request('/api/v1/rates/assignments', json('POST', {
        utility_account_id: serviceId,
        rate_version_id: saved.versionId,
        provider_mode: draft.provider_mode,
        cost_scope: draft.cost_scope_default,
        effective_from: new Date().toISOString(),
      }))
    },
    onSuccess: async () => {
      setAssigned(true)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['electric-services'] }),
        client.invalidateQueries({ queryKey: ['home-summary'] }),
      ])
    },
  })

  const mutationError = save.error ?? validate.error ?? calculate.error ?? activate.error ?? assign.error
  return (
    <div className="rate-editor-shell">
      <header className="rate-editor-heading">
        <div><span className="section-eyebrow">Complete custom rate plan</span><h3>{draft.plan_name || 'Untitled rate plan'}</h3><p>Exact values remain strings until the server validates them with Decimal arithmetic.</p></div>
        <button type="button" className="button secondary" onClick={onClose}>Close editor</button>
      </header>
      <TabList idBase="rate-editor" label="Rate plan editor sections" value={section} items={sections} onChange={setSection} />

      <form className="rate-editor-form" onSubmit={(event) => { event.preventDefault() }}>
        {section === 'details' && <EditorPanel title="Plan details" description="Identity, provider, ownership, source, and notes.">
          <div className="form-grid">
            <Field label="Plan name"><input value={draft.plan_name} onChange={(event) => { update((next) => { next.plan_name = event.target.value }) }} required /></Field>
            <Field label="Plan code"><input value={draft.plan_code} onChange={(event) => { update((next) => { next.plan_code = event.target.value.toUpperCase().replace(/[^A-Z0-9._-]/g, '-') }) }} required /></Field>
            <Field label="Utility or provider"><input value={draft.utility} onChange={(event) => { update((next) => { next.utility = event.target.value }) }} /></Field>
            <Field label="Timezone"><input value={draft.timezone} onChange={(event) => { update((next) => { next.timezone = event.target.value }) }} /></Field>
            <Field label="Description" wide><textarea rows={3} value={draft.description} onChange={(event) => { update((next) => { next.description = event.target.value }) }} /></Field>
            <Field label="Source label"><input value={draft.source_label} onChange={(event) => { update((next) => { next.source_label = event.target.value }) }} /></Field>
            <Field label="Source note"><input value={draft.source_note} onChange={(event) => { update((next) => { next.source_note = event.target.value }) }} /></Field>
            <Field label="Administrator notes" wide><textarea rows={3} value={draft.custom_notes} onChange={(event) => { update((next) => { next.custom_notes = event.target.value }) }} /></Field>
          </div>
        </EditorPanel>}

        {section === 'model' && <EditorPanel title="Pricing model and components" description="Choose one authoritative calculation strategy; component values remain explicit.">
          <div className="form-grid">
            <Field label="Pricing model"><select value={draft.pricing_model} onChange={(event) => { changeModel(event.target.value as PricingModel) }}><option value="flat">Flat</option><option value="time_of_use">Time of use</option><option value="tiered">Billing-cycle tiered</option><option value="time_of_use_tiered">TOU + tiered</option></select></Field>
            <Field label="Currency"><input value={draft.currency} maxLength={3} onChange={(event) => { update((next) => { next.currency = event.target.value.toUpperCase() }) }} /></Field>
            {draft.pricing_model === 'flat' && <Field label="Flat energy rate"><DecimalInput label="Flat energy rate" value={draft.flat_rate_per_kwh ?? ''} onChange={(value) => { update((next) => { next.flat_rate_per_kwh = value }) }} /></Field>}
            <Field label="Provider composition"><select value={draft.provider_mode} onChange={(event) => { update((next) => { next.provider_mode = event.target.value as RatePlanDraft['provider_mode'] }) }}><option value="sce_delivery_generation">SCE delivery + generation</option><option value="sce_delivery_cca">SCE delivery + CCA</option><option value="sce_delivery_direct_access">SCE delivery + Direct Access</option><option value="custom_combined">Custom combined</option></select></Field>
            <Field label="Default cost scope"><select value={draft.cost_scope_default} onChange={(event) => { update((next) => { next.cost_scope_default = event.target.value as RatePlanDraft['cost_scope_default'] }) }}><option value="energy_only">Energy only</option><option value="allocated_account_estimate">Allocated account estimate</option><option value="full_account_estimate">Complete utility-account estimate</option></select></Field>
            {draft.pricing_model === 'time_of_use_tiered' && <Field label="Hybrid method"><select value={draft.hybrid_pricing?.method ?? 'tier_period_matrix'} onChange={(event) => { update((next) => { next.hybrid_pricing = { method: event.target.value as NonNullable<RatePlanDraft['hybrid_pricing']>['method'] } }) }}><option value="tier_period_matrix">Rate for each tier and period</option><option value="tier_base_plus_tou_adder">Tier base + TOU adder</option><option value="tou_base_plus_tier_adder">TOU base + tier adder</option></select></Field>}
          </div>
        </EditorPanel>}

        {section === 'dates' && <EditorPanel title="Effective dates and seasons" description="Versions are effective-dated; seasons use local month-day boundaries.">
          <div className="form-grid">
            <Field label="Effective from"><input type="date" value={draft.effective_from} onChange={(event) => { update((next) => { next.effective_from = event.target.value }) }} /></Field>
            <Field label="Effective through (optional)"><input type="date" value={draft.effective_through ?? ''} onChange={(event) => { update((next) => { next.effective_through = event.target.value || null }) }} /></Field>
          </div>
          <div className="structured-editor-list">
            {draft.seasons.map((season, index) => <article key={`${season.name}-${index}`} className="editor-row-card">
              <header><strong>{season.name || `Season ${index + 1}`}</strong>{draft.seasons.length > 1 && <button type="button" className="icon-button" aria-label={`Remove season ${index + 1}`} onClick={() => { update((next) => { next.seasons.splice(index, 1) }) }}><Trash2 /></button>}</header>
              <div className="form-grid">
                <Field label="Season name"><input value={season.name} onChange={(event) => { update((next) => { required(next.seasons, index).name = event.target.value }) }} /></Field>
                <Field label="Starts (MM-DD)"><input value={season.start} onChange={(event) => { update((next) => { required(next.seasons, index).start = event.target.value }) }} /></Field>
                <Field label="Ends (MM-DD)"><input value={season.end} onChange={(event) => { update((next) => { required(next.seasons, index).end = event.target.value }) }} /></Field>
                <Field label="Priority"><input type="number" value={season.priority} onChange={(event) => { update((next) => { required(next.seasons, index).priority = Number(event.target.value) }) }} /></Field>
              </div>
            </article>)}
          </div>
          <button type="button" className="button secondary compact" onClick={() => { update((next) => { next.seasons.push({ name: `Season ${next.seasons.length + 1}`, start: '01-01', end: '12-31', priority: next.seasons.length, leap_day_behavior: 'include', schedules: [{ day_type: 'all-days', dates: [], periods: [period('Flat', 0, 1440, '0.25000000', 0)] }] }) }) }}><Plus size={15} /> Add season</button>
        </EditorPanel>}

        {section === 'tiers' && <EditorPanel title="Billing-cycle tiers" description="Use any number of ordered tiers. The final tier is always open-ended.">
          {!isTiered ? <InlineNotice>This pricing model does not use billing-cycle tiers.</InlineNotice> : <>
            <div className="form-grid">
              <Field label="Threshold basis"><select value={draft.billing_cycle.threshold.basis} onChange={(event) => { changeThresholdBasis(event.target.value as RatePlanDraft['billing_cycle']['threshold']['basis']) }}><option value="fixed_cycle_kwh">Fixed billing-cycle kWh</option><option value="daily_baseline_kwh">Daily baseline × exact cycle days</option></select></Field>
              {draft.billing_cycle.threshold.basis === 'daily_baseline_kwh' && <Field label="Daily baseline (kWh/day)"><DecimalInput label="Daily baseline" value={draft.billing_cycle.threshold.daily_baseline_kwh ?? ''} onChange={(value) => { update((next) => { next.billing_cycle.threshold.daily_baseline_kwh = value }) }} /></Field>}
            </div>
            <div className="structured-editor-list">
              {draft.tiers.map((item, index) => <article key={item.tier_id} className="editor-row-card">
                <header><strong>{item.name}</strong>{draft.tiers.length > 2 && <button type="button" className="icon-button" aria-label={`Remove tier ${index + 1}`} onClick={() => { update((next) => { next.tiers.splice(index, 1); normalizeTiers(next) }) }}><Trash2 /></button>}</header>
                <div className="form-grid">
                  <Field label="Tier name"><input value={item.name} onChange={(event) => { update((next) => { required(next.tiers, index).name = event.target.value }) }} /></Field>
                  <Field label={draft.billing_cycle.threshold.basis === 'daily_baseline_kwh' ? 'Upper baseline multiple' : 'Upper kWh (blank = open)'}><DecimalInput label={`Tier ${index + 1} upper bound`} value={draft.billing_cycle.threshold.basis === 'daily_baseline_kwh' ? item.upper_bound_multiplier ?? '' : item.upper_bound_exclusive_kwh ?? ''} disabled={index === draft.tiers.length - 1} onChange={(value) => { update((next) => { const selected = required(next.tiers, index); if (next.billing_cycle.threshold.basis === 'daily_baseline_kwh') selected.upper_bound_multiplier = value || null; else selected.upper_bound_exclusive_kwh = value || null; normalizeTiers(next) }) }} /></Field>
                  <Field label="Base rate"><DecimalInput label={`Tier ${index + 1} rate`} value={item.price_per_kwh} onChange={(value) => { update((next) => { required(next.tiers, index).price_per_kwh = value }) }} /></Field>
                  {draft.pricing_model === 'time_of_use_tiered' && periodLabels.map((label) => <Field key={label} label={`${label} rate`}><DecimalInput label={`${item.name} ${label} rate`} value={item.tou_prices[label] ?? ''} onChange={(value) => { update((next) => { required(next.tiers, index).tou_prices[label] = value }) }} /></Field>)}
                </div>
              </article>)}
            </div>
            <button type="button" className="button secondary compact" onClick={() => { update((next) => { const previous = next.tiers.at(-1); if (previous) previous.upper_bound_exclusive_kwh = previous.upper_bound_exclusive_kwh ?? String((next.tiers.length) * 500); next.tiers.push(tier(next.tiers.length, previous?.upper_bound_exclusive_kwh ?? '0', null)); normalizeTiers(next) }) }}><Plus size={15} /> Add tier</button>
          </>}
        </EditorPanel>}

        {section === 'schedules' && <EditorPanel title="Time-of-use schedules" description="Build complete local-day schedules. Split an overnight period at midnight into two rows with the same label.">
          {!isTou ? <InlineNotice>This pricing model does not use time-of-use schedules.</InlineNotice> : <div className="structured-editor-list">
            {draft.seasons.map((season, seasonIndex) => <article key={`${season.name}-${seasonIndex}`} className="editor-row-card">
              <header><strong>{season.name}</strong><button type="button" className="button ghost compact" onClick={() => { update((next) => { const selectedSeason = required(next.seasons, seasonIndex); const copy = structuredClone(selectedSeason.schedules[0] ?? { day_type: 'all-days' as const, dates: [], periods: [] }); copy.day_type = copy.day_type === 'weekday' ? 'weekend' : 'weekday'; selectedSeason.schedules.push(copy) }) }}><Copy size={14} /> Copy schedule</button></header>
              {season.schedules.map((schedule, scheduleIndex) => <div key={`${schedule.day_type}-${scheduleIndex}`} className="schedule-draft">
                <div className="form-grid">
                  <Field label="Day type"><select value={schedule.day_type} onChange={(event) => { update((next) => { required(required(next.seasons, seasonIndex).schedules, scheduleIndex).day_type = event.target.value as typeof schedule.day_type }) }}><option value="all-days">All days</option><option value="weekday">Weekday</option><option value="weekend">Weekend</option><option value="holiday">Holiday</option><option value="date-override">Date override</option></select></Field>
                  {schedule.day_type === 'date-override' && <Field label="Dates (comma separated)"><input value={schedule.dates.join(', ')} onChange={(event) => { update((next) => { required(required(next.seasons, seasonIndex).schedules, scheduleIndex).dates = event.target.value.split(',').map((value) => value.trim()).filter(Boolean) }) }} /></Field>}
                </div>
                <div className="period-editor">
                  {schedule.periods.map((item, periodIndex) => <div key={periodIndex}>
                    <input aria-label="Period label" value={item.label} onChange={(event) => { update((next) => { selectedPeriod(next, seasonIndex, scheduleIndex, periodIndex).label = event.target.value }) }} />
                    <input aria-label="Start minute" type="number" min="0" max="1439" value={item.start_minute} onChange={(event) => { update((next) => { selectedPeriod(next, seasonIndex, scheduleIndex, periodIndex).start_minute = Number(event.target.value) }) }} />
                    <input aria-label="End minute" type="number" min="1" max="1440" value={item.end_minute} onChange={(event) => { update((next) => { selectedPeriod(next, seasonIndex, scheduleIndex, periodIndex).end_minute = Number(event.target.value) }) }} />
                    <DecimalInput label={`${item.label} price`} value={item.price_per_kwh} onChange={(value) => { update((next) => { selectedPeriod(next, seasonIndex, scheduleIndex, periodIndex).price_per_kwh = value }) }} />
                    <DecimalInput label={`${item.label} delivery component`} value={item.delivery_per_kwh} onChange={(value) => { update((next) => { selectedPeriod(next, seasonIndex, scheduleIndex, periodIndex).delivery_per_kwh = value }) }} />
                    <DecimalInput label={`${item.label} generation component`} value={item.generation_per_kwh} onChange={(value) => { update((next) => { selectedPeriod(next, seasonIndex, scheduleIndex, periodIndex).generation_per_kwh = value }) }} />
                    <button type="button" className="icon-button" aria-label={`Remove period ${periodIndex + 1}`} onClick={() => { update((next) => { required(required(next.seasons, seasonIndex).schedules, scheduleIndex).periods.splice(periodIndex, 1) }) }}><Trash2 /></button>
                  </div>)}
                </div>
                <button type="button" className="button secondary compact" onClick={() => { update((next) => { const periods = required(required(next.seasons, seasonIndex).schedules, scheduleIndex).periods; const start = periods.at(-1)?.end_minute ?? 0; periods.push(period('New period', start, 1440, '0.25000000', periods.length)) }) }}><Plus size={15} /> Add period</button>
              </div>)}
            </article>)}
          </div>}
        </EditorPanel>}

        {section === 'adjustments' && <EditorPanel title="Charges, credits, and adjustments" description="Each item has an explicit operation, unit, scope, order, and effective window.">
          <InlineNotice><ShieldCheck size={16} /> Whole-account charges and credits are ignored for energy-only scope and apply once only to a complete account aggregate.</InlineNotice>
          <div className="structured-editor-list">
            {draft.adjustments.map((item, index) => <article key={index} className="editor-row-card">
              <header><strong>{item.name}</strong><button type="button" className="icon-button" aria-label={`Remove adjustment ${index + 1}`} onClick={() => { update((next) => { next.adjustments.splice(index, 1) }) }}><Trash2 /></button></header>
              <div className="form-grid">
                <Field label="Name"><input value={item.name} onChange={(event) => { changeAdjustment(index, (next) => { next.name = event.target.value }) }} /></Field>
                <Field label="Component"><select value={item.component} onChange={(event) => { changeAdjustment(index, (next) => { next.component = event.target.value as RateAdjustmentDraft['component'] }) }}>{adjustmentComponents.map((value) => <option key={value} value={value}>{statusLabel(value)}</option>)}</select></Field>
                <Field label="Operation"><select value={item.operation} onChange={(event) => { changeAdjustment(index, (next) => { next.operation = event.target.value as RateAdjustmentDraft['operation'] }) }}><option value="add">Add</option><option value="subtract">Subtract</option><option value="minimum">Minimum</option><option value="multiply">Multiply</option></select></Field>
                <Field label="Exact value"><DecimalInput label={`${item.name} value`} value={item.value} onChange={(value) => { changeAdjustment(index, (next) => { next.value = value }) }} /></Field>
                <Field label="Unit"><input value={item.unit} onChange={(event) => { changeAdjustment(index, (next) => { next.unit = event.target.value }) }} /></Field>
                <Field label="Scope"><select value={item.scope} onChange={(event) => { changeAdjustment(index, (next) => { next.scope = event.target.value }) }}><option value="all_energy">All energy</option><option value="allocated_account_estimate">Allocated account estimate</option><option value="full_account_estimate">Complete utility-account estimate</option></select></Field>
                <Field label="Effective from"><input type="date" value={item.effective_from ?? ''} onChange={(event) => { changeAdjustment(index, (next) => { next.effective_from = event.target.value || null }) }} /></Field>
                <Field label="Effective through"><input type="date" value={item.effective_to ?? ''} onChange={(event) => { changeAdjustment(index, (next) => { next.effective_to = event.target.value || null }) }} /></Field>
                <Field label="Description" wide><input value={item.description} onChange={(event) => { changeAdjustment(index, (next) => { next.description = event.target.value }) }} /></Field>
              </div>
            </article>)}
          </div>
          <button type="button" className="button secondary compact" onClick={() => { update((next) => { next.adjustments.push(adjustment(next.adjustments.length)) }) }}><Plus size={15} /> Add charge or adjustment</button>
        </EditorPanel>}

        {section === 'cycle' && <EditorPanel title="Billing-cycle rules" description="Thresholds use exact cycle dates and configurable rounding.">
          <div className="form-grid">
            <Field label="Expected cycle start day"><input type="number" min="1" max="31" value={draft.billing_cycle.expected_start_day} onChange={(event) => { update((next) => { next.billing_cycle.expected_start_day = Number(event.target.value) }) }} /></Field>
            <Field label="Threshold rounding"><select value={draft.billing_cycle.threshold.rounding_policy} onChange={(event) => { update((next) => { next.billing_cycle.threshold.rounding_policy = event.target.value as RatePlanDraft['billing_cycle']['threshold']['rounding_policy'] }) }}><option value="none">No rounding</option><option value="nearest_kwh">Nearest kWh</option><option value="floor_kwh">Floor kWh</option><option value="ceil_kwh">Ceiling kWh</option></select></Field>
            <Field label="Baseline region"><input value={draft.billing_cycle.threshold.baseline_region ?? ''} onChange={(event) => { update((next) => { next.billing_cycle.threshold.baseline_region = event.target.value || null }) }} /></Field>
            <Field label="Baseline category"><input value={draft.billing_cycle.threshold.baseline_category ?? ''} onChange={(event) => { update((next) => { next.billing_cycle.threshold.baseline_category = event.target.value || null }) }} /></Field>
          </div>
          {draft.billing_cycle.threshold.basis === 'daily_baseline_kwh' && <div className="cycle-threshold-preview">{[28, 29, 30, 31].map((days) => <span key={days}><small>{days}-day cycle</small><strong>{(Number(draft.billing_cycle.threshold.daily_baseline_kwh ?? 0) * days).toFixed(3)} kWh</strong></span>)}</div>}
        </EditorPanel>}

        {section === 'assignment' && <EditorPanel title="Electric-service assignment" description="Assignment is a separate effective-dated action after validation and activation.">
          {services.length === 0 ? <InlineNotice tone="warning">Create an electric service before assigning this plan. You can still save and validate the draft.</InlineNotice> : <div className="form-grid">
            <Field label="Electric service"><select value={serviceId} onChange={(event) => { setServiceId(event.target.value) }}>{services.map((service) => <option key={service.id} value={service.id}>{service.name} · {statusLabel(service.costScope)}</option>)}</select></Field>
            <Field label="Cost scope"><select value={draft.cost_scope_default} onChange={(event) => { update((next) => { next.cost_scope_default = event.target.value as RatePlanDraft['cost_scope_default'] }) }}><option value="energy_only">Energy only</option><option value="allocated_account_estimate">Allocated account estimate</option><option value="full_account_estimate">Complete utility-account estimate</option></select></Field>
          </div>}
          <InlineNotice>Fixed charges and baseline credits remain disabled unless topology explicitly represents the complete utility account.</InlineNotice>
        </EditorPanel>}

        {section === 'preview' && <EditorPanel title="Validation and cost preview" description="The server validates exact decimals, effective dates, schedule coverage, tier boundaries, and scope.">
          <div className="preview-toolbar">
            <Field label="Sample energy (kWh)"><DecimalInput label="Sample energy" value={sampleKwh} onChange={setSampleKwh} /></Field>
            <button type="button" className="button secondary" disabled={validate.isPending} onClick={() => { validate.mutate() }}>Validate draft</button>
            <button type="button" className="button secondary" disabled={calculate.isPending} onClick={() => { calculate.mutate() }}>Calculate preview</button>
          </div>
          {validation && <div className={`validation-card ${validation.valid ? 'success' : 'danger'}`}><strong>{validation.valid ? 'Validation passed' : `${validation.errors.length} blocking issue${validation.errors.length === 1 ? '' : 's'}`}</strong><code>{validation.integrity_sha256}</code>{[...validation.errors, ...validation.warnings].map((issue, index) => <p key={`${issue.code}-${index}`}><AlertTriangle size={15} /><span><strong>{issue.message}</strong><small>{issue.path} · {issue.code}</small></span></p>)}</div>}
          {preview && <div className="preview-result-grid"><MetricValue label="Energy charge" value={preview.display?.energy_charge ?? money(preview.energy_charge, draft.currency)} /><MetricValue label="Blended rate" value={preview.display?.blended_energy_rate ?? rate(preview.blended_energy_rate ?? undefined, draft.currency)} /><MetricValue label="Estimated total" value={preview.display?.estimated_total ?? money(preview.display_total, draft.currency)} /></div>}
          <details className="json-preview"><summary>Normalized plan document</summary><pre>{JSON.stringify(draft, null, 2)}</pre></details>
        </EditorPanel>}

        {section === 'lifecycle' && <EditorPanel title="Save, publish, and assign" description="Drafts stay editable. Activated versions are immutable and historical assignments remain preserved.">
          <ol className="lifecycle-steps">
            <li className={saved ? 'complete' : ''}><span>{saved ? <Check /> : 1}</span><div><strong>Save draft</strong><p>Create or update the editable version.</p></div><button type="button" className="button secondary compact" disabled={save.isPending || activated} onClick={() => { save.mutate() }}>{save.isPending ? 'Saving…' : saved ? 'Save changes' : 'Save draft'}</button></li>
            <li className={validation?.valid ? 'complete' : ''}><span>{validation?.valid ? <Check /> : 2}</span><div><strong>Validate exact document</strong><p>Resolve every blocking issue before activation.</p></div><button type="button" className="button secondary compact" disabled={!saved || validate.isPending || activated} onClick={() => { validate.mutate() }}>Validate</button></li>
            <li className={activated ? 'complete' : ''}><span>{activated ? <Check /> : 3}</span><div><strong>Publish version</strong><p>Activates now or schedules by its effective date; the version becomes immutable.</p></div><button type="button" className="button primary compact" disabled={!saved || !validation?.valid || activate.isPending || activated} onClick={() => { activate.mutate() }}>{activate.isPending ? 'Publishing…' : activated ? 'Published' : 'Publish version'}</button></li>
            <li className={assigned ? 'complete' : ''}><span>{assigned ? <Check /> : 4}</span><div><strong>Assign to electric service</strong><p>Closes the previous effective assignment without changing historical costs.</p></div><button type="button" className="button secondary compact" disabled={!activated || !serviceId || assign.isPending || assigned} onClick={() => { assign.mutate() }}>{assign.isPending ? 'Assigning…' : assigned ? 'Assigned' : 'Assign plan'}</button></li>
          </ol>
        </EditorPanel>}

        {mutationError && <div className="workflow-error" role="alert"><p>{errorMessage(mutationError)}</p></div>}
        <footer className="rate-editor-footer">
          <span>{saved ? `Draft ${saved.versionId.slice(0, 8)} · ${statusLabel(saved.status)}` : 'Unsaved draft'}</span>
          <div className="inline-actions">
            <button type="button" className="button secondary" disabled={save.isPending || activated} onClick={() => { save.mutate() }}><Save size={15} /> {saved ? 'Save changes' : 'Save draft'}</button>
            <button type="button" className="button primary" onClick={() => { const index = sections.findIndex(([id]) => id === section); const next = sections[Math.min(sections.length - 1, index + 1)]; if (next) setSection(next[0]) }}>{section === 'lifecycle' ? 'Review complete' : 'Next section'}</button>
          </div>
        </footer>
      </form>
    </div>
  )

  function changeModel(model: PricingModel) {
    update((next) => {
      next.pricing_model = model
      next.flat_rate_per_kwh = model === 'flat' ? (next.flat_rate_per_kwh ?? '0.25000000') : null
      if (model === 'tiered' || model === 'time_of_use_tiered') {
        if (next.tiers.length < 2) next.tiers = [tier(0, '0', '500'), tier(1, '500', null)]
      } else next.tiers = []
      next.hybrid_pricing = model === 'time_of_use_tiered' ? { method: 'tier_period_matrix' } : null
    })
  }

  function changeThresholdBasis(basis: RatePlanDraft['billing_cycle']['threshold']['basis']) {
    update((next) => {
      next.billing_cycle.threshold.basis = basis
      next.billing_cycle.threshold.daily_baseline_kwh = basis === 'daily_baseline_kwh' ? (next.billing_cycle.threshold.daily_baseline_kwh ?? '19.3') : null
      normalizeTiers(next)
    })
  }

  function changeAdjustment(index: number, change: (item: RateAdjustmentDraft) => void) {
    update((next) => {
      const item = next.adjustments[index]
      if (!item) throw new Error('The selected adjustment no longer exists.')
      change(item)
    })
  }
}

function normalizeTiers(draft: RatePlanDraft) {
  draft.tiers.forEach((item, index) => {
    item.order = index
    const previous = draft.tiers[index - 1]
    if (draft.billing_cycle.threshold.basis === 'fixed_cycle_kwh') {
      item.lower_bound_inclusive_kwh = index === 0 ? '0' : (previous?.upper_bound_exclusive_kwh ?? item.lower_bound_inclusive_kwh)
      item.lower_bound_multiplier = null
      item.upper_bound_multiplier = null
    } else {
      item.lower_bound_multiplier = index === 0 ? '0' : (previous?.upper_bound_multiplier ?? item.lower_bound_multiplier)
      item.upper_bound_exclusive_kwh = null
    }
    if (index === draft.tiers.length - 1) {
      item.upper_bound_exclusive_kwh = null
      item.upper_bound_multiplier = null
    }
  })
}

function required<T>(items: T[], index: number): T {
  const item = items[index]
  if (!item) throw new Error('The selected editor row no longer exists.')
  return item
}

function selectedPeriod(draft: RatePlanDraft, seasonIndex: number, scheduleIndex: number, periodIndex: number) {
  return required(required(required(draft.seasons, seasonIndex).schedules, scheduleIndex).periods, periodIndex)
}

function EditorPanel({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <section className="rate-editor-panel"><header><h4>{title}</h4><p>{description}</p></header>{children}</section>
}

function Field({ label, wide = false, children }: { label: string; wide?: boolean; children: ReactNode }) {
  return <label className={wide ? 'wide' : undefined}><span>{label}</span>{children}</label>
}

function DecimalInput({ label, value, disabled = false, onChange }: { label: string; value: string; disabled?: boolean; onChange: (value: string) => void }) {
  return <input aria-label={label} inputMode="decimal" disabled={disabled} value={value} onChange={(event) => { onChange(event.target.value) }} />
}

function MetricValue({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>
}

const adjustmentComponents: RateAdjustmentDraft['component'][] = [
  'daily_fixed_charge',
  'monthly_fixed_charge',
  'minimum_charge',
  'baseline_credit',
  'percentage_tax',
  'fixed_tax',
  'generation_provider',
  'cca',
  'direct_access',
  'manual_credit',
  'other',
]
