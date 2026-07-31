import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  CalendarDays,
  Check,
  ChevronDown,
  CircleOff,
  FileClock,
  FileSearch,
  FlaskConical,
  MoreHorizontal,
  Pencil,
  Plus,
  ReceiptText,
  ShieldCheck,
  Trash2,
  Upload,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from '../../app/router'
import {
  adaptBills,
  adaptRateAssignmentResult,
  adaptRatePlanDependencies,
  adaptRateVersions,
} from '../../api/adapters'
import { errorMessage, json, request } from '../../api/client'
import { objectList, record, stringValue } from '../../api/validation'
import { Metric, Surface } from '../../components/data-display/Surface'
import { EmptyState, ErrorState, InlineNotice, LoadingState } from '../../components/feedback/States'
import { MetadataItem, MetadataList, Page, PageHeader, StatGrid } from '../../components/layout/Layout'
import { ModalLayer } from '../../components/overlays/ModalLayer'
import { DropdownMenu, DropdownMenuItem } from '../../components/overlays/DropdownMenu'
import { BillImportFlow } from '../../features/bill-import/BillImportFlow'
import { CostCalculationSetup } from '../../features/billing/CostCalculationSetup'
import { AdvancedRateSettings } from '../../features/rates/AdvancedRateSettings'
import { ConfigurationStatusChip } from '../../features/configuration/ConfigurationStatusSurface'
import { useAuth } from '../../state/AuthContext'
import { useLiveHome } from '../../state/LiveHomeContext'
import { useSingleHome } from '../../state/SingleHomeContext'
import { useTestMode } from '../../state/TestModeContext'
import type { BillSummary, ElectricService, RateAssignmentResult } from '../../types/models'
import { dateRange, dateTime, energy, money, percentage, rate, statusLabel } from '../../utils/format'
import { hasAnyPermission, hasPermission } from '../../access/permissions'

interface LibraryPlan {
  id: string
  name: string
  code: string
  status: string
  lifecycleRevision: number
  versionId?: string
  version?: number
  pricingModel?: string
  removedAt?: string
  removedBy?: string
  removalReason?: string
  publicationStatus: string
  assignmentStatus: string
}

function libraryPlans(value: unknown): LibraryPlan[] {
  const rows = Array.isArray(value) ? objectList(value) : objectList(record(value).plans)
  return rows.map((row) => {
    const versions = adaptRateVersions(objectList(row.versions))
    const current = versions.find((version) => version.assignmentStatus === 'current')
    const published = versions.find((version) => version.publicationStatus === 'published')
    const assignable = current ?? published
    return {
      id: stringValue(row.id),
      name: stringValue(row.name, stringValue(row.plan_name, 'Rate plan')),
      code: stringValue(row.code, stringValue(row.plan_code)),
      status: stringValue(row.status, 'draft'),
      lifecycleRevision: Number(row.lifecycle_revision ?? row.revision ?? 1),
      versionId: assignable?.id ?? (stringValue(row.rate_version_id) || undefined),
      version: assignable?.version ?? (Number(row.version ?? 0) || undefined),
      pricingModel: assignable?.pricingModel ?? (stringValue(row.pricing_model) || undefined),
      removedAt: stringValue(row.removed_at) || undefined,
      removedBy: stringValue(row.removed_by) || undefined,
      removalReason: stringValue(row.removal_reason) || undefined,
      publicationStatus: assignable?.publicationStatus ?? 'draft',
      assignmentStatus: current ? 'current' : 'unassigned',
    }
  })
}

