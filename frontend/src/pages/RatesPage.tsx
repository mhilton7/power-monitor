import { useMutation, useQuery } from '@tanstack/react-query'
import { CheckCircle2, ClipboardCheck, Copy, FileJson, Plus, RefreshCw, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CanonicalAction } from '../actions'
import { api } from '../api'
import { EmptyState, ErrorState, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'
import type { ManagedRatePlan, PricingModel } from '../rates'
import type { Site } from '../types'

interface CandidateSummary { id: string; status: string; summary: { plan_code?: string; material_differences?: number }; created_at: string }
interface RateAssignmentSummary { id: string; utility_account_id: string; rate_version_id: string; effective_from: string; effective_to?: string }
interface AccountSummary { id: string; name: string; site_id: string }
type ModelFilter = 'all' | PricingModel

function pricingLabel(model?: PricingModel) {
  if (model === 'flat') return 'Flat'
  if (model === 'tiered') return 'Billing-cycle tiered'
  if (model === 'time_of_use_tiered') return 'Hybrid TOU + tiered'
  return 'Time of use'
}

function downloadJson(filename: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export function RatesPage({
  canManage,
}: {
  canManage: boolean
  canImportBills?: boolean
}) {
  const navigate = useNavigate()
  const importInput = useRef<HTMLInputElement>(null)
  const [jobId, setJobId] = useState<string>()
  const [modelFilter, setModelFilter] = useState<ModelFilter>('all')
  const query = useQuery({ queryKey: ['managed-rates'], queryFn: () => api<ManagedRatePlan[]>('/api/v1/rates/plans') })
  const assignments = useQuery({ queryKey: ['rate-assignments'], queryFn: () => api<RateAssignmentSummary[]>('/api/v1/rates/assignments') })
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => api<AccountSummary[]>('/api/v1/utility-accounts') })
  const sites = useQuery({ queryKey: ['sites'], queryFn: () => api<Site[]>('/api/v1/sites') })
  const candidateQuery = useQuery({ queryKey: ['rate-candidates'], queryFn: () => api<CandidateSummary[]>('/api/v1/admin/rate-candidates'), enabled: canManage })
  const check = useMutation({ mutationFn: () => api<{ job_id: string }>('/api/v1/admin/rate-sources/check-now', { method: 'POST' }), onSuccess: (job) => { setJobId(job.job_id) } })
  const syncJob = useQuery({ queryKey: ['rate-sync-job', jobId], queryFn: () => api<{ status: string; progress: { completed?: number; source_ids?: string[] }; result?: { candidate_count?: number } }>(`/api/v1/jobs/${jobId}`), enabled: Boolean(jobId), refetchInterval: (result) => ['queued', 'running'].includes(result.state.data?.status ?? 'queued') ? 1500 : false })
  const clone = useMutation({ mutationFn: (id: string) => api<{ editor_url: string }>(`/api/v1/rates/plans/${id}/clone`, { method: 'POST' }), onSuccess: (result) => navigate(result.editor_url) })
  const activate = useMutation({ mutationFn: (id: string) => api<{ status: string }>(`/api/v1/rates/versions/${id}/activate`, { method: 'POST' }), onSuccess: () => void query.refetch() })
  const pending = candidateQuery.data?.filter((item) => item.status === 'pending_review') ?? []
  const displayedPlans = query.data?.filter((plan) => {
    const version = plan.versions.find((item) => item.is_active) ?? plan.versions[0]
    return modelFilter === 'all' || version?.pricing_model === modelFilter
  })

  async function exportVersion(id: string, code: string) {
    const result = await api<{ document: unknown; integrity_sha256: string }>(`/api/v1/rates/versions/${id}/export`)
    downloadJson(`${code.toLowerCase()}-rate-plan.json`, { ...result.document as object, integrity_sha256: result.integrity_sha256 })
  }

  async function importPlan(file?: File) {
    if (!file) return
    const body = new FormData()
    body.append('upload', file)
    const result = await api<{ plan_id: string; version_id: string }>('/api/v1/rates/import', { method: 'POST', body })
    void navigate(`/billing/rate-plans/${result.plan_id}/versions/${result.version_id}`)
  }

  return (
    <>
      <PageTitle
        eyebrow="Tariff library"
        title="Rate plans"
        description="Effective-dated, source-backed versions preserve historical estimates while new utility changes remain reviewable."
        actions={canManage && <>
          <CanonicalAction id="rate_source.check" surface="workspace_header"><button className="button secondary" disabled={check.isPending} onClick={() => { check.mutate(); }}><RefreshCw size={16} className={check.isPending ? 'spin' : ''} /> Check SCE now</button></CanonicalAction>
          <CanonicalAction id="rate_plan.create_custom" surface="workspace_header"><button className="button primary" onClick={() => navigate('/billing/rate-plans/new')}><Plus size={17} /> Create custom plan</button></CanonicalAction>
        </>}
      />

      {check.isSuccess && <p className="form-success" role="status">SCE source check {syncJob.data?.status ?? 'queued'} · {syncJob.data?.progress.completed ?? 0}/{syncJob.data?.progress.source_ids?.length ?? 4} sources · {syncJob.data?.result?.candidate_count ?? 0} candidates.</p>}
      {check.error && <ErrorState error={check.error} />}
      <div className="rate-library-toolbar" aria-label="Rate library filters">
        <label>Pricing model<select value={modelFilter} onChange={(event) => { setModelFilter(event.target.value as ModelFilter) }}><option value="all">All pricing models</option><option value="flat">Flat</option><option value="time_of_use">Time of use</option><option value="tiered">Billing-cycle tiered</option><option value="time_of_use_tiered">Hybrid TOU + tiered</option></select></label>
        <span>{displayedPlans?.length ?? 0} of {query.data?.length ?? 0} plans</span>
      </div>

      {query.isLoading ? <LoadingState /> : query.error ? <ErrorState error={query.error} retry={() => void query.refetch()} /> : displayedPlans?.length ? (
        <div className="rate-grid">
          {displayedPlans.map((plan) => {
            const version = plan.versions.find((item) => item.is_active) ?? plan.versions[0]
            const now = Date.now()
            const versionIds = new Set(plan.versions.map((item) => item.id))
            const versionAssignments = assignments.data?.filter((item) => versionIds.has(item.rate_version_id)) ?? []
            const effective = versionAssignments.find((item) => new Date(item.effective_from).getTime() <= now && (!item.effective_to || new Date(item.effective_to).getTime() > now))
            const future = versionAssignments.filter((item) => new Date(item.effective_from).getTime() > now).sort((left, right) => new Date(left.effective_from).getTime() - new Date(right.effective_from).getTime())[0]
            const assignedAccount = accounts.data?.find((item) => item.id === effective?.utility_account_id)
            const futureAccount = accounts.data?.find((item) => item.id === future?.utility_account_id)
            const assignedSite = sites.data?.find((item) => item.id === assignedAccount?.site_id)
            const pendingCandidate = pending.some((item) => item.summary.plan_code === plan.code)
            const stateLabel = plan.status === 'retired' ? 'Retired' : assignedAccount ? `Effective now · ${assignedAccount.name}${assignedSite ? ` / ${assignedSite.name}` : ''}` : futureAccount && future ? `Assigned · effective ${new Date(future.effective_from).toLocaleDateString()} · ${futureAccount.name}` : versionAssignments.length ? 'Expired assignment' : pendingCandidate ? 'Candidate awaiting approval' : version?.status === 'draft' ? 'Draft' : 'Published · Available'
            return <Panel key={plan.id} className="rate-card">
              <header className="rate-card-head"><div><span className="plan-code">{plan.code}</span><h2>{plan.name}</h2></div><StatusPill status={effective ? 'healthy' : version?.status ?? plan.status} label={stateLabel} /></header>
              <p>{plan.description || 'No plan description has been provided.'}</p>
              {version && <div className="rate-model-summary"><StatusPill status="info" label={pricingLabel(version.pricing_model)} /><span>{version.tier_count ? `${version.tier_count} tiers · ${version.threshold_basis?.replaceAll('_', ' ')}` : 'No billing-cycle tiers'}</span></div>}
              {pendingCandidate && <StatusPill status="pending" label="Candidate awaiting approval" />}
              {version && <>
                <dl className="rate-meta"><div><dt>Effective</dt><dd>{version.effective_from}</dd></div><div><dt>Source checked</dt><dd>{version.source_checked_at?.slice(0, 10) ?? 'Manual'}</dd></div><div><dt>Version</dt><dd>v{version.version}</dd></div><div><dt>Integrity</dt><dd title={version.integrity_sha256}>{version.integrity_sha256.slice(0, 10)}…</dd></div></dl>
                <div className="source-note"><ClipboardCheck size={17} /><p>{version.source_label || (plan.plan_kind === 'custom' ? 'Administrator-defined plan' : 'SCE archived evidence')}</p></div>
                <footer><button className="link-button" onClick={() => navigate(`/billing/rate-plans/${plan.id}/versions/${version.id}`)}>View details</button><div>
                  <button className="button ghost" onClick={() => void exportVersion(version.id, plan.code)}><FileJson size={15} /> Export</button>
                  {canManage && <CanonicalAction id="rate_plan.clone" surface="resource_row" resourceKey={plan.id}><button className="button secondary" disabled={clone.isPending} onClick={() => { clone.mutate(plan.id); }}><Copy size={15} /> Clone</button></CanonicalAction>}
                  {canManage && version.status !== 'draft' && <button className="button primary" onClick={() => navigate(`/billing/accounts?rate_version_id=${encodeURIComponent(version.id)}`)}><Plus size={15} /> Assign to utility account</button>}
                  {canManage && version.status === 'draft' && <button className="button primary" disabled={activate.isPending} onClick={() => { activate.mutate(version.id); }}><CheckCircle2 size={15} /> Activate</button>}
                </div></footer>
              </>}
            </Panel>
          })}
        </div>
      ) : <EmptyState title={query.data?.length ? 'No matching rate plans' : 'No rate plans'} message={query.data?.length ? 'Choose another pricing model filter.' : 'Run first-time initialization to install effective-dated SCE presets.'} />}

      <p className="rate-disclaimer">This estimate is not an SCE bill. Rates shown may differ when generation is provided by a CCA or Direct Access provider.</p>
      <p className="cross-page-setup"><strong>Plans in this library are not assigned automatically.</strong> <button className="link-button" onClick={() => navigate('/billing/accounts')}>Configure utility account</button></p>

      {canManage && <Panel title="Plan portability" eyebrow="Controlled import and export" actions={<>
        <input ref={importInput} className="sr-only" type="file" accept="application/json,.json" onChange={(event) => void importPlan(event.target.files?.[0])} />
        <button className="button secondary" onClick={() => importInput.current?.click()}><Upload size={15} /> Import plan</button>
      </>}><p className="panel-copy">Imports are schema-validated, size-limited, and always created as inactive drafts. Exports contain exact decimal strings and no credentials.</p></Panel>}

      <Panel title="Activation checklist" eyebrow="Before calculating costs"><div className="checklist"><div><span>1</span><p><strong>Verify the plan code</strong><small>Find it on the current SCE bill.</small></p></div><div><span>2</span><p><strong>Review archived evidence</strong><small>Confirm source dates, exact prices, and any conflicts.</small></p></div><div><span>3</span><p><strong>Set the cost scope</strong><small>One-CT devices default to energy-only.</small></p></div><div><span>4</span><p><strong>Configure provider adjustments</strong><small>CCA and Direct Access generation remain explicit.</small></p></div></div></Panel>
    </>
  )
}
