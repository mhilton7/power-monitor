import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, FileSearch, Pencil, Plus, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { errorMessage, json, request } from '../../api/client'
import {
  adaptRateAdjustments,
  adaptRateEvidence,
  adaptRatePlanDependencies,
  adaptRateAssignmentResult,
  adaptRateSourceCheckRun,
  adaptRateSourceCheckRuns,
  adaptRateSources,
  adaptRateVersions,
} from '../../api/adapters'
import { numberValue, objectList, record, stringValue } from '../../api/validation'
import { ratePlanRemovalRequest } from './lifecycle'
import { EmptyState, ErrorState, InlineNotice, LoadingState } from '../../components/feedback/States'
import { TabList } from '../../components/layout/Layout'
import type {
  ElectricService,
  Home,
  RateAdjustment,
  RateEvidence,
  RatePlanVersion,
  RateSource,
} from '../../types/models'
import { statusLabel } from '../../utils/format'
import {
  StructuredRateEditor,
  type RateRevisionComparison,
  type SavedDraft,
} from './StructuredRateEditor'
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
  removedAt?: string
  removedBy?: string
  removalReason?: string
  versions: RatePlanVersion[]
  publicationStatus: string
  assignmentStatus: string
  currentVersionId?: string
  draftVersionId?: string
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
    const versions = adaptRateVersions(objectList(item.versions))
    const latest = versions[0]
    const current = versions.find((version) => version.assignmentStatus === 'current')
    const draft = versions.find((version) => version.publicationStatus === 'draft')
    return {
      id: stringValue(item.id),
      name: stringValue(item.name, stringValue(item.plan_name, 'Rate plan')),
      code: stringValue(item.code, stringValue(item.plan_code)),
      status: stringValue(item.status, 'draft'),
      revision: Number(item.lifecycle_revision ?? item.revision ?? 1),
      pricingModel: latest?.pricingModel ?? (stringValue(item.pricing_model) || undefined),
      versionId: latest?.id ?? (stringValue(item.rate_version_id) || undefined),
      version: latest?.version ?? (Number(item.version ?? 0) || undefined),
      removedAt: stringValue(item.removed_at) || undefined,
      removedBy: stringValue(item.removed_by) || undefined,
      removalReason: stringValue(item.removal_reason) || undefined,
      versions,
      publicationStatus: latest?.publicationStatus ?? 'draft',
      assignmentStatus: current ? 'current' : latest?.assignmentStatus ?? 'unassigned',
      currentVersionId: current?.id,
      draftVersionId: draft?.id,
    }
  })
}