export function BillingPage() {
  const { resolution } = useSingleHome()
  const { services, sensors, cycle, configuration, refresh } = useLiveHome()
  const { session } = useAuth()
  const canViewPrivateBills = hasPermission(session, 'utility_bills.view')
  const canManageBills = hasPermission(session, 'utility_bills.manage')
  const canManageServices = hasPermission(session, 'utility_accounts.manage')
  const canManageCustomRates = hasPermission(session, 'rates.manage_custom')
  const canAssignRates = hasPermission(session, 'rates.assign')
  const canRemoveRates = hasPermission(session, 'rates.remove')
  const canManageUsageAuthority = hasPermission(session, 'usage_imports.manage')
  const canManageAdvancedRates = hasAnyPermission(session, [
    'rates.manage_custom', 'rates.manage_sources', 'rates.check_sources',
    'rates.review_candidates', 'rates.approve_candidates', 'rates.assign',
    'rates.remove', 'rates.restore', 'adjustments.manage',
  ])
  const testMode = useTestMode()
  const [params, setParams] = useSearchParams()
  const importOpen = canManageBills && params.get('action') === 'upload'
  const [planDetail, setPlanDetail] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(params.get('advanced') === 'rates')
  const [replaceOpen, setReplaceOpen] = useState(false)
  const [lifecycleAction, setLifecycleAction] = useState<'unassign' | 'retire' | 'remove'>()
  const [assignmentNotice, setAssignmentNotice] = useState('')
  const importTriggerRef = useRef<HTMLButtonElement>(null)
  const restoreImportFocusRef = useRef(false)
  const home = resolution?.state === 'ready' ? resolution.home : undefined
  const service = services[0]
  const bills = useQuery({
    queryKey: ['bills', service?.id],
    queryFn: () => request(`/api/v1/admin/utility-bill-imports${service?.id ? `?utility_account_id=${encodeURIComponent(service.id)}` : ''}`, {}, adaptBills),
    retry: 1,
    enabled: canViewPrivateBills,
  })
  const plans = useQuery({
    queryKey: ['billing-plan-library'],
    queryFn: () => request('/api/v1/rates/plans', {}, libraryPlans),
  })
  const createService = useMutation({
    mutationFn: () => request('/api/v1/utility-accounts', json('POST', {
      site_id: home?.id,
      name: 'Home electric service',
      timezone: home?.timezone,
      currency: home?.currency,
      billing_cycle_start_day: 1,
      generation_provider: 'sce',
    })),
    onSuccess: () => refresh(),
  })
  const currentLibraryPlan = plans.data?.find((plan) => plan.name === service?.currentPlan || plan.code === service?.planCode)
  const tieredCostSetupRequired = currentLibraryPlan?.pricingModel === 'tiered'
    || currentLibraryPlan?.pricingModel === 'time_of_use_tiered'
  const openImporter = useCallback(() => {
    restoreImportFocusRef.current = true
    const next = new URLSearchParams(params)
    next.set('action', 'upload')
    setParams(next)
  }, [params, setParams])
  const closeImporter = useCallback(() => {
    const next = new URLSearchParams(params)
    next.delete('action')
    setParams(next, { replace: true })
    void bills.refetch()
  }, [bills, params, setParams])
  useEffect(() => {
    if (importOpen || !restoreImportFocusRef.current) return
    restoreImportFocusRef.current = false
    const timer = window.setTimeout(() => {
      const trigger = importTriggerRef.current
        ?? document.querySelector<HTMLButtonElement>('[data-canonical-action="bill.upload"]')
      trigger?.focus({ preventScroll: true })
    }, 100)
    return () => { window.clearTimeout(timer) }
  }, [importOpen])
  const advancedTab = ['plans', 'sources', 'versions', 'evidence', 'removed', 'adjustments'].includes(params.get('tab') ?? '')
    ? params.get('tab') as 'plans' | 'sources' | 'versions' | 'evidence' | 'removed' | 'adjustments'
    : 'plans'
  const openAdvanced = useCallback((tab: typeof advancedTab = 'plans') => {
    const next = new URLSearchParams(params)
    next.set('advanced', 'rates')
    next.set('tab', tab)
    setParams(next)
    setAdvancedOpen(true)
  }, [params, setParams])
  const advancedRequested = canManageAdvancedRates && params.get('advanced') === 'rates'

  if (!home) return <ErrorState error={new Error('The default home is unavailable.')} />
  return (
    <Page className="billing-page">
      <PageHeader
        title="Billing"
        description="Rates from your plan, usage from your Power Monitor sensors."
        action={canManageBills && <button ref={importTriggerRef} type="button" className="button primary" data-canonical-action="bill.upload" onClick={openImporter}>
          <Upload size={17} /> Import rates from bill
        </button>}
      />
      {testMode.state?.enabled && (
        <Surface className="test-mode-surface active" title="Test Mode cost preview" subtitle="Temporary energy-only estimate · never saved as a bill, finalized cost, adjustment, or export.">
          <div className="test-mode-inline-heading">
            <FlaskConical />
            <span><strong>Sensor Test Mode</strong><small>{energy(testMode.state.totalEnergyKwh)} synthetic energy using isolated in-memory samples</small></span>
            <span className="pill warning">Test Mode</span>
          </div>
          {testMode.state.costPreview?.enabled ? (
            <div className="test-preview-cost">
              <strong>{testMode.state.costPreview.available ? money(testMode.state.costPreview.estimatedEnergyCost, testMode.state.costPreview.currency ?? home.currency) : 'Preview unavailable'}</strong>
              <span>{testMode.state.costPreview.disclosure}</span>
            </div>
          ) : (
            <InlineNotice>Cost preview is off by default. Opt in under Settings → Advanced → Sensor Test Mode.</InlineNotice>
          )}
        </Surface>
      )}
      {assignmentNotice && <InlineNotice tone="success">{assignmentNotice}</InlineNotice>}

      <StatGrid className="billing-top-metrics">
        <Metric label="Current rate plan" value={service?.currentPlan ?? 'Not configured'} identity="billing.current_plan" detail={service?.currentVersion ? `Version ${service.currentVersion}` : 'Import rates from a bill or choose a plan'} />
        <Metric label="Usage source" value={cycle?.usageSourceType === 'sensor_measurements' ? 'Power Monitor sensors' : cycle?.usageSourceType === 'advanced_external_correction' ? 'Advanced external correction' : 'Needs setup'} identity="billing.usage_source" detail="Bill-reported kWh is reference only" />
        <Metric label="Billing cycle" value={dateRange(service?.billingStartsAt ?? cycle?.startsAt, service?.billingEndsAt ?? cycle?.endsAt)} identity="billing.cycle" detail={cycle?.daysRemaining != null ? `${cycle.daysRemaining} days remaining` : undefined} />
      </StatGrid>
      <InlineNotice tone="info">
        Your uploaded bill supplies rate prices and tier rules. Your Power Monitor sensors
        supply monitored usage, tier progress, and projections.
      </InlineNotice>

      {!service ? (
        <Surface>
          <EmptyState title="Set up your electric service" message={canManageServices ? 'Create the household billing record before assigning a rate plan or applying a bill.' : 'The home owner has not configured an electric service yet.'} action={canManageServices && <button className="button secondary" type="button" disabled={createService.isPending} onClick={() => { createService.mutate(); }}><Plus size={17} /> {createService.isPending ? 'Creating…' : 'Create electric service'}</button>} />
          {createService.error && <p className="form-error" role="alert">{errorMessage(createService.error)}</p>}
        </Surface>
      ) : (
        <div className="billing-main-grid">
          <div className="billing-main-column">
            <Surface className="service-card">
              <div className="service-card-heading">
                <div><span>{service.provider}</span><h2>{service.name}</h2><p>{home.name} · {service.currentPlan ?? 'Rate plan not configured'}{service.currentVersion ? ` (v${service.currentVersion})` : ''}</p></div>
                <ConfigurationStatusChip status={configuration} />
              </div>
              <div className="service-facts">
                <MetadataList>
                  <MetadataItem icon={<CalendarDays />} label="Current monitored usage" value={energy(cycle?.usageKwh)} />
                  <MetadataItem icon={<FileClock />} label="Current tier" value={cycle?.currentTier ?? 'Unavailable'} />
                  <MetadataItem icon={<ShieldCheck />} label="Remaining before next tier" value={energy(cycle?.remainingKwh)} />
                  <MetadataItem icon={<ReceiptText />} label="Estimated energy cost" value={money(cycle?.energyCharge, home.currency)} />
                </MetadataList>
              </div>
              <div className="card-actions">
                <button type="button" className="button secondary" onClick={() => { setPlanDetail(!planDetail); }}><FileSearch size={16} /> Review plan</button>
                {canManageCustomRates && <button type="button" className="button secondary" onClick={() => { openAdvanced('plans'); }}><Pencil size={16} /> Edit plan</button>}
                {(canAssignRates || canRemoveRates) && <div className="more-menu">
                  <DropdownMenu label="Rate plan actions" trigger={<><MoreHorizontal size={17} /> More <ChevronDown size={14} /></>}>
                    {canAssignRates && <DropdownMenuItem actionId={service.currentPlan ? 'rate_assignment.replace_current' : 'rate_assignment.make_current'} onSelect={() => { setReplaceOpen(true) }}>{service.currentPlan ? 'Replace current plan' : 'Make a plan current'}</DropdownMenuItem>}
                    {canAssignRates && currentLibraryPlan && <DropdownMenuItem actionId="rate_assignment.end" onSelect={() => { setLifecycleAction('unassign') }}><CircleOff size={15} /> End current assignment</DropdownMenuItem>}
                    {canRemoveRates && currentLibraryPlan && <DropdownMenuItem actionId="rate_plan.retire" onSelect={() => { setLifecycleAction('retire') }}><Archive size={15} /> Retire plan</DropdownMenuItem>}
                    {canRemoveRates && currentLibraryPlan && <DropdownMenuItem actionId="rate_plan.remove" className="danger" onSelect={() => { setLifecycleAction('remove') }}><Trash2 size={15} /> Remove plan</DropdownMenuItem>}
                  </DropdownMenu>
                </div>}
              </div>
              {planDetail && <PlanDetail service={service} model={currentLibraryPlan?.pricingModel} cycle={cycle} currency={home.currency} />}
              {canAssignRates && replaceOpen && <ReplacePlanV2 service={service} homeId={home.id} plans={plans.data ?? []} onClose={() => { setReplaceOpen(false); }} onDone={(result) => {
                setReplaceOpen(false)
                setAssignmentNotice(result.state === 'current'
                  ? `Current plan updated to version ${result.version}. Billing, Home, and History now use this assignment.`
                  : `Version ${result.version} is scheduled for ${dateTime(result.effectiveFrom)}.`)
                void refresh()
              }} />}
            </Surface>

            {tieredCostSetupRequired && canManageUsageAuthority && (
              <CostCalculationSetup
                service={service}
                sensors={sensors}
                cycle={cycle}
                onRefresh={refresh}
              />
            )}

            <Surface title="Billing-cycle details" subtitle={dateRange(cycle?.startsAt, cycle?.endsAt)}>
              {!cycle?.available ? <EmptyState compact title="Sensor-based estimate unavailable" message="Choose a complete-service sensor source and add billing-cycle dates. Bill-reported usage is never used as a fallback." action={canManageUsageAuthority && <button type="button" className="button secondary compact" onClick={() => { document.querySelector('.cost-calculation-setup')?.scrollIntoView({ behavior: 'smooth' }) }}><ShieldCheck size={16} /> Configure sensor usage</button>} /> : (
                <>
                  <StatGrid className="cycle-metrics">
                    <Metric label="Current monitored usage" value={energy(cycle.usageKwh)} identity="billing.cycle_usage" detail="Power Monitor sensors" />
                    <Metric label="Estimated energy cost" value={money(cycle.energyCharge, home.currency)} identity="billing.cycle_charge" detail="Rates applied to sensor kWh" />
                    <Metric label="Projected sensor usage" value={energy(cycle.projectedUsageKwh)} identity="billing.projected_usage" detail="Sensor trend only" />
                    <Metric label="Projected energy cost" value={money(cycle.projectedEnergyCharge, home.currency)} identity="billing.projected_energy_cost" detail="Not a utility bill" />
                  </StatGrid>
                  {cycle.tiers.length > 0 ? (
                    <div className="tier-list">
                      {cycle.tiers.map((tier) => <div key={tier.id} className={tier.name === cycle.currentTier ? 'current' : ''}><span><strong>{tier.name}</strong><small>{tier.upperKwh ? `${tier.lowerKwh}–${tier.upperKwh} kWh` : `${tier.lowerKwh}+ kWh`}</small></span><span><strong>{energy(tier.usageKwh)}</strong><small>{rate(tier.rate, home.currency)} · {money(tier.cost, home.currency)}</small></span></div>)}
                    </div>
                  ) : (
                    <div className="period-summary"><span><small>Current period</small><strong>{cycle.currentPeriod ?? service.currentPeriod ?? 'Flat rate'}</strong></span><span><small>Current price</small><strong>{rate(cycle.currentRate ?? service.currentRate, home.currency)}</strong></span></div>
                  )}
                </>
              )}
            </Surface>

            {canViewPrivateBills && <Surface title="Reference bills" subtitle="Imported statements retained as evidence; not used in monitored calculations">
              {bills.isLoading ? <LoadingState label="Loading past bills…" /> : bills.error ? <ErrorState error={bills.error} retry={() => void bills.refetch()} /> : (bills.data?.length ?? 0) === 0 ? <EmptyState title="No past bills yet" message="Your first reviewed bill will appear here." /> : <PastBills bills={bills.data ?? []} currency={home.currency} />}
            </Surface>}
          </div>
          <aside className="billing-side-column">
            {canViewPrivateBills && <Surface title="Latest reference bill" subtitle="Reference only · not used in monitored calculations">
              {bills.data?.[0] ? <LatestBill bill={bills.data[0]} currency={home.currency} /> : <EmptyState title="No imported bill" message="Upload a PDF to prepare reviewed rate rules." />}
            </Surface>}
            <Surface title="Estimate confidence">
              <div className="confidence-card"><span>{cycle?.confidence ? statusLabel(cycle.confidence) : 'Waiting for history'}</span><p>Coverage {percentage(cycle?.coveragePercent)}. Estimates improve as synchronized readings fill the cycle.</p></div>
            </Surface>
          </aside>
        </div>
      )}

      {canManageAdvancedRates && (
        <details className="advanced-disclosure" open={advancedOpen || advancedRequested} onToggle={(event) => {
          setAdvancedOpen(event.currentTarget.open)
          if (!event.currentTarget.open && advancedRequested) {
            const next = new URLSearchParams(params)
            next.delete('advanced')
            next.delete('tab')
            setParams(next, { replace: true })
          }
        }}>
          <summary><span><strong>Advanced Rate Settings</strong><small>Custom editor, exact components, sources, versions, evidence, and adjustments</small></span><ChevronDown /></summary>
          <AdvancedRateSettings key={advancedTab} home={home} services={services} initialView={advancedTab} />
        </details>
      )}

      {canManageBills && importOpen && <ModalLayer onRequestClose={closeImporter} returnFocusRef={importTriggerRef}><BillImportFlow home={home} services={services} onClose={closeImporter} /></ModalLayer>}
      {canRemoveRates && lifecycleAction && currentLibraryPlan && service && (
        <ModalLayer onRequestClose={() => { setLifecycleAction(undefined) }}>
          <PlanLifecycleDialog
            action={lifecycleAction}
            plan={currentLibraryPlan}
            service={service}
            onClose={() => { setLifecycleAction(undefined) }}
            onDone={() => {
              setLifecycleAction(undefined)
              void refresh()
              void plans.refetch()
            }}
          />
        </ModalLayer>
      )}
    </Page>
  )
}

