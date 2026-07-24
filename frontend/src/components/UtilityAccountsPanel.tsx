import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, CalendarClock, ChevronLeft, ChevronRight, CircleDollarSign, Pencil, Plus, RefreshCw, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { CanonicalAction } from '../actions'
import { api, ApiError } from '../api'
import { formatBillingPeriod, formatCurrency, formatEnergy, formatEnergyRate } from '../formatters'
import type { ManagedRatePlan, ManagedRateVersion } from '../rates'
import type { AggregateSet, Device, Site, TierStatus, UtilityAccount } from '../types'
import { EmptyState, ErrorState, LoadingState, Panel, StatusPill, formatTime } from './UI'

type CostScope = UtilityAccount['cost_scope']
type AdjustmentComponent = 'cca_generation' | 'direct_access' | 'baseline_credit' | 'service_charge' | 'tax_fee' | 'custom_fixed' | 'custom_per_kwh'
interface WizardAdjustment { component: AdjustmentComponent; value: string; unit: 'per_kwh' | 'fixed' | 'percent'; provenance: string; effectiveFrom: string; effectiveTo: string }
interface VersionContext { current_period?: string; current_price_per_kwh?: string; next_period?: string; next_price_per_kwh?: string; next_period_at?: string; provider_mode: string; account_adjustments: Array<{ name: string; component: string; value: string; unit: string; scope: string }> }
interface SetupReadiness { monitoring: { state: string; device_count: number; latest_signed_heartbeat_at?: string }; rate_and_cost: { state: string; cost_state: string; account_count: number; effective_account_count: number; cost_ready_account_count: number; pending_candidate_count: number } }
interface UtilityBillSummary {
  id: string
  utility_account_id: string
  utility_account_name: string
  status: string
  source_role: string
  extraction_method: string
  created_at: string
  billing_cycle?: {
    starts_at?: string
    ends_at?: string
    total_usage_kwh?: string
    estimated_total?: string
    status: string
    billing_cycle_id?: string
  }
}
const isTierStatus = (value: unknown): value is TierStatus => (
  typeof value === 'object'
  && value !== null
  && !Array.isArray(value)
  && 'cycle' in value
)
interface WizardState {
  siteId: string
  name: string
  nickname: string
  suffix: string
  utilityProvider: 'sce' | 'cca' | 'direct_access' | 'custom'
  generationProvider: 'sce' | 'cca' | 'direct_access' | 'custom'
  providerMode: string
  billingDay: string
  serviceClass: string
  versionId: string
  effectiveFrom: string
  effectiveTo: string
  reason: string
  costScope: CostScope
  allocationMethod: string
  fullOverride: boolean
  adjustments: WizardAdjustment[]
}

const todayInput = () => {
  const now = new Date()
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset())
  return now.toISOString().slice(0, 16)
}

const initialWizard = (siteId = ''): WizardState => ({
  siteId,
  name: '',
  nickname: '',
  suffix: '',
  utilityProvider: 'sce',
  generationProvider: 'sce',
  providerMode: 'sce_bundled',
  billingDay: '1',
  serviceClass: 'Residential',
  versionId: '',
  effectiveFrom: todayInput(),
  effectiveTo: '',
  reason: 'Initial account setup',
  costScope: 'energy_only',
  allocationMethod: '',
  fullOverride: false,
  adjustments: [],
})

const steps = ['Utility Setup', 'Utility & provider', 'Billing details', 'Rate & version', 'Cost scope', 'Adjustments', 'Review & create']

function approvedVersions(plans: ManagedRatePlan[]) {
  return plans.flatMap((plan) => plan.versions
    .filter((version) => ['active', 'approved'].includes(version.status))
    .map((version) => ({ plan, version })))
}

function dateWithOffset(value: string) {
  return new Date(value).toISOString()
}

function readableScope(scope: CostScope) {
  if (scope === 'energy_only') return 'Energy-only monitored scope'
  if (scope === 'allocated_account_estimate') return 'Allocated account estimate'
  return 'Complete utility-account estimate'
}

