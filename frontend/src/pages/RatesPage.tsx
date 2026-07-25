import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  CheckCircle2,
  ClipboardCheck,
  Copy,
  FileJson,
  Plus,
  RefreshCw,
  RotateCcw,
  Upload,
  X,
} from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CanonicalAction } from '../actions'
import { api } from '../api'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageTitle,
  Panel,
  StatusPill,
} from '../components/UI'
import type { ManagedRatePlan, PricingModel } from '../rates'
import type { Site } from '../types'

interface CandidateSummary {
  id: string
  status: string
  summary: { plan_code?: string; material_differences?: number }
  created_at: string
}

interface RateAssignmentSummary {
  id: string
  utility_account_id: string
  rate_version_id: string
  effective_from: string
  effective_to?: string
}

interface AccountSummary {
  id: string
  name: string
  site_id: string
  status?: 'active' | 'archived'
  active_rate_version_id?: string
}

interface AssignmentTarget {
  plan: ManagedRatePlan
  versionId: string
}

interface AssignmentResult {
  id: string
  effective_from: string
  effective_to?: string
  effective_now: boolean
  replaced_assignment_ids: string[]
}

interface RatePlanDependencies {
  plan_id: string
  plan_code: string
  plan_name: string
  plan_kind: string
  origin: 'custom' | 'managed'
  status: string
  lifecycle_revision: number
  version_count: number
  active_assignments: Array<{ id: string; utility_account_id: string }>
  future_assignments: Array<{ id: string; utility_account_id: string }>
  active_account_pointers: Array<{ id: string; name: string }>
  historical_assignment_count: number
  historical_calculation_count: number
  report_count: number
  source_evidence_count: number
  bill_import_count: number
  managed_candidate_count: number
  cloned_plan_count: number
  candidate_version_reference_count: number
  permanent_draft_deletion_eligible: boolean
  removal_blocked: boolean
  dependency_actions: string[]
  restore_eligible: boolean
}

type ModelFilter = 'all' | PricingModel
type LifecycleView = 'active' | 'removed_or_retired'

function pricingLabel(model?: PricingModel) {
  if (model === 'flat') return 'Flat'
  if (model === 'tiered') return 'Billing-cycle tiered'
  if (model === 'time_of_use_tiered') return 'Hybrid TOU + tiered'
  return 'Time of use'
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'The rate-plan action could not be completed.'
}