function PlanDetail({ service, model, cycle, currency }: { service: ElectricService; model?: string; cycle?: ReturnType<typeof useLiveHome>['cycle']; currency: string }) {
  return <div className="plan-detail"><dl><div><dt>Plan</dt><dd>{service.currentPlan ?? 'Not configured'}</dd></div><div><dt>Pricing model</dt><dd>{statusLabel(model ?? cycle?.pricingModel ?? 'unknown')}</dd></div><div><dt>Effective version</dt><dd>{service.currentVersion ? `Version ${service.currentVersion}` : 'Unavailable'}</dd></div><div><dt>Current period</dt><dd>{service.currentPeriod ?? cycle?.currentPeriod ?? 'Flat rate'}</dd></div><div><dt>Current price</dt><dd>{rate(service.currentRate ?? cycle?.currentRate, currency)}</dd></div><div><dt>Evidence</dt><dd>Retained with this exact version</dd></div></dl></div>
}

function ReplacePlanV2({
  service,
  homeId,
  plans,
  onClose,
  onDone,
}: {
  service: ElectricService
  homeId: string
  plans: LibraryPlan[]
  onClose: () => void
  onDone: (result: RateAssignmentResult) => void
}) {
  const client = useQueryClient()
  const [versionId, setVersionId] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [reason, setReason] = useState(
    'Owner reviewed the current rate-plan replacement',
  )
  const [effectiveChoice, setEffectiveChoice] = useState<'now' | 'next_cycle' | 'custom'>('now')
  const [customEffective, setCustomEffective] = useState(new Date().toISOString().slice(0, 16))
  const mutation = useMutation({
    mutationFn: () => {
      const effectiveFrom = effectiveChoice === 'now'
        ? new Date().toISOString()
        : effectiveChoice === 'next_cycle'
          ? service.billingEndsAt
          : new Date(customEffective).toISOString()
      if (!effectiveFrom) throw new Error('The next billing-cycle boundary is unavailable.')
      return request(
        '/api/v1/rates/assignments/replace',
        json('POST', {
          utility_account_id: service.id,
          rate_version_id: versionId,
          effective_from: effectiveFrom,
          effective_to: null,
          assignment_reason: reason,
          replace_current: true,
          idempotency_key: crypto.randomUUID(),
          confirmation: 'REPLACE CURRENT',
          expected_account_revision: service.revision,
          expected_current_assignment_revision: service.currentAssignmentRevision,
        }),
        adaptRateAssignmentResult,
      )
    },
    onSuccess: (result) => {
      const selectedPlan = plans.find((plan) => plan.versionId === result.versionId)
      client.setQueryData<ElectricService[]>(
        ['electric-services', homeId],
        (current) => current?.map((item) => item.id === result.electricServiceId
          ? {
              ...item,
              revision: result.serviceRevision,
              currentPlan: selectedPlan?.name,
              planCode: selectedPlan?.code,
              rateVersionId: result.versionId,
              currentVersion: result.version,
              readiness: { ...item.readiness, rate: result.state === 'current' ? 'rate_configured_effective' : 'rate_not_yet_effective' },
            }
          : item),
      )
      onDone(result)
      void Promise.all([
        client.invalidateQueries({ queryKey: ['electric-services'] }),
        client.invalidateQueries({ queryKey: ['managed-rate-plans'] }),
        client.invalidateQueries({ queryKey: ['billing-plan-library'] }),
        client.invalidateQueries({ queryKey: ['home-summary'] }),
        client.invalidateQueries({ queryKey: ['billing-cycle-summary'] }),
        client.invalidateQueries({ queryKey: ['history'] }),
        client.invalidateQueries({ queryKey: ['configuration-status'] }),
        client.invalidateQueries({ queryKey: ['current-rate-assignment'] }),
      ]).then(() => client.refetchQueries({ queryKey: ['electric-services', homeId], exact: true }))
    },
  })
  const hasCurrent = Boolean(service.currentPlan)
  return (
    <div className="replace-plan">
      <h3>{hasCurrent ? 'Replace current plan' : 'Make plan current'}</h3>
      <p>
        {hasCurrent ? 'The previous assignment and historical costs remain preserved.' : 'This creates the first effective plan assignment.'}
        Unfinalized estimates are recalculated from the effective boundary.
      </p>
      <label>
        <span>Published plan</span>
        <select
          value={versionId}
          onChange={(event) => {
            setVersionId(event.target.value)
          }}
        >
          <option value="">Choose a plan</option>
          {plans
            .filter(
              (plan) =>
                plan.versionId &&
                plan.name !== service.currentPlan &&
                plan.publicationStatus === 'published' &&
                !['removed', 'retired'].includes(plan.status),
            )
            .map((plan) => (
              <option key={plan.id} value={plan.versionId}>
                {plan.name} · v{plan.version}
              </option>
            ))}
        </select>
      </label>
      <label>
        <span>Effective timing</span>
        <select value={effectiveChoice} onChange={(event) => { setEffectiveChoice(event.target.value as typeof effectiveChoice) }}>
          <option value="now">Now</option>
          <option value="next_cycle" disabled={!service.billingEndsAt}>Next billing cycle</option>
          <option value="custom">Custom date and time</option>
        </select>
      </label>
      {effectiveChoice === 'custom' && <label><span>Effective from</span><input type="datetime-local" value={customEffective} onChange={(event) => { setCustomEffective(event.target.value) }} /></label>}
      <label>
        <span>Reason</span>
        <input
          value={reason}
          onChange={(event) => {
            setReason(event.target.value)
          }}
        />
      </label>
      <label className="confirmation-check">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(event) => {
            setConfirmed(event.target.checked)
          }}
        />
        <span>I confirm this plan should become Current at the selected effective boundary.</span>
      </label>
      {mutation.error && (
        <p className="form-error" role="alert">
          {errorMessage(mutation.error)}
        </p>
      )}
      <div className="inline-actions">
        <button type="button" className="button secondary" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="button primary"
          disabled={
            !versionId ||
            !confirmed ||
            reason.trim().length < 8 ||
            mutation.isPending
          }
          onClick={() => {
            mutation.mutate()
          }}
        >
          {mutation.isPending ? 'Switching…' : hasCurrent ? 'Replace current' : 'Make current'}
        </button>
      </div>
    </div>
  )
}

