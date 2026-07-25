import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Archive,
  CalendarDays,
  Check,
  ChevronDown,
  CircleOff,
  FileClock,
  FileSearch,
  MoreHorizontal,
  Pencil,
  Plus,
  ReceiptText,
  ShieldCheck,
  Trash2,
  Upload,
} from 'lucide-react'
import { useCallback, useState } from 'react'
import { useSearchParams } from '../../app/router'
import { adaptBills, adaptRatePlanDependencies } from '../../api/adapters'
import { errorMessage, json, request } from '../../api/client'
import { objectList, record, stringValue } from '../../api/validation'
import { Metric, Surface } from '../../components/data-display/Surface'
import { EmptyState, ErrorState, InlineNotice, LoadingState } from '../../components/feedback/States'
import { MetadataItem, MetadataList, Page, PageHeader, StatGrid } from '../../components/layout/Layout'
import { ModalLayer } from '../../components/overlays/ModalLayer'
import { DropdownMenu, DropdownMenuItem } from '../../components/overlays/DropdownMenu'
import { BillImportFlow } from '../../features/bill-import/BillImportFlow'
import { AdvancedRateSettings } from '../../features/rates/AdvancedRateSettings'
import { useAuth } from '../../state/AuthContext'
import { useLiveHome } from '../../state/LiveHomeContext'
import { useSingleHome } from '../../state/SingleHomeContext'
import type { BillSummary, ElectricService } from '../../types/models'
import { dateRange, dateTime, energy, money, rate, statusLabel } from '../../utils/format'
import { isOwner } from '../../access/permissions'

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
}

function libraryPlans(value: unknown): LibraryPlan[] {
  const rows = Array.isArray(value) ? objectList(value) : objectList(record(value).plans)
  return rows.map((row) => {
    const latest = row.latest_version && typeof row.latest_version === 'object'
      ? record(row.latest_version)
      : objectList(row.versions)[0] ?? {}
    return {
      id: stringValue(row.id),
      name: stringValue(row.name, stringValue(row.plan_name, 'Rate plan')),
      code: stringValue(row.code, stringValue(row.plan_code)),
      status: stringValue(row.status, 'draft'),
      lifecycleRevision: Number(row.lifecycle_revision ?? row.revision ?? 1),
      versionId: stringValue(latest.id, stringValue(row.rate_version_id)) || undefined,
      version: Number(latest.version ?? row.version ?? 0) || undefined,
      pricingModel: stringValue(latest.pricing_model, stringValue(row.pricing_model)) || undefined,
      removedAt: stringValue(row.removed_at) || undefined,
      removedBy: stringValue(row.removed_by) || undefined,
      removalReason: stringValue(row.removal_reason) || undefined,
    }
  })
}

