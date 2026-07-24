import { useMutation, useQuery } from '@tanstack/react-query'
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
  const importInput = useRef<HTMLInputElement>(null)
  const [jobId, setJobId] = useState<string>()
  const [modelFilter, setModelFilter] = useState<ModelFilter>('all')
  const [lifecycleView, setLifecycleView] = useState<LifecycleView>('active')
  const [lifecyclePlan, setLifecyclePlan] = useState<ManagedRatePlan>()
  const [reason, setReason] = useState('')
  const [confirmation, setConfirmation] = useState('')

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
        idempotency_key: `remove-${plan.id}-${crypto.randomUUID()}`,
      }
      if (dependencies.permanent_draft_deletion_eligible) {
        return api<void>(`/api/v1/admin/rate-plan-drafts/${plan.id}`, {
          method: 'DELETE',
          body: JSON.stringify(payload),
        })
      }
      return api(`/api/v1/admin/rate-plans/${plan.id}/remove`, {
        method: 'POST',
        body: JSON.stringify(payload),
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
                              onClick={() =>
                                navigate(
                                  `/billing/accounts?rate_version_id=${encodeURIComponent(
                                    version.id,
                                  )}`,
                                )
                              }
                            >
                              <Plus size={15} /> Assign to utility account
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
    </>
  )
}