export function LegacyReplacePlan({ service, plans, onClose, onDone }: { service: ElectricService; plans: LibraryPlan[]; onClose: () => void; onDone: () => void }) {
  const [versionId, setVersionId] = useState('')
  const mutation = useMutation({
    mutationFn: () => request(`/api/v1/admin/utility-accounts/${service.id}/rate-assignments`, json('POST', { rate_version_id: versionId, effective_from: new Date().toISOString(), assignment_reason: 'Replaced from Single Home Billing', replace_current: true })),
    onSuccess: onDone,
  })
  return <div className="replace-plan"><h3>Replace current plan</h3><p>The previous assignment remains in billing history.</p><label><span>Published plan</span><select value={versionId} onChange={(event) => { setVersionId(event.target.value); }}><option value="">Choose a plan</option>{plans.filter((plan) => plan.versionId && plan.name !== service.currentPlan && !['removed', 'retired'].includes(plan.status)).map((plan) => <option key={plan.id} value={plan.versionId}>{plan.name} · v{plan.version}</option>)}</select></label>{mutation.error && <p className="form-error" role="alert">{errorMessage(mutation.error)}</p>}<div className="inline-actions"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="button" className="button primary" disabled={!versionId || mutation.isPending} onClick={() => { mutation.mutate(); }}>{mutation.isPending ? 'Switching…' : 'Use selected plan'}</button></div></div>
}