export function UtilityAccountsPanel({
  sites,
  initialRateVersionId,
  initialSiteId,
  openCreate = false,
  canViewBills = false,
}: {
  sites: Site[]
  initialRateVersionId?: string
  initialSiteId?: string
  openCreate?: boolean
  canViewBills?: boolean
}) {
  const queryClient = useQueryClient()
  const initialSite = sites.some((site) => site.id === initialSiteId) ? initialSiteId : sites[0]?.id
  const [selectedSiteId, setSelectedSiteId] = useState(initialSite ?? '')
  const [wizard, setWizard] = useState<WizardState>()
  const [step, setStep] = useState(0)
  const [success, setSuccess] = useState('')
  const [expanded, setExpanded] = useState<string>()
  const openedFromRoute = useRef(false)
  const siteId = selectedSiteId || sites[0]?.id || ''
  const accounts = useQuery({
    queryKey: ['utility-accounts', siteId],
    queryFn: () => api<UtilityAccount[]>(`/api/v1/admin/sites/${siteId}/utility-accounts`),
    enabled: Boolean(siteId),
  })
  const readiness = useQuery({ queryKey: ['setup-readiness', siteId], queryFn: () => api<SetupReadiness>(`/api/v1/sites/${siteId}/setup-readiness`), enabled: Boolean(siteId) })
  const rates = useQuery({ queryKey: ['managed-rates'], queryFn: () => api<ManagedRatePlan[]>('/api/v1/rates/plans') })
  const bills = useQuery({
    queryKey: ['utility-bill-imports', 'billing-overview'],
    queryFn: () => api<UtilityBillSummary[]>('/api/v1/admin/utility-bill-imports'),
    enabled: canViewBills,
  })
  const versions = useMemo(() => approvedVersions(rates.data ?? []), [rates.data])
  const selectedVersion = versions.find((item) => item.version.id === wizard?.versionId)
  const selectedContext = useQuery({ queryKey: ['rate-version-current-context', wizard?.versionId], queryFn: () => api<VersionContext>(`/api/v1/rates/versions/${wizard?.versionId}/current-context`), enabled: Boolean(wizard?.versionId && step === 3) })

  const create = useMutation({
    mutationFn: (value: WizardState) => api<UtilityAccount>(`/api/v1/admin/sites/${value.siteId}/utility-accounts`, {
      method: 'POST',
      body: JSON.stringify({
        name: value.name,
        nickname: value.nickname || null,
        account_number_suffix: value.suffix || null,
        utility_provider: value.utilityProvider,
        generation_provider: value.generationProvider,
        provider_mode: value.providerMode,
        billing_cycle_start_day: Number(value.billingDay),
        currency: 'USD',
        service_class: value.serviceClass || null,
        rate_assignment: {
          rate_version_id: value.versionId,
          effective_from: dateWithOffset(value.effectiveFrom),
          effective_to: value.effectiveTo ? dateWithOffset(value.effectiveTo) : null,
          assignment_reason: value.reason || null,
        },
        cost_scope: value.costScope,
        allocation_method: value.allocationMethod || null,
        full_account_override: value.fullOverride,
        adjustments: value.adjustments.map((item) => ({
          component: item.component, value: item.value, unit: item.unit,
          provenance: item.provenance, effective_from: dateWithOffset(item.effectiveFrom),
          effective_to: item.effectiveTo ? dateWithOffset(item.effectiveTo) : null, enabled: true,
        })),
        confirmation: true,
      }),
    }),
    onSuccess: async (account) => {
      setSuccess(account.rate_context.state === 'rate_configured_effective'
        ? 'Utility account created and rate assignment activated.'
        : `Utility account created. The selected rate becomes effective on ${formatTime(account.rate_context.next_assignment?.effective_from)}.`)
      setWizard(undefined)
      setStep(0)
      await queryClient.invalidateQueries({ queryKey: ['utility-accounts'] })
      await queryClient.invalidateQueries({ queryKey: ['setup-readiness'] })
      await queryClient.invalidateQueries({ queryKey: ['status-indicator-values'] })
    },
  })
  const archive = useMutation({
    mutationFn: (accountId: string) => api<UtilityAccount>(`/api/v1/admin/utility-accounts/${accountId}/archive`, { method: 'POST' }),
    onSuccess: async () => {
      setSuccess('Utility account archived. Historical assignments and costs were preserved.')
      await queryClient.invalidateQueries({ queryKey: ['utility-accounts'] })
    },
  })
  const recalculate = useMutation({
    mutationFn: (accountId: string) => api<{ queued_runs: number }>(`/api/v1/admin/utility-accounts/${accountId}/recalculate`, { method: 'POST' }),
    onSuccess: (result) => { setSuccess(`Cost recalculation queued for ${result.queued_runs} unfinalized run${result.queued_runs === 1 ? '' : 's'}.`); },
  })

  const openWizard = useCallback(() => {
    const value = initialWizard(siteId)
    value.versionId = versions.some((item) => item.version.id === initialRateVersionId)
      ? initialRateVersionId ?? ''
      : versions[0]?.version.id ?? ''
    setWizard(value)
    setStep(0)
    setSuccess('')
    create.reset()
  }, [create, initialRateVersionId, siteId, versions])

  useEffect(() => {
    if (!openCreate || openedFromRoute.current || !siteId || rates.isLoading) return
    openedFromRoute.current = true
    openWizard()
  }, [openCreate, openWizard, rates.isLoading, siteId])

  function validStep(value: WizardState) {
    if (step === 0) return Boolean(value.siteId && value.name.trim())
    if (step === 2) return Number(value.billingDay) >= 1 && Number(value.billingDay) <= 31
    if (step === 3) return Boolean(value.versionId && value.effectiveFrom)
    if (step === 4) return (value.costScope !== 'allocated_account_estimate' || Boolean(value.allocationMethod.trim())) && (value.costScope !== 'full_account_estimate' || value.fullOverride)
    if (step === 5) return value.adjustments.every((item) => Boolean(item.value && item.provenance.trim() && item.effectiveFrom))
    return true
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!wizard) return
    if (step < steps.length - 1) setStep((current) => current + 1)
    else create.mutate(wizard)
  }

  const siteAccounts = accounts.data ?? []
  const effectiveAccounts = siteAccounts.filter(
    (account) => account.rate_context.state === 'rate_configured_effective',
  )
  const rateNames = [...new Set(effectiveAccounts.map((account) => account.rate_context.current_plan).filter(Boolean))]
  const prices = [...new Set(effectiveAccounts.map((account) => account.rate_context.current_price_per_kwh).filter(Boolean))]
  const recentBills = (bills.data ?? [])
    .filter((bill) => siteAccounts.some((account) => account.id === bill.utility_account_id))
    .slice(0, 8)

  return <>
    <Panel
      title="Utility accounts"
      eyebrow="Rate and cost setup"
      actions={(
        <CanonicalAction id="utility_account.create" surface="panel_header">
          <button className="button primary" onClick={openWizard}><Plus size={16} /> Create utility account</button>
        </CanonicalAction>
      )}
    >
      <div className="account-toolbar">
        <label><span>Physical site</span><select value={siteId} onChange={(event) => { setSelectedSiteId(event.target.value); }}>{sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}</select></label>
        <p>Sensors measure energy. Utility accounts independently determine the rate period and cost.</p>
      </div>
      {!accounts.isLoading && !accounts.error && (
        <section className="billing-summary-grid" aria-label="Billing summary">
          <article><span>Active Utility Accounts</span><strong>{siteAccounts.length}</strong><small>{siteAccounts.length ? `for ${sites.find((site) => site.id === siteId)?.name ?? 'selected site'}` : 'No accounts configured'}</small></article>
          <article><span>Current Rate</span><strong>{rateNames.length === 1 ? rateNames[0] : rateNames.length > 1 ? 'Multiple configured plans' : 'Not configured'}</strong><small>{effectiveAccounts.length ? `${effectiveAccounts.length} effective account${effectiveAccounts.length === 1 ? '' : 's'}` : 'Assign an effective rate version'}</small></article>
          <article><span>Current Energy Price</span><strong>{prices.length === 1 ? formatEnergyRate(prices[0], { currency: effectiveAccounts[0]?.currency }) : prices.length > 1 ? 'Varies by account' : 'Unavailable'}</strong><small>{prices.length ? 'Evaluated at the current local time' : 'No effective price context'}</small></article>
        </section>
      )}
      {readiness.data && <div className="setup-readiness-grid"><article><span>Monitoring setup</span><strong>{readiness.data.monitoring.state.replaceAll('_', ' ')}</strong><small>{readiness.data.monitoring.device_count} enrolled sensor{readiness.data.monitoring.device_count === 1 ? '' : 's'}{readiness.data.monitoring.latest_signed_heartbeat_at ? ` · last signed heartbeat ${formatTime(readiness.data.monitoring.latest_signed_heartbeat_at)}` : ''}</small></article><article><span>Rate setup</span><strong>{readiness.data.rate_and_cost.state.replaceAll('_', ' ')}</strong><small>{readiness.data.rate_and_cost.effective_account_count} of {readiness.data.rate_and_cost.account_count} accounts currently effective{readiness.data.rate_and_cost.pending_candidate_count ? ` · ${readiness.data.rate_and_cost.pending_candidate_count} candidate awaiting approval` : ''}</small></article><article><span>Cost setup</span><strong>{readiness.data.rate_and_cost.cost_state.replaceAll('_', ' ')}</strong><small>{readiness.data.rate_and_cost.cost_ready_account_count} account{readiness.data.rate_and_cost.cost_ready_account_count === 1 ? '' : 's'} ready for calculation</small></article></div>}
      {success && <p className="form-success" role="status">{success}</p>}
      {accounts.isLoading ? <LoadingState /> : accounts.error ? <ErrorState error={accounts.error} /> : accounts.data?.length ? <div className="utility-account-list">
        {accounts.data.map((account) => <article className="utility-account-card" key={account.id}>
          <header>
            <div><span className="plan-code">{account.utility_name}</span><h3>{account.name}</h3><p>{account.site_name} · Billing day {account.billing_cycle_start_day} · {account.generation_provider.replaceAll('_', ' ')}</p></div>
            <StatusPill status={account.readiness.cost === 'cost_calculation_ready' ? 'healthy' : account.rate_context.state === 'rate_configured_effective' ? 'pending' : 'failed'} label={account.readiness.cost === 'cost_calculation_ready' ? 'Cost calculation ready' : account.rate_context.state === 'rate_configured_effective' ? 'Rate ready · readings needed' : 'Rate setup required'} />
          </header>
          <dl className="account-readiness-grid">
            <div><dt>Rate plan</dt><dd>{account.rate_context.current_plan ?? 'Not assigned'}</dd><small>{account.rate_context.current_version ? `Version ${account.rate_context.current_version}` : 'Choose a published version'}</small></div>
            <div><dt>Current period</dt><dd>{account.rate_context.current_period ?? 'Unavailable'}</dd><small>{account.rate_context.assignment_effective_from ? `Effective ${formatTime(account.rate_context.assignment_effective_from)}` : 'No current assignment'}</small></div>
            <div><dt>Current energy price</dt><dd>{formatEnergyRate(account.rate_context.current_price_per_kwh, { currency: account.rate_context.current_currency })}</dd><small>{account.rate_context.next_period ? `Next: ${account.rate_context.next_period} at ${formatEnergyRate(account.rate_context.next_price_per_kwh, { currency: account.rate_context.current_currency })}` : 'Rate context does not require a sensor'}</small></div>
            <div><dt>Cost scope</dt><dd>{readableScope(account.cost_scope)}</dd><small>{account.device_count} assigned sensor{account.device_count === 1 ? '' : 's'}</small></div>
          </dl>
          {account.rate_context.next_assignment && <p className="inline-notice"><CalendarClock size={15} /> Next: {account.rate_context.next_assignment.plan} on {formatTime(account.rate_context.next_assignment.effective_from)}</p>}
          <footer className="account-actions">
            <button className="button ghost" onClick={() => { setExpanded(expanded === account.id ? undefined : account.id); }}>View {expanded === account.id ? 'less' : 'details'}</button>
            <CanonicalAction id="utility_account.manage" surface="resource_row" resourceKey={account.id}>
              <button className="button ghost" onClick={() => { setExpanded(account.id); }}><Pencil size={14} /> Manage</button>
            </CanonicalAction>
            <CanonicalAction id="utility_account.recalculate" surface="resource_row" resourceKey={account.id}>
              <button className="button ghost" disabled={recalculate.isPending} onClick={() => { recalculate.mutate(account.id); }}><RefreshCw size={14} /> Recalculate costs</button>
            </CanonicalAction>
            <button className="button ghost danger-text" disabled={archive.isPending} onClick={() => { if (window.confirm(`Archive ${account.name}? Historical assignments and costs will remain.`)) archive.mutate(account.id) }}><Archive size={14} /> Archive</button>
          </footer>
          {expanded === account.id && <>
            <AccountManager account={account} versions={versions} onSaved={async (message) => { setSuccess(message); await queryClient.invalidateQueries({ queryKey: ['utility-accounts'] }) }} />
            <UsageAuthorityManager account={account} onSaved={async (message) => { setSuccess(message); await queryClient.invalidateQueries({ queryKey: ['utility-accounts'] }) }} />
          </>}
        </article>)}
      </div> : <EmptyState title="No utility account configured" message="Use Create utility account above to assign a rate plan, determine the current time-of-use period, and calculate energy costs for this site." />}
      {canViewBills && (
        <section className="recent-bills">
          <header><div><span className="plan-code">Reviewed account evidence</span><h3>Recent bills and statements</h3></div></header>
          {bills.isLoading ? <LoadingState label="Loading recent statements…" />
            : bills.error ? <ErrorState error={bills.error} />
              : recentBills.length ? (
                <div className="responsive-table">
                  <table>
                    <thead><tr><th>Bill period</th><th>Account</th><th>Usage</th><th>Estimated total</th><th>Status</th><th>Imported on</th></tr></thead>
                    <tbody>{recentBills.map((bill) => (
                      <tr key={bill.id}>
                        <td>{formatBillingPeriod(bill.billing_cycle?.starts_at, bill.billing_cycle?.ends_at)}</td>
                        <td>{bill.utility_account_name}</td>
                        <td>{formatEnergy(bill.billing_cycle?.total_usage_kwh)}</td>
                        <td>{formatCurrency(bill.billing_cycle?.estimated_total, { currency: siteAccounts.find((account) => account.id === bill.utility_account_id)?.currency })}</td>
                        <td><StatusPill status={bill.billing_cycle?.status ?? bill.status} /></td>
                        <td>{formatTime(bill.created_at)}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              ) : <p className="field-help">No reviewed bill or statement evidence is attached to this site yet.</p>}
        </section>
      )}
    </Panel>

    {wizard && <div className="modal-backdrop modal-top" onMouseDown={(event) => { if (event.currentTarget === event.target) setWizard(undefined) }}>
      <section className="account-wizard" role="dialog" aria-modal="true" aria-labelledby="account-wizard-title">
        <header><div><span className="plan-code">Step {step + 1} of {steps.length}</span><h2 id="account-wizard-title">{steps[step]}</h2></div><button className="icon-button" onClick={() => { setWizard(undefined); }} aria-label="Close utility account setup"><X /></button></header>
        <ol className="wizard-steps" aria-label="Utility account setup progress">{steps.map((label, index) => <li className={index === step ? 'active' : index < step ? 'complete' : ''} aria-current={index === step ? 'step' : undefined} key={label}><span aria-hidden="true">{index + 1}</span><span className="wizard-step-label">{label}</span></li>)}</ol>
        <form onSubmit={submit}>
          {step === 0 && <div className="stack-form"><label><span>Site</span><select value={wizard.siteId} onChange={(event) => { setWizard({ ...wizard, siteId: event.target.value }); }}>{sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}</select></label><label><span>Account display name</span><input required autoFocus value={wizard.name} onChange={(event) => { setWizard({ ...wizard, name: event.target.value }); }} placeholder="Main electric account" /></label><div className="form-columns"><label><span>Nickname (optional)</span><input value={wizard.nickname} onChange={(event) => { setWizard({ ...wizard, nickname: event.target.value }); }} /></label><label><span>Masked account suffix (optional)</span><input maxLength={8} value={wizard.suffix} onChange={(event) => { setWizard({ ...wizard, suffix: event.target.value }); }} placeholder="1234" /></label></div><label><span>Account status</span><input value="Active" readOnly /></label><p className="field-help">The full utility account number is not stored.</p></div>}
          {step === 1 && <div className="stack-form"><label><span>Utility provider</span><select value={wizard.utilityProvider} onChange={(event) => { const provider = event.target.value as WizardState['utilityProvider']; setWizard({ ...wizard, utilityProvider: provider, generationProvider: provider, providerMode: provider === 'sce' ? 'sce_bundled' : provider === 'cca' ? 'sce_delivery_cca' : provider === 'direct_access' ? 'sce_delivery_direct_access' : 'custom_combined' }) }}><option value="sce">Southern California Edison</option><option value="cca">Community Choice Aggregator</option><option value="direct_access">Direct Access</option><option value="custom">Custom/manual provider</option></select></label><label><span>Generation provider mode</span><select value={wizard.providerMode} onChange={(event) => { setWizard({ ...wizard, providerMode: event.target.value }); }}><option value="sce_bundled">SCE bundled generation and delivery</option><option value="sce_delivery_generation">SCE delivery and generation</option><option value="sce_delivery_cca">SCE delivery with CCA generation</option><option value="sce_delivery_direct_access">SCE delivery with Direct Access generation</option><option value="custom_combined">Custom combined service</option></select></label><p className="scope-warning">CCA and Direct Access generation prices can differ from SCE bundled-service prices. Confirm the provider shown on the bill.</p></div>}
          {step === 2 && <div className="stack-form"><label><span>Timezone</span><input value={sites.find((site) => site.id === wizard.siteId)?.timezone ?? ''} readOnly /></label><div className="form-columns"><label><span>Billing-cycle start day</span><input type="number" min="1" max="31" value={wizard.billingDay} onChange={(event) => { setWizard({ ...wizard, billingDay: event.target.value }); }} /></label><label><span>Currency</span><input value="USD" readOnly /></label></div><label><span>Service class (optional)</span><input value={wizard.serviceClass} onChange={(event) => { setWizard({ ...wizard, serviceClass: event.target.value }); }} /></label><p className="field-help">For shorter months, billing-cycle boundaries use the last valid day of the month.</p></div>}
          {step === 3 && <div className="stack-form"><label><span>Published rate plan and version</span><select value={wizard.versionId} onChange={(event) => { setWizard({ ...wizard, versionId: event.target.value }); }}><option value="">Choose a published rate version</option>{versions.map(({ plan, version }) => <option key={version.id} value={version.id}>{plan.code} · {plan.name} · v{version.version} · effective {version.effective_from}</option>)}</select></label>{selectedVersion && <RateEvidence plan={selectedVersion.plan} version={selectedVersion.version} context={selectedContext.data} />}<div className="form-columns"><label><span>Assignment effective from</span><input type="datetime-local" value={wizard.effectiveFrom} onChange={(event) => { setWizard({ ...wizard, effectiveFrom: event.target.value }); }} /></label><label><span>Effective through (optional)</span><input type="datetime-local" value={wizard.effectiveTo} onChange={(event) => { setWizard({ ...wizard, effectiveTo: event.target.value }); }} /></label></div><label><span>Assignment reason</span><input value={wizard.reason} onChange={(event) => { setWizard({ ...wizard, reason: event.target.value }); }} /></label></div>}
          {step === 4 && <fieldset className="scope-options"><legend>Choose cost scope</legend>{(['energy_only', 'allocated_account_estimate', 'full_account_estimate'] as CostScope[]).map((scope) => <label className={wizard.costScope === scope ? 'selected' : ''} key={scope}><input type="radio" name="cost-scope" value={scope} checked={wizard.costScope === scope} onChange={() => { setWizard({ ...wizard, costScope: scope }); }} /><span><strong>{readableScope(scope)}</strong><small>{scope === 'energy_only' ? 'Recommended for one-CT, branch-circuit, and partial-site monitoring. Fixed account charges and baseline credits are excluded.' : scope === 'allocated_account_estimate' ? 'Allocates selected account charges using a documented method.' : 'Includes account-level charges only when topology represents the complete service.'}</small></span></label>)}{wizard.costScope === 'allocated_account_estimate' && <label><span>Allocation method</span><input value={wizard.allocationMethod} onChange={(event) => { setWizard({ ...wizard, allocationMethod: event.target.value }); }} placeholder="Proportional to monitored kWh" /></label>}{wizard.costScope === 'full_account_estimate' && <label className="confirm-check"><input type="checkbox" checked={wizard.fullOverride} onChange={(event) => { setWizard({ ...wizard, fullOverride: event.target.checked }); }} /><span>I confirm the configured topology represents the complete utility account, or I accept the coverage warning.</span></label>}</fieldset>}
          {step === 5 && <div className="stack-form"><p>Values in the selected rate version are not duplicated. Add only separately sourced provider or account adjustments, each with provenance and effective dates.</p>{wizard.adjustments.map((item, index) => <section className="wizard-adjustment" key={index}><div className="form-columns"><label><span>Adjustment type</span><select value={item.component} onChange={(event) => { const component = event.target.value as AdjustmentComponent; const unit = component === 'tax_fee' ? 'percent' : ['service_charge', 'custom_fixed'].includes(component) ? 'fixed' : 'per_kwh'; setWizard({ ...wizard, adjustments: wizard.adjustments.map((entry, row) => row === index ? { ...entry, component, unit } : entry) }); }}><option value="cca_generation">CCA generation adjustment</option><option value="direct_access">Direct Access adjustment</option><option value="baseline_credit">Baseline credit</option><option value="service_charge">Service charge</option><option value="tax_fee">Tax or fee</option><option value="custom_fixed">Custom fixed adjustment</option><option value="custom_per_kwh">Custom per-kWh adjustment</option></select></label><label><span>Exact value</span><input type="number" step="0.00000001" value={item.value} onChange={(event) => { setWizard({ ...wizard, adjustments: wizard.adjustments.map((entry, row) => row === index ? { ...entry, value: event.target.value } : entry) }); }} /></label></div><label><span>Source / provenance</span><input value={item.provenance} onChange={(event) => { setWizard({ ...wizard, adjustments: wizard.adjustments.map((entry, row) => row === index ? { ...entry, provenance: event.target.value } : entry) }); }} /></label><div className="form-columns"><label><span>Effective from</span><input type="datetime-local" value={item.effectiveFrom} onChange={(event) => { setWizard({ ...wizard, adjustments: wizard.adjustments.map((entry, row) => row === index ? { ...entry, effectiveFrom: event.target.value } : entry) }); }} /></label><label><span>Effective through (optional)</span><input type="datetime-local" value={item.effectiveTo} onChange={(event) => { setWizard({ ...wizard, adjustments: wizard.adjustments.map((entry, row) => row === index ? { ...entry, effectiveTo: event.target.value } : entry) }); }} /></label></div><footer><span>{item.unit.replaceAll('_', ' ')}</span><button type="button" className="button ghost danger-text" onClick={() => { setWizard({ ...wizard, adjustments: wizard.adjustments.filter((_, row) => row !== index) }); }}>Remove adjustment</button></footer></section>)}<button type="button" className="button secondary" onClick={() => { setWizard({ ...wizard, adjustments: [...wizard.adjustments, { component: 'custom_per_kwh', value: '', unit: 'per_kwh', provenance: 'Administrator configured', effectiveFrom: wizard.effectiveFrom, effectiveTo: '' }] }); }}><Plus size={15} /> Add adjustment or credit</button><p className="field-help">Energy-only scope excludes fixed account charges and credits. Provider and per-kWh energy adjustments remain explicit.</p></div>}
          {step === 6 && <div className="review-grid"><Review label="Site" value={sites.find((site) => site.id === wizard.siteId)?.name} /><Review label="Account" value={wizard.name} /><Review label="Utility / generation" value={`${wizard.utilityProvider} / ${wizard.generationProvider}`} /><Review label="Billing" value={`Day ${wizard.billingDay} · ${sites.find((site) => site.id === wizard.siteId)?.timezone} · USD`} /><Review label="Rate" value={selectedVersion ? `${selectedVersion.plan.code} · v${selectedVersion.version.version}` : 'Not selected'} /><Review label="Effective" value={formatTime(dateWithOffset(wizard.effectiveFrom))} /><Review label="Cost scope" value={readableScope(wizard.costScope)} /><Review label="Adjustments" value={`${wizard.adjustments.length} separately sourced`} /><Review label="Account charges" value={wizard.costScope === 'energy_only' ? 'Excluded' : 'Subject to configured scope and coverage'} /><Review label="Readiness" value="Rate context ready after creation; costs wait for assigned sensor readings" /></div>}
          {create.error && <WizardError error={create.error} />}
          <footer><button type="button" className="button secondary" disabled={step === 0 || create.isPending} onClick={() => { setStep((current) => current - 1); }}><ChevronLeft size={16} /> Previous</button><button className="button primary" disabled={!validStep(wizard) || create.isPending}>{step === steps.length - 1 ? create.isPending ? 'Creating…' : 'Confirm and create' : <>Next <ChevronRight size={16} /></>}</button></footer>
        </form>
      </section>
    </div>}
  </>
}

function RateEvidence({ plan, version, context }: { plan: ManagedRatePlan; version: ManagedRateVersion; context?: VersionContext }) {
  const currentContext = context?.current_price_per_kwh
    ? `${context.current_period} ${formatEnergyRate(context.current_price_per_kwh)}${context.next_period && context.next_price_per_kwh ? ` · next ${context.next_period} ${formatEnergyRate(context.next_price_per_kwh)}` : ''}`
    : context?.current_period ?? 'Loading server rate context…'
  return <dl className="rate-evidence"><div><dt>Plan</dt><dd>{plan.code} · {plan.name}</dd></div><div><dt>Library state</dt><dd>Published · Available</dd></div><div><dt>Source</dt><dd>{version.source_kind} · checked {version.source_checked_at?.slice(0, 10) ?? 'manually'}</dd></div><div><dt>Version dates</dt><dd>{version.effective_from}{version.effective_through ? ` through ${version.effective_through}` : ''}</dd></div><div><dt>Current rate context</dt><dd>{currentContext}</dd></div><div><dt>Provider assumption</dt><dd>{context?.provider_mode.replaceAll('_', ' ') ?? 'Loading…'}</dd></div><div><dt>Account charges / credits</dt><dd>{context?.account_adjustments.length ? context.account_adjustments.map((item) => `${item.name} (${item.value} ${item.unit})`).join(', ') : 'None in this rate version'}</dd></div></dl>
}

function Review({ label, value }: { label: string; value?: string }) {
  return <div><dt>{label}</dt><dd>{value || 'Not provided'}</dd></div>
}

function WizardError({ error }: { error: Error }) {
  const problem = error instanceof ApiError ? error.problem : undefined
  return <div className="form-error" role="alert"><strong>{problem?.title ?? 'Account was not created'}</strong><span>{problem?.detail ?? error.message}</span></div>
}

function AccountManager({ account, versions, onSaved }: { account: UtilityAccount; versions: Array<{ plan: ManagedRatePlan; version: ManagedRateVersion }>; onSaved: (message: string) => Promise<void> }) {
  const [name, setName] = useState(account.name)
  const [billingDay, setBillingDay] = useState(String(account.billing_cycle_start_day))
  const [versionId, setVersionId] = useState(versions[0]?.version.id ?? '')
  const [effectiveFrom, setEffectiveFrom] = useState(todayInput())
  const [scope, setScope] = useState<CostScope>(account.cost_scope)
  const [allocation, setAllocation] = useState(account.allocation_method ?? '')
  const [fullOverride, setFullOverride] = useState(account.full_account_override)
  const [adjustment, setAdjustment] = useState('')
  const [message, setMessage] = useState('')
  const history = useQuery({ queryKey: ['account-rate-history', account.id], queryFn: () => api<Array<{ id: string; plan_code?: string; plan_name?: string; version?: number; effective_from: string; effective_to?: string; assignment_reason?: string }>>(`/api/v1/admin/utility-accounts/${account.id}/rate-assignments`) })
  const adjustments = useQuery({ queryKey: ['account-adjustments', account.id], queryFn: () => api<Array<{ id: string; component: string; value: string; unit: string; provenance: string; effective_from: string; effective_to?: string; enabled: boolean }>>(`/api/v1/admin/utility-accounts/${account.id}/adjustments`) })
  const edit = useMutation({ mutationFn: () => api<UtilityAccount>(`/api/v1/admin/utility-accounts/${account.id}`, { method: 'PUT', body: JSON.stringify({ revision: account.revision, name, billing_cycle_start_day: Number(billingDay) }) }), onSuccess: async () => { setMessage('Utility account updated.'); await onSaved('Utility account updated.') } })
  const assign = useMutation({ mutationFn: () => api(`/api/v1/admin/utility-accounts/${account.id}/rate-assignments`, { method: 'POST', body: JSON.stringify({ rate_version_id: versionId, effective_from: dateWithOffset(effectiveFrom), assignment_reason: 'Administrator rate change' }) }), onSuccess: async () => { setMessage('Rate assignment saved.'); await onSaved('Rate assignment saved.') } })
  const saveScope = useMutation({ mutationFn: () => api<UtilityAccount>(`/api/v1/admin/utility-accounts/${account.id}/cost-scope`, { method: 'POST', body: JSON.stringify({ revision: account.revision, cost_scope: scope, allocation_method: allocation || null, full_account_override: scope === 'full_account_estimate' && fullOverride }) }), onSuccess: async () => { setMessage('Cost scope updated.'); await onSaved('Cost scope updated.') } })
  const addAdjustment = useMutation({ mutationFn: () => api(`/api/v1/admin/utility-accounts/${account.id}/adjustments`, { method: 'POST', body: JSON.stringify({ component: 'custom_per_kwh', value: adjustment, unit: 'per_kwh', provenance: 'Administrator configured', effective_from: new Date().toISOString(), enabled: true }) }), onSuccess: async () => { setAdjustment(''); setMessage('Adjustment added.'); await adjustments.refetch(); await onSaved('Adjustment added.') } })
  return <div className="account-manager">
    <h4>Manage account</h4>{message && <p className="form-success" role="status">{message}</p>}
    <div className="account-manager-grid"><section><h5>Edit account</h5><label><span>Name</span><input value={name} onChange={(event) => { setName(event.target.value); }} /></label><label><span>Billing day</span><input type="number" min="1" max="31" value={billingDay} onChange={(event) => { setBillingDay(event.target.value); }} /></label><button className="button secondary" disabled={edit.isPending} onClick={() => { edit.mutate(); }}>Save account</button></section>
      <section><h5>Change or schedule rate</h5><label><span>Published version</span><select value={versionId} onChange={(event) => { setVersionId(event.target.value); }}>{versions.map(({ plan, version }) => <option key={version.id} value={version.id}>{plan.code} · v{version.version}</option>)}</select></label><label><span>Effective from</span><input type="datetime-local" value={effectiveFrom} onChange={(event) => { setEffectiveFrom(event.target.value); }} /></label><button className="button secondary" disabled={assign.isPending || !versionId} onClick={() => { assign.mutate(); }}>Save rate assignment</button></section>
      <section><h5>Cost scope</h5><label><span>Scope</span><select value={scope} onChange={(event) => { setScope(event.target.value as CostScope); }}><option value="energy_only">Energy-only monitored</option><option value="allocated_account_estimate">Allocated account</option><option value="full_account_estimate">Complete account</option></select></label>{scope === 'allocated_account_estimate' && <label><span>Allocation method</span><input value={allocation} onChange={(event) => { setAllocation(event.target.value); }} /></label>}{scope === 'full_account_estimate' && !account.readiness.topology_complete && <label className="confirm-check"><input type="checkbox" checked={fullOverride} onChange={(event) => { setFullOverride(event.target.checked); }} /><span>I confirm complete account coverage despite the topology warning.</span></label>}<button className="button secondary" disabled={saveScope.isPending || (scope === 'full_account_estimate' && !account.readiness.topology_complete && !fullOverride)} onClick={() => { saveScope.mutate(); }}>Update cost scope</button></section>
      <section><h5>Adjustments</h5><label><span>New custom $/kWh</span><input type="number" step="0.00000001" value={adjustment} onChange={(event) => { setAdjustment(event.target.value); }} /></label><button className="button secondary" disabled={addAdjustment.isPending || !adjustment} onClick={() => { addAdjustment.mutate(); }}><CircleDollarSign size={14} /> Add adjustment</button>{adjustments.isLoading ? <LoadingState /> : adjustments.error ? <ErrorState error={adjustments.error} /> : adjustments.data?.length ? <ul className="adjustment-history">{adjustments.data.map((item) => <li key={item.id}><strong>{item.component.replaceAll('_', ' ')}</strong><span>{item.value} {item.unit.replaceAll('_', ' ')}</span><small>{item.provenance} · effective {formatTime(item.effective_from)}{item.effective_to ? ` through ${formatTime(item.effective_to)}` : ''}</small></li>)}</ul> : <p className="field-help">No account-level adjustments. Rate-version charges remain separate.</p>}</section></div>
    <h5>Immutable assignment history</h5>{history.isLoading ? <LoadingState /> : history.error ? <ErrorState error={history.error} /> : <div className="responsive-table"><table><thead><tr><th>Plan</th><th>Version</th><th>Effective from</th><th>Effective through</th><th>Reason</th></tr></thead><tbody>{history.data?.map((item) => <tr key={item.id}><td>{item.plan_code} · {item.plan_name}</td><td>{item.version}</td><td>{formatTime(item.effective_from)}</td><td>{item.effective_to ? formatTime(item.effective_to) : 'Open'}</td><td>{item.assignment_reason || '—'}</td></tr>)}</tbody></table></div>}
  </div>
}

type AuthorityType =
  | 'complete_site_aggregate'
  | 'service_leg_pair'
  | 'whole_account_meter'
  | 'utility_interval_import'
  | 'manual_cycle_usage'
  | 'external_feed'
  | 'partial_monitored_circuits'

interface Authority {
  configured: boolean
  authority_type?: AuthorityType
  aggregate_set_id?: string
  device_ids: string[]
  source_reference?: string
  confidence: 'unverified' | 'low' | 'medium' | 'high' | 'utility_verified'
  complete_account: boolean
  revision: number
}

type UsageImportKind = 'interval' | 'daily' | 'cycle_cumulative' | 'cycle_dates' | 'bill_total'
type UsageConflictPolicy = 'reject' | 'prefer_utility' | 'prefer_monitored' | 'keep_separate'
interface UsageImportPreview {
  content_sha256: string
  row_count: number
  duplicate: boolean
  conflict_count: number
  duplicate_row_count: number
  overlap_count: number
  gap_count: number
  affected_cycle_count: number
  finalized_cycle_conflict: boolean
  normalized_preview: Array<Record<string, unknown>>
  will_commit: boolean
  id?: string
  status?: string
}

function UsageImportManager({ account, onImported }: { account: UtilityAccount; onImported: () => Promise<void> }) {
  const [kind, setKind] = useState<UsageImportKind>('interval')
  const [timezone, setTimezone] = useState(account.timezone)
  const [sourceName, setSourceName] = useState('')
  const [mappingText, setMappingText] = useState('{}')
  const [rowsText, setRowsText] = useState('[]')
  const [conflictPolicy, setConflictPolicy] = useState<UsageConflictPolicy>('reject')
  const [preview, setPreview] = useState<UsageImportPreview>()
  const [message, setMessage] = useState('')

  function payload(commit: boolean) {
    const rows: unknown = JSON.parse(rowsText)
    const fieldMapping: unknown = JSON.parse(mappingText)
    if (!Array.isArray(rows) || rows.length === 0 || !rows.every((row) => typeof row === 'object' && row !== null && !Array.isArray(row))) {
      throw new Error('Usage rows must be a non-empty JSON array of objects.')
    }
    if (typeof fieldMapping !== 'object' || fieldMapping === null || Array.isArray(fieldMapping)) {
      throw new Error('Field mapping must be a JSON object.')
    }
    return {
      import_kind: kind,
      timezone,
      source_name: sourceName,
      field_mapping: fieldMapping,
      rows,
      conflict_policy: conflictPolicy,
      commit,
    }
  }

  const previewImport = useMutation({
    mutationFn: () => api<UsageImportPreview>(`/api/v1/admin/utility-accounts/${account.id}/usage-imports`, {
      method: 'POST',
      body: JSON.stringify(payload(false)),
    }),
    onSuccess: (result) => {
      setPreview(result)
      setMessage('Import preview normalized. Review every warning before committing.')
    },
  })
  const commitImport = useMutation({
    mutationFn: () => api<UsageImportPreview>(`/api/v1/admin/utility-accounts/${account.id}/usage-imports`, {
      method: 'POST',
      body: JSON.stringify(payload(true)),
    }),
    onSuccess: async (result) => {
      setPreview(result)
      setMessage(`Usage evidence committed. ${result.affected_cycle_count} billing cycle(s) queued for review or recalculation.`)
      await onImported()
    },
  })
  const invalidatePreview = () => {
    setPreview(undefined)
    setMessage('')
  }
  const commitBlocked = !preview
    || preview.duplicate
    || preview.duplicate_row_count > 0
    || preview.overlap_count > 0
    || (preview.conflict_count > 0 && conflictPolicy === 'reject')
    || (kind === 'cycle_dates' && preview.finalized_cycle_conflict)

  return <section className="usage-import-manager">
    <header><div><span className="plan-code">Reviewed evidence intake</span><h5>Utility usage import</h5></div>{preview && <StatusPill status={commitBlocked ? 'warning' : 'healthy'} label={`${preview.row_count} normalized row${preview.row_count === 1 ? '' : 's'}`} />}</header>
    <p className="field-help">Paste canonical JSON rows or map canonical field names to names in a utility export. Preview performs timezone normalization, exact-decimal validation, hashing, duplicate detection, gap/overlap analysis, monitored-data conflict checks, and billing-cycle impact checks.</p>
    {message && <p className="form-success" role="status">{message}</p>}
    {previewImport.error && <ErrorState error={previewImport.error} />}
    {commitImport.error && <ErrorState error={commitImport.error} />}
    <div className="usage-import-grid">
      <label><span>Import kind</span><select value={kind} onChange={(event) => { setKind(event.target.value as UsageImportKind); invalidatePreview() }}><option value="interval">Utility interval usage</option><option value="daily">Daily usage</option><option value="cycle_cumulative">Cycle-to-date cumulative usage</option><option value="cycle_dates">Exact billing-cycle dates</option><option value="bill_total">Final utility bill total</option></select></label>
      <label><span>Import timezone</span><input value={timezone} onChange={(event) => { setTimezone(event.target.value); invalidatePreview() }} /></label>
      <label><span>Source name</span><input value={sourceName} onChange={(event) => { setSourceName(event.target.value); invalidatePreview() }} placeholder="SCE Green Button export, bill, or portal" /></label>
      <label><span>Overlap policy</span><select value={conflictPolicy} onChange={(event) => { setConflictPolicy(event.target.value as UsageConflictPolicy); invalidatePreview() }}><option value="reject">Reject monitored overlap</option><option value="prefer_utility">Prefer utility evidence</option><option value="prefer_monitored">Prefer monitored readings</option><option value="keep_separate">Keep both as separate evidence</option></select></label>
    </div>
    <label><span>Field mapping (canonical name to source field)</span><textarea rows={3} spellCheck={false} value={mappingText} onChange={(event) => { setMappingText(event.target.value); invalidatePreview() }} aria-describedby={`usage-import-mapping-${account.id}`} /></label>
    <p id={`usage-import-mapping-${account.id}`} className="field-help">Example: {`{"start":"interval_start","end":"interval_end","energy_kwh":"usage"}`}. Use {`{}`} when rows already use canonical names.</p>
    <label><span>Usage rows (JSON)</span><textarea rows={9} spellCheck={false} value={rowsText} onChange={(event) => { setRowsText(event.target.value); invalidatePreview() }} placeholder='[{"start":"2026-07-22T00:00:00-07:00","end":"2026-07-22T01:00:00-07:00","energy_kwh":"1.25"}]' /></label>
    <div className="button-row">
      <button className="button secondary" disabled={!sourceName.trim() || previewImport.isPending} onClick={() => { previewImport.mutate() }}>Preview and validate</button>
      <button className="button primary" disabled={commitBlocked || commitImport.isPending} onClick={() => { commitImport.mutate() }}>Commit reviewed evidence</button>
    </div>
    {preview && <div className="usage-import-preview">
      <dl className="authority-status">
        <div><dt>Content SHA-256</dt><dd><code>{preview.content_sha256}</code></dd></div>
        <div><dt>Existing duplicate</dt><dd>{preview.duplicate ? 'Yes - commit blocked' : 'No'}</dd></div>
        <div><dt>Internal duplicates / overlaps</dt><dd>{preview.duplicate_row_count} / {preview.overlap_count}</dd></div>
        <div><dt>Gaps</dt><dd>{preview.gap_count}</dd></div>
        <div><dt>Monitored overlaps</dt><dd>{preview.conflict_count}</dd></div>
        <div><dt>Affected cycles</dt><dd>{preview.affected_cycle_count}{preview.finalized_cycle_conflict ? ' (includes finalized evidence)' : ''}</dd></div>
      </dl>
      {preview.gap_count > 0 && <p className="scope-warning">The import contains gaps. It can remain evidence, but coverage and projections will disclose the missing windows.</p>}
      {preview.conflict_count > 0 && conflictPolicy === 'reject' && <p className="scope-warning">Choose and review an explicit overlap policy before committing.</p>}
      <details><summary>Normalized preview</summary><pre>{JSON.stringify(preview.normalized_preview, null, 2)}</pre></details>
    </div>}
  </section>
}

function UsageAuthorityManager({ account, onSaved }: { account: UtilityAccount; onSaved: (message: string) => Promise<void> }) {
  const [authorityType, setAuthorityType] = useState<AuthorityType>('complete_site_aggregate')
  const [aggregateId, setAggregateId] = useState('')
  const [deviceIds, setDeviceIds] = useState<string[]>([])
  const [sourceReference, setSourceReference] = useState('')
  const [confidence, setConfidence] = useState<Authority['confidence']>('unverified')
  const [manualKwh, setManualKwh] = useState('')
  const [manualNote, setManualNote] = useState('')
  const [cycleStart, setCycleStart] = useState('')
  const [cycleEnd, setCycleEnd] = useState('')
  const [message, setMessage] = useState('')
  const authority = useQuery({
    queryKey: ['usage-authority', account.id],
    queryFn: () => api<Authority>(`/api/v1/admin/utility-accounts/${account.id}/usage-authority`),
  })
  const devices = useQuery({ queryKey: ['devices', account.site_id], queryFn: () => api<Device[]>('/api/v1/devices') })
  const aggregates = useQuery({ queryKey: ['aggregates', account.site_id], queryFn: () => api<AggregateSet[]>('/api/v1/aggregate-sets') })
  const tierStatus = useQuery({
    queryKey: ['tier-status', account.id],
    queryFn: () => api<TierStatus>(`/api/v1/utility-accounts/${account.id}/tier-status`),
  })
  const tierData = isTierStatus(tierStatus.data) ? tierStatus.data : undefined

  useEffect(() => {
    if (!authority.data) return
    if (typeof authority.data.configured !== 'boolean') return
    setAuthorityType(authority.data.authority_type ?? 'complete_site_aggregate')
    setAggregateId(authority.data.aggregate_set_id ?? '')
    setDeviceIds(authority.data.device_ids)
    setSourceReference(authority.data.source_reference ?? '')
    setConfidence(authority.data.confidence)
  }, [authority.data])

  const accountDevices = (devices.data ?? []).filter((device) => device.site_id === account.site_id && device.lifecycle_status !== 'decommissioned')
  const accountAggregates = (aggregates.data ?? []).filter((aggregate) => aggregate.site_id === account.site_id && aggregate.cost_scope === 'full_account')
  const requiredDevices = authorityType === 'service_leg_pair' ? 2 : authorityType === 'whole_account_meter' ? 1 : undefined
  const authorityValid = (authorityType !== 'complete_site_aggregate' || Boolean(aggregateId))
    && (requiredDevices === undefined || deviceIds.length === requiredDevices)

  const saveAuthority = useMutation({
    mutationFn: () => api<Authority>(`/api/v1/admin/utility-accounts/${account.id}/usage-authority`, {
      method: 'PUT',
      body: JSON.stringify({
        revision: authority.data?.configured ? authority.data.revision : null,
        authority_type: authorityType,
        aggregate_set_id: authorityType === 'complete_site_aggregate' ? aggregateId : null,
        device_ids: ['service_leg_pair', 'whole_account_meter', 'partial_monitored_circuits'].includes(authorityType) ? deviceIds : [],
        source_reference: sourceReference || null,
        confidence,
        complete_account: authorityType !== 'partial_monitored_circuits',
      }),
    }),
    onSuccess: async () => {
      setMessage('Account usage authority saved.')
      await authority.refetch()
      await tierStatus.refetch()
      await onSaved('Account usage authority saved.')
    },
  })
  const addManual = useMutation({
    mutationFn: () => api(`/api/v1/admin/utility-accounts/${account.id}/manual-usage`, {
      method: 'POST',
      body: JSON.stringify({
        effective_at: new Date().toISOString(),
        cumulative_kwh: manualKwh,
        source_note: manualNote,
        evidence_reference: sourceReference || null,
        verification_status: confidence === 'utility_verified' ? 'verified' : 'unverified',
        idempotency_key: crypto.randomUUID(),
      }),
    }),
    onSuccess: async () => {
      setManualKwh('')
      setManualNote('')
      setMessage('Manual cycle usage recorded with an audit trail.')
      await tierStatus.refetch()
    },
  })
  const overrideCycle = useMutation({
    mutationFn: () => api(`/api/v1/admin/utility-accounts/${account.id}/billing-cycles`, {
      method: 'POST',
      body: JSON.stringify({
        starts_at: new Date(cycleStart).toISOString(),
        ends_at: new Date(cycleEnd).toISOString(),
        source: 'manual_override',
        reason: 'Administrator confirmed exact meter cycle dates',
      }),
    }),
    onSuccess: async () => {
      setMessage('Exact billing-cycle dates saved.')
      await tierStatus.refetch()
    },
  })
  const recalculateTier = useMutation({
    mutationFn: () => api<TierStatus>(`/api/v1/admin/utility-accounts/${account.id}/billing-cycles/${tierData?.cycle.id}/recalculate`, { method: 'POST' }),
    onSuccess: async () => {
      setMessage('Chronological tier allocation recalculated.')
      await tierStatus.refetch()
    },
  })

  return <section className="usage-authority-manager">
    <header><div><span className="plan-code">Billing-cycle usage authority</span><h4>Tier calculation source</h4></div>{tierData?.available && <StatusPill status="healthy" label={`${tierData.current_tier?.name ?? 'Tier'} / ${tierData.authoritative_usage_kwh} kWh`} />}</header>
    <p className="field-help">Tier progression uses one explicit complete-account source. Partial circuit readings remain visible but cannot silently drive an account tier.</p>
    {message && <p className="form-success" role="status">{message}</p>}
    {authority.error && <ErrorState error={authority.error} />}
    <div className="account-manager-grid">
      <section>
        <h5>Authoritative source</h5>
        <label><span>Source type</span><select value={authorityType} onChange={(event) => { setAuthorityType(event.target.value as AuthorityType); setDeviceIds([]) }}><option value="complete_site_aggregate">Verified full-account aggregate</option><option value="service_leg_pair">Paired service-leg sensors</option><option value="whole_account_meter">Whole-account meter sensor</option><option value="utility_interval_import">Utility interval import</option><option value="manual_cycle_usage">Manual cycle-to-date usage</option><option value="external_feed">External account feed</option><option value="partial_monitored_circuits">Partial monitored circuits (not tier authoritative)</option></select></label>
        {authorityType === 'complete_site_aggregate' && <label><span>Full-account aggregate</span><select value={aggregateId} onChange={(event) => { setAggregateId(event.target.value) }}><option value="">Choose an aggregate</option>{accountAggregates.map((aggregate) => <option key={aggregate.id} value={aggregate.id}>{aggregate.name}</option>)}</select></label>}
        {['service_leg_pair', 'whole_account_meter', 'partial_monitored_circuits'].includes(authorityType) && <fieldset className="authority-device-list"><legend>Sensors {requiredDevices ? `(choose ${requiredDevices})` : ''}</legend>{accountDevices.map((device) => <label key={device.id}><input type="checkbox" checked={deviceIds.includes(device.id)} onChange={(event) => { setDeviceIds(event.target.checked ? [...deviceIds, device.id] : deviceIds.filter((id) => id !== device.id)) }} /><span>{device.name}<small>{device.measurement_role}</small></span></label>)}</fieldset>}
        <label><span>Evidence or source reference</span><input value={sourceReference} onChange={(event) => { setSourceReference(event.target.value) }} placeholder="Bill, Green Button export, or topology note" /></label>
        <label><span>Confidence</span><select value={confidence} onChange={(event) => { setConfidence(event.target.value as Authority['confidence']) }}><option value="unverified">Unverified</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="utility_verified">Utility verified</option></select></label>
        <button className="button secondary" disabled={!authorityValid || saveAuthority.isPending} onClick={() => { saveAuthority.mutate() }}>Save usage authority</button>
      </section>
      <section>
        <h5>Manual cycle-to-date reading</h5>
        <label><span>Cumulative usage (kWh)</span><input type="number" min="0" step="0.001" value={manualKwh} onChange={(event) => { setManualKwh(event.target.value) }} /></label>
        <label><span>Evidence note</span><input value={manualNote} onChange={(event) => { setManualNote(event.target.value) }} placeholder="Reading copied from utility portal" /></label>
        <button className="button secondary" disabled={!manualKwh || !manualNote.trim() || addManual.isPending} onClick={() => { addManual.mutate() }}>Record manual usage</button>
        <p className="field-help">Manual readings are append-only and idempotent. Existing evidence is never overwritten.</p>
      </section>
      <section>
        <h5>Exact billing-cycle dates</h5>
        <label><span>Meter cycle starts</span><input type="datetime-local" value={cycleStart} onChange={(event) => { setCycleStart(event.target.value) }} /></label>
        <label><span>Meter cycle ends</span><input type="datetime-local" value={cycleEnd} onChange={(event) => { setCycleEnd(event.target.value) }} /></label>
        <button className="button secondary" disabled={!cycleStart || !cycleEnd || overrideCycle.isPending} onClick={() => { overrideCycle.mutate() }}>Save exact cycle</button>
        <p className="field-help">Expected dates remain visible until exact utility meter dates are confirmed.</p>
      </section>
      <section>
        <h5>Recalculation state</h5>
        {tierStatus.isLoading ? <LoadingState /> : tierStatus.error ? <ErrorState error={tierStatus.error} /> : tierData ? <dl className="authority-status"><div><dt>Cycle</dt><dd>{formatTime(tierData.cycle.starts_at)} - {formatTime(tierData.cycle.ends_at)}</dd></div><div><dt>Authority</dt><dd>{tierData.usage_authority.authority_type?.replaceAll('_', ' ') ?? 'Not configured'}</dd></div><div><dt>Calculation version</dt><dd>{tierData.recalculation_version ?? 0}</dd></div></dl> : null}
        <button className="button secondary" disabled={!tierData || Boolean(tierData.cycle.finalized_at) || recalculateTier.isPending} onClick={() => { recalculateTier.mutate() }}><RefreshCw size={14} /> Recalculate tier allocation</button>
        {tierData?.cycle.finalized_at && <p className="field-help">This cycle is finalized and immutable. Reconcile differences instead of rewriting history.</p>}
      </section>
    </div>
    <UsageImportManager account={account} onImported={async () => {
      await tierStatus.refetch()
      await onSaved('Utility usage evidence imported.')
    }} />
  </section>
}