export function BillingPage() {
  const { resolution } = useSingleHome()
  const { services, cycle, refresh } = useLiveHome()
  const { session } = useAuth()
  const [params, setParams] = useSearchParams()
  const importOpen = params.get('action') === 'upload'
  const [planDetail, setPlanDetail] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(params.get('advanced') === 'rates')
  const [replaceOpen, setReplaceOpen] = useState(false)
  const [lifecycleAction, setLifecycleAction] = useState<'unassign' | 'retire' | 'remove'>()
  const home = resolution?.state === 'ready' ? resolution.home : undefined
  const service = services[0]
  const bills = useQuery({
    queryKey: ['bills', service?.id],
    queryFn: () => request(`/api/v1/admin/utility-bill-imports${service?.id ? `?utility_account_id=${encodeURIComponent(service.id)}` : ''}`, {}, adaptBills),
    retry: 1,
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
  const openImporter = useCallback(() => {
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

  if (!home) return <ErrorState error={new Error('The default home is unavailable.')} />
  return (
    <Page className="billing-page">
      <PageHeader
        title="Billing"
        description="Your electric service, rate plan, billing cycle, and imported statements."
        action={<button type="button" className="button primary" data-canonical-action="bill.upload" onClick={openImporter}>
          <Upload size={17} /> Upload electric bill
        </button>}
      />

      <StatGrid className="billing-top-metrics">
        <Metric label="Electric service" value={service ? '1' : '0'} identity="billing.service_count" detail={service ? service.name : 'Setup needed'} />
        <Metric label="Current plan" value={service?.currentPlan ?? 'Not configured'} identity="billing.current_plan" detail={service?.currentVersion ? `Version ${service.currentVersion}` : 'Upload a bill to begin'} />
        <Metric label="Current energy price" value={rate(service?.currentRate ?? cycle?.currentRate, home.currency)} identity="billing.current_price" detail={service?.nextRate ? `Next: ${rate(service.nextRate, home.currency)}` : undefined} />
      </StatGrid>

      {!service ? (
        <Surface>
          <EmptyState title="Set up your electric service" message="Create the household billing record before assigning a rate plan or applying a bill." action={<button className="button secondary" type="button" disabled={createService.isPending} onClick={() => { createService.mutate(); }}><Plus size={17} /> {createService.isPending ? 'Creating…' : 'Create electric service'}</button>} />
          {createService.error && <p className="form-error" role="alert">{errorMessage(createService.error)}</p>}
        </Surface>
      ) : (
        <div className="billing-main-grid">
          <div className="billing-main-column">
            <Surface className="service-card">
              <div className="service-card-heading">
                <div><span>{service.provider}</span><h2>{service.name}</h2><p>{home.name} · {service.currentPlan ?? 'Rate plan not configured'}{service.currentVersion ? ` (v${service.currentVersion})` : ''}</p></div>
                <span className={`pill ${service.currentPlan ? 'success' : 'warning'}`}>{service.currentPlan ? 'Rate active' : 'Setup needed'}</span>
              </div>
              <div className="service-facts">
                <MetadataList>
                  <MetadataItem icon={<CalendarDays />} label="Billing day" value={ordinal(service.billingDay)} />
                  <MetadataItem icon={<FileClock />} label="Current period" value={dateRange(service.billingStartsAt ?? cycle?.startsAt, service.billingEndsAt ?? cycle?.endsAt)} />
                  <MetadataItem icon={<ShieldCheck />} label="Cost scope" value={statusLabel(service.costScope)} />
                  <MetadataItem icon={<ReceiptText />} label="Projected bill" value={money(cycle?.projectedBill, home.currency)} />
                </MetadataList>
              </div>
              <div className="card-actions">
                <button type="button" className="button secondary" onClick={() => { setPlanDetail(!planDetail); }}><FileSearch size={16} /> Review plan</button>
                {isOwner(session ?? { authenticated: false, bootstrapRequired: false }) && <button type="button" className="button secondary" onClick={() => { setAdvancedOpen(true); }}><Pencil size={16} /> Edit plan</button>}
                <div className="more-menu">
                  <DropdownMenu label="Rate plan actions" trigger={<><MoreHorizontal size={17} /> More <ChevronDown size={14} /></>}>
                    <DropdownMenuItem actionId="rate_plan.replace_assignment" onSelect={() => { setReplaceOpen(true) }}>Replace plan</DropdownMenuItem>
                    {currentLibraryPlan && <DropdownMenuItem actionId="rate_plan.unassign" onSelect={() => { setLifecycleAction('unassign') }}><CircleOff size={15} /> Remove from Electric Service</DropdownMenuItem>}
                    <DropdownMenuItem onSelect={() => { setAdvancedOpen(true) }}>View versions</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => { setAdvancedOpen(true) }}>View evidence</DropdownMenuItem>
                    {currentLibraryPlan && <DropdownMenuItem actionId="rate_plan.retire" onSelect={() => { setLifecycleAction('retire') }}><Archive size={15} /> Retire plan</DropdownMenuItem>}
                    {currentLibraryPlan && <DropdownMenuItem actionId="rate_plan.remove" className="danger" onSelect={() => { setLifecycleAction('remove') }}><Trash2 size={15} /> Remove plan</DropdownMenuItem>}
                  </DropdownMenu>
                </div>
              </div>
              {planDetail && <PlanDetail service={service} model={currentLibraryPlan?.pricingModel} cycle={cycle} currency={home.currency} />}
              {replaceOpen && <ReplacePlan service={service} plans={plans.data ?? []} onClose={() => { setReplaceOpen(false); }} onDone={() => { setReplaceOpen(false); void refresh() }} />}
            </Surface>

            <Surface title="Billing-cycle details" subtitle={dateRange(cycle?.startsAt, cycle?.endsAt)}>
              {!cycle?.available ? <EmptyState compact title="Billing cycle not ready" message="Upload a bill or add exact cycle dates to calculate tier progress and projections." action={<button type="button" className="button secondary compact" onClick={openImporter}><Upload size={16} /> Upload bill</button>} /> : (
                <>
                  <StatGrid className="cycle-metrics">
                    <Metric label="Usage" value={energy(cycle.usageKwh)} identity="billing.cycle_usage" />
                    <Metric label="Energy charge" value={money(cycle.energyCharge, home.currency)} identity="billing.cycle_charge" />
                    <Metric label="Projected usage" value={energy(cycle.projectedUsageKwh)} identity="billing.projected_usage" />
                    <Metric label="Projected bill" value={money(cycle.projectedBill, home.currency)} identity="billing.projected_bill" />
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

            <Surface title="Past bills" subtitle="Imported statements and calculated cycle records">
              {bills.isLoading ? <LoadingState label="Loading past bills…" /> : bills.error ? <ErrorState error={bills.error} retry={() => void bills.refetch()} /> : (bills.data?.length ?? 0) === 0 ? <EmptyState title="No past bills yet" message="Your first reviewed bill will appear here." /> : <PastBills bills={bills.data ?? []} currency={home.currency} />}
            </Surface>
          </div>
          <aside className="billing-side-column">
            <Surface title="Latest bill" subtitle="Most recent local import">
              {bills.data?.[0] ? <LatestBill bill={bills.data[0]} currency={home.currency} /> : <EmptyState title="No imported bill" message="Upload a PDF to prepare a reviewed rate plan and billing cycle." />}
            </Surface>
            <Surface title="Estimate confidence">
              <div className="confidence-card"><span>{cycle?.confidence ? statusLabel(cycle.confidence) : 'Waiting for history'}</span><p>Coverage {cycle?.coveragePercent ?? '0'}%. Estimates improve as synchronized readings fill the cycle.</p></div>
            </Surface>
          </aside>
        </div>
      )}

      {isOwner(session ?? { authenticated: false, bootstrapRequired: false }) && (
        <details className="advanced-disclosure" open={advancedOpen} onToggle={(event) => { setAdvancedOpen(event.currentTarget.open); }}>
          <summary><span><strong>Advanced Rate Settings</strong><small>Custom editor, exact components, sources, versions, evidence, and adjustments</small></span><ChevronDown /></summary>
          <AdvancedRateSettings home={home} services={services} />
        </details>
      )}

      {importOpen && <ModalLayer onRequestClose={closeImporter}><BillImportFlow home={home} services={services} onClose={closeImporter} /></ModalLayer>}
      {lifecycleAction && currentLibraryPlan && service && (
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

function ReplacePlan({ service, plans, onClose, onDone }: { service: ElectricService; plans: LibraryPlan[]; onClose: () => void; onDone: () => void }) {
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
        return request(`/api/v1/admin/rate-plans/${plan.id}/unassign`, json('POST', {
          utility_account_id: service.id,
          expected_revision: plan.lifecycleRevision,
          expected_dependency_token: dependencies.data.dependencyToken,
          effective_at: new Date(effectiveAt).toISOString(),
          reason,
          confirmation,
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
  const expectedConfirmation = action === 'unassign' ? `UNASSIGN ${plan.code}` : plan.code
  const blocked = action !== 'unassign' && dependencies.data?.removalBlocked === true
  const ready = confirmation.trim().toLocaleLowerCase() === expectedConfirmation.toLocaleLowerCase()
    && reason.trim().length >= 8
    && Boolean(dependencies.data)
    && !blocked
  const title = action === 'unassign'
    ? 'Remove plan from Electric Service'
    : action === 'retire'
      ? 'Retire rate plan'
      : 'Remove rate plan'
  return (
    <section className="workflow lifecycle-dialog" role="dialog" aria-modal="true" aria-labelledby="plan-lifecycle-title">
      <header className="workflow-header"><div><p>Dependency-aware lifecycle</p><h2 id="plan-lifecycle-title">{title}</h2></div></header>
      <div className="workflow-body">
        <InlineNotice tone="warning">
          {action === 'unassign'
            ? 'Cost calculation stops after the effective time. Historical assignments, costs, reports, bill imports, and evidence remain intact.'
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

function ordinal(value: number): string {
  const suffix = value % 10 === 1 && value % 100 !== 11 ? 'st' : value % 10 === 2 && value % 100 !== 12 ? 'nd' : value % 10 === 3 && value % 100 !== 13 ? 'rd' : 'th'
  return `${value}${suffix}`
}