function PlanLifecycleDialog({
  action,
  plan,
  service,
  onClose,
  onDone,
}: {
  action: 'unassign' | 'retire' | 'remove'
  plan: LibraryPlan
  service: ElectricService
  onClose: () => void
  onDone: () => void
}) {
  const dependencies = useQuery({
    queryKey: ['rate-plan-dependencies', plan.id],
    queryFn: () => request(`/api/v1/admin/rate-plans/${plan.id}/dependencies`, {}, adaptRatePlanDependencies),
  })
  const [reason, setReason] = useState(
    action === 'unassign'
      ? 'Owner removed the current plan from this electric service'
      : `Owner requested rate-plan ${action}`,
  )
  const [confirmation, setConfirmation] = useState('')
  const [effectiveAt, setEffectiveAt] = useState(new Date().toISOString().slice(0, 16))
  const mutation = useMutation({
    mutationFn: () => {
      if (!dependencies.data) throw new Error('Dependency review is still loading.')
      if (action === 'unassign') {
        return request('/api/v1/rates/assignments/end', json('POST', {
          utility_account_id: service.id,
          effective_at: new Date(effectiveAt).toISOString(),
          reason,
          confirmation: 'END CURRENT',
          idempotency_key: crypto.randomUUID(),
        }))
      }
      return request(`/api/v1/admin/rate-plans/${plan.id}/${action === 'retire' ? 'retire' : 'remove'}`, json('POST', {
        expected_revision: plan.lifecycleRevision,
        expected_dependency_token: dependencies.data.dependencyToken,
        reason,
        confirmation,
        idempotency_key: crypto.randomUUID(),
      }))
    },
    onSuccess: onDone,
  })
  const expectedConfirmation = action === 'unassign' ? 'END CURRENT' : plan.code
  const blocked = action !== 'unassign' && dependencies.data?.removalBlocked === true
  const ready = confirmation.trim().toLocaleLowerCase() === expectedConfirmation.toLocaleLowerCase()
    && reason.trim().length >= 8
    && Boolean(dependencies.data)
    && !blocked
  const title = action === 'unassign'
    ? 'End current assignment'
    : action === 'retire'
      ? 'Retire rate plan'
      : 'Remove rate plan'
  return (
    <section className="workflow lifecycle-dialog" role="dialog" aria-modal="true" aria-labelledby="plan-lifecycle-title">
      <header className="workflow-header"><div><p>Dependency-aware lifecycle</p><h2 id="plan-lifecycle-title">{title}</h2></div></header>
      <div className="workflow-body">
        <InlineNotice tone="warning">
          {action === 'unassign'
            ? 'Cost estimates will be unavailable after this date until another plan is assigned. Historical assignments, costs, reports, bill imports, and evidence remain intact.'
            : 'This plan cannot be changed while any active or future electric-service assignment remains.'}
        </InlineNotice>
        {dependencies.isLoading ? <LoadingState label="Reviewing plan impact…" /> : dependencies.error ? <ErrorState error={dependencies.error} retry={() => { void dependencies.refetch() }} /> : dependencies.data && (
          <div className="dependency-summary">
            <span><small>Current assignments</small><strong>{dependencies.data.activeAssignments.length + dependencies.data.activeAccountPointers.length}</strong></span>
            <span><small>Future assignments</small><strong>{dependencies.data.futureAssignments.length}</strong></span>
            <span><small>Historical assignments</small><strong>{dependencies.data.historicalAssignmentCount}</strong></span>
            <span><small>Cost calculations</small><strong>{dependencies.data.historicalCalculationCount}</strong></span>
            <span><small>Evidence records</small><strong>{dependencies.data.sourceEvidenceCount}</strong></span>
            <span><small>Imported bills</small><strong>{dependencies.data.billImportCount}</strong></span>
          </div>
        )}
        {blocked && <InlineNotice tone="danger">Replace or explicitly unassign all current and future assignments before continuing.</InlineNotice>}
        {action === 'unassign' && <label><span>Effective date and time</span><input type="datetime-local" value={effectiveAt} onChange={(event) => { setEffectiveAt(event.target.value) }} /></label>}
        <label><span>Reason</span><textarea value={reason} onChange={(event) => { setReason(event.target.value) }} /></label>
        <label><span>Type {expectedConfirmation} to confirm</span><input value={confirmation} autoComplete="off" onChange={(event) => { setConfirmation(event.target.value) }} /></label>
        {mutation.error && <InlineNotice tone="danger">{errorMessage(mutation.error)}</InlineNotice>}
      </div>
      <footer className="workflow-footer"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="button" className="button danger" disabled={!ready || mutation.isPending} onClick={() => { mutation.mutate() }}>{mutation.isPending ? 'Applying…' : title}</button></footer>
    </section>
  )
}

function PastBills({ bills, currency }: { bills: BillSummary[]; currency: string }) {
  return <div className="table-scroll"><table><thead><tr><th>Period</th><th>Usage</th><th>Total</th><th>Status</th><th>Imported</th></tr></thead><tbody>{bills.map((bill) => <tr key={bill.id}><th scope="row">{dateRange(bill.startsAt, bill.endsAt)}</th><td>{energy(bill.usageKwh)}</td><td>{money(bill.total, currency)}</td><td><span className="pill">{statusLabel(bill.status)}</span></td><td>{dateTime(bill.createdAt)}</td></tr>)}</tbody></table></div>
}

function LatestBill({ bill, currency }: { bill: BillSummary; currency: string }) {
  return <div className="latest-bill"><span className="pill success"><Check size={13} /> {statusLabel(bill.status)}</span><dl><div><dt>Bill period</dt><dd>{dateRange(bill.startsAt, bill.endsAt)}</dd></div><div><dt>Usage</dt><dd>{energy(bill.usageKwh)}</dd></div><div><dt>Total charges</dt><dd>{money(bill.total, currency)}</dd></div><div><dt>Imported</dt><dd>{dateTime(bill.createdAt)}</dd></div></dl></div>
}