export function AdvancedRateSettings({
  home,
  services,
  initialView = 'plans',
}: {
  home: Home
  services: ElectricService[]
  initialView?: RateView
}) {
  const [view, setView] = useState<RateView>(initialView)
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
  const removedVersions = useQuery({
    queryKey: ['removed-rate-versions'],
    queryFn: async () => {
      const allPlans = await request(
        '/api/v1/rates/plans?status=all',
        {},
        plansAdapter,
      )
      return allPlans.flatMap((plan) =>
        plan.versions
          .filter((version) =>
            ['removed', 'retired'].includes(version.publicationStatus),
          )
          .map((version) => ({
            ...version,
            planName: plan.name,
            planCode: plan.code,
          })),
      )
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
        {view === 'plans' && <PlanManagerV2 home={home} services={services} plans={plans.data ?? []} loading={plans.isLoading} error={plans.error} />}
        {view === 'sources' && <SourceManager sources={sources.data ?? []} loading={sources.isLoading} error={sources.error} />}
        {view === 'versions' && <VersionList home={home} service={services[0]} plans={plans.data ?? []} />}
        {view === 'evidence' && <EvidenceList plans={plans.data ?? []} />}
        {view === 'removed' && <><RemovedPlans plans={removedPlans.data ?? []} loading={removedPlans.isLoading} error={removedPlans.error} /><PagedVersionList rows={removedVersions.data ?? []} /></>}
        {view === 'adjustments' && <Adjustments services={services} />}
      </div>
    </div>
  )
}

function VersionList({
  home,
  service,
  plans,
}: {
  home: Home
  service?: ElectricService
  plans: PlanRow[]
}) {
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
  return <PagedVersionList home={home} service={service} rows={versions.data ?? []} />
}

export function LegacyPagedVersionList({ rows }: { rows: Array<RatePlanVersion & { planName: string; planCode: string }> }) {
  const [query, setQuery] = useState('')
  const matching = rows.filter((row) => `${row.planName} ${row.planCode} ${row.status} ${row.version}`.toLowerCase().includes(query.trim().toLowerCase()))
  return <section className="rate-advanced-panel"><div className="section-heading"><div><h3>Rate versions</h3><p>Every effective-dated version, including immutable history.</p></div><label className="compact-search"><span className="sr-only">Search rate versions</span><input type="search" placeholder="Search versions" value={query} onChange={(event) => { setQuery(event.target.value) }} /></label></div>{matching.length === 0 ? <EmptyState compact title="No rate versions" message="Create or import a rate plan to prepare its first draft." /> : <ul className="structured-list">{matching.map((row) => <li key={row.id}><div><strong>{row.planName} · v{row.version}</strong><span>{row.planCode} · {statusLabel(row.status)} · effective {row.effectiveFrom ?? 'not set'}{row.immutable ? ' · immutable' : ' · editable draft'}</span></div><span className={`pill ${row.status === 'active' ? 'success' : ''}`}>{statusLabel(row.pricingModel ?? 'unknown')}</span></li>)}</ul>}</section>
}

function PagedVersionList({
  rows,
  home,
  service,
}: {
  rows: Array<RatePlanVersion & { planName: string; planCode: string }>
  home?: Home
  service?: ElectricService
}) {
  const client = useQueryClient()
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<
    RatePlanVersion & { planName: string; planCode: string }
  >()
  const [reason, setReason] = useState('Administrator reviewed version lifecycle')
  const [confirmation, setConfirmation] = useState('')
  const [assignmentTarget, setAssignmentTarget] = useState<
    RatePlanVersion & { planName: string; planCode: string }
  >()
  const [assignmentReason, setAssignmentReason] = useState(
    'Owner reviewed the current rate-plan version',
  )
  const [effectiveChoice, setEffectiveChoice] = useState<'now' | 'next_cycle' | 'custom'>('now')
  const [customEffective, setCustomEffective] = useState(
    new Date().toISOString().slice(0, 16),
  )
  const dependencies = useQuery({
    queryKey: ['rate-version-dependencies', selected?.id],
    queryFn: () =>
      request<Record<string, unknown>>(
        `/api/v1/rates/versions/${selected?.id ?? ''}/dependencies`,
      ),
    enabled: Boolean(selected),
  })
  const lifecycle = useMutation({
    mutationFn: async (action: 'delete' | 'remove' | 'retire' | 'restore') => {
      if (!selected) throw new Error('Choose a rate version.')
      if (action === 'restore') {
        return request(
          `/api/v1/rates/versions/${selected.id}/restore`,
          json('POST', {
            expected_revision: selected.lifecycleRevision,
            reason,
            idempotency_key: crypto.randomUUID(),
          }),
        )
      }
      const payload = {
        expected_revision: selected.lifecycleRevision,
        reason,
        confirmation,
        idempotency_key: crypto.randomUUID(),
      }
      if (action === 'delete') {
        return request(
          `/api/v1/rates/versions/${selected.id}/draft`,
          json('DELETE', payload),
        )
      }
      return request(
        `/api/v1/rates/versions/${selected.id}/${action}`,
        json('POST', payload),
      )
    },
    onSuccess: async () => {
      setSelected(undefined)
      setConfirmation('')
      await Promise.all([
        client.invalidateQueries({ queryKey: ['all-rate-versions'] }),
        client.invalidateQueries({ queryKey: ['managed-rate-plans'] }),
        client.invalidateQueries({ queryKey: ['removed-rate-plans'] }),
      ])
    },
  })
  const cancelSchedule = useMutation({
    mutationFn: (assignmentId: string) =>
      request(
        `/api/v1/rates/assignments/${assignmentId}`,
        { method: 'DELETE' },
      ),
    onSuccess: async () => {
      setSelected(undefined)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['all-rate-versions'] }),
        client.invalidateQueries({ queryKey: ['managed-rate-plans'] }),
      ])
    },
  })
  const makeCurrent = useMutation({
    mutationFn: (version: RatePlanVersion & { planName: string; planCode: string }) => {
      if (!service) throw new Error('Create an electric service before assigning a plan.')
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
          rate_version_id: version.id,
          effective_from: effectiveFrom,
          effective_to: null,
          assignment_reason: assignmentReason,
          replace_current: true,
          confirmation: 'REPLACE CURRENT',
          idempotency_key: crypto.randomUUID(),
          expected_account_revision: service.revision,
          expected_current_assignment_revision: service.currentAssignmentRevision,
        }),
        adaptRateAssignmentResult,
      )
    },
    onSuccess: async (result, version) => {
      if (home) {
        client.setQueryData<ElectricService[]>(
          ['electric-services', home.id],
          (current) => current?.map((item) => item.id === result.electricServiceId
            ? {
                ...item,
                revision: result.serviceRevision,
                currentPlan: version.planName,
                planCode: version.planCode,
                rateVersionId: version.id,
                currentVersion: version.version,
                readiness: { ...item.readiness, rate: result.state === 'current' ? 'rate_configured_effective' : 'rate_not_yet_effective' },
              }
            : item),
        )
      }
      setAssignmentTarget(undefined)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['all-rate-versions'] }),
        client.invalidateQueries({ queryKey: ['managed-rate-plans'] }),
        client.invalidateQueries({ queryKey: ['electric-services'] }),
        client.invalidateQueries({ queryKey: ['home-summary'] }),
        client.invalidateQueries({ queryKey: ['billing-cycle-summary'] }),
        client.invalidateQueries({ queryKey: ['history'] }),
        client.invalidateQueries({ queryKey: ['configuration-status'] }),
        client.invalidateQueries({ queryKey: ['current-rate-assignment'] }),
      ])
      if (home) {
        await client.refetchQueries({ queryKey: ['electric-services', home.id], exact: true })
      }
    },
  })
  const matching = rows.filter((row) =>
    `${row.planName} ${row.planCode} ${row.publicationStatus} ${row.assignmentStatus} ${row.version}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  )
  const dependency = dependencies.data
  const currentOrFuture = selected?.assignments.some((assignment) =>
    ['current', 'scheduled'].includes(assignment.state),
  )
  return (
    <section className="rate-advanced-panel">
      <div className="section-heading">
        <div>
          <h3>Rate versions</h3>
          <p>
            Publication and effective assignment are independent lifecycle
            states.
          </p>
        </div>
        <label className="compact-search">
          <span className="sr-only">Search rate versions</span>
          <input
            type="search"
            placeholder="Search versions"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
            }}
          />
        </label>
      </div>
      {assignmentTarget && service && (
        <section className="plan-lifecycle-panel" aria-label="Make rate version current">
          <div className="section-heading">
            <div><h4>Make {assignmentTarget.planName} v{assignmentTarget.version} current</h4><p>The previous effective assignment and finalized costs remain in history.</p></div>
            <button type="button" className="button ghost compact" onClick={() => { setAssignmentTarget(undefined); makeCurrent.reset() }}>Cancel</button>
          </div>
          <div className="form-grid">
            <label><span>Effective timing</span><select value={effectiveChoice} onChange={(event) => { setEffectiveChoice(event.target.value as typeof effectiveChoice) }}><option value="now">Now</option><option value="next_cycle" disabled={!service.billingEndsAt}>Next billing cycle</option><option value="custom">Custom date and time</option></select></label>
            {effectiveChoice === 'custom' && <label><span>Effective from</span><input type="datetime-local" value={customEffective} onChange={(event) => { setCustomEffective(event.target.value) }} /></label>}
            <label className="wide"><span>Reason</span><input value={assignmentReason} onChange={(event) => { setAssignmentReason(event.target.value) }} /></label>
          </div>
          <button type="button" className="button primary" disabled={makeCurrent.isPending || assignmentReason.trim().length < 8} onClick={() => { makeCurrent.mutate(assignmentTarget) }}>{makeCurrent.isPending ? 'Applying…' : service.currentPlan ? 'Replace current' : 'Make current'}</button>
        </section>
      )}
      {selected && (
        <section className="plan-lifecycle-panel">
          <div className="section-heading">
            <div>
              <h4>
                {selected.planName} · v{selected.version}
              </h4>
              <p>
                Current and scheduled versions must be replaced or ended before
                removal. Historical references remain intact.
              </p>
            </div>
            <button
              type="button"
              className="button ghost compact"
              onClick={() => {
                setSelected(undefined)
              }}
            >
              Cancel
            </button>
          </div>
          {dependencies.isLoading ? (
            <LoadingState label="Reviewing version dependencies…" />
          ) : dependencies.error ? (
            <ErrorState error={dependencies.error} />
          ) : (
            <>
              <div className="dependency-summary">
                <span>
                  <small>Publication</small>
                  <strong>{statusLabel(selected.publicationStatus)}</strong>
                </span>
                <span>
                  <small>Assignment</small>
                  <strong>{statusLabel(selected.assignmentStatus)}</strong>
                </span>
                <span>
                  <small>Historical assignments</small>
                  <strong>
                    {numberValue(dependency?.historical_assignment_count)}
                  </strong>
                </span>
              </div>
              {!['removed', 'retired'].includes(
                selected.publicationStatus,
              ) && (
                <div className="form-grid">
                  <label>
                    <span>Reason</span>
                    <input
                      value={reason}
                      onChange={(event) => {
                        setReason(event.target.value)
                      }}
                    />
                  </label>
                  <label>
                    <span>Type version {selected.version}</span>
                    <input
                      value={confirmation}
                      onChange={(event) => {
                        setConfirmation(event.target.value)
                      }}
                    />
                  </label>
                </div>
              )}
              {currentOrFuture && (
                <InlineNotice tone="warning">
                  Replace, end, or cancel its effective assignment before
                  changing this version.
                </InlineNotice>
              )}
              {selected.assignments
                .filter((assignment) => assignment.state === 'scheduled')
                .map((assignment) => (
                  <div className="assignment-lifecycle-row" key={assignment.id}>
                    <div>
                      <strong>Scheduled assignment</strong>
                      <span>
                        Effective {assignment.effectiveFrom}; cancellation
                        preserves the schedule in audit history.
                      </span>
                    </div>
                    <button
                      type="button"
                      className="button danger compact"
                      disabled={cancelSchedule.isPending}
                      onClick={() => {
                        cancelSchedule.mutate(assignment.id)
                      }}
                    >
                      Cancel schedule
                    </button>
                  </div>
                ))}
              <div className="inline-actions">
                {['removed', 'retired'].includes(
                  selected.publicationStatus,
                ) ? (
                  <button
                    type="button"
                    className="button secondary"
                    disabled={lifecycle.isPending || reason.trim().length < 3}
                    onClick={() => {
                      lifecycle.mutate('restore')
                    }}
                  >
                    Restore version
                  </button>
                ) : selected.publicationStatus === 'draft' &&
                  dependency?.delete_draft_eligible === true ? (
                  <button
                    type="button"
                    className="button danger"
                    disabled={
                      lifecycle.isPending ||
                      confirmation !== String(selected.version)
                    }
                    onClick={() => {
                      lifecycle.mutate('delete')
                    }}
                  >
                    Delete unused draft
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      className="button secondary"
                      disabled={
                        lifecycle.isPending ||
                        Boolean(currentOrFuture) ||
                        confirmation !== String(selected.version)
                      }
                      onClick={() => {
                        lifecycle.mutate('retire')
                      }}
                    >
                      Retire
                    </button>
                    <button
                      type="button"
                      className="button danger"
                      disabled={
                        lifecycle.isPending ||
                        Boolean(currentOrFuture) ||
                        confirmation !== String(selected.version)
                      }
                      onClick={() => {
                        lifecycle.mutate('remove')
                      }}
                    >
                      Remove
                    </button>
                  </>
                )}
              </div>
            </>
          )}
        </section>
      )}
      {matching.length === 0 ? (
        <EmptyState
          compact
          title="No rate versions"
          message="Create or import a rate plan to prepare its first draft."
        />
      ) : (
        <ul className="structured-list">
          {matching.map((row) => (
            <li key={row.id}>
              <div>
                <strong>
                  {row.planName} · v{row.version}
                </strong>
                <span>
                  {row.planCode} · {statusLabel(row.publicationStatus)} ·{' '}
                  {statusLabel(row.assignmentStatus)} · effective{' '}
                  {row.effectiveFrom ?? 'not set'}
                  {row.immutable ? ' · immutable' : ' · editable draft'}
                </span>
              </div>
              <div className="inline-actions">
                {row.publicationStatus === 'published' && row.assignmentStatus !== 'current' && (
                  <button type="button" className="button primary compact" disabled={!service} onClick={() => { setAssignmentTarget(row); makeCurrent.reset() }}>
                    {service?.currentPlan ? 'Replace current' : 'Make current'}
                  </button>
                )}
                <button
                  type="button"
                  className="button ghost compact"
                  onClick={() => {
                    setSelected(row)
                    setConfirmation('')
                  }}
                >
                  Lifecycle
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {(lifecycle.error || cancelSchedule.error || makeCurrent.error) && (
        <InlineNotice tone="danger">
          {errorMessage(lifecycle.error ?? cancelSchedule.error ?? makeCurrent.error)}
        </InlineNotice>
      )}
    </section>
  )
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
    mutationFn: ({ plan, dependencyToken }: { plan: PlanRow; dependencyToken: string }) => request(`/api/v1/admin/rate-plans/${plan.id}/restore`, json('POST', {
      expected_revision: plan.revision,
      expected_dependency_token: dependencyToken,
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
        : <ul className="structured-list">{plans.map((plan) => <RemovedPlanRecord key={plan.id} plan={plan} pending={restore.isPending} onRestore={(dependencyToken) => { restore.mutate({ plan, dependencyToken }) }} />)}</ul>}
      {restore.error && <InlineNotice tone="danger">{errorMessage(restore.error)}</InlineNotice>}
    </section>
  )
}

function RemovedPlanRecord({ plan, pending, onRestore }: { plan: PlanRow; pending: boolean; onRestore: (dependencyToken: string) => void }) {
  const dependencies = useQuery({
    queryKey: ['removed-rate-plan-dependencies', plan.id],
    queryFn: () => request(`/api/v1/admin/rate-plans/${plan.id}/dependencies`, {}, adaptRatePlanDependencies),
  })
  return <li><div><strong>{plan.name}</strong><span>{statusLabel(plan.status)}{plan.removedAt ? ` · removed ${plan.removedAt}` : ''}{plan.removedBy ? ` · actor ${plan.removedBy}` : ''}</span><small>{plan.removalReason ?? 'Lifecycle reason retained in the audit record'} · {dependencies.data?.historicalAssignmentCount ?? 0} historical assignments · {dependencies.data?.historicalCalculationCount ?? 0} cost calculations · assignments are not restored automatically</small></div><button type="button" className="button secondary compact" data-canonical-action="rate_plan.restore" disabled={pending || dependencies.isLoading || !dependencies.data?.dependencyToken} onClick={() => { if (dependencies.data?.dependencyToken) onRestore(dependencies.data.dependencyToken) }}>Restore</button></li>
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

export function LegacyPlanManager({ home, services, plans, loading, error }: { home: Home; services: ElectricService[]; plans: PlanRow[]; loading: boolean; error: unknown }) {
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
    queryFn: () => request(`/api/v1/admin/rate-plans/${lifecycleTarget?.id ?? ''}/dependencies`, {}, adaptRatePlanDependencies),
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
      const review = dependencies.data
      if (!review) throw new Error('Dependency review is still loading.')
      const deleteDraft = review.permanentDraftDeletionEligible
      const lifecycle = ratePlanRemovalRequest({
        planId: plan.id,
        expectedRevision: plan.revision,
        dependencyToken: review.dependencyToken,
        confirmation: lifecycleConfirmation,
        reason: lifecycleReason,
        permanentDraftDeletion: deleteDraft,
      })
      return request(lifecycle.path, json(lifecycle.method, lifecycle.payload))
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
  const removalBlocked = dependencies.data?.removalBlocked === true
  const removalReady = Boolean(
    lifecycleTarget
    && lifecycleConfirmation.trim().toLocaleLowerCase() === lifecycleTarget.code.trim().toLocaleLowerCase()
    && lifecycleReason.trim().length >= 8
    && Boolean(dependencies.data)
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
                <span><small>Permanent draft deletion</small><strong>{dependencies.data?.permanentDraftDeletionEligible === true ? 'Eligible' : 'Not eligible'}</strong></span>
                <span><small>Removal</small><strong>{removalBlocked ? 'Blocked by active assignments' : 'Available after confirmation'}</strong></span>
                <span><small>History</small><strong>Versions and evidence preserved</strong></span>
              </div>
              <div className="form-grid">
                <label><span>Reason</span><input value={lifecycleReason} onChange={(event) => { setLifecycleReason(event.target.value) }} /></label>
                <label><span>Type {lifecycleTarget.code} to confirm removal</span><input value={lifecycleConfirmation} onChange={(event) => { setLifecycleConfirmation(event.target.value) }} /></label>
              </div>
              {removalBlocked && <InlineNotice tone="warning">Replace or end active and future assignments before removing this plan.</InlineNotice>}
              <div className="inline-actions">
                {lifecycleTarget.versionId && !['draft', 'retired'].includes(lifecycleTarget.status) && <button type="button" className="button secondary compact" data-canonical-action="rate_plan.retire" disabled={retire.isPending} onClick={() => { retire.mutate(lifecycleTarget) }}><Archive size={15} /> {retire.isPending ? 'Retiring…' : 'Retire version'}</button>}
                <button type="button" className="button danger compact" data-canonical-action={dependencies.data?.permanentDraftDeletionEligible === true ? 'rate_plan.delete_draft' : 'rate_plan.remove'} disabled={!removalReady || remove.isPending} onClick={() => { remove.mutate(lifecycleTarget) }}>{remove.isPending ? 'Removing…' : dependencies.data?.permanentDraftDeletionEligible === true ? 'Delete unused draft' : 'Remove plan'}</button>
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

function PlanManagerV2({
  home,
  services,
  plans,
  loading,
  error,
}: {
  home: Home
  services: ElectricService[]
  plans: PlanRow[]
  loading: boolean
  error: unknown
}) {
  const client = useQueryClient()
  const service = services[0]
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState<PlanRow>()
  const [assignmentTarget, setAssignmentTarget] = useState<PlanRow>()
  const [lifecycleTarget, setLifecycleTarget] = useState<PlanRow>()
  const [effectiveChoice, setEffectiveChoice] = useState<'now' | 'next_cycle' | 'custom'>('now')
  const [customEffective, setCustomEffective] = useState(
    new Date().toISOString().slice(0, 16),
  )
  const [assignmentReason, setAssignmentReason] = useState(
    'Owner reviewed the effective rate-plan change',
  )
  const [lifecycleReason, setLifecycleReason] = useState(
    'Administrator reviewed rate-plan lifecycle',
  )
  const [lifecycleConfirmation, setLifecycleConfirmation] = useState('')
  const currentPlan = plans.find(
    (plan) =>
      plan.assignmentStatus === 'current' ||
      plan.versions.some((version) => version.id === service?.rateVersionId),
  )
  const editorVersionId = target?.draftVersionId ?? target?.versionId
  const editorDraft = useQuery({
    queryKey: ['rate-editor-version', editorVersionId],
    queryFn: () =>
      request(
        `/api/v1/rates/versions/${editorVersionId ?? ''}`,
        {},
        adaptRatePlanDraft,
      ),
    enabled: open && Boolean(editorVersionId),
  })
  const comparisonVersion = target?.versions
    .filter(
      (version) =>
        version.id !== editorVersionId &&
        ['published', 'superseded'].includes(version.publicationStatus),
    )
    .sort((first, second) => second.version - first.version)[0]
  const comparisonDraft = useQuery({
    queryKey: ['rate-editor-comparison-version', comparisonVersion?.id],
    queryFn: () =>
      request(
        `/api/v1/rates/versions/${comparisonVersion?.id ?? ''}`,
        {},
        adaptRatePlanDraft,
      ),
    enabled: open && Boolean(target?.draftVersionId && comparisonVersion?.id),
  })
  const dependencies = useQuery({
    queryKey: ['rate-plan-dependencies', lifecycleTarget?.id],
    queryFn: () =>
      request(
        `/api/v1/admin/rate-plans/${lifecycleTarget?.id ?? ''}/dependencies`,
        {},
        adaptRatePlanDependencies,
      ),
    enabled: Boolean(lifecycleTarget),
  })
  const conflicts = useQuery({
    queryKey: ['rate-assignment-conflicts'],
    queryFn: async () =>
      objectList(
        record(await request('/api/v1/rates/assignments/conflicts')).conflicts,
      ),
  })
  const newVersion = useMutation({
    mutationFn: async (plan: PlanRow) => {
      const response = record(
        await request(
          `/api/v1/rates/plans/${plan.id}/versions`,
          json('POST'),
        ),
        'adjusted rate version',
      )
      const versionId = stringValue(response.id)
      if (!versionId) {
        throw new Error('The server did not return the editable draft revision.')
      }
      return {
        ...plan,
        versionId,
        draftVersionId: versionId,
        version: Number(response.version ?? (plan.version ?? 0) + 1),
        publicationStatus: 'draft',
      }
    },
    onSuccess: (plan) => {
      setTarget(plan)
      setOpen(true)
      void client.invalidateQueries({ queryKey: ['managed-rate-plans'] })
    },
  })
  const replace = useMutation({
    mutationFn: (plan: PlanRow) => {
      if (!service) {
        throw new Error(
          'Create an electric service before choosing a current plan.',
        )
      }
      const version = publishedVersion(plan)
      if (!version) {
        throw new Error('Publish a version before making this plan current.')
      }
      const effectiveFrom =
        effectiveChoice === 'now'
          ? new Date().toISOString()
          : effectiveChoice === 'next_cycle'
            ? service.billingEndsAt
            : new Date(customEffective).toISOString()
      if (!effectiveFrom) {
        throw new Error('The next billing-cycle boundary is unavailable.')
      }
      return request(
        '/api/v1/rates/assignments/replace',
        json('POST', {
          utility_account_id: service.id,
          rate_version_id: version.id,
          effective_from: effectiveFrom,
          effective_to: null,
          replace_current: true,
          assignment_reason: assignmentReason,
          idempotency_key: crypto.randomUUID(),
          confirmation: 'REPLACE CURRENT',
          expected_account_revision: service.revision,
          expected_current_assignment_revision: service.currentAssignmentRevision,
        }),
        adaptRateAssignmentResult,
      )
    },
    onSuccess: async (result, plan) => {
      const selectedVersion = publishedVersion(plan)
      client.setQueryData<ElectricService[]>(
        ['electric-services', home.id],
        (current) => current?.map((item) => item.id === result.electricServiceId
          ? {
              ...item,
              revision: result.serviceRevision,
              currentPlan: plan.name,
              planCode: plan.code,
              rateVersionId: result.versionId,
              currentVersion: selectedVersion?.version ?? result.version,
              readiness: { ...item.readiness, rate: result.state === 'current' ? 'rate_configured_effective' : 'rate_not_yet_effective' },
            }
          : item),
      )
      setAssignmentTarget(undefined)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['managed-rate-plans'] }),
        client.invalidateQueries({ queryKey: ['electric-services'] }),
        client.invalidateQueries({ queryKey: ['rate-assignment-conflicts'] }),
        client.invalidateQueries({ queryKey: ['home-summary'] }),
        client.invalidateQueries({ queryKey: ['billing-cycle-summary'] }),
        client.invalidateQueries({ queryKey: ['history'] }),
        client.invalidateQueries({ queryKey: ['configuration-status'] }),
        client.invalidateQueries({ queryKey: ['current-rate-assignment'] }),
      ])
      await client.refetchQueries({ queryKey: ['electric-services', home.id], exact: true })
    },
  })
  const repair = useMutation({
    mutationFn: ({
      conflict,
      keepId,
    }: {
      conflict: Record<string, unknown>
      keepId: string
    }) => {
      const assignments = objectList(conflict.assignments)
      return request(
        '/api/v1/rates/assignments/conflicts/resolve',
        json('POST', {
          utility_account_id: stringValue(conflict.utility_account_id),
          keep_assignment_id: keepId,
          expected_assignment_ids: assignments.map((item) =>
            stringValue(item.assignment_id),
          ),
          reason: 'Administrator selected the authoritative current assignment',
          confirmation: 'REPAIR ASSIGNMENTS',
          idempotency_key: crypto.randomUUID(),
        }),
      )
    },
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['rate-assignment-conflicts'] }),
        client.invalidateQueries({ queryKey: ['managed-rate-plans'] }),
        client.invalidateQueries({ queryKey: ['electric-services'] }),
      ])
    },
  })
  const remove = useMutation({
    mutationFn: (plan: PlanRow) => {
      const review = dependencies.data
      if (!review) throw new Error('Dependency review is still loading.')
      const lifecycle = ratePlanRemovalRequest({
        planId: plan.id,
        expectedRevision: plan.revision,
        dependencyToken: review.dependencyToken,
        confirmation: lifecycleConfirmation,
        reason: lifecycleReason,
        permanentDraftDeletion: review.permanentDraftDeletionEligible,
      })
      return request(lifecycle.path, json(lifecycle.method, lifecycle.payload))
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
  const removalBlocked = dependencies.data?.removalBlocked === true
  const removalReady = Boolean(
    lifecycleTarget &&
      lifecycleConfirmation.trim().toLocaleLowerCase() ===
        lifecycleTarget.code.trim().toLocaleLowerCase() &&
      lifecycleReason.trim().length >= 8 &&
      dependencies.data &&
      !removalBlocked,
  )

  return (
    <section className="rate-advanced-panel">
      <div className="section-heading">
        <div>
          <h3>Rate plans</h3>
          <p>
            Published means available. Current means the one effective version
            for this Electric Service now.
          </p>
        </div>
        <button
          type="button"
          className="button secondary"
          onClick={() => {
            replace.reset()
            setTarget(undefined)
            setOpen(!open)
          }}
        >
          <Plus size={16} /> {open && !target ? 'Hide editor' : 'New plan'}
        </button>
      </div>

      {(conflicts.data?.length ?? 0) > 0 && (
        <section
          className="plan-lifecycle-panel"
          aria-label="Repair conflicting rate assignments"
        >
          <h4>Assignment repair required</h4>
          <p>
            Existing data contains overlapping current or future assignments.
            Select the authoritative assignment; every row and historical cost
            remains preserved.
          </p>
          {conflicts.data?.map((conflict) => (
            <div
              key={stringValue(conflict.utility_account_id)}
              className="inline-actions"
            >
              {objectList(conflict.assignments).map((assignment) => (
                <button
                  key={stringValue(assignment.assignment_id)}
                  type="button"
                  className="button secondary"
                  disabled={repair.isPending}
                  onClick={() => {
                    repair.mutate({
                      conflict,
                      keepId: stringValue(assignment.assignment_id),
                    })
                  }}
                >
                  Keep{' '}
                  {stringValue(
                    assignment.plan_name,
                    stringValue(assignment.plan_code),
                  )}{' '}
                  v{numberValue(assignment.version)}
                </button>
              ))}
            </div>
          ))}
        </section>
      )}

      {open &&
        (!target ? (
          <StructuredRateEditor
            home={home}
            services={services}
            onClose={() => {
              setOpen(false)
            }}
          />
        ) : editorDraft.isLoading ? (
          <LoadingState label="Opening editable rate version…" />
        ) : editorDraft.error ? (
          <ErrorState
            error={editorDraft.error}
            retry={() => {
              void editorDraft.refetch()
            }}
          />
        ) : editorDraft.data ? (
          <StructuredRateEditor
            key={editorVersionId}
            home={home}
            services={services}
            initialDraft={editorDraft.data}
            initialSaved={
              {
                planId: target.id,
                versionId: editorVersionId ?? '',
                status: target.publicationStatus,
              } satisfies SavedDraft
            }
            comparison={
              comparisonDraft.data
                ? ({
                    currentLabel: `Published v${comparisonVersion?.version ?? ''}`,
                    proposedLabel: `Draft v${target.version ?? ''}`,
                    current: comparisonDraft.data,
                  } satisfies RateRevisionComparison)
                : undefined
            }
            onClose={() => {
              setOpen(false)
              setTarget(undefined)
            }}
          />
        ) : null)}

      {assignmentTarget && service && (
        <section
          className="plan-lifecycle-panel"
          aria-label={`${currentPlan ? 'Replace' : 'Make'} current plan`}
        >
          <div className="section-heading">
            <div>
              <h4>{currentPlan ? 'Replace current plan' : 'Make plan current'}</h4>
              <p>
                {currentPlan
                  ? `${currentPlan.name} remains in assignment and cost history.`
                  : 'This creates the first effective assignment.'}
              </p>
            </div>
            <button
              type="button"
              className="button ghost compact"
              onClick={() => {
                setAssignmentTarget(undefined)
                replace.reset()
              }}
            >
              Cancel
            </button>
          </div>
          <div className="dependency-summary">
            <span>
              <small>Current</small>
              <strong>{currentPlan?.name ?? 'None'}</strong>
            </span>
            <span>
              <small>New selection</small>
              <strong>{assignmentTarget.name}</strong>
            </span>
            <span>
              <small>Historical costs</small>
              <strong>Preserved</strong>
            </span>
          </div>
          <div className="form-grid">
            <label>
              <span>Effective timing</span>
              <select
                value={effectiveChoice}
                onChange={(event) => {
                  setEffectiveChoice(event.target.value as typeof effectiveChoice)
                }}
              >
                <option value="now">Now</option>
                <option value="next_cycle" disabled={!service.billingEndsAt}>
                  Next billing cycle
                </option>
                <option value="custom">Custom date and time</option>
              </select>
            </label>
            {effectiveChoice === 'custom' && (
              <label>
                <span>Effective from</span>
                <input
                  type="datetime-local"
                  value={customEffective}
                  onChange={(event) => {
                    setCustomEffective(event.target.value)
                  }}
                />
              </label>
            )}
            <label className="wide">
              <span>Reason</span>
              <input
                value={assignmentReason}
                onChange={(event) => {
                  setAssignmentReason(event.target.value)
                }}
                required
              />
            </label>
          </div>
          <InlineNotice tone="warning">
            This changes future estimates from the effective boundary and queues
            only unfinalized cost recalculation. Finalized history is not
            rewritten.
          </InlineNotice>
          <button
            type="button"
            className="button primary"
            disabled={replace.isPending || assignmentReason.trim().length < 8}
            onClick={() => {
              replace.mutate(assignmentTarget)
            }}
          >
            {replace.isPending
              ? 'Applying…'
              : currentPlan
                ? 'Replace current'
                : 'Make current'}
          </button>
        </section>
      )}

      {lifecycleTarget && (
        <section
          className="plan-lifecycle-panel"
          aria-label={`Lifecycle controls for ${lifecycleTarget.name}`}
        >
          <div className="section-heading">
            <div>
              <h4>Remove {lifecycleTarget.name}</h4>
              <p>
                The dependency review preserves versions, assignments, costs,
                evidence, and audit records.
              </p>
            </div>
            <button
              type="button"
              className="button ghost compact"
              onClick={() => {
                setLifecycleTarget(undefined)
                setLifecycleConfirmation('')
              }}
            >
              Cancel
            </button>
          </div>
          {dependencies.isLoading ? (
            <LoadingState label="Reviewing plan dependencies…" />
          ) : dependencies.error ? (
            <ErrorState
              error={dependencies.error}
              retry={() => {
                void dependencies.refetch()
              }}
            />
          ) : (
            <>
              <div className="dependency-summary">
                <span>
                  <small>Unused draft deletion</small>
                  <strong>
                    {dependencies.data?.permanentDraftDeletionEligible
                      ? 'Available'
                      : 'Not eligible'}
                  </strong>
                </span>
                <span>
                  <small>Removal</small>
                  <strong>
                    {removalBlocked ? 'Blocked by assignments' : 'Available'}
                  </strong>
                </span>
                <span>
                  <small>History</small>
                  <strong>Preserved</strong>
                </span>
              </div>
              <div className="form-grid">
                <label>
                  <span>Reason</span>
                  <input
                    value={lifecycleReason}
                    onChange={(event) => {
                      setLifecycleReason(event.target.value)
                    }}
                  />
                </label>
                <label>
                  <span>Type {lifecycleTarget.code}</span>
                  <input
                    value={lifecycleConfirmation}
                    onChange={(event) => {
                      setLifecycleConfirmation(event.target.value)
                    }}
                  />
                </label>
              </div>
              {removalBlocked && (
                <InlineNotice tone="warning">
                  Replace or end current and future assignments before removing
                  this plan.
                </InlineNotice>
              )}
              <button
                type="button"
                className="button danger compact"
                disabled={!removalReady || remove.isPending}
                onClick={() => {
                  remove.mutate(lifecycleTarget)
                }}
              >
                {remove.isPending
                  ? 'Removing…'
                  : dependencies.data?.permanentDraftDeletionEligible
                    ? 'Delete unused draft'
                    : 'Remove plan'}
              </button>
            </>
          )}
        </section>
      )}

      {plans.length === 0 ? (
        <EmptyState
          compact
          title="No rate plans"
          message="Create a custom plan or upload an electric bill."
        />
      ) : (
        <ul className="structured-list rate-plan-library">
          {plans.map((plan) => {
            const published = publishedVersion(plan)
            const assigned = plan.versions.find(
              (version) => version.assignmentStatus === 'current',
            )
            return (
              <li key={plan.id}>
                <div>
                  <strong>{plan.name}</strong>
                  <span>
                    {plan.code} · {statusLabel(plan.pricingModel ?? 'unknown')} ·{' '}
                    {published
                      ? `Published v${published.version}`
                      : assigned
                        ? `Current v${assigned.version} (${statusLabel(assigned.publicationStatus)})`
                        : 'Draft only'}{' '}
                    · {assigned ? `Current v${assigned.version}` : 'Not current'}
                  </span>
                </div>
                <div className="inline-actions">
                  {plan.draftVersionId ? (
                    <button
                      type="button"
                      className="button ghost compact"
                      onClick={() => {
                        setTarget({ ...plan, versionId: plan.draftVersionId })
                        setOpen(true)
                      }}
                    >
                      <Pencil size={15} /> Edit draft
                    </button>
                  ) : (
                    (published ?? assigned) && (
                      <button
                        type="button"
                        className="button ghost compact"
                        disabled={newVersion.isPending}
                        onClick={() => {
                          replace.reset()
                          newVersion.mutate(plan)
                        }}
                      >
                        <Pencil size={15} /> Adjust rates
                      </button>
                    )
                  )}
                  {published && published.id !== assigned?.id && (
                    <button
                      type="button"
                      className="button primary compact"
                      disabled={!service || Boolean(conflicts.data?.length)}
                      onClick={() => {
                        setAssignmentTarget(plan)
                      }}
                    >
                      {currentPlan ? 'Replace current' : 'Make current'}
                    </button>
                  )}
                  <button
                    type="button"
                    className="button ghost compact"
                    onClick={() => {
                      setLifecycleTarget(plan)
                      setLifecycleConfirmation('')
                    }}
                  >
                    Lifecycle
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}
      {(newVersion.error || replace.error || repair.error || remove.error) && (
        <InlineNotice tone="danger">
          {errorMessage(
            newVersion.error ?? replace.error ?? repair.error ?? remove.error,
          )}
        </InlineNotice>
      )}
    </section>
  )
}

function publishedVersion(plan: PlanRow): RatePlanVersion | undefined {
  return [...plan.versions]
    .filter((version) => version.publicationStatus === 'published')
    .sort((first, second) => second.version - first.version)[0]
}

function SourceManager({
  sources,
  loading,
  error,
}: {
  sources: RateSource[]
  loading: boolean
  error: unknown
}) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [parser, setParser] = useState('sce_public_tou_html_v1')
  const [effective, setEffective] = useState(
    new Date().toISOString().slice(0, 10),
  )
  const [activeJobId, setActiveJobId] = useState<string>()
  const [checkRequested, setCheckRequested] = useState(false)
  const create = useMutation({
    mutationFn: () =>
      request(
        '/api/v1/admin/rate-sources',
        json('POST', {
          name,
          url,
          parser_id: parser,
          effective_from:
            parser === 'sce_public_tou_html_v1' ? effective : undefined,
        }),
      ),
    onSuccess: async () => {
      setName('')
      setUrl('')
      setOpen(false)
      await client.invalidateQueries({ queryKey: ['rate-sources'] })
    },
  })
  const check = useMutation({
    mutationFn: async (sourceId: string | null) => {
      const init = json('POST')
      init.headers = { 'Idempotency-Key': crypto.randomUUID() }
      const response = record(
        await request(
          sourceId
            ? `/api/v1/admin/rate-sources/${sourceId}/check`
            : '/api/v1/admin/rate-sources/check-now',
          init,
        ),
        'rate source check',
      )
      return stringValue(response.job_id)
    },
    onSuccess: (jobId) => {
      setActiveJobId(jobId)
      setCheckRequested(false)
    },
    onError: () => {
      setCheckRequested(false)
    },
  })
  const activeRun = useQuery({
    queryKey: ['rate-source-check-run', activeJobId],
    queryFn: () =>
      request(
        `/api/v1/admin/rate-sources/check-runs/${activeJobId ?? ''}`,
        {},
        adaptRateSourceCheckRun,
      ),
    enabled: Boolean(activeJobId),
    refetchInterval: (query) => {
      const data = query.state.data
      return data && ['queued', 'running'].includes(data.status) ? 1000 : false
    },
  })
  const history = useQuery({
    queryKey: ['rate-source-check-runs'],
    queryFn: () =>
      request(
        '/api/v1/admin/rate-sources/check-runs',
        {},
        adaptRateSourceCheckRuns,
      ),
  })
  const terminal = activeRun.data?.status
  useEffect(() => {
    if (!terminal || ['queued', 'running'].includes(terminal)) return
    void Promise.all([
      client.invalidateQueries({ queryKey: ['rate-sources'] }),
      client.invalidateQueries({ queryKey: ['rate-source-check-runs'] }),
      client.invalidateQueries({ queryKey: ['managed-rate-plans'] }),
      client.invalidateQueries({ queryKey: ['all-rate-versions'] }),
      client.invalidateQueries({ queryKey: ['all-rate-evidence'] }),
    ])
  }, [client, terminal])

  if (loading) return <LoadingState label="Loading approved sources…" />
  if (error) return <ErrorState error={error} />
  const run = activeRun.data
  const busy =
    checkRequested ||
    check.isPending ||
    Boolean(
      activeJobId &&
        (activeRun.isLoading ||
          !run ||
          ['queued', 'running'].includes(run.status)),
    )
  return (
    <section className="rate-advanced-panel">
      <div className="section-heading">
        <div>
          <h3>Managed rate sources</h3>
          <p>
            Approved HTTPS sources are fetched, archived, parsed, compared, and
            audited before any candidate can be reviewed.
          </p>
        </div>
        <div className="inline-actions">
          <button
            type="button"
            className="button secondary"
            aria-label="Check rate sources now"
            onClick={() => {
              setCheckRequested(true)
              setActiveJobId(undefined)
              check.mutate(null)
            }}
            disabled={busy}
          >
            <RefreshCw size={16} className={busy ? 'spin' : undefined} />{' '}
            {busy ? 'Checking…' : 'Check now'}
          </button>
          <button
            type="button"
            className="button secondary"
            onClick={() => {
              setOpen(!open)
            }}
          >
            <Plus size={16} /> Add source
          </button>
        </div>
      </div>
      {open && (
        <form
          className="structured-editor"
          onSubmit={(event) => {
            event.preventDefault()
            create.mutate()
          }}
        >
          <div className="form-grid">
            <label>
              <span>Name</span>
              <input
                value={name}
                onChange={(event) => {
                  setName(event.target.value)
                }}
                required
                minLength={3}
              />
            </label>
            <label>
              <span>Approved HTTPS URL</span>
              <input
                type="url"
                value={url}
                onChange={(event) => {
                  setUrl(event.target.value)
                }}
                required
              />
            </label>
            <label>
              <span>Source type</span>
              <select
                value={parser}
                onChange={(event) => {
                  setParser(event.target.value)
                }}
              >
                <option value="sce_public_tou_html_v1">
                  SCE public TOU page
                </option>
                <option value="sce_tariff_pdf_v1">SCE tariff PDF</option>
              </select>
            </label>
            {parser === 'sce_public_tou_html_v1' && (
              <label>
                <span>Effective date</span>
                <input
                  type="date"
                  value={effective}
                  onChange={(event) => {
                    setEffective(event.target.value)
                  }}
                />
              </label>
            )}
          </div>
          {create.error && (
            <p className="form-error" role="alert">
              {errorMessage(create.error)}
            </p>
          )}
          <div className="form-actions">
            <button className="button primary" disabled={create.isPending}>
              {create.isPending ? 'Adding…' : 'Add approved source'}
            </button>
          </div>
        </form>
      )}

      {run && (
        <section className="source-check-progress" aria-live="polite">
          <div className="section-heading">
            <div>
              <h4>
                {['queued', 'running'].includes(run.status)
                  ? 'Source check in progress'
                  : run.status === 'succeeded'
                    ? 'Source check completed'
                    : 'Source check needs attention'}
              </h4>
              <p>
                {run.progress.completed} of {run.progress.total} sources ·{' '}
                {run.candidates} candidates · {run.archivedEvidence} archived
                artifacts
              </p>
            </div>
            <span
              className={`pill ${run.status === 'succeeded' ? 'success' : ''}`}
            >
              {statusLabel(run.status)}
            </span>
          </div>
          <progress
            max={Math.max(run.progress.total, 1)}
            value={run.progress.completed}
          />
          {run.items.map((item) => (
            <div key={item.checkId} className="source-result-row">
              <span>
                <strong>{item.sourceName}</strong>
                <small>
                  {statusLabel(item.outcome)}
                  {item.httpStatus ? ` · HTTP ${item.httpStatus}` : ''}
                  {item.candidateCount
                    ? ` · ${item.candidateCount} candidate(s)`
                    : ''}
                </small>
              </span>
              {item.outcome === 'failed' && (
                <button
                  type="button"
                  className="button secondary compact"
                  disabled={busy}
                  onClick={() => {
                    setCheckRequested(true)
                    setActiveJobId(undefined)
                    check.mutate(item.sourceId)
                  }}
                >
                  Retry
                </button>
              )}
            </div>
          ))}
          {run.error && (
            <InlineNotice tone="danger">{run.error.detail}</InlineNotice>
          )}
        </section>
      )}
      {check.error && (
        <InlineNotice tone="danger">{errorMessage(check.error)}</InlineNotice>
      )}

      {sources.length === 0 ? (
        <EmptyState
          compact
          title="No approved sources"
          message="Add an official SCE page or tariff PDF to start managed checks."
        />
      ) : (
        <ul className="structured-list">
          {sources.map((source) => (
            <li key={source.id}>
              <div>
                <strong>{source.name}</strong>
                <span>
                  {source.displayOrigin} · {source.sourceType} ·{' '}
                  {source.lastResult
                    ? statusLabel(source.lastResult.outcome)
                    : 'Never checked'}
                  {source.lastCheckedAt
                    ? ` · ${new Date(source.lastCheckedAt).toLocaleString()}`
                    : ''}
                </span>
                <small>
                  {source.candidateCount} candidates ·{' '}
                  {source.lastResult?.artifactCount ?? 0} artifacts in latest
                  run
                </small>
                <details className="technical-details">
                  <summary>Technical details</summary>
                  <code>{source.technicalUrl}</code>
                  <code>{source.parserId}</code>
                  {source.lastResult?.errorDetail && (
                    <code>{source.lastResult.errorDetail}</code>
                  )}
                </details>
              </div>
              <div className="inline-actions">
                <span className={`pill ${source.enabled ? 'success' : ''}`}>
                  {source.enabled ? 'Enabled' : 'Disabled'}
                </span>
                <button
                  type="button"
                  className="button ghost compact"
                  disabled={busy || !source.enabled}
                  onClick={() => {
                    setCheckRequested(true)
                    setActiveJobId(undefined)
                    check.mutate(source.id)
                  }}
                >
                  Check source
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <details className="technical-details">
        <summary>Source check history</summary>
        {history.isLoading ? (
          <LoadingState label="Loading check history…" />
        ) : history.error ? (
          <ErrorState error={history.error} />
        ) : (
          <ul className="structured-list">
            {(history.data ?? []).map((item) => (
              <li key={item.id}>
                <div>
                  <strong>
                    {item.completedAt
                      ? new Date(item.completedAt).toLocaleString()
                      : 'Queued source check'}
                  </strong>
                  <span>
                    {statusLabel(item.status)} · {item.successes} succeeded ·{' '}
                    {item.failures} failed · {item.candidates} candidates
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </details>
    </section>
  )
}

export function LegacySourceManager({ sources, loading, error }: { sources: RateSource[]; loading: boolean; error: unknown }) {
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
  const client = useQueryClient()
  const [serviceId, setServiceId] = useState(services[0]?.id ?? '')
  const [editing, setEditing] = useState<RateAdjustment>()
  const [component, setComponent] = useState('custom_per_kwh')
  const [value, setValue] = useState('0')
  const [unit, setUnit] = useState('per_kwh')
  const [provenance, setProvenance] = useState('Administrator entry')
  const [reason, setReason] = useState('')
  const [evidenceReference, setEvidenceReference] = useState('')
  const [effectiveFrom, setEffectiveFrom] = useState(
    new Date().toISOString().slice(0, 16),
  )
  const [effectiveThrough, setEffectiveThrough] = useState('')
  const rows = useQuery({
    queryKey: ['rate-adjustments', serviceId],
    queryFn: () =>
      request(
        `/api/v1/admin/utility-accounts/${serviceId}/adjustments`,
        {},
        adaptRateAdjustments,
      ),
    enabled: Boolean(serviceId),
  })
  const payload = () => ({
    component,
    value,
    unit,
    provenance,
    reason,
    evidence_reference: evidenceReference || null,
    effective_from: new Date(effectiveFrom).toISOString(),
    effective_to: effectiveThrough
      ? new Date(effectiveThrough).toISOString()
      : null,
    enabled: true,
    ...(editing ? { revision: editing.revision } : {}),
  })
  const save = useMutation({
    mutationFn: () =>
      request(
        editing
          ? `/api/v1/admin/utility-accounts/${serviceId}/adjustments/${editing.id}`
          : `/api/v1/admin/utility-accounts/${serviceId}/adjustments`,
        json(editing ? 'PATCH' : 'POST', payload()),
      ),
    onSuccess: async () => {
      setEditing(undefined)
      setReason('')
      setEvidenceReference('')
      await client.invalidateQueries({
        queryKey: ['rate-adjustments', serviceId],
      })
    },
  })
  const remove = useMutation({
    mutationFn: (item: RateAdjustment) =>
      request(
        `/api/v1/admin/utility-accounts/${serviceId}/adjustments/${item.id}?revision=${item.revision}`,
        json('DELETE'),
      ),
    onSuccess: async () =>
      client.invalidateQueries({ queryKey: ['rate-adjustments', serviceId] }),
  })
  const beginEdit = (item: RateAdjustment) => {
    setEditing(item)
    setComponent(item.component)
    setValue(item.value)
    setUnit(item.unit)
    setProvenance(item.provenance)
    setReason(item.reason)
    setEvidenceReference(item.evidenceReference ?? '')
    setEffectiveFrom(item.effectiveFrom.slice(0, 16))
    setEffectiveThrough(item.effectiveThrough?.slice(0, 16) ?? '')
  }
  if (services.length === 0) {
    return (
      <EmptyState
        title="No electric service"
        message="Create an electric service before adding rate adjustments."
      />
    )
  }
  return (
    <section className="rate-advanced-panel">
      <div className="section-heading">
        <div>
          <h3>Manual adjustments</h3>
          <p>
            Effective-dated charges and credits are versioned independently and
            audited with their reason and evidence.
          </p>
        </div>
        <label>
          <span>Electric service</span>
          <select
            value={serviceId}
            onChange={(event) => {
              setServiceId(event.target.value)
              setEditing(undefined)
            }}
          >
            {services.map((service) => (
              <option key={service.id} value={service.id}>
                {service.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <InlineNotice>
        <ShieldCheck size={16} /> Fixed charges and credits remain scoped to
        this Electric Service and apply only once when full-account authority
        permits them.
      </InlineNotice>
      <form
        className="structured-editor"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate()
        }}
      >
        <div className="form-grid">
          <label>
            <span>Component</span>
            <select
              value={component}
              onChange={(event) => {
                setComponent(event.target.value)
              }}
            >
              <option value="custom_per_kwh">Custom per-kWh charge</option>
              <option value="custom_fixed">Custom fixed charge</option>
              <option value="baseline_credit">Baseline credit</option>
              <option value="service_charge">Service charge</option>
              <option value="tax_fee">Tax or fee</option>
              <option value="cca_generation">CCA generation</option>
              <option value="direct_access">Direct Access</option>
            </select>
          </label>
          <label>
            <span>Value</span>
            <input
              inputMode="decimal"
              value={value}
              onChange={(event) => {
                setValue(event.target.value)
              }}
              required
            />
          </label>
          <label>
            <span>Unit</span>
            <select
              value={unit}
              onChange={(event) => {
                setUnit(event.target.value)
              }}
            >
              <option value="per_kwh">Per kWh</option>
              <option value="fixed">Fixed</option>
              <option value="percent">Percent</option>
              <option value="included">Included</option>
            </select>
          </label>
          <label>
            <span>Effective from</span>
            <input
              type="datetime-local"
              value={effectiveFrom}
              onChange={(event) => {
                setEffectiveFrom(event.target.value)
              }}
              required
            />
          </label>
          <label>
            <span>Effective through (optional)</span>
            <input
              type="datetime-local"
              value={effectiveThrough}
              onChange={(event) => {
                setEffectiveThrough(event.target.value)
              }}
            />
          </label>
          <label>
            <span>Provenance</span>
            <input
              value={provenance}
              onChange={(event) => {
                setProvenance(event.target.value)
              }}
              required
            />
          </label>
          <label className="wide">
            <span>Reason</span>
            <input
              value={reason}
              onChange={(event) => {
                setReason(event.target.value)
              }}
              required
              minLength={3}
            />
          </label>
          <label className="wide">
            <span>Evidence reference (optional)</span>
            <input
              value={evidenceReference}
              onChange={(event) => {
                setEvidenceReference(event.target.value)
              }}
              placeholder="Bill, tariff, or internal approval reference"
            />
          </label>
        </div>
        <div className="form-actions">
          {editing && (
            <button
              type="button"
              className="button secondary"
              onClick={() => {
                setEditing(undefined)
              }}
            >
              Cancel edit
            </button>
          )}
          <button
            type="submit"
            className="button primary"
            disabled={save.isPending || reason.trim().length < 3}
          >
            {save.isPending
              ? 'Saving…'
              : editing
                ? 'Save revision'
                : 'Add adjustment'}
          </button>
        </div>
      </form>
      {(save.error || remove.error) && (
        <InlineNotice tone="danger">
          {errorMessage(save.error ?? remove.error)}
        </InlineNotice>
      )}
      {rows.isLoading ? (
        <LoadingState label="Loading adjustments…" />
      ) : rows.error ? (
        <ErrorState
          error={rows.error}
          retry={() => {
            void rows.refetch()
          }}
        />
      ) : (rows.data?.length ?? 0) === 0 ? (
        <EmptyState
          compact
          title="No manual adjustments"
          message="The rate plan remains authoritative until an effective-dated adjustment is added."
        />
      ) : (
        <ul className="structured-list">
          {rows.data?.map((item) => (
            <li key={item.id}>
              <div>
                <strong>{statusLabel(item.component)}</strong>
                <span>
                  {item.value} {statusLabel(item.unit)} · effective{' '}
                  {new Date(item.effectiveFrom).toLocaleString()}
                  {item.effectiveThrough
                    ? ` through ${new Date(item.effectiveThrough).toLocaleString()}`
                    : ''}
                </span>
                <small>
                  {item.reason} · {item.provenance}
                  {item.evidenceReference
                    ? ` · evidence ${item.evidenceReference}`
                    : ''}
                </small>
              </div>
              <div className="inline-actions">
                <button
                  type="button"
                  className="button ghost compact"
                  onClick={() => {
                    beginEdit(item)
                  }}
                >
                  <Pencil size={15} /> Edit
                </button>
                <button
                  type="button"
                  className="button danger compact"
                  disabled={remove.isPending}
                  onClick={() => {
                    remove.mutate(item)
                  }}
                >
                  <Trash2 size={15} /> Remove
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export function LegacyAdjustments({ services }: { services: ElectricService[] }) {
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