function downloadJson(filename: string, value: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }),
  )
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export function RatesPage({
  canManage,
  canRemove = false,
  canRestore = false,
}: {
  canManage: boolean
  canImportBills?: boolean
  canRemove?: boolean
  canRestore?: boolean
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const importInput = useRef<HTMLInputElement>(null)
  const [jobId, setJobId] = useState<string>()
  const [modelFilter, setModelFilter] = useState<ModelFilter>('all')
  const [lifecycleView, setLifecycleView] = useState<LifecycleView>('active')
  const [lifecyclePlan, setLifecyclePlan] = useState<ManagedRatePlan>()
  const [reason, setReason] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [assignmentTarget, setAssignmentTarget] = useState<AssignmentTarget>()
  const [assignmentAccountId, setAssignmentAccountId] = useState('')
  const [assignmentTiming, setAssignmentTiming] = useState<'now' | 'scheduled'>(
    'now',
  )
  const [assignmentEffectiveFrom, setAssignmentEffectiveFrom] = useState('')
  const [assignmentReason, setAssignmentReason] = useState(
    'Administrator selected a new active rate plan',
  )
  const [assignmentSuccess, setAssignmentSuccess] = useState('')

  const query = useQuery({
    queryKey: ['managed-rates', lifecycleView],
    queryFn: () =>
      api<ManagedRatePlan[]>(`/api/v1/rates/plans?status=${lifecycleView}`),
  })
  const assignments = useQuery({
    queryKey: ['rate-assignments'],
    queryFn: () => api<RateAssignmentSummary[]>('/api/v1/rates/assignments'),
  })
  const accounts = useQuery({
    queryKey: ['accounts'],
    queryFn: () => api<AccountSummary[]>('/api/v1/utility-accounts'),
  })
  const sites = useQuery({
    queryKey: ['sites'],
    queryFn: () => api<Site[]>('/api/v1/sites'),
  })
  const candidateQuery = useQuery({
    queryKey: ['rate-candidates'],
    queryFn: () => api<CandidateSummary[]>('/api/v1/admin/rate-candidates'),
    enabled: canManage,
  })
  const dependencyQuery = useQuery({
    queryKey: ['rate-plan-dependencies', lifecyclePlan?.id],
    queryFn: () =>
      api<RatePlanDependencies>(
        `/api/v1/admin/rate-plans/${lifecyclePlan?.id}/dependencies`,
      ),
    enabled: Boolean(lifecyclePlan),
  })
  const check = useMutation({
    mutationFn: () =>
      api<{ job_id: string }>('/api/v1/admin/rate-sources/check-now', {
        method: 'POST',
      }),
    onSuccess: (job) => {
      setJobId(job.job_id)
    },
  })
  const syncJob = useQuery({
    queryKey: ['rate-sync-job', jobId],
    queryFn: () =>
      api<{
        status: string
        progress: { completed?: number; source_ids?: string[] }
        result?: { candidate_count?: number }
      }>(`/api/v1/jobs/${jobId}`),
    enabled: Boolean(jobId),
    refetchInterval: (result) =>
      ['queued', 'running'].includes(result.state.data?.status ?? 'queued')
        ? 1500
        : false,
  })
  const clone = useMutation({
    mutationFn: (id: string) =>
      api<{ editor_url: string }>(`/api/v1/rates/plans/${id}/clone`, {
        method: 'POST',
      }),
    onSuccess: (result) => navigate(result.editor_url),
  })
  const activate = useMutation({
    mutationFn: (id: string) =>
      api<{ status: string }>(`/api/v1/rates/versions/${id}/activate`, {
        method: 'POST',
      }),
    onSuccess: () => void query.refetch(),
  })
  const remove = useMutation({
    mutationFn: async ({
      plan,
      dependencies,
    }: {
      plan: ManagedRatePlan
      dependencies: RatePlanDependencies
    }) => {
      const payload = {
        expected_revision: plan.lifecycle_revision,
        confirmation,
        reason,
      }
      if (dependencies.permanent_draft_deletion_eligible) {
        return api<void>(`/api/v1/admin/rate-plan-drafts/${plan.id}`, {
          method: 'DELETE',
          body: JSON.stringify(payload),
        })
      }
      return api(`/api/v1/admin/rate-plans/${plan.id}/remove`, {
        method: 'POST',
        body: JSON.stringify({
          ...payload,
          idempotency_key: `remove-${plan.id}-${crypto.randomUUID()}`,
        }),
      })
    },
    onSuccess: () => {
      setLifecyclePlan(undefined)
      setReason('')
      setConfirmation('')
      void query.refetch()
    },
  })
  const restore = useMutation({
    mutationFn: (plan: ManagedRatePlan) =>
      api(`/api/v1/admin/rate-plans/${plan.id}/restore`, {
        method: 'POST',
        body: JSON.stringify({
          expected_revision: plan.lifecycle_revision,
          reason,
          idempotency_key: `restore-${plan.id}-${crypto.randomUUID()}`,
        }),
      }),
    onSuccess: () => {
      setLifecyclePlan(undefined)
      setReason('')
      void query.refetch()
    },
  })
  const assign = useMutation({
    mutationFn: ({
      accountId,
      versionId,
      effectiveFrom,
      reason: requestedReason,
    }: {
      accountId: string
      versionId: string
      effectiveFrom: string
      reason: string
    }) =>
      api<AssignmentResult>(
        `/api/v1/admin/utility-accounts/${accountId}/rate-assignments`,
        {
          method: 'POST',
          body: JSON.stringify({
            rate_version_id: versionId,
            effective_from: effectiveFrom,
            assignment_reason: requestedReason,
            replace_current: true,
          }),
        },
      ),
    onSuccess: async (result, variables) => {
      const account = accounts.data?.find(
        (item) => item.id === variables.accountId,
      )
      const plan = assignmentTarget?.plan
      setAssignmentSuccess(
        result.effective_now
          ? `${plan?.name ?? 'The selected plan'} is now active for ${
              account?.name ?? 'the utility account'
            }. The previous assignment remains in history.`
          : `${plan?.name ?? 'The selected plan'} is scheduled for ${
              account?.name ?? 'the utility account'
            }.`,
      )
      setAssignmentTarget(undefined)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['rate-assignments'] }),
        queryClient.invalidateQueries({ queryKey: ['accounts'] }),
        queryClient.invalidateQueries({ queryKey: ['utility-accounts'] }),
        queryClient.invalidateQueries({ queryKey: ['status-indicator-values'] }),
      ])
    },
  })

  const pending =
    candidateQuery.data?.filter((item) => item.status === 'pending_review') ?? []
  const displayedPlans = query.data?.filter((plan) => {
    const version =
      plan.versions.find((item) => item.is_active) ?? plan.versions[0]
    return modelFilter === 'all' || version?.pricing_model === modelFilter
  })

  async function exportVersion(id: string, code: string) {
    const result = await api<{ document: unknown; integrity_sha256: string }>(
      `/api/v1/rates/versions/${id}/export`,
    )
    downloadJson(`${code.toLowerCase()}-rate-plan.json`, {
      ...(result.document as object),
      integrity_sha256: result.integrity_sha256,
    })
  }

  async function importPlan(file?: File) {
    if (!file) return
    const body = new FormData()
    body.append('upload', file)
    const result = await api<{ plan_id: string; version_id: string }>(
      '/api/v1/rates/import',
      { method: 'POST', body },
    )
    void navigate(
      `/billing/rate-plans/${result.plan_id}/versions/${result.version_id}`,
    )
  }

  function openLifecycle(plan: ManagedRatePlan) {
    setLifecyclePlan(plan)
    setReason('')
    setConfirmation('')
  }

  function openAssignment(plan: ManagedRatePlan, versionId: string) {
    const activeAccounts =
      accounts.data?.filter((account) => account.status !== 'archived') ?? []
    setAssignmentTarget({ plan, versionId })
    setAssignmentAccountId(activeAccounts.length === 1 ? activeAccounts[0]?.id ?? '' : '')
    setAssignmentTiming('now')
    setAssignmentEffectiveFrom('')
    setAssignmentReason('Administrator selected a new active rate plan')
    assign.reset()
  }

  function currentAssignmentFor(accountId: string) {
    const now = Date.now()
    return assignments.data?.find(
      (item) =>
        item.utility_account_id === accountId &&
        new Date(item.effective_from).getTime() <= now &&
        (!item.effective_to || new Date(item.effective_to).getTime() > now),
    )
  }

  function versionLabel(versionId?: string) {
    if (!versionId) return 'No current rate plan'
    for (const plan of query.data ?? []) {
      const version = plan.versions.find((item) => item.id === versionId)
      if (version) return `${plan.name} · v${version.version}`
    }
    return 'Current published rate version'
  }

  const dependencies = dependencyQuery.data
  const lifecycleIsRemoved =
    lifecyclePlan?.status === 'removed' || lifecyclePlan?.status === 'retired'
  const confirmationMatches =
    confirmation.trim().toLocaleLowerCase() ===
      lifecyclePlan?.code.trim().toLocaleLowerCase() ||
    confirmation.trim().toLocaleLowerCase() ===
      lifecyclePlan?.name.trim().toLocaleLowerCase()
  const removalReady =
    Boolean(dependencies) &&
    !dependencies?.removal_blocked &&
    reason.trim().length >= 3 &&
    confirmationMatches

  return (
    <>
      <PageTitle
        eyebrow="Tariff library"
        title="Rate plans"
        description="Effective-dated, source-backed versions preserve historical estimates while new utility changes remain reviewable."
        actions={
          canManage &&
          lifecycleView === 'active' && (
            <>
              <CanonicalAction id="rate_source.check" surface="workspace_header">
                <button
                  className="button secondary"
                  disabled={check.isPending}
                  onClick={() => {
                    check.mutate()
                  }}
                >
                  <RefreshCw
                    size={16}
                    className={check.isPending ? 'spin' : ''}
                  />{' '}
                  Check SCE now
                </button>
              </CanonicalAction>
              <CanonicalAction
                id="rate_plan.create_custom"
                surface="workspace_header"
              >
                <button
                  className="button primary"
                  onClick={() => navigate('/billing/rate-plans/new')}
                >
                  <Plus size={17} /> Create custom plan
                </button>
              </CanonicalAction>
            </>
          )
        }
      />

      {check.isSuccess && (
        <p className="form-success" role="status">
          SCE source check {syncJob.data?.status ?? 'queued'} ·{' '}
          {syncJob.data?.progress.completed ?? 0}/
          {syncJob.data?.progress.source_ids?.length ?? 4} sources ·{' '}
          {syncJob.data?.result?.candidate_count ?? 0} candidates.
        </p>
      )}
      {check.error && <ErrorState error={check.error} />}
      {assignmentSuccess && (
        <p className="form-success" role="status">
          <CheckCircle2 size={16} /> {assignmentSuccess}
        </p>
      )}

      <div className="rate-library-toolbar" aria-label="Rate library filters">
        <div className="segmented-control" aria-label="Rate plan lifecycle">
          <button
            aria-selected={lifecycleView === 'active'}
            onClick={() => {
              setLifecycleView('active')
            }}
          >
            Active
          </button>
          <button
            aria-selected={lifecycleView === 'removed_or_retired'}
            onClick={() => {
              setLifecycleView('removed_or_retired')
            }}
          >
            Removed / Retired
          </button>
        </div>
        <label>
          Pricing model
          <select
            value={modelFilter}
            onChange={(event) => {
              setModelFilter(event.target.value as ModelFilter)
            }}
          >
            <option value="all">All pricing models</option>
            <option value="flat">Flat</option>
            <option value="time_of_use">Time of use</option>
            <option value="tiered">Billing-cycle tiered</option>
            <option value="time_of_use_tiered">Hybrid TOU + tiered</option>
          </select>
        </label>
        <span>
          {displayedPlans?.length ?? 0} of {query.data?.length ?? 0} plans
        </span>
      </div>

      {query.isLoading ? (
        <LoadingState />
      ) : query.error ? (
        <ErrorState error={query.error} retry={() => void query.refetch()} />
      ) : displayedPlans?.length ? (
        <div className="rate-grid">
          {displayedPlans.map((plan) => {
            const version =
              plan.versions.find((item) => item.is_active) ?? plan.versions[0]
            const now = Date.now()
            const versionIds = new Set(plan.versions.map((item) => item.id))
            const versionAssignments =
              assignments.data?.filter((item) =>
                versionIds.has(item.rate_version_id),
              ) ?? []
            const effective = versionAssignments.find(
              (item) =>
                new Date(item.effective_from).getTime() <= now &&
                (!item.effective_to ||
                  new Date(item.effective_to).getTime() > now),
            )
            const future = versionAssignments
              .filter((item) => new Date(item.effective_from).getTime() > now)
              .sort(
                (left, right) =>
                  new Date(left.effective_from).getTime() -
                  new Date(right.effective_from).getTime(),
              )[0]
            const assignedAccount = accounts.data?.find(
              (item) => item.id === effective?.utility_account_id,
            )
            const futureAccount = accounts.data?.find(
              (item) => item.id === future?.utility_account_id,
            )
            const assignedSite = sites.data?.find(
              (item) => item.id === assignedAccount?.site_id,
            )
            const pendingCandidate = pending.some(
              (item) => item.summary.plan_code === plan.code,
            )
            const unavailable =
              plan.status === 'removed' || plan.status === 'retired'
            const stateLabel = unavailable
              ? `${plan.status === 'retired' ? 'Retired' : 'Removed'}${
                  plan.removed_at
                    ? ` · ${new Date(plan.removed_at).toLocaleDateString()}`
                    : ''
                }`
              : assignedAccount
                ? `Effective now · ${assignedAccount.name}${
                    assignedSite ? ` / ${assignedSite.name}` : ''
                  }`
                : futureAccount && future
                  ? `Assigned · effective ${new Date(
                      future.effective_from,
                    ).toLocaleDateString()} · ${futureAccount.name}`
                  : versionAssignments.length
                    ? 'Expired assignment'
                    : pendingCandidate
                      ? 'Candidate awaiting approval'
                      : version?.status === 'draft'
                        ? 'Draft'
                        : 'Published · Available'
            return (
              <Panel key={plan.id} className="rate-card">
                <header className="rate-card-head">
                  <div>
                    <span className="plan-code">{plan.code}</span>
                    <h2>{plan.name}</h2>
                  </div>
                  <StatusPill
                    status={effective ? 'healthy' : plan.status}
                    label={stateLabel}
                  />
                </header>
                <p>{plan.description || 'No plan description has been provided.'}</p>
                {unavailable && (
                  <div className="rate-removal-note">
                    <strong>Future assignments disabled</strong>
                    <span>
                      {plan.removal_reason || 'No removal reason was recorded.'}
                    </span>
                  </div>
                )}
                {version && (
                  <div className="rate-model-summary">
                    <StatusPill
                      status="info"
                      label={pricingLabel(version.pricing_model)}
                    />
                    <span>
                      {version.tier_count
                        ? `${version.tier_count} tiers · ${version.threshold_basis?.replaceAll(
                            '_',
                            ' ',
                          )}`
                        : 'No billing-cycle tiers'}
                    </span>
                  </div>
                )}
                {pendingCandidate && (
                  <StatusPill
                    status="pending"
                    label="Candidate awaiting approval"
                  />
                )}
                {version && (
                  <>
                    <dl className="rate-meta">
                      <div>
                        <dt>Effective</dt>
                        <dd>{version.effective_from}</dd>
                      </div>
                      <div>
                        <dt>Source checked</dt>
                        <dd>
                          {version.source_checked_at?.slice(0, 10) ?? 'Manual'}
                        </dd>
                      </div>
                      <div>
                        <dt>Version</dt>
                        <dd>v{version.version}</dd>
                      </div>
                      <div>
                        <dt>Integrity</dt>
                        <dd title={version.integrity_sha256}>
                          {version.integrity_sha256.slice(0, 10)}…
                        </dd>
                      </div>
                    </dl>
                    <div className="source-note">
                      <ClipboardCheck size={17} />
                      <p>
                        {version.source_label ||
                          (plan.plan_kind === 'custom'
                            ? 'Administrator-defined plan'
                            : 'SCE archived evidence')}
                      </p>
                    </div>
                    <footer className="rate-card-actions">
                      <button
                        className="link-button rate-card-details"
                        onClick={() =>
                          navigate(
                            `/billing/rate-plans/${plan.id}/versions/${version.id}`,
                          )
                        }
                      >
                        View details
                      </button>
                      <div className="rate-card-action-group">
                        <button
                          className="button ghost"
                          onClick={() =>
                            void exportVersion(version.id, plan.code)
                          }
                        >
                          <FileJson size={15} /> Export
                        </button>
                        {!unavailable && canManage && (
                          <CanonicalAction
                            id="rate_plan.clone"
                            surface="resource_row"
                            resourceKey={plan.id}
                          >
                            <button
                              className="button secondary"
                              disabled={clone.isPending}
                              onClick={() => {
                                clone.mutate(plan.id)
                              }}
                            >
                              <Copy size={15} /> Clone
                            </button>
                          </CanonicalAction>
                        )}
                        {!unavailable &&
                          canManage &&
                          version.status !== 'draft' && (
                            <button
                              className="button primary"
                              onClick={() => {
                                openAssignment(plan, version.id)
                              }}
                            >
                              <Plus size={15} /> Use this plan
                            </button>
                          )}
                        {!unavailable &&
                          canManage &&
                          version.status === 'draft' && (
                            <button
                              className="button primary"
                              disabled={activate.isPending}
                              onClick={() => {
                                activate.mutate(version.id)
                              }}
                            >
                              <CheckCircle2 size={15} /> Activate
                            </button>
                          )}
                        {!unavailable &&
                          canRemove &&
                          lifecyclePlan?.id !== plan.id && (
                            <CanonicalAction
                              id="rate_plan.remove"
                              surface="resource_row"
                              resourceKey={plan.id}
                            >
                              <button
                                className="button ghost danger-text"
                                onClick={() => {
                                  openLifecycle(plan)
                                }}
                              >
                                <Archive size={15} /> Remove rate plan
                              </button>
                            </CanonicalAction>
                          )}
                        {unavailable &&
                          canRestore &&
                          lifecyclePlan?.id !== plan.id && (
                            <CanonicalAction
                              id="rate_plan.restore"
                              surface="resource_row"
                              resourceKey={plan.id}
                            >
                              <button
                                className="button secondary"
                                onClick={() => {
                                  openLifecycle(plan)
                                }}
                              >
                                <RotateCcw size={15} /> Restore
                              </button>
                            </CanonicalAction>
                          )}
                      </div>
                    </footer>
                  </>
                )}
              </Panel>
            )
          })}
        </div>
      ) : (
        <EmptyState
          title={
            query.data?.length
              ? 'No matching rate plans'
              : lifecycleView === 'active'
                ? 'No active rate plans'
                : 'No removed or retired rate plans'
          }
          message={
            query.data?.length
              ? 'Choose another pricing model filter.'
              : lifecycleView === 'active'
                ? 'Run first-time initialization to install effective-dated SCE presets.'
                : 'Plans removed from future use will appear here without losing history.'
          }
        />
      )}

      <p className="rate-disclaimer">
        This estimate is not an SCE bill. Rates shown may differ when generation is
        provided by a CCA or Direct Access provider.
      </p>
      <p className="cross-page-setup">
        <strong>Plans in this library are not assigned automatically.</strong>{' '}
        <button
          className="link-button"
          onClick={() => navigate('/billing/accounts')}
        >
          Configure utility account
        </button>
      </p>

      {canManage && lifecycleView === 'active' && (
        <Panel
          title="Plan portability"
          eyebrow="Controlled import and export"
          actions={
            <>
              <input
                ref={importInput}
                className="sr-only"
                type="file"
                accept="application/json,.json"
                onChange={(event) => void importPlan(event.target.files?.[0])}
              />
              <button
                className="button secondary"
                onClick={() => importInput.current?.click()}
              >
                <Upload size={15} /> Import plan
              </button>
            </>
          }
        >
          <p className="panel-copy">
            Imports are schema-validated, size-limited, and always created as
            inactive drafts. Exports contain exact decimal strings and no
            credentials.
          </p>
        </Panel>
      )}

      {lifecyclePlan && (
        <div className="modal-backdrop modal-top" role="presentation">
          <section
            className="confirm-dialog rate-plan-lifecycle-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={
              lifecycleIsRemoved ? 'Restore rate plan' : 'Remove rate plan'
            }
          >
            <header>
              <div>
                <span className="eyebrow">
                  {lifecycleIsRemoved
                    ? 'Restore future availability'
                    : 'Dependency-aware lifecycle'}
                </span>
                <h2>
                  {lifecycleIsRemoved
                    ? `Restore ${lifecyclePlan.code}?`
                    : `Remove ${lifecyclePlan.code}?`}
                </h2>
              </div>
              <button
                className="icon-button"
                aria-label="Close rate-plan action"
                onClick={() => {
                  setLifecyclePlan(undefined)
                }}
              >
                <X />
              </button>
            </header>

            {dependencyQuery.isLoading ? (
              <LoadingState label="Reviewing rate-plan dependencies…" />
            ) : dependencyQuery.error ? (
              <ErrorState
                error={dependencyQuery.error}
                retry={() => void dependencyQuery.refetch()}
              />
            ) : dependencies ? (
              <>
                <dl className="rate-plan-dependency-summary">
                  <div>
                    <dt>Pricing model</dt>
                    <dd>
                      {pricingLabel(
                        lifecyclePlan.versions[0]?.pricing_model,
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>Status</dt>
                    <dd>{lifecyclePlan.status}</dd>
                  </div>
                  <div>
                    <dt>Origin</dt>
                    <dd>{dependencies.origin}</dd>
                  </div>
                  <div>
                    <dt>Versions</dt>
                    <dd>{dependencies.version_count}</dd>
                  </div>
                  <div>
                    <dt>Active assignments</dt>
                    <dd>
                      {dependencies.active_assignments.length +
                        dependencies.active_account_pointers.length}
                    </dd>
                  </div>
                  <div>
                    <dt>Future assignments</dt>
                    <dd>{dependencies.future_assignments.length}</dd>
                  </div>
                  <div>
                    <dt>Historical assignments</dt>
                    <dd>{dependencies.historical_assignment_count}</dd>
                  </div>
                  <div>
                    <dt>Historical calculations</dt>
                    <dd>{dependencies.historical_calculation_count}</dd>
                  </div>
                  <div>
                    <dt>Source evidence</dt>
                    <dd>{dependencies.source_evidence_count}</dd>
                  </div>
                  <div>
                    <dt>Imported bills</dt>
                    <dd>{dependencies.bill_import_count}</dd>
                  </div>
                </dl>

                {dependencies.removal_blocked && !lifecycleIsRemoved && (
                  <div className="form-error" role="alert">
                    <strong>Removal is blocked</strong>
                    <span>
                      Replace or end the active and future utility-account
                      assignments first. No account will be left silently without
                      a rate.
                    </span>
                  </div>
                )}
                {!lifecycleIsRemoved &&
                  dependencies.permanent_draft_deletion_eligible && (
                    <div className="high-risk-warning">
                      <strong>Unused draft will be permanently deleted</strong>
                      <p>
                        This custom draft has never been published and has no
                        assignments, calculations, evidence, bills, candidates, or
                        clones requiring preservation.
                      </p>
                    </div>
                  )}
                {!lifecycleIsRemoved &&
                  !dependencies.permanent_draft_deletion_eligible &&
                  !dependencies.removal_blocked && (
                    <div className="removal-impact">
                      <strong>Historical records are retained</strong>
                      <p>
                        Versions, assignments, costs, reports, imported bills,
                        source evidence, candidates, and audit history remain
                        intact. Only future selection and assignment are disabled.
                      </p>
                    </div>
                  )}

                <label>
                  <span>
                    {lifecycleIsRemoved ? 'Restore reason' : 'Removal reason'}
                  </span>
                  <textarea
                    value={reason}
                    maxLength={500}
                    onChange={(event) => {
                      setReason(event.target.value)
                    }}
                  />
                </label>
                {!lifecycleIsRemoved && (
                  <label>
                    <span>Type exact plan name or code</span>
                    <input
                      value={confirmation}
                      onChange={(event) => {
                        setConfirmation(event.target.value)
                      }}
                    />
                    <small>
                      Enter {lifecyclePlan.name} or {lifecyclePlan.code}
                    </small>
                  </label>
                )}
              </>
            ) : null}

            {(remove.error || restore.error) && (
              <div className="form-error" role="alert">
                <strong>Rate-plan action was not completed</strong>
                <span>{errorMessage(remove.error ?? restore.error)}</span>
              </div>
            )}
            <footer>
              <button
                className="button secondary"
                onClick={() => {
                  setLifecyclePlan(undefined)
                }}
              >
                Cancel
              </button>
              {dependencies?.removal_blocked && !lifecycleIsRemoved ? (
                <button
                  className="button primary"
                  onClick={() => navigate('/billing/accounts')}
                >
                  Resolve assignments
                </button>
              ) : lifecycleIsRemoved ? (
                <CanonicalAction
                  id="rate_plan.restore"
                  surface="dialog"
                  resourceKey={lifecyclePlan.id}
                >
                  <button
                    className="button primary"
                    disabled={
                      restore.isPending ||
                      reason.trim().length < 3 ||
                      !dependencies
                    }
                    onClick={() => {
                      restore.mutate(lifecyclePlan)
                    }}
                  >
                    {restore.isPending ? 'Restoring…' : 'Restore rate plan'}
                  </button>
                </CanonicalAction>
              ) : (
                <CanonicalAction
                  id="rate_plan.remove"
                  surface="dialog"
                  resourceKey={lifecyclePlan.id}
                >
                  <button
                    className="button danger"
                    disabled={remove.isPending || !removalReady}
                    onClick={() => {
                      if (dependencies) {
                        remove.mutate({
                          plan: lifecyclePlan,
                          dependencies,
                        })
                      }
                    }}
                  >
                    {remove.isPending
                      ? 'Removing…'
                      : dependencies?.permanent_draft_deletion_eligible
                        ? 'Delete unused draft'
                        : 'Remove rate plan'}
                  </button>
                </CanonicalAction>
              )}
            </footer>
          </section>
        </div>
      )}

      {assignmentTarget && (
        <div
          className="modal-backdrop modal-top"
          role="presentation"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) {
              setAssignmentTarget(undefined)
            }
          }}
        >
          <section
            className="confirm-dialog rate-assignment-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="rate-assignment-title"
          >
            <header>
              <div>
                <span className="eyebrow">Effective-dated account rate</span>
                <h2 id="rate-assignment-title">Use {assignmentTarget.plan.name}</h2>
              </div>
              <button
                className="icon-button"
                aria-label="Close rate assignment"
                onClick={() => {
                  setAssignmentTarget(undefined)
                }}
              >
                <X />
              </button>
            </header>

            {accounts.isLoading || assignments.isLoading ? (
              <LoadingState label="Loading utility-account assignments…" />
            ) : accounts.error || assignments.error ? (
              <ErrorState
                error={accounts.error ?? assignments.error}
                retry={() => {
                  void accounts.refetch()
                  void assignments.refetch()
                }}
              />
            ) : (accounts.data?.filter(
                (account) => account.status !== 'archived',
              ).length ?? 0) === 0 ? (
              <EmptyState
                title="Create a utility account first"
                message="A published rate plan becomes active through an effective-dated utility-account assignment."
                action={
                  <button
                    className="button primary"
                    onClick={() =>
                      navigate(
                        `/billing/accounts?rate_version_id=${encodeURIComponent(
                          assignmentTarget.versionId,
                        )}&create=account`,
                      )
                    }
                  >
                    Create utility account
                  </button>
                }
              />
            ) : (
              <>
                <label>
                  <span>Utility account</span>
                  <select
                    value={assignmentAccountId}
                    onChange={(event) => {
                      setAssignmentAccountId(event.target.value)
                      assign.reset()
                    }}
                  >
                    <option value="">Choose an account</option>
                    {accounts.data
                      ?.filter((account) => account.status !== 'archived')
                      .map((account) => (
                        <option key={account.id} value={account.id}>
                          {account.name}
                          {sites.data?.find((site) => site.id === account.site_id)
                            ? ` · ${
                                sites.data.find(
                                  (site) => site.id === account.site_id,
                                )?.name
                              }`
                            : ''}
                        </option>
                      ))}
                  </select>
                </label>

                {assignmentAccountId && (
                  <dl className="rate-switch-summary">
                    <div>
                      <dt>Current plan</dt>
                      <dd>
                        {versionLabel(
                          currentAssignmentFor(assignmentAccountId)?.rate_version_id,
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>New plan</dt>
                      <dd>{assignmentTarget.plan.name}</dd>
                    </div>
                  </dl>
                )}

                <fieldset className="rate-assignment-timing">
                  <legend>When should this plan take effect?</legend>
                  <label>
                    <input
                      type="radio"
                      name="rate-assignment-timing"
                      checked={assignmentTiming === 'now'}
                      onChange={() => {
                        setAssignmentTiming('now')
                      }}
                    />
                    <span>
                      <strong>Switch now</strong>
                      <small>
                        End the current assignment at the switch time and preserve
                        it in history.
                      </small>
                    </span>
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="rate-assignment-timing"
                      checked={assignmentTiming === 'scheduled'}
                      onChange={() => {
                        setAssignmentTiming('scheduled')
                      }}
                    />
                    <span>
                      <strong>Schedule a change</strong>
                      <small>Keep the current plan active until the selected time.</small>
                    </span>
                  </label>
                </fieldset>

                {assignmentTiming === 'scheduled' && (
                  <label>
                    <span>Effective from</span>
                    <input
                      type="datetime-local"
                      value={assignmentEffectiveFrom}
                      min={new Date().toISOString().slice(0, 16)}
                      onChange={(event) => {
                        setAssignmentEffectiveFrom(event.target.value)
                      }}
                    />
                  </label>
                )}

                <label>
                  <span>Assignment reason</span>
                  <input
                    value={assignmentReason}
                    maxLength={500}
                    onChange={(event) => {
                      setAssignmentReason(event.target.value)
                    }}
                  />
                </label>

                {assignmentAccountId &&
                  currentAssignmentFor(assignmentAccountId)?.rate_version_id ===
                    assignmentTarget.versionId && (
                    <p className="form-success" role="status">
                      <CheckCircle2 size={16} /> This plan is already active for the
                      selected account.
                    </p>
                  )}

                {assign.error && (
                  <div className="form-error" role="alert">
                    <strong>Rate plan was not changed</strong>
                    <span>{errorMessage(assign.error)}</span>
                  </div>
                )}
              </>
            )}

            <footer>
              <button
                className="button secondary"
                onClick={() => {
                  setAssignmentTarget(undefined)
                }}
              >
                Cancel
              </button>
              {(accounts.data?.filter((account) => account.status !== 'archived')
                .length ?? 0) > 0 && (
                <button
                  className="button primary"
                  disabled={
                    assign.isPending ||
                    !assignmentAccountId ||
                    !assignmentReason.trim() ||
                    (assignmentTiming === 'scheduled' &&
                      !assignmentEffectiveFrom) ||
                    currentAssignmentFor(assignmentAccountId)?.rate_version_id ===
                      assignmentTarget.versionId
                  }
                  onClick={() => {
                    if (!assignmentAccountId) return
                    assign.mutate({
                      accountId: assignmentAccountId,
                      versionId: assignmentTarget.versionId,
                      effectiveFrom:
                        assignmentTiming === 'now'
                          ? new Date().toISOString()
                          : new Date(assignmentEffectiveFrom).toISOString(),
                      reason: assignmentReason.trim(),
                    })
                  }}
                >
                  {assign.isPending
                    ? 'Saving…'
                    : assignmentTiming === 'now'
                      ? 'Switch active plan'
                      : 'Schedule plan'}
                </button>
              )}
            </footer>
          </section>
        </div>
      )}
    </>
  )
}
