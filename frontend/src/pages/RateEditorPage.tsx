import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Copy,
  Plus,
  Save,
  Trash2,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { ErrorState, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'
import {
  emptyRateDocument,
  type DayScheduleDocument,
  type ManagedRateVersion,
  type RateAdjustmentDocument,
  type RatePeriodDocument,
  type RatePlanDocument,
  type RateSeasonDocument,
  type ValidationReport,
} from '../rates'

interface VersionResponse { version: ManagedRateVersion; document: RatePlanDocument }
interface Account { id: string; name: string }

const stepNames = [
  'Plan details',
  'Seasons & schedules',
  'Charges & adjustments',
  'Validate & preview',
]

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

export function RateEditorPage({ canManage }: { canManage: boolean }) {
  const { planId, versionId } = useParams()
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [document, setDocument] = useState<RatePlanDocument>(emptyRateDocument)
  const [validation, setValidation] = useState<ValidationReport>()
  const [saved, setSaved] = useState<{ planId: string; versionId: string } | undefined>(
    planId && versionId ? { planId, versionId } : undefined,
  )
  const [sampleCost, setSampleCost] = useState<string>()
  const activationDialog = useRef<HTMLDialogElement>(null)
  const query = useQuery({
    queryKey: ['rate-version', versionId],
    queryFn: () => api<VersionResponse>(`/api/v1/rates/versions/${versionId}`),
    enabled: Boolean(versionId),
  })
  const accounts = useQuery({
    queryKey: ['utility-accounts'],
    queryFn: () => api<Account[]>('/api/v1/utility-accounts'),
    enabled: canManage && step === 3,
  })
  const editable = canManage && (!query.data || query.data.version.status === 'draft')

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
      void navigate(`/rates/${result.planId}/versions/${result.versionId}`, { replace: true })
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
    mutationFn: () => api<{ display_total: string }>('/api/v1/rates/preview-cost', {
      method: 'POST',
      body: JSON.stringify({
        document,
        interval_start: `${document.effective_from}T00:00:00-07:00`,
        interval_end: `${document.effective_from}T01:00:00-07:00`,
        energy_kwh: '1.000000',
        cost_scope: document.cost_scope_default,
      }),
    }),
    onSuccess: (result) => { setSampleCost(result.display_total); },
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
        actions={<button className="button secondary" onClick={() => navigate('/rates')}><ArrowLeft size={16} /> Rate plans</button>}
      />
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
        </div>
      )}

      {step === 2 && (
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

      {step === 3 && (
        <div className="validation-layout">
          <Panel title="Validation" eyebrow="Blocking checks and warnings" actions={canManage && <button className="button secondary" disabled={validate.isPending} onClick={() => { validate.mutate(); }}><CheckCircle2 size={15} /> Validate</button>}>
            {!validation ? <p className="panel-copy">Run validation to check annual and 24-hour coverage, exact decimals, provider assumptions, and account-charge scope.</p> : <>
              <div className="validation-summary"><StatusPill status={validation.valid ? 'validated' : 'failed'} label={validation.valid ? 'Ready to activate' : `${validation.errors.length} blocking errors`} /><code>{validation.integrity_sha256}</code></div>
              {[...validation.errors, ...validation.warnings].map((issue, index) => <div className={`validation-issue ${issue.level}`} key={`${issue.code}-${index}`}><AlertTriangle size={16} /><p><strong>{issue.message}</strong><small>{issue.path} · {issue.code}</small></p></div>)}
            </>}
          </Panel>
          <Panel title="Coverage and sample" eyebrow="Normalized preview">
            <div className="coverage-list">{coverage.map((item) => <div key={item.key}><span>{item.key}</span><strong className={item.valid ? 'good-text' : 'danger-text'}>{item.valid ? 'Complete' : `${item.minutes}/1,440 min · invalid`}</strong></div>)}</div>
            <button className="button secondary" disabled={preview.isPending} onClick={() => { preview.mutate(); }}>Preview 1 kWh sample</button>
            {sampleCost && <p className="sample-cost"><span>Estimated sample cost</span><strong>${sampleCost}</strong></p>}
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
          {step < 3 ? <button className="button primary" onClick={() => { setStep((value) => value + 1); }}>Next <ArrowRight size={15} /></button> : editable && saved && <button className="button primary" disabled={!validation?.valid || activate.isPending} onClick={() => { activationDialog.current?.showModal() }}><CheckCircle2 size={15} /> Activate</button>}
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
